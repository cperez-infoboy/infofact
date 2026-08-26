"""Ensamblador del SRS: orquesta los motores y arma el payload de SrsDocument.

Punto único que coordina la generación de un SRS candidato:
  analyze_quality -> infer_goals -> compute_coverage -> build_traceability
y combina sus salidas en el payload que ``srs_store.create_srs`` persiste:
estructura 29148/Volere (secciones ``projected`` vs ``authored``), narrativa
editable (borrador por subsection), markdown snapshot, resumen de calidad,
cobertura, matriz de trazabilidad y los códigos de requerimiento.

Vive aparte de ``srs_builder`` (que es la proyección pura a Markdown) para no
acoplar la síntesis del entregable con la proyección; el builder se invoca aquí
para el cuerpo de requerimientos.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.llm import structured_llm
from backend.agents.retrieval import store as retrieval
from backend.services.goals_engine import infer_goals
from sqlalchemy import select

from backend.models.srs import Goal, GoalLink
from backend.services.requirement_store import list_requirements
from backend.services.srs_builder import (
    _TYPE_LABELS,
    SRS_STRUCTURE,
    build_srs,
)
from backend.services.srs_coverage import compute_coverage
from backend.services.srs_quality import analyze_quality
from backend.services.srs_store import build_traceability, replace_findings

# Estados vivos (consistente con srs_builder).
from backend.models.requirement import Priority, ReqStatus

logger = logging.getLogger(__name__)

_LIVE_STATUSES = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)

# Orden de prioridad para la muestra de requerimientos (MUST primero).
_PRIORITY_RANK = {
    Priority.MUST: 0,
    Priority.SHOULD: 1,
    Priority.COULD: 2,
    Priority.WONT: 3,
}


# ---------------------------------------------------------------------------
# Schema para el draft narrativo asistido por LLM (8 secciones authored).
# ---------------------------------------------------------------------------


class SrsNarrativeDraft(BaseModel):
    """LLM-generated prose for the 8 authored SRS subsections."""

    purpose: str = Field(
        description=(
            "Sección 1.1 Propósito: 2-3 párrafos sobre el propósito del "
            "producto y del documento SRS."
        )
    )
    scope: str = Field(
        description="Sección 1.2 Alcance: qué incluye el producto y qué queda fuera."
    )
    definitions: str = Field(
        description=(
            "Sección 1.3 Definiciones: términos y acrónimos del dominio en "
            "formato Markdown con viñetas."
        )
    )
    references: str = Field(
        description=(
            "Sección 1.4 Referencias: normas y documentos referenciados en "
            "formato Markdown con viñetas."
        )
    )
    perspective: str = Field(
        description=(
            "Sección 2.1 Perspectiva: descripción del producto, dependencias "
            "y contexto. NO incluir conteos."
        )
    )
    users: str = Field(
        description=(
            "Sección 2.3 Usuarios: roles detectados con privilegios y "
            "frecuencia estimada."
        )
    )
    environment: str = Field(
        description=(
            "Sección 2.4 Entorno operativo: plataforma, tecnologías e "
            "integraciones inferidas."
        )
    )
    assumptions: str = Field(
        description="Sección 2.5 Supuestos y dependencias del proyecto."
    )


_NARRATIVE_SYSTEM = (
    "Eres un analista de requerimientos de software que redacta secciones "
    "de una Especificación de Requerimientos de Software (SRS) conforme a "
    "ISO/IEC/IEEE 29148:2018. Recibirás el nombre del proyecto, su "
    "descripción, una muestra de requerimientos y fragmentos de documentos fuente.\n"
    "Reglas:\n"
    "- IDIOMA: español neutro y profesional. Sin regionalismos.\n"
    "- TONO: objetivo, técnico, impersonal (tercera persona).\n"
    "- No inventes funcionalidades que no estén respaldadas por los "
    "requerimientos o fragmentos proporcionados.\n"
    "- Para definitions: extrae términos técnicos y acrónimos del dominio "
    "que aparezcan en los requerimientos o fragmentos.\n"
    "- Para references: cita ISO/IEC/IEEE 29148:2018, ISO/IEC 25010:2011 y "
    "cualquier norma detectada en los requerimientos.\n"
    "- Cada sección debe ser prosa coherente, excepto definitions y "
    "references que pueden usar viñetas Markdown.\n"
)


def _feature_line(it: Any) -> str:
    """Línea de requerimiento para la sección 2.2: MoSCoW + tipo + resumen."""
    stmt = it.statement or ""
    # Truncate to ~120 chars at word boundary (overview, no catálogo completo).
    if len(stmt) > 120:
        stmt = stmt[:117].rsplit(" ", 1)[0] + "…"
    prio = getattr(getattr(it, "priority", None), "value", "?").upper()
    type_val = getattr(getattr(it, "type", None), "value", "?")
    type_label = _TYPE_LABELS.get(type_val, type_val)
    return f"- `{it.code}` ({prio} · {type_label}) — {stmt}"


def _draft_narrative(
    project_name: str,
    project_description: str,
    quality_summary: dict[str, Any],
    coverage: dict[str, Any],
    goals_summary: dict[str, Any],
    live_count: int,
    live_items: list | None = None,
    goal_groups: list[tuple[str, str, list]] | None = None,
) -> dict[str, str]:
    """Borrador de la prosa editable por subsection.

    Determinista (sin LLM): el usuario lo pule vía PATCH. Devuelve un dict con
    claves por subsection ID (e.g. ``"intro.purpose"``) más claves legacy
    (``"intro"``, ``"overall"``) que concatenan las subsections para
    retrocompatibilidad con versiones anteriores.
    """
    title = project_name or "Especificación de Requerimientos de Software"
    desc_line = project_description.strip() if project_description else ""

    total = coverage.get("totals", {}).get("live", live_count)
    nfr_count = coverage.get("totals", {}).get("nfr", 0)
    func_count = coverage.get("totals", {}).get("functional", 0)
    blockers = quality_summary.get("blockers", 0)
    findings = quality_summary.get("total_findings", 0)

    # --- Subsections: intro ------------------------------------------------
    intro_purpose = (
        f"Este documento especifica los requerimientos de **{title}**. "
        f"_Editor: completar el propósito del producto._"
    )
    intro_scope = "_Editor: describir el alcance del producto y qué queda fuera._"
    intro_definitions = (
        "_Editor: listar definiciones, acrónimos y términos del dominio._"
    )
    intro_references = (
        "_Editor: listar normas y documentos referenciados._"
    )
    intro_overview = (
        "Este documento se organiza en las siguientes secciones: "
        "Sección 1 (Introducción), Sección 2 (Descripción general), "
        "Sección 3 (Objetivos y modelo de goals), "
        "Sección 4 (Requerimientos funcionales), "
        "Sección 5 (Reglas de negocio), "
        "Sección 6 (Requerimientos no funcionales), "
        "Sección 7 (Restricciones y cumplimiento), "
        "Sección 8 (Requerimientos de interfaces y datos), "
        "y los Anexos A-C (Calidad, Cobertura y Trazabilidad)."
    )

    # --- Subsections: overall ----------------------------------------------
    overall_perspective_parts = [
        "_Editor: describir la perspectiva del producto y sus dependencias._",
    ]
    if desc_line:
        overall_perspective_parts.append(desc_line)
    overall_perspective_parts.append(
        f"\n**Resumen del alcance especificado:**\n"
        f"- Requerimientos en el SRS: **{total}** (funcionales: {func_count}, "
        f"no funcionales: {nfr_count}).\n"
        f"- Goals modelados: **{goals_summary.get('goals', 0)}** "
        f"(softgoals: {goals_summary.get('softgoals', 0)}, obstáculos: "
        f"{goals_summary.get('obstacles', 0)}).\n"
        f"- Hallazgos de calidad: **{findings}** (bloqueantes: {blockers})."
    )
    overall_perspective = "\n\n".join(overall_perspective_parts)

    # Deterministic: funcionalidades agrupadas por goal funcional (sección 2.2).
    # Cada ítem muestra MoSCoW + tipo; el catálogo formal vive en secciones 4-8.
    if live_items:
        func_items = [
            it for it in live_items
            if it.type.value == "functional" and it.status in _LIVE_STATUSES
        ]
        if func_items:
            linked: set[int] = set()
            features_lines: list[str] = []
            for goal_code, goal_stmt, gitems in goal_groups or []:
                features_lines.append(f"**`{goal_code}`** — {goal_stmt}")
                features_lines.append("")
                for it in gitems:
                    linked.add(id(it))
                    features_lines.append(_feature_line(it))
                features_lines.append("")
            unlinked = [it for it in func_items if id(it) not in linked]
            if unlinked:
                features_lines.append("**Sin goal asociado**")
                features_lines.append("")
                for it in unlinked:
                    features_lines.append(_feature_line(it))
            overall_features = "\n".join(features_lines).strip()
        else:
            overall_features = "_Sin requerimientos funcionales para listar._"
    else:
        overall_features = "_Sin requerimientos funcionales para listar._"

    overall_users = (
        "_Editor: describir las clases de usuario, su frecuencia de uso, "
        "privilegios y nivel de experiencia._"
    )
    overall_environment = (
        "_Editor: describir el entorno operativo (plataforma, sistema "
        "operativo, navegadores, integraciones)._"
    )
    overall_assumptions = (
        "_Editor: listar supuestos y dependencias del proyecto._"
    )

    # --- Build subsection-keyed narrative ----------------------------------
    narrative: dict[str, str] = {
        "intro.purpose": intro_purpose,
        "intro.scope": intro_scope,
        "intro.definitions": intro_definitions,
        "intro.references": intro_references,
        "intro.overview": intro_overview,
        "overall.perspective": overall_perspective,
        "overall.features": overall_features,
        "overall.users": overall_users,
        "overall.environment": overall_environment,
        "overall.assumptions": overall_assumptions,
    }

    # --- Legacy aggregated keys for backward compatibility -----------------
    narrative["intro"] = "\n\n".join(
        narrative[k]
        for k in [
            "intro.purpose",
            "intro.scope",
            "intro.definitions",
            "intro.references",
            "intro.overview",
        ]
    )
    narrative["overall"] = "\n\n".join(
        narrative[k]
        for k in [
            "overall.perspective",
            "overall.features",
            "overall.users",
            "overall.environment",
            "overall.assumptions",
        ]
    )

    return narrative


# ---------------------------------------------------------------------------
# Draft narrativo asistido por LLM (8 secciones authored).
# ---------------------------------------------------------------------------


async def _rag_context_for_section(project_id: int, section_type: str) -> str:
    """Busca en los documentos de captura contexto relevante para una sección.

    Cada sección tiene una query adaptada a su propósito; ``used_in_capture_only``
    restringe a los documentos que originaron los requerimientos vivos (no
    documentos sueltos del proyecto sin capturar).
    """
    queries = {
        "purpose": "propósito objetivo producto sistema",
        "scope": "alcance del producto límites",
        "definitions": "términos técnicos definiciones glosario acrónimos",
        "references": "normas estándares ISO IEEE regulaciones",
        "perspective": "contexto del producto dependencias integraciones",
        "users": "roles de usuario tipos de usuario",
        "environment": "entorno operativo plataforma tecnologías",
        "assumptions": "supuestos dependencias del proyecto",
    }
    query = queries.get(section_type, section_type)
    try:
        hits = await retrieval.search(
            project_id, query, top_k=3, used_in_capture_only=True
        )
    except Exception:  # noqa: BLE001 — RAG es best-effort
        logger.warning(
            "_rag_context_for_section: retrieval failed for %s",
            section_type,
            exc_info=True,
        )
        return ""
    if not hits:
        return ""
    return "\n".join(
        f"[{h.section_path} p.{h.page}] {h.text[:300]}" for h in hits
    )


async def _build_narrative_context(
    project_id: int,
    project_name: str,
    project_description: str,
    live_items: list,
    quality_summary: dict[str, Any],
    coverage: dict[str, Any],
    goals_summary: dict[str, Any],
) -> str:
    """Construye el mensaje de usuario para la llamada LLM narrativa.

    Incluye: nombre y descripción del proyecto, top-20 requerimientos vivos
    ordenados por prioridad (MUST primero), métricas agregadas y fragmentos
    RAG por tipo de sección.
    """
    parts: list[str] = []
    parts.append(f"# Proyecto: {project_name or '(sin nombre)'}")
    if project_description:
        parts.append(f"\n## Descripción del proyecto\n{project_description}")

    # Reglas persistentes del proyecto (scope srs + all): consideraciones
    # duraderas del usuario que dirigen la redacción. Best-effort — si el
    # harness no carga, la narrativa sigue sin reglas.
    try:
        from backend.database import AsyncSessionLocal
        from backend.models.project_rule import RuleScope
        from backend.services import project_rules_store

        async with AsyncSessionLocal() as session:
            rules_block = await project_rules_store.rules_block_for(
                session, project_id, RuleScope.SRS
            )
        if rules_block:
            parts.append("\n## Reglas del proyecto\n" + rules_block)
    except Exception:  # noqa: BLE001 — best-effort
        import logging

        logging.getLogger(__name__).warning(
            "narrative: no se pudieron cargar las reglas del proyecto",
            exc_info=True,
        )

    # Top-20 requerimientos por prioridad.
    parts.append("\n## Requerimientos (muestra)")
    sorted_items = sorted(
        live_items,
        key=lambda it: _PRIORITY_RANK.get(
            getattr(it, "priority", Priority.WONT), 4
        ),
    )
    for it in sorted_items[:20]:
        stmt = (it.statement or "")[:150]
        prio = getattr(getattr(it, "priority", None), "value", "?")
        parts.append(f"- [{it.code}] ({prio}): {stmt}")

    # Métricas.
    totals = coverage.get("totals", {})
    parts.append("\n## Métricas")
    parts.append(f"- Total requerimientos vivos: {totals.get('live', len(live_items))}")
    parts.append(f"- Funcionales: {totals.get('functional', 0)}")
    parts.append(f"- No funcionales: {totals.get('nfr', 0)}")
    parts.append(f"- Goals modelados: {goals_summary.get('goals', 0)}")
    parts.append(f"- Hallazgos de calidad: {quality_summary.get('total_findings', 0)}")

    # Contexto RAG por sección.
    parts.append("\n## Fragmentos de documentos fuente por sección")
    for section in (
        "purpose",
        "scope",
        "definitions",
        "references",
        "perspective",
        "users",
        "environment",
        "assumptions",
    ):
        ctx = await _rag_context_for_section(project_id, section)
        if ctx:
            parts.append(f"\n### {section}\n{ctx}")

    return "\n".join(parts)


async def draft_narrative_llm(
    narrative: dict[str, str],
    *,
    project_id: int,
    project_name: str,
    project_description: str,
    live_items: list,
    quality_summary: dict[str, Any],
    coverage: dict[str, Any],
    goals_summary: dict[str, Any],
    instructions: str | None = None,
) -> dict[str, str]:
    """Enriquece la narrativa determinista con prosa generada por LLM.

    Sobrescribe las 8 subsecciones authored (purpose, scope, definitions,
    references, perspective, users, environment, assumptions) con texto del
    LLM, preservando las claves deterministas (``intro.overview`` y
    ``overall.features``). El bloque de conteos de ``overall.perspective``
    se reapende después del texto del LLM para mantener el resumen de alcance.

    ``instructions`` (opcional) son indicaciones narrativas del usuario
    (p. ej. "incorporar el carácter multi-industria en propósito y alcance").
    Sin este canal las indicaciones conversacionales NUNCA llegan al
    redactor: el prompt se arma solo desde store + RAG.

    Si la llamada LLM falla, devuelve ``narrative`` sin cambios (fallback
    determinista).
    """
    try:
        user_msg = await _build_narrative_context(
            project_id,
            project_name,
            project_description,
            live_items,
            quality_summary,
            coverage,
            goals_summary,
        )
        if instructions:
            user_msg += (
                "\n\n## INDICACIONES DEL USUARIO SOBRE LA NARRATIVA "
                "(prioridad maxima)\n"
                + instructions.strip()
                + "\n\nIncorpora estas indicaciones en las subsecciones que "
                "correspondan, respetando el resto del contexto y sin inventar "
                "hechos sin respaldo."
            )
        runner = structured_llm(SrsNarrativeDraft)
        draft = await runner.ainvoke(
            [("system", _NARRATIVE_SYSTEM), ("user", user_msg)]
        )
    except Exception:  # noqa: BLE001 — fallback graceful
        logger.warning(
            "draft_narrative_llm: fallo LLM, usando narrativa determinista",
            exc_info=True,
        )
        return narrative

    # Extraer el bloque de conteos de la perspectiva determinista.
    det_perspective = narrative.get("overall.perspective", "")
    marker = "\n**Resumen del alcance especificado:**"
    counts_block = ""
    if marker in det_perspective:
        counts_block = det_perspective[det_perspective.index(marker):]

    updated = dict(narrative)
    # 8 subsecciones authored -> prosa LLM.
    updated["intro.purpose"] = draft.purpose
    updated["intro.scope"] = draft.scope
    updated["intro.definitions"] = draft.definitions
    updated["intro.references"] = draft.references
    updated["overall.perspective"] = draft.perspective + (
        "\n\n" + counts_block if counts_block else ""
    )
    updated["overall.users"] = draft.users
    updated["overall.environment"] = draft.environment
    updated["overall.assumptions"] = draft.assumptions

    # Preservar deterministicos: intro.overview, overall.features (ya en updated).

    # Reconstruir claves legacy.
    updated["intro"] = "\n\n".join(
        updated[k]
        for k in [
            "intro.purpose",
            "intro.scope",
            "intro.definitions",
            "intro.references",
            "intro.overview",
        ]
    )
    updated["overall"] = "\n\n".join(
        updated[k]
        for k in [
            "overall.perspective",
            "overall.features",
            "overall.users",
            "overall.environment",
            "overall.assumptions",
        ]
    )
    return updated


async def _functional_goal_groups(
    session: AsyncSession,
    project_id: int,
    live_items: list,
) -> list[tuple[str, str, list]]:
    """Agrupa requerimientos funcionales vivos bajo su goal funcional.

    Devuelve ``(goal_code, goal_statement, items)`` para cada goal funcional
    que tenga al menos un requerimiento funcional vivo enlazado (relación
    ``realizes``/``contributes``). Los ítems sin goal quedan fuera; la sección
    2.2 los lista bajo "Sin goal asociado" en ``_draft_narrative``.
    """
    goals = list(
        await session.scalars(
            select(Goal).where(
                Goal.project_id == project_id,
                Goal.kind == "functional_goal",
            )
        )
    )
    if not goals:
        return []
    links = list(
        await session.scalars(
            select(GoalLink).where(
                GoalLink.goal_id.in_([g.id for g in goals])
            )
        )
    )
    live_by_id = {it.id: it for it in live_items if it.id is not None}
    groups: list[tuple[str, str, list]] = []
    for g in goals:
        gitems = []
        for gl in links:
            if gl.goal_id != g.id:
                continue
            it = live_by_id.get(gl.req_id)
            if it is None:
                continue
            if it.type.value != "functional" or it.status not in _LIVE_STATUSES:
                continue
            gitems.append(it)
        if gitems:
            groups.append((g.code, g.statement, gitems))
    return groups


async def assemble_srs(
    session: AsyncSession,
    project_id: int,
    *,
    project_name: str = "",
    project_description: str = "",
) -> dict[str, Any]:
    """Genera un SRS candidato y devuelve el payload para ``create_srs``.

    Orden: quality -> goals -> coverage -> traceability -> markdown. Cada motor
    lee el estado anterior (coverage lee los goals que infer_goals persistió).
    Los hallazgos de calidad + cobertura se fusionan y se persisten al final.
    """
    # 1. Calidad (programática + LLM).
    quality_summary, q_findings = await analyze_quality(session, project_id)

    # 2. Goals (LLM; persiste goals + links).
    goals_summary = await infer_goals(session, project_id)

    # 3. Cobertura (programática; lee reqs + goals).
    coverage, cov_findings = await compute_coverage(session, project_id)

    # 4. Persistir hallazgos (calidad + cobertura).
    await replace_findings(session, project_id, q_findings + cov_findings)

    # 5. Trazabilidad (lee goals + links).
    traceability = await build_traceability(session, project_id)

    # 6. Items + códigos vivos.
    items = await list_requirements(session, project_id, include_deleted=True)
    live = [it for it in items if it.status in _LIVE_STATUSES]
    codes = [it.code for it in live]

    # 7. Narrativa editable (borrador con subsections).
    # Agrupación de funcionales por goal para la sección 2.2 (overview).
    goal_groups = await _functional_goal_groups(session, project_id, live)

    narrative = _draft_narrative(
        project_name,
        project_description,
        quality_summary,
        coverage,
        goals_summary,
        len(live),
        live_items=live,
        goal_groups=goal_groups,
    )

    # 8. Cuerpo Markdown (proyección con narrative + structure).
    built = await build_srs(
        session,
        project_id,
        project_name=project_name,
        project_description=project_description,
        narrative=narrative,
        structure=SRS_STRUCTURE,
    )

    return {
        "structure": SRS_STRUCTURE,
        "narrative": narrative,
        "markdown": built["markdown"],
        "quality_summary": {**quality_summary, "goals": goals_summary},
        "coverage": coverage,
        "traceability": traceability,
        "requirement_codes": codes,
        "requirement_count": len(live),
        "generated_by": "agent",
    }
