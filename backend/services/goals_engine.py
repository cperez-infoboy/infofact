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
sugerencias/statements conservan el idioma del enunciado fuente; los mensajes
van en español neutro.

Dos fases (incidente v6 de Planitrack2.0): la inferencia monolítica
(catálogo completo + GoalModel en un único JSON) no entra en el presupuesto
de salida con stores grandes — el array de links escala linealmente con los
requerimientos, el JSON llega truncado (finish_reason=length), no parsea y
la etapa terminaba devolviendo 0 goals tras quemar más de una hora por
intento. Ahora la salida se acota por diseño: fase 1 infiere SOLO goals
(5-15, sin links) y fase 2 infiere links por LOTES de requerimientos (la
salida de cada lote escala con el lote, no con el proyecto), en paralelo y
con degradación a thinking desactivado cuando un JSON llega truncado.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.llm import disable_thinking_body, structured_llm
from backend.agents.pipelines._resilience import (
    DEFAULT_CONCURRENCY,
    _chunk,
    invoke_structured_resilient,
)
from backend.models.requirement import ReqStatus
from backend.models.srs import GoalKind
from backend.services.requirement_store import list_requirements
from backend.services.srs_store import replace_goals

logger = logging.getLogger(__name__)

# Intentos de parse por unidad estructurada (fase goals / cada lote de links).
# En el camino sin thinking se permite un reintento ciego más: con thinking
# activo un reintento a 16K tokens cuesta minutos, así que ahí se cambia de
# estrategia en cuanto se confirma el truncado.
_INFER_ATTEMPTS = 2
# Requerimientos por lote de links: la salida de un lote es a lo sumo un link
# por requerimiento (~50 tokens c/u), así que 120 deja el JSON con margen de
# ~10x respecto del presupuesto de salida, thinking incluido.
_LINK_BATCH_SIZE = 120


class GoalsInferenceError(RuntimeError):
    """La inferencia del modelo de goals falló (truncado persistente, parse
    o transient agotado, o cero goals inferidos).

    Antes la etapa se degradaba a un ``GoalModel()`` vacío y devolvía
    ``goals=0`` en silencio (el orquestador quemaba su ciclo reintentando lo
    imposible); ahora el caller (tool infer_goals) recibe el error explícito.
    """


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


class GoalGoals(BaseModel):
    """Fase 1: solo goals. La salida no escala con el tamaño del proyecto."""

    goals: list[InferredGoal] = Field(default_factory=list)


class GoalLinks(BaseModel):
    """Fase 2 (por lote): solo links entre los goals y los reqs DEL lote."""

    links: list[InferredLink] = Field(default_factory=list)


_GOALS_SYSTEM = (
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
    "- OBSTACLE: a risk / anti-goal that some requirement must mitigate.\n\n"
    "Rules:\n"
    "- This phase infers ONLY goals: do NOT emit links (a separate step does "
    "the linking).\n"
    "- LANGUAGE: every statement and rationale MUST stay in the SAME LANGUAGE "
    "as the requirements. Never translate.\n"
    "- Keep the model FOCUSED: aim for 5-15 goals, not one per requirement. "
    "Group related requirements under a shared goal.\n"
    "- parent_code must reference a goal's `code` you emit.\n"
    "- Return ONLY the structured object."
)

_LINKS_SYSTEM = (
    "You are a GOAL MODELING analyst linking a project's goals to its "
    "requirements (GORE traceability).\n\n"
    "You receive the project's GOALS and ONE BATCH of requirements. Emit the "
    "LINKS between them:\n"
    "- relation=realizes when the requirement fully satisfies the goal; "
    "contributes when it satisfies it partially; conflicts when the "
    "requirement opposes the goal.\n\n"
    "Rules:\n"
    "- Only reference goals and requirements EXACTLY as listed, using their "
    "codes verbatim. Never invent codes.\n"
    "- Link a requirement only when the relation is meaningful; skip the "
    "rest. A typical batch yields links for a fraction of its requirements.\n"
    "- LANGUAGE: every rationale MUST stay in the SAME LANGUAGE as the "
    "requirements. Never translate.\n"
    "- Return ONLY the structured object."
)


async def _invoke_unit(schema, msgs: list, *, label: str):
    """Una unidad estructurada (fase goals / un lote de links).

    Degradación por truncado delegada al helper compartido
    ``invoke_structured_resilient`` (mismo tratamiento que ``srs_quality``):
    un finish_reason=length con JSON roto levanta
    ``StructuredOutputTruncatedError`` y el reintento pasa a thinking
    desactivado (el pensamiento interno comparte el presupuesto de salida).
    El resto de los fallos (transient 429/5xx, parse sin truncar) queda
    acotado por ``_invoke_with_retry``, que propaga la excepción al agotarse.
    """
    return await invoke_structured_resilient(
        lambda **kw: structured_llm(schema, **kw),
        msgs,
        context_label=label,
        max_parse=_INFER_ATTEMPTS,
        thinking_off_body=disable_thinking_body(),
    )


