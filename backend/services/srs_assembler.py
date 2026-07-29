"""Ensamblador del SRS: orquesta los motores y arma el payload de SrsDocument.

Punto único que coordina la generación de un SRS candidato:
  analyze_quality -> infer_goals -> compute_coverage -> build_traceability
y combina sus salidas en el payload que ``srs_store.create_srs`` persiste:
estructura 29148/Volere (secciones ``projected`` vs ``authored``), narrativa
editable (borrador), markdown snapshot, resumen de calidad, cobertura,
matriz de trazabilidad y los códigos de requerimiento.

Vive aparte de ``srs_builder`` (que es la proyección pura a Markdown) para no
acoplar la síntesis del entregable con la proyección; el builder se invoca aquí
para el cuerpo de requerimientos.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.goals_engine import infer_goals
from backend.services.requirement_store import list_requirements
from backend.services.srs_builder import build_srs
from backend.services.srs_coverage import compute_coverage
from backend.services.srs_quality import analyze_quality
from backend.services.srs_store import build_traceability, replace_findings

# Estados vivos (consistente con srs_builder).
from backend.models.requirement import ReqStatus

_LIVE_STATUSES = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)

# Árbol de secciones del SRS (ISO/IEC/IEEE 29148 + Volere).
# kind: "authored" = prosa editable por el usuario; "projected" = proyección
# de RequirementItem / goals / hallazgos (solo lectura, re-proyectable).
SRS_STRUCTURE: list[dict[str, Any]] = [
    {"id": "intro", "title": "1. Introducción", "kind": "authored"},
    {"id": "overall", "title": "2. Descripción general", "kind": "authored"},
    {
        "id": "goals",
        "title": "3. Objetivos y modelo de goals",
        "kind": "projected",
    },
    {
        "id": "functional",
        "title": "4. Requerimientos funcionales",
        "kind": "projected",
    },
    {
        "id": "nfr",
        "title": "5. Requerimientos no funcionales",
        "kind": "projected",
    },
    {
        "id": "constraints",
        "title": "6. Restricciones y cumplimiento",
        "kind": "projected",
    },
    {
        "id": "data",
        "title": "7. Datos e interfaces",
        "kind": "projected",
    },
    {
        "id": "quality",
        "title": "Anexo A. Análisis de calidad",
        "kind": "projected",
    },
    {
        "id": "coverage",
        "title": "Anexo B. Cobertura (ISO/IEC 25010)",
        "kind": "projected",
    },
    {
        "id": "traceability",
        "title": "Anexo C. Matriz de trazabilidad",
        "kind": "projected",
    },
]


def _draft_narrative(
    project_name: str,
    project_description: str,
    quality_summary: dict[str, Any],
    coverage: dict[str, Any],
    goals_summary: dict[str, Any],
    live_count: int,
) -> dict[str, str]:
    """Borrador de la prosa editable (intro + descripción general).

    Determinista (sin LLM): el usuario lo pule. Se basa en metadatos del
    proyecto + conteos del análisis.
    """
    title = project_name or "Especificación de Requerimientos de Software"
    desc_line = project_description.strip() if project_description else ""

    intro = (
        f"# {title}\n\n"
        f"## 1. Introducción\n\n"
        f"### Propósito\n\n"
        f"Este documento especifica los requerimientos de **{title}**. "
        f"_{('Editor: completar el propósito del producto.')}_\n\n"
        f"### Alcance\n\n"
        f"_{('Editor: describir el alcance del producto y qué queda fuera.')}_\n"
    )

    total = coverage.get("totals", {}).get("live", live_count)
    nfr = coverage.get("totals", {}).get("nfr", 0)
    func = coverage.get("totals", {}).get("functional", 0)
    blockers = quality_summary.get("blockers", 0)
    findings = quality_summary.get("total_findings", 0)

    overall = (
        f"## 2. Descripción general\n\n"
        f"### Perspectiva del producto\n\n"
        f"_{('Editor: perspectiva del producto y dependencias.')}_\n"
    )
    if desc_line:
        overall += f"\n{desc_line}\n"
    overall += (
        f"\n### Resumen del alcance especificado\n\n"
        f"- Requerimientos en el SRS: **{total}** (funcionales: {func}, no "
        f"funcionales: {nfr}).\n"
        f"- Goals modelados: **{goals_summary.get('goals', 0)}** "
        f"(softgoals: {goals_summary.get('softgoals', 0)}, obstáculos: "
        f"{goals_summary.get('obstacles', 0)}).\n"
        f"- Hallazgos de calidad: **{findings}** (bloqueantes: {blockers}).\n"
    )

    return {"intro": intro, "overall": overall}


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

    # 6. Cuerpo Markdown (proyección existente) + códigos vivos.
    built = await build_srs(
        session,
        project_id,
        project_name=project_name,
        project_description=project_description,
    )
    items = await list_requirements(session, project_id, include_deleted=True)
    live = [it for it in items if it.status in _LIVE_STATUSES]
    codes = [it.code for it in live]

    # 7. Narrativa editable (borrador).
    narrative = _draft_narrative(
        project_name,
        project_description,
        quality_summary,
        coverage,
        goals_summary,
        len(live),
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
