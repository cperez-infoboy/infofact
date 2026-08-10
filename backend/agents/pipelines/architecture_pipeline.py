"""Architecture pipeline: system architecture + infrastructure diagrams.

Multi-pass pipeline (5 passes):

  Pass 1 — discovery (narrow: identify main system components from NFR
  stack + sub-projects + project description).
  Pass 2 — gap pass (deterministic): add skeleton components for missing
  layers (data layer if MER entities exist, presentation if sub-projects
  exist). No LLM.
  Pass 3 — detail: produce full ArchitectureResultSchema (components with
  technology/responsibility, connections, containers, descriptions).
  Pass 4 — validation (deterministic): check dangling connection refs,
  empty components/containers. No LLM.
  Pass 5 — LLM critique (missing_layer, orphan_subproject, etc.).

Takes the NfrResult (stack recommendations), AdrResult (architecture
decisions), SubProjectResult (sub-project decomposition) and optionally
MerResult (entity names) and produces:
- System architecture diagram (Mermaid graph TD) with components grouped
  by layer and connections showing communication protocols.
- Infrastructure diagram (Mermaid graph TD) with containers grouped by
  network and depends_on edges.

The LLM produces structured data (components, connections, containers) and
Python renders the Mermaid deterministically (Pattern A) — the LLM never
writes raw Mermaid syntax. This guarantees syntactically valid diagrams
that match the structured data exactly.

Design notes (mirrors process_pipeline.py and subproject_pipeline.py
patterns):

- Does NOT write to the DB. Returns an ArchitectureResult dataclass
  consumed by the analysis assembler / store layer.
- Uses structured_llm with narrow per-pass schemas for fence-tolerant
  structured output.
- Retry policy: centralized via _invoke_with_retry from _resilience.
- Prompts are in Spanish neutro (no voseo, no spanglish) following project
  conventions for LLM-facing text.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from backend.agents.llm import structured_llm
from backend.agents.pipelines._resilience import (
    DEFAULT_CONCURRENCY,
    _format_feedback,
    _format_goals,
    _invoke_with_retry,
    _sanitize_graph_label,
    _sanitize_mermaid_id,
)

if TYPE_CHECKING:
    from backend.agents.pipelines.adr_pipeline import AdrResult
    from backend.agents.pipelines.mer_pipeline import MerResult
    from backend.agents.pipelines.nfr_pipeline import NfrResult
    from backend.agents.pipelines.subproject_pipeline import SubProjectResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM-facing schemas
# ---------------------------------------------------------------------------


class ArchitectureComponent(BaseModel):
    """One component in the system architecture."""

    id: str = Field(
        description="Identificador unico en kebab-case, ej. 'frontend-spa'"
    )
    name: str = Field(
        description="Nombre para mostrar, ej. 'Frontend SPA'"
    )
    component_type: str = Field(
        description=(
            "frontend | backend | database | external | agent | auth | "
            "cache | messaging"
        ),
    )
    layer: str = Field(
        description="presentation | application | data | external"
    )
    technology: str = Field(
        default="",
        description="Tecnologia principal, ej. 'SvelteKit', 'FastAPI'",
    )
    responsibility: str = Field(
        default="",
        description="Responsabilidad en una oracion",
    )


class ArchitectureConnection(BaseModel):
    """One connection between two components."""

    from_component: str = Field(description="ID del componente origen")
    to_component: str = Field(description="ID del componente destino")
    protocol: str = Field(
        default="",
        description=(
            "Protocolo de comunicacion: HTTP, SSE, gRPC, SDK, SQL, OAuth"
        ),
    )
    label: str = Field(
        default="",
        description="Descripcion breve de que fluye por esta conexion",
    )


class InfraContainer(BaseModel):
    """One deployment container in the infrastructure topology."""

    id: str = Field(
        description="Identificador del contenedor, ej. 'backend', 'db', 'proxy'"
    )
    name: str = Field(description="Nombre para mostrar")
    image: str = Field(
        default="",
        description="Imagen o runtime, ej. 'python:3.13-slim'",
    )
    network: str = Field(
        default="default", description="Red a la que pertenece"
    )
    ports: list[str] = Field(
        default_factory=list,
        description="Puertos expuestos, ej. ['8000:8000']",
    )
    volumes: list[str] = Field(
        default_factory=list,
        description="Volumenes montados",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="IDs de contenedores de los que depende",
    )


# ---------------------------------------------------------------------------
# Narrow per-pass schemas (multi-pass pipeline)
# ---------------------------------------------------------------------------


class ComponentSkeleton(BaseModel):
    """Narrow schema for Pass 1: component identification only."""

    id: str
    name: str
    component_type: str
    layer: str


class ArchitectureSkeletonSchema(BaseModel):
    """Pass 1 output schema."""

    components: list[ComponentSkeleton]


class ArchitectureResultSchema(BaseModel):
    """Pass 3 wrapper schema: full architecture + infrastructure."""

    components: list[ArchitectureComponent]
    connections: list[ArchitectureConnection]
    containers: list[InfraContainer]
    system_description: str = Field(
        description=(
            "Narrativa de 2-3 oraciones sobre la arquitectura general del "
            "sistema"
        ),
    )
    infrastructure_description: str = Field(
        description=(
            "Narrativa de 2-3 oraciones sobre la topologia de despliegue "
            "sugerida"
        ),
    )


class ArchitectureCritiqueSchema(BaseModel):
    """Pass 5 output schema."""

    issues: list[dict] = Field(default_factory=list)
    verdict: str = ""


# ---------------------------------------------------------------------------
# Prompts (Spanish neutro — no voseo, no spanglish)
# ---------------------------------------------------------------------------

_ARCH_DISCOVERY_PROMPT = (
    "Eres un arquitecto de software. A partir del stack tecnologico "
    "recomendado, los sub-proyectos propuestos y la descripcion del "
    "proyecto, identifica los componentes principales del sistema. Tu UNICA "
    "tarea es identificar los componentes — NO generes diagramas ni "
    "conexiones.\n\n"
    "Reglas:\n"
    "- IDIOMA: manten el idioma original.\n"
    "- Cubre todas las capas: presentation, application, data, external.\n"
    "- Los componentes deben representar piezas reales del sistema "
    "(aplicacion frontend, API backend, base de datos, proveedor de "
    "autenticacion, integraciones externas, agente, cache, mensajeria).\n"
    "- component_type debe ser uno de: frontend, backend, database, "
    "external, agent, auth, cache, messaging.\n"
    "- layer debe ser uno de: presentation, application, data, external.\n"
    "Devuelve SOLO el objeto estructurado."
)

_ARCH_DETAIL_PROMPT = (
    "Eres un arquitecto de software. A partir de los componentes "
    "identificados y el contexto del proyecto, produce la arquitectura "
    "completa del sistema.\n\n"
    "Para cada componente añade tecnologia y responsabilidad. Produce "
    "conexiones mostrando como se comunican los componentes (protocolo y "
    "descripcion de lo que fluye). Produce contenedores de despliegue "
    "mapeando componentes a contenedores (agrupando componentes que "
    "ejecutarian en el mismo contenedor/proceso, asignando redes, puertos y "
    "volumenes).\n\n"
    "Reglas:\n"
    "- IDIOMA: manten el idioma original.\n"
    "- Usa como restriccion los componentes identificados en el "
    "descubrimiento (puedes añadir technology y responsibility, pero no "
    "eliminar componentes).\n"
    "- connections: las conexiones deben ser entre componentes existentes.\n"
    "- protocol: HTTP, SSE, gRPC, SDK, SQL, OAuth, etc.\n"
    "- containers: cada contenedor debe tener image (runtime), network, "
    "ports y volumes adecuados.\n"
    "- system_description: narrativa de 2-3 oraciones sobre la "
    "arquitectura.\n"
    "- infrastructure_description: narrativa de 2-3 oraciones sobre la "
    "topologia de despliegue.\n"
    "Devuelve SOLO el objeto estructurado."
)

_ARCH_CRITIQUE_PROMPT = (
    "Eres un critico de arquitectura de software. Se te da la arquitectura "
    "del sistema generada a partir del stack, los sub-proyectos y las "
    "decisiones arquitectonicas. Tu tarea es identificar problemas:\n\n"
    "Categorias de fallo a revisar:\n"
    "1. missing_layer: falta una capa completa (presentation, application, "
    "data o external).\n"
    "2. orphan_subproject: sub-proyecto sin contenedor asignado.\n"
    "3. unjustified_component: componente no trazable a ningun NFR, ADR o "
    "sub-proyecto.\n"
    "4. missing_external_dependency: dependencia externa (auth, payments, "
    "etc.) no modelada como componente.\n"
    "5. invalid_connection: conexion entre componentes inexistentes o con "
    "protocolo inconsistente.\n\n"
    "Si la arquitectura esta correcta, devuelve issues vacio.\n"
    "Devuelve SOLO el objeto estructurado."
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ArchitectureResult:
    """Output of the architecture pipeline."""

    system_architecture_diagram: str = ""
    system_architecture_description: str = ""
    infrastructure_diagram: str = ""
    infrastructure_description: str = ""
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Layer ordering + display names
# ---------------------------------------------------------------------------

_LAYER_ORDER = ["presentation", "application", "data", "external"]

_LAYER_DISPLAY = {
    "presentation": "Capa de Presentacion",
    "application": "Capa de Aplicacion",
    "data": "Capa de Datos",
    "external": "Servicios Externos",
}


# ---------------------------------------------------------------------------
# Deterministic Mermaid renderers
# ---------------------------------------------------------------------------


def _render_system_architecture(
    components: list[ArchitectureComponent],
    connections: list[ArchitectureConnection],
) -> str:
    """Render a valid Mermaid graph TD from structured components and connections.

    Components are grouped by layer into subgraphs. Connections become
    labeled edges. All identifiers and labels are sanitized to guarantee
    syntactically valid Mermaid.
    """
    lines: list[str] = ["graph TD"]

    # Group components by layer.
    by_layer: dict[str, list[ArchitectureComponent]] = {}
    for comp in components:
        layer = comp.layer if comp.layer in _LAYER_ORDER else "external"
        by_layer.setdefault(layer, []).append(comp)

    # Emit subgraphs in defined layer order.
    for layer in _LAYER_ORDER:
        comps = by_layer.get(layer)
        if not comps:
            continue
        sub_id = _sanitize_mermaid_id(f"layer_{layer}")
        display = _LAYER_DISPLAY.get(layer, layer)
        lines.append(f'    subgraph {sub_id} ["{display}"]')
        for comp in comps:
            cid = _sanitize_mermaid_id(comp.id)
            if comp.technology:
                label = f"{comp.name} / {comp.technology}"
            else:
                label = comp.name
            clabel = _sanitize_graph_label(label)
            lines.append(f'        {cid} ["{clabel}"]')
        lines.append("    end")

    # Emit connections.
    for conn in connections:
        from_id = _sanitize_mermaid_id(conn.from_component)
        to_id = _sanitize_mermaid_id(conn.to_component)
        parts: list[str] = []
        if conn.protocol:
            parts.append(conn.protocol)
        if conn.label:
            parts.append(conn.label)
        edge_label = _sanitize_graph_label(" - ".join(parts))
        if edge_label:
            lines.append(f"    {from_id} -->|{edge_label}| {to_id}")
        else:
            lines.append(f"    {from_id} --> {to_id}")

    return "\n".join(lines)


def _render_infrastructure(containers: list[InfraContainer]) -> str:
    """Render a valid Mermaid graph TD from structured containers.

    Containers are grouped by network into subgraphs. ``depends_on``
    entries become plain edges. All identifiers and labels are sanitized.
    """
    lines: list[str] = ["graph TD"]

    # Group containers by network.
    by_network: dict[str, list[InfraContainer]] = {}
    for c in containers:
        network = c.network or "default"
        by_network.setdefault(network, []).append(c)

    # Emit subgraphs in deterministic (sorted) order.
    for network in sorted(by_network):
        net_id = _sanitize_mermaid_id(f"net_{network}")
        lines.append(
            f'    subgraph {net_id} ["{_sanitize_graph_label(network)}"]'
        )
        for c in by_network[network]:
            cid = _sanitize_mermaid_id(c.id)
            label_parts = [c.name]
            if c.image:
                label_parts.append(c.image)
            if c.ports:
                label_parts.append(":" + ",".join(c.ports))
            clabel = _sanitize_graph_label(" / ".join(label_parts))
            lines.append(f'        {cid} ["{clabel}"]')
        lines.append("    end")

    # Emit depends_on edges.
    for c in containers:
        cid = _sanitize_mermaid_id(c.id)
        for dep in c.depends_on:
            dep_id = _sanitize_mermaid_id(dep)
            lines.append(f"    {cid} --> {dep_id}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def _build_architecture_context(
    project_name: str,
    project_description: str,
    goals: list[Any] | None = None,
    mer_result=None,
    nfr_result=None,
    adr_result=None,
    subproject_result=None,
) -> str:
    """Build shared context text from project, goals, NFR, ADR and sub-projects."""
    lines: list[str] = []

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

    if mer_result is not None and getattr(mer_result, "entities", None):
        ent_names = [e.name for e in mer_result.entities]
        lines.append("")
        lines.append(
            f"ENTIDADES DE DOMINIO ({len(ent_names)}): "
            + ", ".join(ent_names[:30])
        )

    if nfr_result is not None and getattr(nfr_result, "stack", None):
        lines.append("")
        lines.append("STACK RECOMENDADO:")
        for s in nfr_result.stack:
            lines.append(f"- {s.layer}: {s.technology}")

    if adr_result is not None and getattr(adr_result, "adrs", None):
        lines.append("")
        lines.append("DECISIONES ARQUITECTONICAS (ADRs):")
        for i, a in enumerate(adr_result.adrs[:10]):
            entry = f"- ADR-{i + 1}: {a.title}"
            if a.decision:
                entry += f" — {a.decision}"
            lines.append(entry)

    if (
        subproject_result is not None
        and getattr(subproject_result, "sub_projects", None)
    ):
        lines.append("")
        lines.append("SUB-PROYECTOS:")
        for sp in subproject_result.sub_projects:
            lines.append(f"- {sp.name}: {sp.responsibility}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pass 1: discovery (narrow)
# ---------------------------------------------------------------------------


async def _discover_components(
    *,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
    mer_result=None,
    nfr_result=None,
    adr_result=None,
    subproject_result=None,
    feedback: str = "",
) -> list[ComponentSkeleton]:
    """Pass 1: discover system component skeletons."""
    context = _build_architecture_context(
        project_name, project_description, goals,
        mer_result, nfr_result, adr_result, subproject_result,
    )
    if feedback:
        context = _format_feedback(feedback) + context
    msgs = [("system", _ARCH_DISCOVERY_PROMPT), ("human", context)]
    llm = structured_llm(ArchitectureSkeletonSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="arch_discovery"
        )
        return list(result.components)
    except Exception:
        logger.exception("_discover_components: all retries exhausted")
        return []


# ---------------------------------------------------------------------------
# Pass 2: gap pass (deterministic — no LLM)
# ---------------------------------------------------------------------------


def _gap_pass(
    skeletons: list[ComponentSkeleton],
    *,
    mer_result=None,
    subproject_result=None,
) -> list[ComponentSkeleton]:
    """Pass 2: add skeleton components for missing critical layers.

    Deterministic — no LLM. If no component for the ``data`` layer exists
    but there are MER entities, add a database skeleton. If no
    ``presentation`` layer component exists but there are sub-projects,
    add a frontend skeleton.
    """
    extras: list[ComponentSkeleton] = []
    layers_present = {s.layer for s in skeletons}

    if (
        "data" not in layers_present
        and mer_result is not None
        and getattr(mer_result, "entities", None)
    ):
        extras.append(
            ComponentSkeleton(
                id="database",
                name="Base de Datos",
                component_type="database",
                layer="data",
            )
        )

    if (
        "presentation" not in layers_present
        and subproject_result is not None
        and getattr(subproject_result, "sub_projects", None)
    ):
        extras.append(
            ComponentSkeleton(
                id="frontend",
                name="Frontend",
                component_type="frontend",
                layer="presentation",
            )
        )

    return extras


# ---------------------------------------------------------------------------
# Pass 3: detail (full architecture + infrastructure)
# ---------------------------------------------------------------------------


async def _detail_architecture(
    skeletons: list[ComponentSkeleton],
    *,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
    mer_result=None,
    nfr_result=None,
    adr_result=None,
    subproject_result=None,
    feedback: str = "",
) -> ArchitectureResultSchema | None:
    """Pass 3: produce full architecture result constrained by skeletons."""
    context = _build_architecture_context(
        project_name, project_description, goals,
        mer_result, nfr_result, adr_result, subproject_result,
    )
    if feedback:
        context = _format_feedback(feedback) + context

    skeleton_lines = []
    for s in skeletons:
        skeleton_lines.append(
            f"- {s.id} ({s.component_type}, layer={s.layer}): {s.name}"
        )
    context += (
        "\n\nCOMPONENTES IDENTIFICADOS (enriquece cada uno con tecnologia, "
        "responsabilidad, conexiones y contenedores):\n"
        + "\n".join(skeleton_lines)
        + "\n"
    )

    msgs = [("system", _ARCH_DETAIL_PROMPT), ("human", context)]
    llm = structured_llm(ArchitectureResultSchema)
    try:
        return await _invoke_with_retry(
            llm, msgs, context_label="arch_detail"
        )
    except Exception:
        logger.exception("_detail_architecture: all retries exhausted")
        return None


# ---------------------------------------------------------------------------
# Pass 4: validation (deterministic — no LLM)
# ---------------------------------------------------------------------------


def _validate_architecture(
    components: list[ArchitectureComponent],
    connections: list[ArchitectureConnection],
    containers: list[InfraContainer],
) -> tuple[list[ArchitectureConnection], list[str]]:
    """Pass 4: deterministic validation.

    Check for dangling connection refs and empty lists. Drop invalid
    connections. Returns ``(valid_connections, warnings)``.
    """
    warnings: list[str] = []

    if not components:
        warnings.append("No se generaron componentes de arquitectura")
    if not containers:
        warnings.append("No se generaron contenedores de infraestructura")

    comp_ids = {c.id for c in components}
    valid_connections: list[ArchitectureConnection] = []
    for conn in connections:
        if conn.from_component not in comp_ids:
            warnings.append(
                f"Conexion con componente origen inexistente: "
                f"{conn.from_component}"
            )
            continue
        if conn.to_component not in comp_ids:
            warnings.append(
                f"Conexion con componente destino inexistente: "
                f"{conn.to_component}"
            )
            continue
        valid_connections.append(conn)

    container_ids = {c.id for c in containers}
    for c in containers:
        for dep in c.depends_on:
            if dep not in container_ids:
                warnings.append(
                    f"Contenedor '{c.id}' depende de contenedor inexistente: "
                    f"{dep}"
                )

    return (valid_connections, warnings)


# ---------------------------------------------------------------------------
# Pass 5: LLM critique
# ---------------------------------------------------------------------------


async def _critique_architecture(
    components: list[ArchitectureComponent],
    connections: list[ArchitectureConnection],
    containers: list[InfraContainer],
    *,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
) -> list[dict]:
    """Pass 5: LLM critique covering architecture failure modes."""
    comp_lines = []
    for comp in components:
        tech = f" / {comp.technology}" if comp.technology else ""
        comp_lines.append(
            f"- {comp.id} ({comp.layer}, {comp.component_type}){tech}: "
            f"{comp.responsibility or '(sin responsabilidad)'}"
        )
    comp_summary = "\n".join(comp_lines) or "(ninguno)"

    conn_lines = []
    for conn in connections:
        label = f" — {conn.label}" if conn.label else ""
        conn_lines.append(
            f"- {conn.from_component} -> {conn.to_component} "
            f"({conn.protocol}){label}"
        )
    conn_summary = "\n".join(conn_lines) or "(ninguna)"

    ctr_lines = []
    for c in containers:
        ports = ", ".join(c.ports) if c.ports else "(sin puertos)"
        ctr_lines.append(
            f"- {c.id} ({c.image or 'sin imagen'}, network={c.network}): "
            f"ports=[{ports}]"
        )
    ctr_summary = "\n".join(ctr_lines) or "(ninguno)"

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
        f"COMPONENTES:\n{comp_summary}\n\n"
        f"CONEXIONES:\n{conn_summary}\n\n"
        f"CONTENEDORES:\n{ctr_summary}\n"
    )
    msgs = [("system", _ARCH_CRITIQUE_PROMPT), ("human", user_text)]
    llm = structured_llm(ArchitectureCritiqueSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="arch_critique"
        )
        return list(result.issues)
    except Exception:
        logger.exception("_critique_architecture: all retries exhausted")
        return []


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


async def generate_architecture(
    mer_result: "MerResult | None" = None,
    nfr_result: "NfrResult | None" = None,
    adr_result: "AdrResult | None" = None,
    subproject_result: "SubProjectResult | None" = None,
    *,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
    enable_critique: bool = True,
    feedback: str = "",
) -> ArchitectureResult:
    """Generate system architecture + infrastructure diagrams.

    Multi-pass pipeline:

      Pass 1 — discovery (narrow: component identification).
      Pass 2 — gap pass (deterministic: missing layer skeletons).
      Pass 3 — detail (full architecture + infrastructure).
      Pass 4 — validation (deterministic: dangling refs).
      Pass 5 — LLM critique (optional, enabled by default).

    Takes NfrResult (stack), AdrResult (decisions), SubProjectResult
    (decomposition) and optionally MerResult (entity names) and produces
    an ArchitectureResult with Mermaid graph TD diagrams rendered
    deterministically from structured LLM output.
    """
    # Prerequisites: need at least NFR or sub-project data.
    if nfr_result is None and subproject_result is None:
        return ArchitectureResult(stats={"skipped": True})

    # Pass 1: discovery.
    skeletons = await _discover_components(
        project_name=project_name,
        project_description=project_description,
        goals=goals,
        mer_result=mer_result,
        nfr_result=nfr_result,
        adr_result=adr_result,
        subproject_result=subproject_result,
        feedback=feedback,
    )

    # Pass 2: gap pass (deterministic).
    gap_skeletons = _gap_pass(
        skeletons,
        mer_result=mer_result,
        subproject_result=subproject_result,
    )
    all_skeletons = skeletons + gap_skeletons

    # Pass 3: detail.
    batch = await _detail_architecture(
        all_skeletons,
        project_name=project_name,
        project_description=project_description,
        goals=goals,
        mer_result=mer_result,
        nfr_result=nfr_result,
        adr_result=adr_result,
        subproject_result=subproject_result,
        feedback=feedback,
    )

    if batch is None:
        return ArchitectureResult(stats={
            "error": "detailing_failed",
            "components": 0,
            "connections": 0,
            "containers": 0,
        })

    components = list(batch.components)
    connections = list(batch.connections)
    containers = list(batch.containers)
    system_description = batch.system_description
    infrastructure_description = batch.infrastructure_description

    # Pass 4: validation (deterministic).
    valid_connections, validation_warnings = _validate_architecture(
        components, connections, containers
    )
    connections = valid_connections

    # Render Mermaid diagrams deterministically.
    system_diagram = _render_system_architecture(components, connections)
    infra_diagram = _render_infrastructure(containers)

    # Pass 5: LLM critique (optional).
    critique_issues: list[dict] = []
    if enable_critique:
        critique_issues = await _critique_architecture(
            components, connections, containers,
            project_name=project_name,
            project_description=project_description,
            goals=goals,
        )

    stats = {
        "components": len(components),
        "connections": len(connections),
        "containers": len(containers),
        "skeletons_identified": len(skeletons),
        "gap_pass_found": len(gap_skeletons),
        "validation_warnings": validation_warnings,
        "critique_issues": critique_issues,
    }
    logger.info(
        "generate_architecture: %d components, %d connections, %d containers "
        "(gap +%d, %d warnings, %d critique issues)",
        stats["components"],
        stats["connections"],
        stats["containers"],
        stats["gap_pass_found"],
        len(validation_warnings),
        len(critique_issues),
    )
    return ArchitectureResult(
        system_architecture_diagram=system_diagram,
        system_architecture_description=system_description,
        infrastructure_diagram=infra_diagram,
        infrastructure_description=infrastructure_description,
        stats=stats,
    )
