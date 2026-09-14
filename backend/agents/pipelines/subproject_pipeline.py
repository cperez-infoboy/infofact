"""Sub-project pipeline: decomposition into bounded contexts with contracts.

Multi-pass pipeline (5 passes):

  Pass 1 — decomposition skeleton (boundaries only: name, responsibility,
  entity_codes, bounded_contexts).
  Pass 2 — gap pass: re-scan MER entities not assigned to any sub-project.
  Pass 3 — stack + contracts + diagram (skeleton-constrained).
  Pass 4 — deterministic validation: partition check, contract references,
  _validate_mermaid for component_diagram_mermaid; auto-repair if invalid.
  Pass 5 — LLM critique (oversplit/undersplit/orphan/missing_contract/etc.).

Takes the MerResult (entity names + bounded contexts), AdrResult (architecture
decisions), and NfrResult (stack recommendations) and proposes a set of
sub-projects with explicit contracts between them, plus a Mermaid component
diagram (graph TD).

The LLM generates the component diagram Mermaid string directly (free-form
graph layout), unlike the MER erDiagram which is rendered in code. Pass 4
validates and repairs the generated Mermaid.

Design notes (mirrors mer_pipeline.py patterns):

- Does NOT write to the DB. Returns a SubProjectResult dataclass consumed by
  the analysis assembler / store layer.
- Uses structured_llm with narrow per-pass schemas for fence-tolerant
  structured output.
- Retry policy: centralized via _invoke_with_retry from _resilience.
- Prompts are in Spanish neutro (no voseo, no spanglish) following project
  conventions for LLM-facing text.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from backend.agents.llm import structured_llm
from backend.agents.pipelines._diagram_colors import (
    BC_PALETTE,
    class_def,
)
from backend.agents.pipelines._resilience import (
    DEFAULT_CONCURRENCY,
    _format_feedback,
    _format_goals,
    _invoke_with_retry,
    _repair_mermaid,
    _sanitize_mermaid,
    _sanitize_mermaid_id,
    _validate_mermaid,
    invoke_structured_resilient,
)

if TYPE_CHECKING:
    from backend.agents.pipelines.adr_pipeline import AdrResult
    from backend.agents.pipelines.mer_pipeline import MerResult
    from backend.agents.pipelines.nfr_pipeline import NfrResult
    from backend.agents.pipelines.project_pipeline import ProjectResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM-facing schemas
# ---------------------------------------------------------------------------


class SubProjectSchema(BaseModel):
    """One proposed sub-project from the architectural decomposition."""

    project_name: str = Field(
        default="",
        description=(
            "Nombre del proyecto (area) al que pertenece este sub-proyecto. "
            "Debe coincidir con un nombre de los proyectos descubiertos."
        ),
    )
    name: str = Field(
        description=(
            "Nombre del sub-proyecto en kebab-case (ej. 'order-service', "
            "'billing-api', 'identity-service')."
        ),
    )
    responsibility: str = Field(
        description=(
            "Responsabilidad principal del sub-proyecto en una oracion clara."
        ),
    )
    stack: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Stack tecnologico del sub-proyecto: {language, framework, "
            "database}. Ej. {'language': 'Python', 'framework': 'FastAPI', "
            "'database': 'PostgreSQL'}."
        ),
    )
    bounded_contexts: list[str] = Field(
        default_factory=list,
        description=(
            "Contextos delimitados DDD que posee este sub-proyecto (ej. "
            "['Sales', 'Shipping'])."
        ),
    )
    nfr_codes: list[str] = Field(
        default_factory=list,
        description=(
            "Codigos REQ-XXXX de NFRs que aplican especificamente a este "
            "sub-proyecto. Tomarlos literalmente del input."
        ),
    )
    entity_codes: list[str] = Field(
        default_factory=list,
        description=(
            "Nombres de las entidades de dominio que posee este sub-proyecto "
            "(ej. ['Order', 'OrderItem']). Deben coincidir con entidades del "
            "MER."
        ),
    )
    goal_codes: list[str] = Field(
        default_factory=list,
        description=(
            "Codigos GOAL-XXXX de los goals que este sub-proyecto cubre."
        ),
    )


class ContractSchema(BaseModel):
    """One contract between two sub-projects."""

    from_subproject: str = Field(
        description=(
            "Nombre del sub-proyecto origen (el que llama/expone). Debe "
            "coincidir con un nombre en sub_projects."
        ),
    )
    to_subproject: str = Field(
        description=(
            "Nombre del sub-proyecto destino (el que recibe/consume). Debe "
            "coincidir con un nombre en sub_projects."
        ),
    )
    contract_type: str = Field(
        description=(
            "Tipo de contrato: exactamente uno de openapi | asyncapi | "
            "event_schema."
        ),
    )
    name: str = Field(
        description=(
            "Endpoint o evento del contrato (ej. 'POST /orders', "
            "'order.created')."
        ),
    )
    spec: str = Field(
        default="",
        description=(
            "Especificacion del contrato: OpenAPI YAML, AsyncAPI YAML, o JSON "
            "Schema segun contract_type."
        ),
    )
    description: str = Field(default="")


class SubProjectResultSchema(BaseModel):
    """Wrapper for the sub-project detailing result (Pass 3)."""

    sub_projects: list[SubProjectSchema] = Field(
        default_factory=list,
    )
    contracts: list[ContractSchema] = Field(
        default_factory=list,
    )
    component_diagram_mermaid: str = Field(
        default="",
    )
    component_diagram_description: str = Field(
        default="",
        description=(
            "Descripcion breve (2-3 oraciones, en el idioma original) del "
            "diagrama de componentes: que sub-proyectos existen, como se "
            "relacionan via contratos y que bounded contexts cubren."
        ),
    )
    # Lotes fallidos del detailing batcheado (degradacion graciosa).
    failed_batches: int = 0


class ContractData(BaseModel):
    """One contract between sub-projects WITHOUT the full spec text.

    The OpenAPI/AsyncAPI YAML lives in :class:`ContractSchema.spec` when the
    caller needs it; including that fat text in the batched structured output
    is what overflowed the completion budget (Planitrack2.0: 304 entities ->
    always finish_reason=length -> detailing_failed with 0 sub-projects).
    """

    from_subproject: str = Field(
        description="Nombre del sub-proyecto origen (debe existir en sub_projects).",
    )
    to_subproject: str = Field(
        description="Nombre del sub-proyecto destino (debe existir en sub_projects).",
    )
    contract_type: str = Field(
        description="Tipo de contrato: openapi | asyncapi | event_schema.",
    )
    name: str = Field(
        description="Endpoint o evento del contrato (ej. 'POST /orders', 'order.created').",
    )
    description: str = Field(
        default="",
        description="Descripcion breve del contrato (1 oracion).",
    )


class SubProjectBatchDetailSchema(BaseModel):
    """Narrow schema for the batched Pass 3 (one batch per project area).

    A single structured call for ALL sub-projects + contracts-with-full-specs
    + component diagram overflows the completion budget at Planitrack2.0
    scale. The batch detail keeps the fat ``spec`` OUT of the structured
    output: contracts carry only endpoint/event + short description.
    """

    sub_projects: list[SubProjectSchema] = Field(
        default_factory=list,
    )
    contracts: list[ContractData] = Field(
        default_factory=list,
    )
    # Lotes del detailing que fallaron (reportado por propose_subprojects).
    failed_batches: int = 0


def _render_component_diagram(
    sub_projects: list[SubProjectSchema],
    contracts: list[ContractSchema],
) -> str:
    """Render the component diagram deterministically (Mermaid flowchart TD).

    El LLM ya no genera el diagrama (viajaba en el output estructurado y era
    parte del overflow de tokens); se construye desde los sub-proyectos y
    contratos ya validados: un subgraph por sub-proyecto, aristas etiquetadas
    por contrato y una paleta fija por area.
    """
    if not sub_projects:
        return ""
    lines: list[str] = ["graph TD"]
    class_defs: list[str] = []
    for i, sp in enumerate(sub_projects):
        sid = _sanitize_mermaid_id(sp.name)
        cls = f"sp_{i % len(BC_PALETTE)}"
        fill, stroke = BC_PALETTE[i % len(BC_PALETTE)]
        class_defs.append(class_def(cls, fill, stroke))
        label = sp.name.replace("\"", "")
        lines.append(f"    subgraph {sid} [\"{label}\"]")
        comp = _sanitize_mermaid_id(f"{sp.name}_core")
        lines.append(f"        {comp}[\"Nucleo {label}\"]:::{cls}")
        lines.append("    end")
    for j, c in enumerate(contracts):
        if c.contract_type not in {"openapi", "asyncapi", "event_schema"}:
            continue
        src = _sanitize_mermaid_id(f"{c.from_subproject}_core")
        dst = _sanitize_mermaid_id(f"{c.to_subproject}_core")
        lines.append(f"    {src} -->|{c.name}| {dst}")
    lines.append("")
    lines.extend(f"    {cd}" for cd in class_defs)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Narrow per-pass schemas (multi-pass pipeline)
# ---------------------------------------------------------------------------


class SubProjectSkeleton(BaseModel):
    """Narrow schema for Pass 1/2: decomposition boundaries only."""

    project_name: str = Field(
        default="",
        description="Nombre del proyecto (area) al que pertenece",
    )
    name: str = Field(description="Nombre del sub-proyecto en kebab-case")
    responsibility: str = Field(
        description="Responsabilidad principal en una oracion"
    )
    entity_codes: list[str] = Field(
        default_factory=list,
        description="Nombres de entidades del MER que posee",
    )
    bounded_contexts: list[str] = Field(
        default_factory=list,
        description="Contextos delimitados DDD",
    )


class SubProjectSkeletonSchema(BaseModel):
    """Pass 1 and Pass 2 output schema."""

    sub_projects: list[SubProjectSkeleton]


class SubProjectCritiqueFinding(BaseModel):
    """Pass 5: critic finding."""

    subproject_name: str = Field(description="Sub-proyecto afectado o 'GLOBAL'")
    issue_type: str = Field(
        description=(
            "oversplit | undersplit | orphan_entity | missing_contract | "
            "spurious_contract | invalid_spec | responsibility_overlap | "
            "invalid_mermaid"
        ),
    )
    description: str
    severity: str = Field(description="blocker | warning | info")


class SubProjectCritiqueSchema(BaseModel):
    """Pass 5 output schema."""

    findings: list[SubProjectCritiqueFinding]


# ---------------------------------------------------------------------------
# Prompts (Spanish neutro — no voseo, no spanglish)
# ---------------------------------------------------------------------------

_SUBPROJECT_DISCOVERY_PROMPT = (
    "Eres un arquitecto de software. A partir de las entidades de dominio y "
    "sus bounded contexts, propone una descomposicion en sub-proyectos. Tu "
    "UNICA tarea es definir los limites: nombre, responsabilidad, entidades "
    "y contextos. NO disenes stack, contratos ni diagramas.\n\n"
    "Reglas:\n"
    "- IDIOMA: manten el idioma original.\n"
    "- NO hagas descomposicion hexagonal interna. Solo limites ENTRE "
    "sub-proyectos.\n"
    "- Prefiere pocos sub-proyectos con responsabilidad clara sobre muchos "
    "pequenos.\n"
    "- Toda entidad del MER debe pertenecer a exactamente un sub-proyecto.\n"
    "- Si el dominio es pequeno, devuelve un solo sub-proyecto monolitico.\n"
    "Devuelve SOLO el objeto estructurado."
)

_SUBPROJECT_GAP_PASS_PROMPT = (
    "Eres un arquitecto de software. Las siguientes entidades del MER NO "
    "fueron asignadas a ningun sub-proyecto en la primera pasada. Revisa cada "
    "una y determina si justifica un sub-proyecto nuevo o si debe asignarse a "
    "uno existente. Si ninguna genera un sub-proyecto nuevo, devuelve una "
    "lista vacia.\n"
    "Devuelve SOLO el objeto estructurado."
)

_SUBPROJECT_DETAIL_PROMPT = (
    "Eres un arquitecto de software. Recibiras entidades de dominio, "
    "decisiones arquitectonicas (ADRs), stack recomendado por capa y una "
    "lista de SUB-PROYECTOS PRELIMINARES que debes enriquecer con stack y "
    "contratos. Detalla SOLO los sub-proyectos de la lista; no inventes "
    "otros.\n\n"
    "Reglas:\n"
    "- IDIOMA: manten el idioma original.\n"
    "- NO hagas descomposicion hexagonal interna.\n"
    "- Para cada par de sub-proyectos detallados que necesite comunicarse, "
    "define un contrato (openapi para REST, asyncapi para mensajeria/eventos, "
    "event_schema para esquemas de eventos).\n"
    "- contract_type debe ser exactamente uno de: openapi, asyncapi, "
    "event_schema.\n"
    "- Los contratos van SIN especificacion completa: solo endpoint/evento "
    "(name), tipo y una descripcion breve de una oracion. NO escribas YAML "
    "ni JSON Schema en la salida.\n"
    "- from_subproject y to_subproject deben coincidir con nombres de los "
    "sub-proyectos detallados o del INVENTARIO COMPLETO de vecinos (ver "
    "abajo). Un contrato cuyo destino es un vecino de otra area es valido "
    "y necesario: define TODAS las colaboraciones que el dominio exija, "
    "incluidas las cruzadas entre areas.\n"
    "- entity_codes: las entidades listadas de cada sub-proyecto deben "
    "permanecer asignadas a ese sub-proyecto.\n"
    "- NO generes diagramas: el diagrama de componentes se construye despues.\n"
    "Devuelve SOLO el objeto estructurado."
)

_SUBPROJECT_CRITIQUE_PROMPT = (
    "Eres un crítico de arquitectura de software. Se te da la descomposicion "
    "en sub-proyectos con contratos generada a partir del MER y los ADRs. Tu "
    "tarea es identificar problemas:\n\n"
    "Categorias de fallo a revisar:\n"
    "1. oversplit: demasiados sub-proyectos para el tamano del dominio.\n"
    "2. undersplit: un solo sub-proyecto monolitico cuando se justifica dividir.\n"
    "3. orphan_entity: entidad del MER sin sub-proyecto asignado.\n"
    "4. missing_contract: sub-proyectos que se comunican sin contrato.\n"
    "5. spurious_contract: contrato innecesario entre sub-proyectos.\n"
    "6. invalid_spec: especificacion de contrato malformada.\n"
    "7. responsibility_overlap: dos sub-proyectos con responsabilidades solapadas.\n"
    "8. invalid_mermaid: diagrama de componentes con errores de sintaxis.\n\n"
    "Si la descomposicion esta correcta, devuelve findings vacio.\n"
    "Devuelve SOLO el objeto estructurado."
)

# Original prompt kept for backward compatibility.
SUBPROJECT_PROMPT = _SUBPROJECT_DETAIL_PROMPT


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SubProjectResult:
    """Output of the sub-project pipeline."""

    sub_projects: list[SubProjectSchema] = field(default_factory=list)
    contracts: list[ContractSchema] = field(default_factory=list)
    component_diagram_mermaid: str = ""
    component_diagram_description: str = ""
    # Lotes del detailing que fallaron (degradacion graciosa por proyecto).
    failed_batches: int = 0
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_mer_summary(mer_result) -> str:
    """Build the user-facing summary of MER entities + bounded contexts."""
    lines: list[str] = []

    if mer_result.entities:
        lines.append("ENTIDADES DE DOMINIO:")
        for e in mer_result.entities:
            entry = f"- {e.name}"
            if e.bounded_context:
                entry += f" [{e.bounded_context}]"
            if e.aggregate_root:
                entry += " (aggregate root)"
            lines.append(entry)
        lines.append("")

    contexts: dict[str, list[str]] = {}
    for e in mer_result.entities:
        bc = e.bounded_context or "(sin contexto)"
        contexts.setdefault(bc, []).append(e.name)
    if len(contexts) > 1 or "(sin contexto)" not in contexts:
        lines.append("CONTEXTOS DELIMITADOS:")
        for bc, ents in contexts.items():
            lines.append(f"- {bc}: {', '.join(ents)}")
        lines.append("")

    return "\n".join(lines)


def _build_adr_summary(adr_result) -> str:
    """Build the user-facing summary of ADR decisions."""
    lines: list[str] = []

    if adr_result.adrs:
        lines.append("DECISIONES ARQUITECTONICAS (ADRs):")
        for a in adr_result.adrs:
            entry = f"- {a.title}"
            if a.decision:
                entry += f" — {a.decision}"
            lines.append(entry)
        lines.append("")

    return "\n".join(lines)


def _build_nfr_stack_summary(nfr_result) -> str:
    """Build the user-facing summary of NFR stack recommendations."""
    lines: list[str] = []

    if nfr_result and nfr_result.stack:
        lines.append("STACK RECOMENDADO POR CAPA:")
        for s in nfr_result.stack:
            lines.append(f"- [{s.layer}] {s.technology}")
        lines.append("")

    if nfr_result and nfr_result.patterns:
        lines.append(f"PATRONES: {nfr_result.patterns}")
        lines.append("")

    if nfr_result and nfr_result.data_consistency:
        lines.append(
            f"ESTRATEGIA DE CONSISTENCIA: {nfr_result.data_consistency}"
        )
        lines.append("")

    return "\n".join(lines)


def _build_subproject_context(
    mer_result,
    adr_result,
    nfr_result,
    project_name: str,
    project_description: str,
    goals: list[Any] | None = None,
    feedback: str = "",
) -> str:
    """Build shared context text (project + goals + MER + ADR + NFR)."""
    lines: list[str] = []
    if feedback:
        lines.append(_format_feedback(feedback).rstrip())
        lines.append("")
    if project_name:
        lines.append(f"PROYECTO: {project_name}")
    if project_description:
        lines.append(f"DESCRIPCION: {project_description}")
    if goals:
        lines.append("")
        lines.append(
            _format_goals(
                goals, section_title="OBJETIVOS DEL SISTEMA"
            ).rstrip()
        )
    lines.append("")

    if mer_result is not None:
        lines.append(_build_mer_summary(mer_result))

    if adr_result is not None and getattr(adr_result, "adrs", None):
        lines.append(_build_adr_summary(adr_result))

    if nfr_result is not None:
        lines.append(_build_nfr_stack_summary(nfr_result))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pass 1: decomposition skeleton (boundaries only)
# ---------------------------------------------------------------------------


async def _discover_subproject_skeletons(
    mer_result,
    adr_result=None,
    nfr_result=None,
    *,
    project_result=None,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
    feedback: str = "",
) -> list[SubProjectSkeleton]:
    """Pass 1: discover sub-project boundaries (name, responsibility, entities)."""
    context = _build_subproject_context(
        mer_result, adr_result, nfr_result,
        project_name, project_description, goals, feedback=feedback,
    )
    # If project areas were discovered, add them as an explicit constraint.
    if project_result and project_result.projects:
        proj_lines = []
        for i, proj in enumerate(project_result.projects):
            proj_lines.append(
                f"- {proj.name} [{proj.domain_type}]: "
                f"BCs={', '.join(proj.bounded_contexts)}"
            )
        context += (
            "\nPROYECTOS DEFINIDOS (cada sub-proyecto debe pertenecer a uno):\n"
            + "\n".join(proj_lines)
            + "\n\nAsigna project_name a cada sub-proyecto.\n"
        )
    msgs = [("system", _SUBPROJECT_DISCOVERY_PROMPT), ("human", context)]
    llm = structured_llm(SubProjectSkeletonSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="subproject_discover"
        )
        return list(result.sub_projects)
    except Exception:
        logger.exception("_discover_subproject_skeletons: all retries exhausted")
        return []


# ---------------------------------------------------------------------------
# Pass 2: gap pass for orphan MER entities
# ---------------------------------------------------------------------------


async def _gap_pass_subproject(
    mer_result,
    skeletons: list[SubProjectSkeleton],
    adr_result=None,
    nfr_result=None,
    *,
    project_result=None,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
    feedback: str = "",
) -> list[SubProjectSkeleton]:
    """Pass 2: detect MER entities not assigned to any sub-project."""
    mer_entity_names = {e.name for e in mer_result.entities}
    assigned: set[str] = set()
    for s in skeletons:
        assigned.update(s.entity_codes)
    orphans = mer_entity_names - assigned
    if not orphans:
        return []

    # Build a focused context highlighting orphan entities.
    from types import SimpleNamespace

    orphan_entities = [
        SimpleNamespace(name=n, bounded_context="", aggregate_root=False)
        for n in sorted(orphans)
    ]
    focused_mer = SimpleNamespace(entities=orphan_entities)

    context = _build_subproject_context(
        focused_mer, adr_result, nfr_result,
        project_name, project_description, goals, feedback=feedback,
    )
    existing = ", ".join(s.name for s in skeletons) or "(ninguno)"
    context += (
        f"\nSUB-PROYECTOS YA DEFINIDOS: {existing}\n"
        "Las entidades anteriores NO fueron asignadas a ningun sub-proyecto. "
        "Determina si justifican un sub-proyecto nuevo o si deben asignarse a "
        "uno existente (en cuyo caso devuelve lista vacia).\n"
    )
    msgs = [("system", _SUBPROJECT_GAP_PASS_PROMPT), ("human", context)]
    llm = structured_llm(SubProjectSkeletonSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="subproject_gap_pass"
        )
        return list(result.sub_projects)
    except Exception:
        logger.exception("_gap_pass_subproject: all retries exhausted")
        return []


# ---------------------------------------------------------------------------
# Pass 3: stack + contracts + diagram (skeleton-constrained)
# ---------------------------------------------------------------------------


async def _detail_subprojects(
    mer_result,
    skeletons: list[SubProjectSkeleton],
    adr_result=None,
    nfr_result=None,
    *,
    project_result=None,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
    feedback: str = "",
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress=None,
) -> SubProjectResultSchema | None:
    """Pass 3: detailing batcheado por proyecto (stack + contratos).

    Un lote por area descubierta (project_name del skeleton); los skeletons
    sin proyecto van a un lote de reservas. Cada lote recibe SOLO sus
    skeletons y devuelve sub-proyectos + contratos LIVIANOS (sin spec
    completa: la spec fat era lo que truncaba el JSON del output). Un lote
    que falla persistentemente se descarta con warning; la pasada devuelve
    None solo si TODOS los lotes fallan.

    ``on_progress`` (opcional) se invoca con un mensaje corto por lote, igual
    que en los pipelines MER/process.
    """
    if not skeletons:
        return SubProjectResultSchema()

    context = _build_subproject_context(
        mer_result, adr_result, nfr_result,
        project_name, project_description, goals, feedback=feedback,
    )

    # Agrupar skeletons por area descubierta (o lote de reservas).
    groups: dict[str, list[SubProjectSkeleton]] = {}
    for s in skeletons:
        key = (s.project_name or "").strip() or "(sin proyecto)"
        groups.setdefault(key, []).append(s)
    batches: list[tuple[str, list[SubProjectSkeleton]]] = list(groups.items())

    total_batches = len(batches)
    state: dict[str, int] = {"done": 0, "failed": 0}

    # Inventario completo de la descomposicion: sin esto, un lote no conoce
    # los sub-proyectos de las DEMAS areas y los contratos cruzados son
    # imposibles (v4 Planitrack2.0: 8 contratos, todos intra-area, 5
    # sub-proyectos grandes en cero contratos).
    inventory_lines = [
        f"- {s.name} — {s.responsibility} (area: "
        f"{(s.project_name or '').strip() or '(sin area)'})"
        for s in skeletons
    ]

    async def _detail(batch_key: str, batch: list[SubProjectSkeleton]):
        batch_lines = []
        for s in batch:
            entities = ", ".join(s.entity_codes) if s.entity_codes else "(sin entidades)"
            contexts = ", ".join(s.bounded_contexts) if s.bounded_contexts else ""
            entry = f"- {s.name} — {s.responsibility} [entidades: {entities}]"
            if contexts:
                entry += f" [contextos: {contexts}]"
            batch_lines.append(entry)
        user_text = context + (
            "\nINVENTARIO COMPLETO de sub-proyectos de la descomposicion "
            "(usalo para decidir colaboraciones; los nombres de destino de "
            "los contratos pueden ser de este inventario):\n"
            + "\n".join(inventory_lines)
            + "\n\nSUB-PROYECTOS A DETALLAR (enriquece SOLO estos con stack y "
            "contratos; area: " + batch_key + "):\n"
            + "\n".join(batch_lines)
            + "\n"
        )
        msgs = [
            ("system", _SUBPROJECT_DETAIL_PROMPT),
            ("human", user_text),
        ]
        try:
            return await invoke_structured_resilient(
                lambda **kw: structured_llm(SubProjectBatchDetailSchema, **kw),
                msgs,
                context_label=(
                    f"subproject_detail ({len(batch)} sub-proyectos, area "
                    f"{batch_key})"
                ),
            )
        except Exception:
            logger.exception(
                "_detail_subprojects: lote del area %s fallo", batch_key
            )
            return None

    sem = asyncio.Semaphore(concurrency)

    async def _guarded(pair):
        batch_key, batch = pair
        async with sem:
            result = await _detail(batch_key, batch)
            state["done"] += 1
            if result is None:
                state["failed"] += 1
            if on_progress is not None:
                detail = (
                    f"lote {state['done']}/{total_batches} completado"
                    if result is not None
                    else f"lote {state['done']}/{total_batches} fallo, se "
                    "continua con el resto"
                )
                await on_progress(
                    f"sub-proyectos ({state['done']}/{total_batches} lotes): {detail}"
                )
            return result

    results = await asyncio.gather(
        *[_guarded(p) for p in batches], return_exceptions=True
    )
    schemas = [r for r in results if isinstance(r, SubProjectBatchDetailSchema)]
    failed = sum(
        1
        for r in results
        if r is None or isinstance(r, Exception)
    )
    logger.info(
        "subproject_detail: %d/%d lotes ok (%d fallidos) sobre %d skeletons",
        total_batches - failed, total_batches, failed, len(skeletons),
    )
    if not schemas:
        return None

    merged = SubProjectResultSchema()
    seen: set[str] = set()
    for schema in schemas:
        for sp in schema.sub_projects:
            key = sp.name.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            merged.sub_projects.append(sp)
        for contract in schema.contracts:
            merged.contracts.append(
                ContractSchema(
                    from_subproject=contract.from_subproject,
                    to_subproject=contract.to_subproject,
                    contract_type=contract.contract_type,
                    name=contract.name,
                    spec="",
                    description=contract.description,
                )
            )
    merged.failed_batches = failed
    return merged


# ---------------------------------------------------------------------------
# Pass 4: deterministic validation + Mermaid repair
# ---------------------------------------------------------------------------


def _drop_self_contracts(
    contracts: list[ContractSchema],
) -> tuple[list[ContractSchema], int]:
    """Descarta contratos cuyo origen y destino son el MISMO sub-proyecto.

    Un contrato modela comunicación ENTRE sistemas; un "auto-contrato"
    (SUB-007 -> SUB-007 en la corrida v2 de Planitrack2.0: 6 de 36) es un
    endpoint interno mal clasificado. Determinista, sin LLM.
    """
    kept = [c for c in contracts if c.from_subproject != c.to_subproject]
    dropped = len(contracts) - len(kept)
    if dropped:
        logger.info("_drop_self_contracts: %d auto-contratos descartados", dropped)
    return kept, dropped


def _reassign_orphan_subprojects(
    sub_projects: list[SubProjectSchema],
    project_result=None,
) -> tuple[list[SubProjectSchema], int]:
    """Re-asigna project_name de sub-proyectos huerfanos (determinista).

    El gap-pass puede crear sub-proyectos sin area; si hay un unico proyecto
    descubierto, heredan ese (no hay ambigüedad). Si hay varios, quedan con
    project_name vacio y la validacion reporta la deuda (decision humana).
    Devuelve (lista, cantidad re-asignados).
    """
    projects = getattr(project_result, "projects", None) or []
    names = [p.name for p in projects if getattr(p, "name", "")]
    if len(names) != 1:
        return sub_projects, 0
    assigned = 0
    for sp in sub_projects:
        if not (sp.project_name or "").strip():
            sp.project_name = names[0]
            assigned += 1
    if assigned:
        logger.info(
            "_reassign_orphan_subprojects: %d huerfanos -> %s",
            assigned, names[0],
        )
    return sub_projects, assigned


async def _validate_subprojects(
    sub_projects: list[SubProjectSchema],
    contracts: list[ContractSchema],
    component_diagram: str,
    mer_entity_names: set[str],
    project_result=None,
) -> tuple[list[str], bool]:
    """Pass 4: deterministic validation + _validate_mermaid for component diagram.

    Returns (warnings, diagram_repaired).
    """
    warnings: list[str] = []
    diagram_repaired = False

    sp_names = {sp.name for sp in sub_projects}

    # Check: partition — every MER entity belongs to at least one sub-project.
    assigned: set[str] = set()
    for sp in sub_projects:
        assigned.update(sp.entity_codes)
    orphans = mer_entity_names - assigned
    if orphans:
        warnings.append(
            f"Entidades del MER sin sub-proyecto: {sorted(orphans)}"
        )

    # Check: contract references exist.
    valid_contract_types = {"openapi", "asyncapi", "event_schema"}
    for c in contracts:
        if c.from_subproject not in sp_names:
            warnings.append(
                f"Contract '{c.name}' referencia sub-proyecto inexistente "
                f"(from): {c.from_subproject}"
            )
        if c.to_subproject not in sp_names:
            warnings.append(
                f"Contract '{c.name}' referencia sub-proyecto inexistente "
                f"(to): {c.to_subproject}"
            )
        if c.contract_type not in valid_contract_types:
            warnings.append(
                f"Contract '{c.name}' con tipo invalido: {c.contract_type}"
            )

    # Check: sub-projects sin area (project_name vacio) cuando hay areas.
    if project_result and getattr(project_result, "projects", None):
        known = {
            p.name for p in project_result.projects if getattr(p, "name", "")
        }
        unassigned = [
            sp.name
            for sp in sub_projects
            if not (sp.project_name or "").strip()
        ]
        if unassigned:
            warnings.append(
                f"Sub-proyectos sin proyecto asignado: {sorted(unassigned)}"
            )
        wrong = [
            sp.name
            for sp in sub_projects
            if (sp.project_name or "").strip() and sp.project_name not in known
        ]
        if wrong:
            warnings.append(
                f"Sub-proyectos con project_name inexistente: {sorted(wrong)}"
            )

    # Check: component diagram Mermaid syntax.
    if component_diagram:
        component_diagram = _sanitize_mermaid(component_diagram, "graph TD")
        is_valid, reason = _validate_mermaid(component_diagram, "graph TD")
        if not is_valid:
            warnings.append(
                f"Diagrama de componentes invalido: {reason}"
            )
            repaired = await _repair_mermaid(
                component_diagram, reason, "graph TD"
            )
            re_valid, re_reason = _validate_mermaid(repaired, "graph TD")
            if re_valid:
                diagram_repaired = True
                # Caller must update the field — return the repaired string
                # via a side-effect on the mutable warnings list isn't ideal,
                # so we signal the repair and let the orchestrator handle it.
                warnings.append("(diagrama reparado tras validacion)")
            else:
                warnings.append(
                    f"Diagrama de componentes sigue invalido tras reparacion: "
                    f"{re_reason}"
                )

    return (warnings, diagram_repaired)


# ---------------------------------------------------------------------------
# Pass 5: LLM critique
# ---------------------------------------------------------------------------


async def _critique_subprojects(
    sub_projects: list[SubProjectSchema],
    contracts: list[ContractSchema],
    mer_result,
    *,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
) -> list[SubProjectCritiqueFinding]:
    """Pass 5: LLM critique covering decomposition failure modes."""
    sp_lines = []
    for sp in sub_projects:
        entities = ", ".join(sp.entity_codes[:5]) if sp.entity_codes else "(sin entidades)"
        sp_lines.append(
            f"- {sp.name} — {sp.responsibility} [entidades: {entities}]"
        )
    sp_summary = "\n".join(sp_lines) or "(ninguno)"

    contract_lines = []
    for c in contracts:
        contract_lines.append(
            f"- {c.from_subproject} -> {c.to_subproject} "
            f"({c.contract_type}): {c.name}"
        )
    contract_summary = "\n".join(contract_lines) or "(ninguno)"

    mer_lines = []
    if mer_result and mer_result.entities:
        for e in mer_result.entities[:20]:
            mer_lines.append(f"- {e.name}")
    mer_summary = "\n".join(mer_lines) or "(ninguna)"

    header_lines = []
    if project_name:
        header_lines.append(f"PROYECTO: {project_name}")
    if project_description:
        header_lines.append(f"DESCRIPCION: {project_description}")
    header = "\n".join(header_lines)

    goals_block = ""
    if goals:
        goals_block = _format_goals(
            goals, section_title="OBJETIVOS DEL SISTEMA"
        )

    user_text = (
        f"{header}\n\n"
        f"{goals_block}"
        f"ENTIDADES DEL MER:\n{mer_summary}\n\n"
        f"SUB-PROYECTOS GENERADOS:\n{sp_summary}\n\n"
        f"CONTRATOS:\n{contract_summary}\n"
    )
    msgs = [("system", _SUBPROJECT_CRITIQUE_PROMPT), ("human", user_text)]
    llm = structured_llm(SubProjectCritiqueSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="subproject_critique"
        )
        return list(result.findings)
    except Exception:
        logger.exception("_critique_subprojects: all retries exhausted")
        return []


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


async def propose_subprojects(
    mer_result: "MerResult | None" = None,
    adr_result: "AdrResult | None" = None,
    nfr_result: "NfrResult | None" = None,
    *,
    project_result: "ProjectResult | None" = None,
    project_name: str = "",
    project_description: str = "",
    concurrency: int = DEFAULT_CONCURRENCY,
    goals: list[Any] | None = None,
    enable_critique: bool = True,
    feedback: str = "",
    on_progress=None,
) -> SubProjectResult:
    """Propose sub-projects with contracts from MER, ADR, and NFR context.

    Multi-pass pipeline:

      Pass 1 — decomposition skeleton (boundaries only).
      Pass 2 — gap pass for orphan MER entities.
      Pass 3 — stack + contracts (batched per project area).
      Pass 4 — deterministic validation + component diagram rendered in code.
      Pass 5 — LLM critique (optional, enabled by default).

    Takes MerResult (entities + bounded contexts), AdrResult (architecture
    decisions), and NfrResult (stack recommendations) and produces a
    SubProjectResult with sub-project definitions, inter-project contracts
    and a Mermaid component diagram. ``on_progress`` recibe un mensaje por
    lote del detailing (misma forma que generate_mer/generate_processes).
    """
    # MER is the minimum required input.
    if mer_result is None or not getattr(mer_result, "entities", None):
        return SubProjectResult(stats={"input_entities": 0})

    mer_entity_names = {e.name for e in mer_result.entities}

    # Pass 1: decomposition skeleton.
    skeletons = await _discover_subproject_skeletons(
        mer_result, adr_result, nfr_result,
        project_result=project_result,
        project_name=project_name,
        project_description=project_description,
        goals=goals, feedback=feedback,
    )

    # Pass 2: gap pass for orphan MER entities.
    gap_skeletons = await _gap_pass_subproject(
        mer_result, skeletons, adr_result, nfr_result,
        project_result=project_result,
        project_name=project_name,
        project_description=project_description,
        goals=goals, feedback=feedback,
    )
    all_skeletons = skeletons + gap_skeletons

    # Pass 3: stack + contracts (batched per project area).
    batch = await _detail_subprojects(
        mer_result, all_skeletons, adr_result, nfr_result,
        project_result=project_result,
        project_name=project_name,
        project_description=project_description,
        goals=goals, feedback=feedback,
        concurrency=concurrency,
        on_progress=on_progress,
    )

    if batch is None:
        # Degradacion final: si TODOS los lotes fallaron, los skeletons del
        # Pass 1 pasan como fallback determinista (la etapa mantiene
        # contenido: nombres, responsabilidades y particion de entidades
        # ya validada) en vez de volver vacia como en la corrida del
        # 2026-09-10 (0 sub-proyectos por truncado).
        if all_skeletons:
            sub_projects = [
                SubProjectSchema(
                    project_name=s.project_name,
                    name=s.name,
                    responsibility=s.responsibility,
                    entity_codes=s.entity_codes,
                    bounded_contexts=s.bounded_contexts,
                )
                for s in all_skeletons
            ]
            logger.warning(
                "propose_subprojects: detailing fallo completo; passthrough "
                "de %d skeletons como fallback",
                len(sub_projects),
            )
            # Las guardas tambien aplican al fallback (huerfanos y
            # auto-contratos no existen en la lista, pero la re-asignacion
            # de areas si).
            sub_projects, orphans_reassigned = _reassign_orphan_subprojects(
                sub_projects, project_result
            )
            return SubProjectResult(
                sub_projects=sub_projects,
                stats={
                    "input_entities": len(mer_result.entities),
                    "sub_projects": len(sub_projects),
                    "contracts": 0,
                    "detailing_fallback": True,
                    "self_contracts_dropped": 0,
                    "orphans_reassigned": orphans_reassigned,
                    "failed_batches": len({
                        (s.project_name or "").strip() or "(sin proyecto)"
                        for s in all_skeletons
                    }),
                },
            )
        return SubProjectResult(stats={
            "input_entities": len(mer_result.entities),
            "error": "detailing_failed",
            "sub_projects": 0,
            "contracts": 0,
        })

    sub_projects = list(batch.sub_projects)
    contracts = list(batch.contracts)

    # Guardas deterministas (sin LLM), corrida v2 Planitrack2.0: 6 de 36
    # contratos eran auto-contratos y 2 sub-proyectos quedaron huerfanos.
    contracts, self_contracts_dropped = _drop_self_contracts(contracts)
    sub_projects, orphans_reassigned = _reassign_orphan_subprojects(
        sub_projects, project_result
    )
    # El diagrama de componentes ya no viaja en el output LLM: se renderiza
    # en codigo desde sub-proyectos y contratos ya validados.
    component_diagram = _render_component_diagram(sub_projects, contracts)
    if component_diagram:
        component_diagram = _sanitize_mermaid(component_diagram, "graph TD")

    # Pass 4: validation + Mermaid repair.
    validation_warnings, diagram_repaired = await _validate_subprojects(
        sub_projects, contracts, component_diagram, mer_entity_names,
        project_result=project_result,
    )

    # If the diagram was repaired, re-generate it via repair.
    if diagram_repaired and component_diagram:
        component_diagram = _sanitize_mermaid(component_diagram, "graph TD")
        is_valid, reason = _validate_mermaid(component_diagram, "graph TD")
        if not is_valid:
            component_diagram = await _repair_mermaid(
                component_diagram, reason, "graph TD"
            )

    # Pass 5: LLM critique (optional).
    critique_findings: list[SubProjectCritiqueFinding] = []
    if enable_critique:
        critique_findings = await _critique_subprojects(
            sub_projects, contracts, mer_result,
            project_name=project_name,
            project_description=project_description,
            goals=goals,
        )

    stats = {
        "input_entities": len(mer_result.entities),
        "sub_projects": len(sub_projects),
        "contracts": len(contracts),
        "has_component_diagram": bool(component_diagram),
        "gap_pass_found": len(gap_skeletons),
        "failed_batches": getattr(batch, "failed_batches", 0),
        "detailing_fallback": False,
        "self_contracts_dropped": self_contracts_dropped,
        "orphans_reassigned": orphans_reassigned,
        "mermaid_repaired": diagram_repaired,
        "validation_warnings": validation_warnings,
        "critique_findings": [f.model_dump() for f in critique_findings],
        "critique_blockers": sum(
            1 for f in critique_findings if f.severity == "blocker"
        ),
    }
    logger.info(
        "propose_subprojects: %d entities -> %d sub-projects, %d contracts "
        "(gap +%d, %d warnings, %d critique findings)",
        stats["input_entities"],
        stats["sub_projects"],
        stats["contracts"],
        stats["gap_pass_found"],
        len(validation_warnings),
        len(critique_findings),
    )
    return SubProjectResult(
        sub_projects=sub_projects,
        contracts=contracts,
        component_diagram_mermaid=component_diagram,
        component_diagram_description=(
            "Diagrama de componentes renderizado desde los sub-proyectos y "
            "contratos validados."
            if component_diagram
            else ""
        ),
        failed_batches=getattr(batch, "failed_batches", 0),
        stats=stats,
    )
