"""Motor de cobertura del SRS (ISO/IEC 25010 + 29148 + goals).

Chequeo PROGRAMÁTICO, determinista y sin LLM: mapea los requerimientos vivos a
las 8 características de calidad de ISO/IEC 25010, cuenta cobertura, marca los
gaps (características sin ningún requerimiento) y audita la cobertura de goals
(goals sin ningún requerimiento que los REALIZES = gap; obstacles sin mitigar =
riesgo). También confirma la presencia de las secciones clave de 29148.

La salida es un dict ``coverage`` (para SrsDocument.coverage) más una lista de
hallazgos ``COVERAGE_GAP`` / ``MISSING_REQ`` que el motor de calidad persiste.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.requirement import ReqStatus, ReqType
from backend.models.srs import (
    FindingDimension,
    FindingScope,
    FindingSeverity,
    GoalStatus,
    LinkRelation,
)
from backend.services.requirement_store import list_requirements
from backend.services.srs_store import list_goal_links, list_goals

# Los 8 caratteristicos de calidad de ISO/IEC 25010 (SQuaRE), ordenados.
ISO_25010: dict[str, str] = {
    "functional_suitability": "Adecuación funcional",
    "performance_efficiency": "Eficiencia en el rendimiento",
    "compatibility": "Compatibilidad",
    "usability": "Usabilidad",
    "reliability": "Confiabilidad",
    "security": "Seguridad",
    "maintainability": "Mantenibilidad",
    "portability": "Portabilidad",
}

# Mapeo ReqType -> característica(s) 25010. Los tipos que no son de calidad
# (compliance, constraint, process, data) se reportan aparte, no como chars.
REQTYPE_TO_25010: dict[ReqType, list[str]] = {
    ReqType.FUNCTIONAL: ["functional_suitability"],
    ReqType.PERFORMANCE: ["performance_efficiency"],
    ReqType.SECURITY: ["security"],
    ReqType.USABILITY: ["usability"],
    ReqType.RELIABILITY: ["reliability"],
    ReqType.MAINTAINABILITY: ["maintainability"],
}

# Estados vivos (consistente con srs_builder._LIVE_STATUSES).
_LIVE_STATUSES = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)


async def compute_coverage(
    session: AsyncSession, project_id: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Calcula la cobertura y devuelve (coverage_dict, findings).

    ``coverage_dict`` tiene: counts por característica 25010, gaps, totales,
    presencia de secciones 29148 y cobertura de goals. ``findings`` son dict
    listos para ``srs_store.replace_findings`` (scope SET, sin req_id).
    """
    items = await list_requirements(session, project_id, include_deleted=True)
    live = [it for it in items if it.status in _LIVE_STATUSES]

    # ----- ISO 25010 -------------------------------------------------------
    char_counts: dict[str, int] = {key: 0 for key in ISO_25010}
    extra: dict[str, int] = defaultdict(int)  # compliance/constraint/process/data
    for it in live:
        chars = REQTYPE_TO_25010.get(it.type, [])
        if chars:
            for c in chars:
                char_counts[c] += 1
        else:
            extra[it.type.value] += 1

    gaps = [key for key, n in char_counts.items() if n == 0]
    by_type: dict[str, int] = defaultdict(int)
    for it in live:
        by_type[it.type.value] += 1

    # ----- 29148 section presence -----------------------------------------
    # Heurística de presencia: la sección de requerimientos específicos existe
    # si hay >=1 vivo; la descripción general si hay metadatos (lo deja el
    # builder); la introducción siempre se proyecta.
    has_specific = len(live) > 0
    has_functional = char_counts["functional_suitability"] > 0
    has_nfr = any(
        n > 0 for k, n in char_counts.items() if k != "functional_suitability"
    )

    # ----- Goals coverage --------------------------------------------------
    all_goals = await list_goals(session, project_id)
    # Los STALE (parked a decisión) no cuentan como gaps: no son parte del
    # catálogo vigente, solo auditoría pendiente.
    goals = [g for g in all_goals if g.status != GoalStatus.STALE]
    stale_goals = len(all_goals) - len(goals)
    links = await list_goal_links(session, project_id)
    realizes_by_goal: dict[int, list[int]] = defaultdict(list)
    any_link_by_goal: set[int] = set()
    linked_req_ids: set[int] = set()
    for l in links:
        any_link_by_goal.add(l.goal_id)
        linked_req_ids.add(l.req_id)
        if l.relation == LinkRelation.REALIZES:
            realizes_by_goal[l.goal_id].append(l.req_id)

    functional_goals = [g for g in goals if g.kind.value != "obstacle"]
    obstacles = [g for g in goals if g.kind.value == "obstacle"]

    # Dirección req->goal (la que faltaba): reqs vivos sin ningún link no
    # aportan a ningún objetivo — es la cola de trazabilidad que el agente
    # debe cerrar con infer_goal_links antes del commit.
    unlinked_codes = [
        it.code for it in live if it.id not in linked_req_ids
    ]
    unlinked_count = len(unlinked_codes)
    goal_gaps = [
        g.code for g in functional_goals if not realizes_by_goal.get(g.id)
    ]
    unmitigated_obstacles = [
        g.code for g in obstacles if g.id not in any_link_by_goal
    ]

    # ----- Findings --------------------------------------------------------
    findings: list[dict[str, Any]] = []
    # Característica 25010 sin ningún requerimiento -> cobertura gap.
    for key in gaps:
        findings.append(
            {
                "scope": FindingScope.SET,
                "dimension": FindingDimension.COVERAGE_GAP,
                "rule_id": f"iso25010.{key}",
                "severity": FindingSeverity.MINOR,
                "message": (
                    f"ISO/IEC 25010 «{ISO_25010[key]}» no tiene ningún "
                    f"requerimiento. Si aplica al producto, conviene "
                    f"declarar al menos uno (o confirmar que está fuera de "
                    f"alcance)."
                ),
                "suggestion": None,
                "detected_by": "programmatic",
            }
        )
    # Sin funcionales -> bloqueante: no hay producto que especificar.
    if not has_functional:
        findings.append(
            {
                "scope": FindingScope.SET,
                "dimension": FindingDimension.MISSING_REQ,
                "rule_id": "29148.no_functional",
                "severity": FindingSeverity.BLOCKER,
                "message": (
                    "No hay requerimientos funcionales. Un SRS necesita al "
                    "menos una capacidad funcional del sistema."
                ),
                "suggestion": None,
                "detected_by": "programmatic",
            }
        )
    # Goals funcionales sin ningún req que los REALIZES.
    for code in goal_gaps:
        findings.append(
            {
                "scope": FindingScope.SET,
                "dimension": FindingDimension.COVERAGE_GAP,
                "rule_id": f"gore.unrealized.{code}",
                "severity": FindingSeverity.MAJOR,
                "message": (
                    f"El goal «{code}» no tiene ningún requerimiento que lo "
                    f"realice (REALIZES). Falta descomponerlo en requerimientos "
                    f"o descartarlo."
                ),
                "suggestion": None,
                "detected_by": "programmatic",
            }
        )
    # Obstacles sin mitigar.
    for code in unmitigated_obstacles:
        findings.append(
            {
                "scope": FindingScope.SET,
                "dimension": FindingDimension.COVERAGE_GAP,
                "rule_id": f"gore.unmitigated_obstacle.{code}",
                "severity": FindingSeverity.MAJOR,
                "message": (
                    f"El obstáculo/riesgo «{code}» no tiene ningún "
                    f"requerimiento que lo mitigue. Debería derivar en al "
                    f"menos un requerimiento."
                ),
                "suggestion": None,
                "detected_by": "programmatic",
            }
        )
    # Reqs vivos sin ningún goal (dirección req->goal): hallazgo AGREGADO de
    # scope proyecto — antes esta vista solo existía en la tool opt-in
    # goal_coverage y el pipeline «terminaba bien» con media matriz.
    if unlinked_count:
        share = unlinked_count / max(1, len(live))
        sample = ", ".join(unlinked_codes[:25])
        more = (
            f" (y {unlinked_count - 25} más)" if unlinked_count > 25 else ""
        )
        findings.append(
            {
                "scope": FindingScope.SET,
                "dimension": FindingDimension.COVERAGE_GAP,
                "rule_id": "gore.unlinked_requirements",
                "severity": (
                    FindingSeverity.MAJOR if share > 0.1 else FindingSeverity.MINOR
                ),
                "message": (
                    f"{unlinked_count} de {len(live)} requerimientos vivos no "
                    f"aportan a ningún goal ({sample}{more}). Cierra la cola "
                    "con infer_goal_links(req_codes=...) antes del commit."
                ),
                "suggestion": None,
                "detected_by": "programmatic",
            }
        )

    coverage = {
        "iso_25010": {
            key: {"label": ISO_25010[key], "count": char_counts[key]}
            for key in ISO_25010
        },
        "gaps_25010": gaps,
        "by_type": dict(by_type),
        "extra_types": dict(extra),
        "totals": {
            "live": len(live),
            "functional": char_counts["functional_suitability"],
            "nfr": sum(
                n for k, n in char_counts.items() if k != "functional_suitability"
            ),
        },
        "iso_29148_sections": {
            "introduction": True,
            "definitions": False,
            "references": False,
            "overall_description": has_specific,
            "product_perspective": has_specific,
            "user_classes": False,
            "operating_environment": False,
            "assumptions": False,
            "specific_requirements": has_specific,
            "functional": has_functional,
            "business_rules": by_type.get("process", 0) > 0,
            "nonfunctional": has_nfr,
            "constraints": (
                by_type.get("constraint", 0) + by_type.get("compliance", 0) > 0
            ),
            "data_interfaces": by_type.get("data", 0) > 0,
        },
        "goals": {
            "total": len(goals),
            "stale": stale_goals,
            "functional": len(functional_goals),
            "softgoals": sum(1 for g in goals if g.kind.value == "softgoal"),
            "obstacles": len(obstacles),
            "unrealized": goal_gaps,
            "unmitigated_obstacles": unmitigated_obstacles,
            "unlinked_requirements": unlinked_count,
            "unlinked_codes": unlinked_codes[:200],
        },
    }
    return coverage, findings