async def infer_goals(
    session: AsyncSession, project_id: int
) -> dict[str, Any]:
    """Infiere el modelo de goals en dos fases y lo persiste (replace_goals).

    Devuelve un resumen {goals, links, softgoals, obstacles,
    link_batches_failed, links_partial}. Levanta ``GoalsInferenceError`` si
    la fase de goals falla o no infiere nada: ya no se degrada a un modelo
    vacío silencioso. Si no hay reqs vivos, limpia goals previos y devuelve
    ceros.
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

    # ------------------------------------------------------------------
    # Fase 1: goals (salida acotada: 5-15, sin links).
    # ------------------------------------------------------------------
    try:
        goals_out: GoalGoals = await _invoke_unit(
            GoalGoals,
            [
                ("system", _GOALS_SYSTEM),
                ("human", f"PROJECT REQUIREMENTS:\n{catalog}"),
            ],
            label="goals",
        )
    except Exception as exc:  # noqa: BLE001 — se reporta como error de etapa
        raise GoalsInferenceError(f"fase goals falló: {exc}") from exc

    goals: list[InferredGoal] = []
    seen_codes: set[str] = set()
    for g in goals_out.goals:
        if not g.code or not g.statement or g.code in seen_codes:
            continue  # sin código/statement, o alias repetido
        seen_codes.add(g.code)
        goals.append(g)
    # Un parent_code hacia un goal no emitido rompería la jerarquía en
    # silencio (replace_goals lo dejaría como raíz): se corta explícitamente.
    goals = [
        g if (not g.parent_code or g.parent_code in seen_codes)
        else g.model_copy(update={"parent_code": None})
        for g in goals
    ]

    if not goals:
        # Un catálogo vivo no puede inferir 0 goals (v6: «989 reqs, 0 goals»):
        # mejor un error explícito que un modelo vacío silencioso.
        raise GoalsInferenceError(
            "el modelo no infirió ningún goal del catálogo de "
            f"{len(live)} requerimientos vivos"
        )

    goals_brief = "\n".join(
        f"- [{g.code}] ({g.kind}) {g.statement}" for g in goals
    )

    # ------------------------------------------------------------------
    # Fase 2: links por lotes (la salida escala con el lote, no con el
    # proyecto). Un lote que falla no arrastra al resto: persiste parcial.
    # ------------------------------------------------------------------
    batches = list(_chunk(live, _LINK_BATCH_SIZE))
    sem = asyncio.Semaphore(max(1, DEFAULT_CONCURRENCY))

    async def _link_batch(batch: list) -> GoalLinks:
        reqs_text = "\n".join(
            f"- {it.code} [{it.type.value}]: {it.statement}" for it in batch
        )
        msgs = [
            ("system", _LINKS_SYSTEM),
            (
                "human",
                f"GOALS:\n{goals_brief}\n\n"
                f"PROJECT REQUIREMENTS (LINK ONLY THESE):\n{reqs_text}",
            ),
        ]
        async with sem:
            return await _invoke_unit(
                GoalLinks,
                msgs,
                label=f"links {batch[0].code}..{batch[-1].code}",
            )

    outcomes = await asyncio.gather(
        *(_link_batch(b) for b in batches), return_exceptions=True
    )

    links_payload: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    failed_batches = 0
    for batch, out in zip(batches, outcomes):
        if isinstance(out, BaseException):
            failed_batches += 1
            logger.warning(
                "links lote %s..%s falló (%s); el resto del proyecto persiste",
                batch[0].code,
                batch[-1].code,
                type(out).__name__,
            )
            continue
        for l in out.links:
            if l.goal_code not in seen_codes:
                continue  # goal inexistente: replace_goals lo descartaría
            edge = (l.goal_code, l.req_code, l.relation)
            if edge in seen_edges:
                continue  # arista duplicada del LLM
            seen_edges.add(edge)
            links_payload.append(
                {
                    "goal_code": l.goal_code,
                    "req_code": l.req_code,
                    "relation": l.relation,
                    "rationale": l.rationale,
                }
            )

    result = await replace_goals(
        session,
        project_id,
        [
            {
                "code": g.code,
                "kind": _safe_kind(g.kind),
                "statement": g.statement,
                "rationale": g.rationale,
                "parent_code": g.parent_code,
                "confidence": 0.7,
            }
            for g in goals
        ],
        links_payload,
        req_by_code=req_by_code,
    )
    softgoals = sum(1 for g in goals if g.kind == "softgoal")
    obstacles = sum(1 for g in goals if g.kind == "obstacle")
    summary = {
        "goals": result["goals"],
        "links": result["links"],
        "softgoals": softgoals,
        "obstacles": obstacles,
        "link_batches_failed": failed_batches,
        "links_partial": failed_batches > 0,
    }
    logger.info(
        "goals: inferred %d goals, %d links (softgoals=%d, obstacles=%d, "
        "lotes de links fallidos=%d/%d)",
        summary["goals"],
        summary["links"],
        softgoals,
        obstacles,
        failed_batches,
        len(batches),
    )
    return summary


def _safe_kind(value: str) -> Any:
    for k in GoalKind:
        if k.value == value:
            return k
    return GoalKind.FUNCTIONAL_GOAL


_LIVE_STATUSES = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)
