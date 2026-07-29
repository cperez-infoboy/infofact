"""Motor de goals (GORE / KAOS-lite): infiere objetivos y los enlaza a reqs.

Sobre el conjunto de requerimientos vivos, un LLM infiere el MODELO DE GOALS:
- FUNCTIONAL_GOAL: qué debe lograr el sistema (el «para qué»).
- SOFTGOAL: objetivo de calidad / NFR (rendimiento, seguridad, ...), puente con
  ISO/IEC 25010.
- OBSTACLE: anti-goal / riesgo que requerimientos deben mitigar.

Con jerarquía de refinamiento (goal -> sub-goal) y vínculos goal <-> req
(REALIZES / CONTRIBUTES / CONFLICTS) que dan la trazabilidad que consume la
fase de diseño.

Anti-alucinación: los links se resuelven contra el mapa REAL de REQ-codes del
proyecto (``replace_goals`` descarta cualquier referencia inexistente). Las
sugerencias/ statements conservan el idioma del enunciado fuente; los mensajes
van en español neutro.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.llm import structured_llm
from backend.agents.pipelines._resilience import _TRANSIENT_RETRIES, is_transient
from backend.models.requirement import ReqStatus
from backend.models.srs import GoalKind, LinkRelation
from backend.services.requirement_store import list_requirements
from backend.services.srs_store import replace_goals

logger = logging.getLogger(__name__)

_INFER_ATTEMPTS = 3
_SOFTGOAL_25010 = {
    "functional_suitability", "performance_efficiency", "compatibility",
    "usability", "reliability", "security", "maintainability", "portability",
}


class InferredGoal(BaseModel):
    code: str  # alias estable para referenciarlo desde links/parent (p.ej. "G1")
    kind: Literal["functional_goal", "softgoal", "obstacle"]
    statement: str
    rationale: str | None = None
    parent_code: str | None = None
    # Solo para softgoals: característica ISO 25010 asociada.
    quality_char: str | None = None


class InferredLink(BaseModel):
    goal_code: str
    req_code: str  # REQ-XXXX existente en el proyecto
    relation: Literal["realizes", "contributes", "conflicts"]
    rationale: str | None = None


class GoalModel(BaseModel):
    goals: list[InferredGoal] = Field(default_factory=list)
    links: list[InferredLink] = Field(default_factory=list)


_GOAL_SYSTEM = (
    "You are a GOAL MODELING analyst (GORE: KAOS + i*/Tropos + NFR Framework) "
    "for a software project. You receive the project's requirements and infer "
    "the GOAL MODEL that explains WHY those requirements exist.\n\n"
    "Model:\n"
    "- FUNCTIONAL_GOAL: what the system must achieve for its users (the "
    "purpose). 1-2 levels: high-level business/user goals refined into "
    "sub-goals via parent_code.\n"
    "- SOFTGOAL: a quality/NFR goal (performance, security, usability, "
    "reliability, ...). Set quality_char to the matching ISO/IEC 25010 "
    "characteristic.\n"
    "- OBSTACLE: a risk / anti-goal that some requirement must mitigate.\n"
    "- LINKS: connect goals to requirements. relation=realizes when the req "
    "fully satisfies the goal; contributes when partial; conflicts when the "
    "req opposes the goal.\n\n"
    "Rules:\n"
    "- LANGUAGE: every statement, rationale and link rationale MUST stay in the "
    "SAME LANGUAGE as the requirements. Never translate. Messages are not "
    "needed here.\n"
    "- Keep the model FOCUSED: aim for 5-15 goals, not one per requirement. "
    "Group related requirements under a shared goal.\n"
    "- Only reference req_code values that appear in the input. Never invent "
    "requirement codes.\n"
    "- parent_code and goal_code in links must reference a goal's `code` you "
    "emit.\n"
    "- Return ONLY the structured object."
)


async def infer_goals(
    session: AsyncSession, project_id: int
) -> dict[str, Any]:
    """Infiere el modelo de goals y lo persiste (replace_goals).

    Devuelve un resumen {goals, links, softgoals, obstacles}. Si no hay reqs
    vivos, limpia goals previos y devuelve ceros.
    """
    items = await list_requirements(session, project_id, include_deleted=True)
    live = [it for it in items if it.status in _LIVE_STATUSES]
    req_by_code = {it.code: it.id for it in live}

    if not live:
        await replace_goals(session, project_id, [], [], req_by_code=req_by_code)
        return {"goals": 0, "links": 0, "softgoals": 0, "obstacles": 0}

    catalog = "\n".join(
        f"- {it.code} [{it.type.value}]: {it.statement}" for it in live
    )
    msgs = [
        ("system", _GOAL_SYSTEM),
        ("human", f"PROJECT REQUIREMENTS:\n{catalog}"),
    ]

    llm = structured_llm(GoalModel)
    model: GoalModel | None = None
    parse_fails = 0
    transient_fails = 0
    while True:
        try:
            model = await llm.ainvoke(msgs)
            break
        except Exception as exc:  # noqa: BLE001
            if is_transient(exc):
                transient_fails += 1
                if transient_fails >= _TRANSIENT_RETRIES:
                    logger.warning(
                        "goals infer transient exhausted (%s); skipping goals",
                        type(exc).__name__,
                    )
                    model = GoalModel()
                    break
                await asyncio.sleep(min(2 ** transient_fails, 60))
            else:
                parse_fails += 1
                if parse_fails >= _INFER_ATTEMPTS:
                    logger.warning(
                        "goals infer parse failed after %d; skipping goals",
                        parse_fails,
                    )
                    model = GoalModel()
                    break

    goals_payload: list[dict[str, Any]] = []
    for g in model.goals:
        kind = _safe_kind(g.kind)
        goals_payload.append(
            {
                "code": g.code,
                "kind": kind,
                "statement": g.statement,
                "rationale": g.rationale,
                "parent_code": g.parent_code,
                "confidence": 0.7,
            }
        )
    links_payload: list[dict[str, Any]] = []
    for l in model.links:
        relation = _safe_relation(l.relation)
        if relation is None:
            continue
        links_payload.append(
            {
                "goal_code": l.goal_code,
                "req_code": l.req_code,
                "relation": relation,
                "rationale": l.rationale,
            }
        )

    result = await replace_goals(
        session, project_id, goals_payload, links_payload, req_by_code=req_by_code
    )
    softgoals = sum(1 for g in model.goals if g.kind == "softgoal")
    obstacles = sum(1 for g in model.goals if g.kind == "obstacle")
    summary = {
        "goals": result["goals"],
        "links": result["links"],
        "softgoals": softgoals,
        "obstacles": obstacles,
    }
    logger.info(
        "goals: inferred %d goals, %d links (softgoals=%d, obstacles=%d)",
        summary["goals"], summary["links"], softgoals, obstacles,
    )
    return summary


def _safe_kind(value: str) -> Any:
    for k in GoalKind:
        if k.value == value:
            return k
    return GoalKind.FUNCTIONAL_GOAL


def _safe_relation(value: str) -> Any:
    for r in LinkRelation:
        if r.value == value:
            return r
    return None


_LIVE_STATUSES = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)
