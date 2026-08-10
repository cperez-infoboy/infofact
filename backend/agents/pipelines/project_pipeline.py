"""Project pipeline: decomposition into project areas (DDD subdomains).

Multi-pass pipeline (4 passes):

  Pass 1 — discovery: group bounded contexts into project areas using
  DDD subdomain cohesion, transactional coupling and requirement
  traceability.
  Pass 2 — coupling metrics (deterministic): compute cross-BC relationship
  counts, entity coverage and size distribution from the MER graph. No LLM.
  Pass 3 — critique: LLM evaluates the proposed boundaries using the
  objective metrics as evidence.
  Pass 4 — refine: if critique verdict is "refine", one final LLM call
  adjusts boundaries.

Takes the MerResult (entities + bounded contexts), ProcessResult (state
machines + sequences for transactional coupling), AdrResult (architecture
decisions that may influence boundaries) and produces a ProjectResult with
project areas (PROJ-NNN) that group bounded contexts into coherent
subdomains.

Design notes (mirrors architecture_pipeline.py and subproject_pipeline.py):

- Does NOT write to the DB. Returns a ProjectResult dataclass consumed by
  the analysis assembler / store layer.
- Uses structured_llm with narrow per-pass schemas for fence-tolerant
  structured output.
- Retry policy: centralized via _invoke_with_retry from _resilience.
- Prompts are in Spanish neutro (no voseo, no spanglish) following project
  conventions for LLM-facing text.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from backend.agents.llm import structured_llm
from backend.agents.pipelines._resilience import (
    _format_feedback,
    _format_goals,
    _invoke_with_retry,
)

if TYPE_CHECKING:
    from backend.agents.pipelines.adr_pipeline import AdrResult
    from backend.agents.pipelines.mer_pipeline import MerResult
    from backend.agents.pipelines.process_pipeline import ProcessResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM-facing schemas
# ---------------------------------------------------------------------------


class ProjectSkeleton(BaseModel):
    """One project area (DDD subdomain grouping)."""

    name: str = Field(
        description=(
            "Nombre del proyecto con un sustantivo de dominio "
            "(ej. 'Gestion Academica', 'Identidad y Acceso'). "
            "NO usar nombres tecnicos (ej. no 'API Layer')."
        ),
    )
    description: str = Field(
        description=(
            "Descripcion breve de la responsabilidad del proyecto en "
            "1-2 oraciones."
        ),
    )
    bounded_contexts: list[str] = Field(
        default_factory=list,
        description=(
            "Contextos delimitados DDD agrupados en este proyecto. "
            "Deben coincidir con los bounded contexts del MER."
        ),
    )
    entity_names: list[str] = Field(
        default_factory=list,
        description=(
            "Nombres de entidades del MER que pertenecen a este proyecto."
        ),
    )
    domain_type: str = Field(
        description=(
            "Tipo de subdominio DDD: 'core' (logica central diferenciadora), "
            "'supporting' (necesario pero no diferenciador), "
            "'generic' (utilidad reemplazable como auth, file storage)."
        ),
    )
    traced_req_codes: list[str] = Field(
        default_factory=list,
        description=(
            "Codigos REQ-XXXX de los requisitos que alimentan este proyecto."
        ),
    )


class ProjectSchema(BaseModel):
    """Wrapper schema for Pass 1 discovery output."""

    projects: list[ProjectSkeleton]


class ProjectCritiqueSchema(BaseModel):
    """Pass 3 critique output schema."""

    issues: list[str] = Field(
        default_factory=list,
        description=(
            "Problemas detectados: coupling alto entre dos proyectos, "
            "BC huerfano, proyecto demasiado grande, etc."
        ),
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description=(
            "Sugerencias concretas de ajuste (ej. 'fusionar PROJ-001 y "
            "PROJ-002', 'mover BC Notificaciones a PROJ-003')."
        ),
    )
    verdict: str = Field(
        description="'approve' si la descomposicion es correcta, 'refine' si necesita ajustes"
    )


# ---------------------------------------------------------------------------
# Prompts (Spanish neutro — no voseo, no spanglish)
# ---------------------------------------------------------------------------

_PROJECT_DISCOVERY_PROMPT = (
    "Eres un arquitecto de software experto en DDD. A partir de las entidades "
    "de dominio agrupadas por bounded context, los procesos del sistema y los "
    "objetivos del proyecto, agrupa los bounded contexts en PROYECTOS (areas "
    "funcionales mayores). Cada proyecto representa un subdominio DDD.\n\n"
    "Criterios de agrupacion (en orden de prioridad):\n"
    "1. COHESION DE SUBDOMINIO: BCs que sirven al mismo proposito de negocio.\n"
    "   - core: logica central que diferencia al sistema.\n"
    "   - supporting: capacidades necesarias pero no diferenciadoras.\n"
    "   - generic: utilidades reemplazables (auth, file storage).\n"
    "2. ACOPLAMIENTO TRANSACCIONAL: BCs que aparecen juntos en los mismos "
    "procesos.\n"
    "3. TRAZABILIDAD DE REQUISITOS: BCs tocados por los mismos grupos de "
    "requisitos.\n\n"
    "Anti-criterios (NO usar para agrupar):\n"
    "- Similitud de nombres tecnicos o de entidades.\n"
    "- Tipo tecnico de componente (API, BD, UI).\n"
    "- Balancear tamano entre proyectos (puede ser desigual — core es mas "
    "grande).\n\n"
    "Reglas:\n"
    "- IDIOMA: manten el idioma original.\n"
    "- Cada bounded context debe pertenecer a EXACTAMENTE UN proyecto.\n"
    "- Entre 2 y 7 proyectos en total.\n"
    "- Nombra cada proyecto con un sustantivo de dominio, no tecnico.\n"
    "- Asigna domain_type (core/supporting/generic) a cada proyecto.\n"
    "Devuelve SOLO el objeto estructurado."
)

_PROJECT_CRITIQUE_PROMPT = (
    "Eres un critico de arquitectura DDD. Se te da la descomposicion en "
    "proyectos propuesta, junto con metricas objetivas de coupling calculadas "
    "desde el MER. Tu tarea es evaluar si las fronteras son correctas.\n\n"
    "Categorias de fallo a revisar:\n"
    "1. high_coupling: dos proyectos con muchas relaciones cruzadas — "
    "considerar fusion.\n"
    "2. orphan_bc: bounded context que no pertenece a ningun proyecto.\n"
    "3. oversize: un proyecto con demasiados entidades relativo al total.\n"
    "4. undersize: proyecto con una sola entidad que podria integrarse a otro.\n"
    "5. missing_core: ningun proyecto marcado como 'core'.\n"
    "6. all_same_type: todos los proyectos son del mismo domain_type.\n\n"
    "Usa las metricas como evidencia objetiva. Si la descomposicion es "
    "correcta, devuelve verdict='approve'.\n"
    "Devuelve SOLO el objeto estructurado."
)

_PROJECT_REFINE_PROMPT = (
    "Eres un arquitecto DDD. La descomposicion en proyectos fue evaluada y "
    "necesita ajustes. Aplica las sugerencias del critico y devuelve la "
    "descomposicion corregida.\n\n"
    "Reglas:\n"
    "- IDIOMA: manten el idioma original.\n"
    "- Cada BC debe seguir en exactamente un proyecto.\n"
    "- Conserva el mismo numero de proyectos o fusiona/divide segun las "
    "sugerencias.\n"
    "Devuelve SOLO el objeto estructurado."
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ProjectResult:
    """Output of the project pipeline."""

    projects: list[ProjectSkeleton] = field(default_factory=list)
    critique_issues: list[str] = field(default_factory=list)
    critique_suggestions: list[str] = field(default_factory=list)
    description: str = ""
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def _build_project_context(
    project_name: str,
    project_description: str,
    goals: list[Any] | None = None,
    mer_result=None,
    process_result=None,
    adr_result=None,
    feedback: str = "",
) -> str:
    """Build shared context text from project, goals, MER, processes and ADRs."""
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

    # MER entities grouped by bounded context.
    if mer_result is not None and getattr(mer_result, "entities", None):
        contexts: dict[str, list[str]] = {}
        for e in mer_result.entities:
            bc = e.bounded_context or "(sin contexto)"
            contexts.setdefault(bc, []).append(e.name)

        lines.append("")
        lines.append("BOUNDED CONTEXTS DEL MER:")
        for bc, ents in contexts.items():
            agg_roots = [
                e.name
                for e in mer_result.entities
                if e.bounded_context == bc and e.aggregate_root
            ]
            root_note = (
                f" (aggregate roots: {', '.join(agg_roots)})"
                if agg_roots
                else ""
            )
            lines.append(f"- {bc}: {', '.join(ents)}{root_note}")
        lines.append("")

    # Process diagrams for transactional coupling inference.
    if process_result is not None:
        sms = getattr(process_result, "state_machines", None) or []
        sqs = getattr(process_result, "sequence_diagrams", None) or []
        if sms or sqs:
            lines.append("PROCESOS DEL SISTEMA:")
            for sm in sms[:10]:
                lines.append(f"- Maquina de estados: {sm.entity_name}")
            for sq in sqs[:10]:
                name = getattr(sq, "name", str(sq))
                lines.append(f"- Secuencia: {name}")
            lines.append("")

    # ADRs that may influence boundaries.
    if adr_result is not None and getattr(adr_result, "adrs", None):
        lines.append("DECISIONES ARQUITECTONICAS RELEVANTES:")
        for a in adr_result.adrs[:8]:
            entry = f"- {a.title}"
            if a.decision:
                entry += f" — {a.decision}"
            lines.append(entry)
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pass 1: discovery
# ---------------------------------------------------------------------------


async def _discover_projects(
    *,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
    mer_result=None,
    process_result=None,
    adr_result=None,
    feedback: str = "",
) -> list[ProjectSkeleton]:
    """Pass 1: discover project areas by grouping bounded contexts."""
    context = _build_project_context(
        project_name, project_description, goals,
        mer_result, process_result, adr_result, feedback,
    )
    msgs = [("system", _PROJECT_DISCOVERY_PROMPT), ("human", context)]
    llm = structured_llm(ProjectSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="project_discover"
        )
        return list(result.projects)
    except Exception:
        logger.exception("_discover_projects: all retries exhausted")
        return []


# ---------------------------------------------------------------------------
# Pass 2: coupling metrics (deterministic — no LLM)
# ---------------------------------------------------------------------------


def _compute_coupling_metrics(
    projects: list[ProjectSkeleton],
    mer_result=None,
) -> dict[str, Any]:
    """Pass 2: compute objective coupling/cohesion metrics from the MER graph.

    Returns a dict with:
    - cross_project_edges: dict of (proj_a, proj_b) -> count of MER
      relationships that cross between entities in those two projects.
    - entity_coverage: (covered_count, total_count, orphans).
    - size_distribution: dict of project_name -> entity_count.
    - high_coupling_pairs: list of pairs with > threshold crossed edges.
    """
    if mer_result is None or not getattr(mer_result, "entities", None):
        return {
            "cross_project_edges": {},
            "entity_coverage": (0, 0, []),
            "size_distribution": {},
            "high_coupling_pairs": [],
        }

    # Map entity name -> project name.
    entity_to_project: dict[str, str] = {}
    for proj in projects:
        for ent_name in proj.entity_names:
            entity_to_project[ent_name] = proj.name

    all_entity_names = {e.name for e in mer_result.entities}
    covered = set(entity_to_project.keys())
    orphans = sorted(all_entity_names - covered)

    # Count cross-project relationship edges.
    cross_edges: dict[tuple[str, str], int] = defaultdict(int)
    relationships = getattr(mer_result, "relationships", None) or []
    for rel in relationships:
        from_proj = entity_to_project.get(rel.from_entity)
        to_proj = entity_to_project.get(rel.to_entity)
        if (
            from_proj
            and to_proj
            and from_proj != to_proj
        ):
            pair = tuple(sorted((from_proj, to_proj)))
            cross_edges[pair] += 1

    # Identify high-coupling pairs (> 5 crossed edges).
    HIGH_COUPLING_THRESHOLD = 5
    high_coupling = [
        {"projects": list(pair), "crossed_edges": count}
        for pair, count in sorted(
            cross_edges.items(), key=lambda x: -x[1]
        )
        if count >= HIGH_COUPLING_THRESHOLD
    ]

    # Size distribution.
    size_dist = {
        proj.name: len(proj.entity_names) for proj in projects
    }

    return {
        "cross_project_edges": {
            f"{a} <-> {b}": count
            for (a, b), count in cross_edges.items()
        },
        "entity_coverage": (
            len(covered),
            len(all_entity_names),
            orphans,
        ),
        "size_distribution": size_dist,
        "high_coupling_pairs": high_coupling,
    }


def _format_metrics_for_critique(metrics: dict[str, Any]) -> str:
    """Format coupling metrics as human-readable text for the critique prompt."""
    lines: list[str] = []

    # Cross-project edges.
    cross = metrics.get("cross_project_edges", {})
    if cross:
        lines.append("METRICAS DE COUPLING ENTRE PROYECTOS:")
        for pair, count in cross.items():
            severity = "ALTO" if count >= 5 else ("medio" if count >= 3 else "bajo")
            lines.append(f"- {pair}: {count} relaciones cruzadas ({severity})")
    else:
        lines.append("METRICAS DE COUPLING: sin relaciones cruzadas entre proyectos.")

    # Entity coverage.
    covered, total, orphans = metrics.get("entity_coverage", (0, 0, []))
    lines.append(
        f"\nCOBERTURA DE ENTIDADES: {covered}/{total} cubiertas"
    )
    if orphans:
        lines.append(
            f"ENTIDADES SIN PROYECTO: {', '.join(orphans[:10])}"
        )

    # Size distribution.
    size = metrics.get("size_distribution", {})
    if size:
        lines.append("\nDISTRIBUCION DE TAMANO:")
        for proj, count in size.items():
            lines.append(f"- {proj}: {count} entidades")

    # High coupling warnings.
    high = metrics.get("high_coupling_pairs", [])
    if high:
        lines.append("\nPROYECTOS CON ACOPLAMIENTO ALTO (considerar fusion):")
        for pair in high:
            lines.append(
                f"- {pair['projects'][0]} y {pair['projects'][1]}: "
                f"{pair['crossed_edges']} relaciones cruzadas"
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pass 3: critique (LLM with metrics)
# ---------------------------------------------------------------------------


async def _critique_projects(
    projects: list[ProjectSkeleton],
    metrics: dict[str, Any],
    *,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
) -> ProjectCritiqueSchema | None:
    """Pass 3: LLM critique using objective metrics."""
    # Format projects for the prompt.
    proj_lines = []
    for i, proj in enumerate(projects):
        proj_lines.append(
            f"- PROJ-{i + 1}: {proj.name} [{proj.domain_type}]\n"
            f"  BCs: {', '.join(proj.bounded_contexts)}\n"
            f"  Entidades ({len(proj.entity_names)}): "
            f"{', '.join(proj.entity_names[:8])}"
        )
    proj_summary = "\n".join(proj_lines) or "(ninguno)"

    metrics_text = _format_metrics_for_critique(metrics)

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
        f"PROYECTOS PROPUESTOS:\n{proj_summary}\n\n"
        f"{metrics_text}\n"
    )
    msgs = [("system", _PROJECT_CRITIQUE_PROMPT), ("human", user_text)]
    llm = structured_llm(ProjectCritiqueSchema)
    try:
        return await _invoke_with_retry(
            llm, msgs, context_label="project_critique"
        )
    except Exception:
        logger.exception("_critique_projects: all retries exhausted")
        return None


# ---------------------------------------------------------------------------
# Pass 4: refine (if needed)
# ---------------------------------------------------------------------------


async def _refine_projects(
    projects: list[ProjectSkeleton],
    critique: ProjectCritiqueSchema,
    *,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
    mer_result=None,
    process_result=None,
    adr_result=None,
) -> list[ProjectSkeleton]:
    """Pass 4: adjust boundaries based on critique suggestions."""
    suggestions_text = "\n".join(
        f"- {s}" for s in critique.suggestions
    )
    issues_text = "\n".join(
        f"- {i}" for i in critique.issues
    )

    proj_lines = []
    for proj in projects:
        proj_lines.append(
            f"- {proj.name} [{proj.domain_type}]: "
            f"BCs={', '.join(proj.bounded_contexts)}, "
            f"entidades={', '.join(proj.entity_names[:6])}"
        )
    proj_summary = "\n".join(proj_lines)

    feedback = (
        f"PROBLEMAS DETECTADOS:\n{issues_text}\n\n"
        f"SUGERENCIAS:\n{suggestions_text}\n\n"
        f"PROYECTOS ACTUALES:\n{proj_summary}"
    )

    context = _build_project_context(
        project_name, project_description, goals,
        mer_result, process_result, adr_result, feedback=feedback,
    )
    msgs = [("system", _PROJECT_REFINE_PROMPT), ("human", context)]
    llm = structured_llm(ProjectSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="project_refine"
        )
        return list(result.projects)
    except Exception:
        logger.exception("_refine_projects: all retries exhausted")
        return projects  # keep original on failure


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


async def discover_projects(
    mer_result: "MerResult | None" = None,
    process_result: "ProcessResult | None" = None,
    adr_result: "AdrResult | None" = None,
    *,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
    enable_critique: bool = True,
    feedback: str = "",
) -> ProjectResult:
    """Discover project areas (DDD subdomains) from MER + processes + ADRs.

    Multi-pass pipeline:

      Pass 1 — discovery (group BCs into projects).
      Pass 2 — coupling metrics (deterministic, from MER graph).
      Pass 3 — LLM critique (uses metrics as evidence).
      Pass 4 — refine (if critique verdict is "refine").

    Takes MerResult (entities + bounded contexts), optionally ProcessResult
    (transactional coupling) and AdrResult (boundary-influencing decisions)
    and produces a ProjectResult with project areas.
    """
    # MER is the minimum required input.
    if mer_result is None or not getattr(mer_result, "entities", None):
        return ProjectResult(stats={"skipped": True, "reason": "no_mer"})

    # Pass 1: discovery.
    projects = await _discover_projects(
        project_name=project_name,
        project_description=project_description,
        goals=goals,
        mer_result=mer_result,
        process_result=process_result,
        adr_result=adr_result,
        feedback=feedback,
    )

    if not projects:
        return ProjectResult(stats={
            "error": "discovery_failed",
            "input_entities": len(mer_result.entities),
        })

    critique_issues: list[str] = []
    critique_suggestions: list[str] = []

    # Passes 2-4: metrics, critique, optional refine.
    if enable_critique:
        # Pass 2: compute coupling metrics (deterministic).
        metrics = _compute_coupling_metrics(projects, mer_result)

        # Pass 3: LLM critique with metrics.
        critique = await _critique_projects(
            projects,
            metrics,
            project_name=project_name,
            project_description=project_description,
            goals=goals,
        )

        if critique is not None:
            critique_issues = list(critique.issues)
            critique_suggestions = list(critique.suggestions)

            # Pass 4: refine if needed.
            if critique.verdict == "refine" and critique.suggestions:
                refined = await _refine_projects(
                    projects,
                    critique,
                    project_name=project_name,
                    project_description=project_description,
                    goals=goals,
                    mer_result=mer_result,
                    process_result=process_result,
                    adr_result=adr_result,
                )
                if refined:
                    projects = refined

    # Build description.
    type_counts: dict[str, int] = {}
    for proj in projects:
        type_counts[proj.domain_type] = (
            type_counts.get(proj.domain_type, 0) + 1
        )
    description = (
        f"El sistema se descompone en {len(projects)} proyectos: "
        + ", ".join(
            f"{p.name} ({p.domain_type})" for p in projects
        )
        + "."
    )

    stats = {
        "input_entities": len(mer_result.entities),
        "projects": len(projects),
        "domain_types": type_counts,
        "critique_issues": critique_issues,
        "critique_suggestions": critique_suggestions,
        "refined": bool(critique_suggestions),
    }
    logger.info(
        "discover_projects: %d entities -> %d projects (%d issues, %d suggestions)",
        stats["input_entities"],
        stats["projects"],
        len(critique_issues),
        len(critique_suggestions),
    )
    return ProjectResult(
        projects=projects,
        critique_issues=critique_issues,
        critique_suggestions=critique_suggestions,
        description=description,
        stats=stats,
    )
