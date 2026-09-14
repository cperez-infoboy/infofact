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

Además (incidente 2026-08-29 del mismo proyecto) la fase 1 se parte en
CHUNKS de requerimientos: la llamada monolítica con el catálogo completo
(~35K tokens de entrada con ~800 reqs) no completaba en el gateway del
proveedor (500 api_error en segundos o request colgada), y el max_tokens
global (32768) hacía inasequible el fallback por créditos. Cada chunk es
una llamada con max_tokens propio y el merge entre chunks es determinista:
renumeración global de códigos, dedupe de statements casi iguales y corte
de parent_code no resolubles.

Recalibración tras el primer run con chunks (mismo día): la salida REAL
escala con los reqs del chunk (un goal cada ~3-5 reqs, ~100 tokens con
statement y rationale) y la de links con el lote (rationale incluido), así
que 270/8192 truncó 2/3 chunks y 7/7 lotes de links. Quedó en chunk 130,
lote de links 60 y max_tokens 12288, debajo del techo de asequibilidad del
fallback (~13.9K).

Incidente 2026-08-30: el gateway del proveedor corta con 408 todo request
que no completa en request_timeout=600s; en ventanas de congestión las
generaciones largas (4-12K tokens) no entran y las chicas (quality, ~1-2K)
sí. Mitigación de nuestro lado: salidas más cortas por llamada — rationale
de goals acotado a ~15 palabras (opcional) y el de links a ~12, presupuesto
de 5-8 goals por chunk y lote de links 40. El fix estructural (subir el
request_timeout del gateway) es infraestructura, no código.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal

from rapidfuzz import fuzz

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.llm import disable_thinking_body, structured_llm
from backend.agents.pipelines._resilience import (
    DEFAULT_CONCURRENCY,
    _chunk,
    invoke_structured_resilient,
)
from backend.models.requirement import ReqStatus
from backend.models.requirement import RequirementItem
from backend.models.srs import GoalKind, LinkRelation
from backend.services.requirement_store import list_requirements
from backend.services.srs_store import (
    add_goal_link,
    list_goal_links,
    list_goals,
    replace_goals,
    upsert_goals,
)

logger = logging.getLogger(__name__)

# Intentos de parse por unidad estructurada (fase goals / cada lote de links).
# En el camino sin thinking se permite un reintento ciego más: con thinking
# activo un reintento a 16K tokens cuesta minutos, así que ahí se cambia de
# estrategia en cuanto se confirma el truncado.
_INFER_ATTEMPTS = 2
# Requerimientos por lote de links: con rationale acotado (~12 palabras,
# incidente 2026-08-30) un lote genera ~1-2.5K tokens; 40 deja margen amplio
# bajo el timeout del gateway incluso con el upstream congestionado.
# Env-tunable para recalibrar sin código.
_LINK_BATCH_SIZE = int(os.environ.get("INFOFACT_LINK_BATCH", "40"))
# Requerimientos por chunk de fase 1: mantiene la entrada muy por debajo del
# umbral donde el gateway del proveedor empezó a fallar (~35K tokens) y acota
# la salida REAL: el modelo emite un goal cada ~3-5 reqs, con statement y
# rationale (~100 tokens c/u). Env-tunable para recalibrar sin código
# (mismo patrón que INFOFACT_CONCURRENCY).
GOALS_CHUNK_SIZE = int(os.environ.get("INFOFACT_GOALS_CHUNK", "130"))
# max_tokens por llamada (chunks de goals y lotes de links): el global
# (llm_max_tokens=32768) pedía reserva de 32K y volvía inasequible el
# fallback por créditos (OpenRouter 402: asequibles ~13.9K). 12288 queda
# debajo de ese techo y, con thinking activo, el truncado degrada al camino
# sin thinking. La calibración fina de la salida la hace el tamaño de
# chunk/lote, no este techo: el primer run con chunks (270 reqs, 8192)
# truncó 2/3 chunks y 7/7 lotes de links.
GOALS_MAX_TOKENS = int(os.environ.get("INFOFACT_GOALS_MAX_TOKENS", "12288"))
# token_sort_ratio mínimo (0-100) para considerar dos goals de chunks
# distintos el mismo objetivo (dedupe del merge).
_GOALS_DEDUPE_RATIO = 90
# Techo de SAFETY del catálogo (NO un tope de diseño): el catálogo se
# mantiene acotado POR INSTRUCCIÓN (consolidación LLM con rango objetivo
# adaptativo); este techo solo existe porque cada lote de vinculación
# incrusta el catálogo completo en su prompt — un catálogo desbocado agranda
# TODAS las llamadas y revive los 408/timeouts documentados. Al superarse se
# re-consolida con instrucción explícita de fusión; el corte raíces-primero
# es el último recurso (nunca el mecanismo normal).
_MAX_GOALS = int(os.environ.get("INFOFACT_MAX_GOALS", "60"))
# Rango objetivo que la consolidación pide al LLM, adaptativo al tamaño del
# proyecto (dentro de [min, max]): ~10 goals para un proyecto chico, ~30
# para uno de más de mil reqs.
_GOALS_TARGET_MIN = 10
_GOALS_TARGET_MAX = 30
# Fallback de embeddings para reqs que el LLM deja sin link tras el retry:
# apagado por defecto (carga sentence-transformers/torch bajo demanda);
# activar con INFOFACT_GOAL_LINK_EMBEDDINGS=1.
_EMBEDDING_FALLBACK = (
    os.environ.get("INFOFACT_GOAL_LINK_EMBEDDINGS", "0") == "1"
)
# Similitud coseno mínima para aceptar el link sugerido por embeddings
# (paraphrase-multilingual-MiniLM: por debajo de ~0.45 es ruido).
_EMBEDDING_LINK_THRESHOLD = 0.45


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
    "- Keep the model FOCUSED: aim for 5-8 goals for THIS subset of "
    "requirements, not one per requirement. Group related requirements "
    "under a shared goal.\n"
    "- rationale is OPTIONAL and SHORT: at most 15 words, or omit it "
    "entirely rather than padding.\n"
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
    "- Assign EVERY requirement in the batch to its single BEST-matching "
    "goal: realizes if it fully satisfies it, contributes if partially. "
    "Traceability must be COMPLETE — do not skip requirements to save "
    "effort. Leave a requirement unlinked ONLY when no goal is even "
    "remotely related, which must be rare.\n"
    "- rationale: at most 12 words, to the point.\n"
    "- LANGUAGE: every statement and rationale MUST stay in the SAME LANGUAGE "
    "as the requirements. Never translate.\n"
    "- Return ONLY the structured object."
)


_CONSOLIDATE_SYSTEM = (
    "You are a GOAL MODELING editor. You receive a DRAFT goal catalog "
    "inferred from different requirement subsets; some goals overlap or "
    "fragment the same objective. Consolidate it into a FOCUSED catalog of "
    "genuinely distinct goals.\n\n"
    "Rules:\n"
    "- Merge goals that express the SAME objective (choose or blend the "
    "clearest statement); keep genuinely distinct objectives apart.\n"
    "- Preserve each goal's kind (functional_goal / softgoal / obstacle).\n"
    "- Recompute parent_code only against codes you emit (renumber G1..Gn).\n"
    "- Do not invent goals beyond the draft's semantics; only fuse and "
    "clarify.\n"
    "- LANGUAGE: statements stay in the draft's language. Never translate.\n"
    "- Return ONLY the structured object."
)


def _target_goal_count(live_count: int) -> int:
    """Rango objetivo del catálogo, adaptativo al tamaño del proyecto."""
    scaled = max(1, (live_count + 39) // 40) + 5
    return max(_GOALS_TARGET_MIN, min(_GOALS_TARGET_MAX, scaled))


def _norm_stmt(statement: str) -> str:
    """Normaliza un statement para matching difuso (espejo de srs_store)."""
    return " ".join((statement or "").lower().split())


def _goals_fp(item) -> str:
    from backend.services.srs_quality import quality_fingerprint

    return quality_fingerprint(
        item.statement, item.type.value if item.type else None
    )


async def _consolidate_goals(
    candidates: list[InferredGoal],
    target: int,
    *,
    hard_limit: int | None = None,
) -> list[InferredGoal]:
    """Una pasada LLM que fusiona el catálogo candidato hacia ~target goals.

    El dedupe fuzzy del merge (ratio 90) no fusiona reformulaciones libres
    del mismo objetivo; esta pasada SEMÁNTICA es la que mantiene el catálogo
    acotado por instrucción, reemplazando el corte duro en 15 que descartaba
    goals legítimos en silencio. ``hard_limit`` cambia el tono a «no
    excedas N» (re-consolidación por overflow del techo de safety). Devuelve
    [] si la llamada falla o el resultado no reduce: el caller conserva los
    candidatos y decide el fallback.
    """
    catalog = "\n".join(
        f"- [{g.code}] ({g.kind}) {g.statement}" for g in candidates
    )
    size = hard_limit if hard_limit is not None else target
    tone = (
        f"TARGET SIZE: about {size} goals — this is a HARD ceiling, fuse "
        "aggressively."
        if hard_limit is not None
        else f"TARGET SIZE: about {size} goals (a few more is fine; do NOT "
        "inflate)."
    )
    msgs = [
        ("system", _CONSOLIDATE_SYSTEM),
        ("human", f"{tone}\n\nDRAFT GOAL CATALOG:\n{catalog}"),
    ]
    try:
        out = await _invoke_unit(
            GoalGoals,
            msgs,
            label="goals consolidation",
            extra={"max_tokens": GOALS_MAX_TOKENS},
        )
    except Exception:  # noqa: BLE001 — conservar candidatos ante fallo
        logger.warning("goals: consolidación falló; se conserva el catálogo candidato")
        return []
    consolidated = _valid_goals(out.goals)
    if not consolidated or len(consolidated) >= len(candidates):
        return []
    return consolidated


def _embedding_fallback_links(
    goals: list[InferredGoal],
    scope: list,
    links_payload: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Links de respaldo por similitud semántica para los reqs sin vincular.

    Sincrónico y best-effort: carga el embedder bajo demanda (misma carga
    lazy del pipeline de consolidación) y marca las aristas con
    ``detected_by="embedding"`` para que agente y UI las traten como
    sugerencia, no como curación.
    """
    from backend.agents.pipelines.consolidation import embed_texts

    linked = {l["req_code"] for l in links_payload}
    unlinked = [it for it in scope if it.code not in linked]
    if not unlinked:
        return []
    goal_vecs = embed_texts([g.statement for g in goals])
    req_vecs = embed_texts([it.statement or "" for it in unlinked])
    out: list[dict[str, Any]] = []
    for it, rv in zip(unlinked, req_vecs):
        sims = goal_vecs @ rv  # vectores normalizados: producto = coseno
        best = int(sims.argmax())
        if float(sims[best]) < _EMBEDDING_LINK_THRESHOLD:
            continue
        out.append(
            {
                "goal_code": goals[best].code,
                "req_code": it.code,
                "relation": "contributes",
                "rationale": "Sugerencia por similitud semántica (embedding)",
                "detected_by": "embedding",
            }
        )
    return out


def _safety_cut(goals: list[InferredGoal]) -> list[InferredGoal]:
    """Último recurso ante overflow del techo de safety: raíces primero."""
    roots = [g for g in goals if not g.parent_code]
    children = [g for g in goals if g.parent_code]
    merged = (roots + children)[:_MAX_GOALS]
    kept = {g.code for g in merged}
    merged = [
        g if (not g.parent_code or g.parent_code in kept)
        else g.model_copy(update={"parent_code": None})
        for g in merged
    ]
    logger.warning(
        "goals: corte de SAFETY a %d goals (la consolidación no alcanzó) — "
        "revisar INFOFACT_MAX_GOALS",
        _MAX_GOALS,
    )
    return merged


async def _invoke_unit(schema, msgs: list, *, label: str, extra: dict | None = None):
    """Una unidad estructurada (chunk de goals / un lote de links).

    Degradación por truncado delegada al helper compartido
    ``invoke_structured_resilient`` (mismo tratamiento que ``srs_quality``):
    un finish_reason=length con JSON roto levanta
    ``StructuredOutputTruncatedError`` y el reintento pasa a thinking
    desactivado (el pensamiento interno comparte el presupuesto de salida).
    El resto de los fallos (transient 429/5xx, parse sin truncar) queda
    acotado por ``_invoke_with_retry``, que propaga la excepción al agotarse.
    ``extra`` (p. ej. ``max_tokens``) viaja a cada ``ainvoke`` en ambas
    pasadas.
    """
    return await invoke_structured_resilient(
        lambda **kw: structured_llm(schema, **kw),
        msgs,
        context_label=label,
        max_parse=_INFER_ATTEMPTS,
        thinking_off_body=disable_thinking_body(),
        extra=extra,
    )


async def infer_goals(
    session: AsyncSession,
    project_id: int,
    on_progress: Callable[[str, int, int], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Infiere el modelo de goals en dos fases y lo persiste (upsert_goals).

    Devuelve un resumen {goals, links, softgoals, obstacles,
    link_batches_failed, links_partial}. Levanta ``GoalsInferenceError`` si
    la fase de goals falla o no infiere nada: ya no se degrada a un modelo
    vacío silencioso. Si no hay reqs vivos, limpia goals previos y devuelve
    ceros.

    ``on_progress(fase, hecho, total)`` (opcional, awaited) reporta el avance
    por unidad de trabajo: cada chunk de goals (fase 1) y cada lote de links
    (fase 2), cuenten o fallen. ``fase`` es la etiqueta legible de la unidad
    («chunks de goals» / «lotes de links»); el caller decide el canal (SSE
    ``srs.progress`` desde el tool del agente, mismo patrón que
    ``srs_quality``).
    """
    items = await list_requirements(session, project_id, include_deleted=True)
    live = [it for it in items if it.status in _LIVE_STATUSES]
    req_by_code = {it.code: it.id for it in live}

    if not live:
        await replace_goals(session, project_id, [], [], req_by_code=req_by_code)
        return {"goals": 0, "links": 0, "softgoals": 0, "obstacles": 0}

    existing_goals = await list_goals(session, project_id)

    # Delta de inferencia (análogo al de calidad): solo los reqs nuevos o
    # editados desde la última inferencia alimentan los chunks de fase 1.
    # Un re-run sin ediciones sobre un catálogo existente es NO-OP: cero
    # llamadas LLM, el catálogo y sus links se conservan tal cual (antes se
    # re-corrian ~42 llamadas que además pisaban la curación).
    fps = {it.id: _goals_fp(it) for it in live}
    to_chunk = [
        it
        for it in live
        if it.goals_fingerprint is None
        or it.goals_fingerprint != fps[it.id]
    ]

    if not to_chunk and existing_goals:
        links_rows = await list_goal_links(session, project_id)
        return {
            "goals": len(existing_goals),
            "links": len(links_rows),
            "softgoals": sum(
                1 for g in existing_goals if g.kind.value == "softgoal"
            ),
            "obstacles": sum(
                1 for g in existing_goals if g.kind.value == "obstacle"
            ),
            "goals_judged": 0,
            "goals_reused": len(live),
            "link_scope": "none",
            "goals_chunks_failed": 0,
            "goals_partial": False,
            "link_batches_failed": 0,
            "links_partial": False,
            "message": (
                "Sin cambios de requerimientos desde la última inferencia: "
                "catálogo de goals y links conservados (0 llamadas LLM)."
            ),
        }

    # Progreso por unidad: el fallo del canal nunca rompe la etapa (mismo
    # contrato que srs_quality).
    async def _report(phase: str, done: int, total: int) -> None:
        if on_progress is None:
            return
        try:
            await on_progress(phase, done, total)
        except Exception:  # noqa: BLE001 — el progreso nunca rompe la etapa
            pass

    # ------------------------------------------------------------------
    # Fase 1: goals por chunks SOLO del delta (salida acotada por chunk,
    # sin links). Primera corrida: to_chunk == live.
    # ------------------------------------------------------------------
    chunks = list(_chunk(to_chunk, GOALS_CHUNK_SIZE))
    sem = asyncio.Semaphore(max(1, DEFAULT_CONCURRENCY))
    chunk_done = 0

    async def _goals_chunk(batch: list) -> GoalGoals:
        nonlocal chunk_done
        catalog = "\n".join(
            f"- {it.code} [{it.type.value}]: {it.statement}" for it in batch
        )
        msgs = [
            ("system", _GOALS_SYSTEM),
            ("human", f"PROJECT REQUIREMENTS:\n{catalog}"),
        ]
        async with sem:
            try:
                return await _invoke_unit(
                    GoalGoals,
                    msgs,
                    label=f"goals {batch[0].code}..{batch[-1].code}",
                    extra={"max_tokens": GOALS_MAX_TOKENS},
                )
            finally:
                # Cuenta también el chunk caído: el avance debe llegar a
                # total aunque la unidad falle (quedaría congelado si no).
                chunk_done += 1
                await _report("chunks de goals", chunk_done, len(chunks))

    outcomes = await asyncio.gather(
        *(_goals_chunk(b) for b in chunks), return_exceptions=True
    )

    chunk_goal_lists: list[list[InferredGoal]] = []
    failed_chunks = 0
    first_error: Exception | None = None
    for chunk, out in zip(chunks, outcomes):
        if isinstance(out, BaseException):
            failed_chunks += 1
            if first_error is None:
                first_error = out
            logger.warning(
                "goals chunk %s..%s falló (%s); el resto del proyecto sigue",
                chunk[0].code,
                chunk[-1].code,
                type(out).__name__,
            )
            continue
        chunk_goal_lists.append(_valid_goals(out.goals))

    if not chunk_goal_lists:
        # Ningún chunk sobrevivió: mismo contrato explícito de la versión
        # monolítica (nunca un modelo vacío silencioso).
        raise GoalsInferenceError(f"fase goals falló: {first_error}") from first_error

    goals = _merge_chunked_goals(chunk_goal_lists)

    if not goals:
        # Un catálogo vivo no puede inferir 0 goals (v6: «989 reqs, 0 goals»):
        # mejor un error explícito que un modelo vacío silencioso.
        raise GoalsInferenceError(
            "el modelo no infirió ningún goal del catálogo de "
            f"{len(live)} requerimientos vivos"
        )

    # Consolidación (acotado por INSTRUCCIÓN, no por corte): si el merge
    # fuzzy deja un catálogo por encima del rango objetivo, una pasada LLM
    # fusiona los solapados. Antes un corte duro en 15 descartaba goals
    # legítimos en silencio (15 goals para ~1.263 reqs).
    target = _target_goal_count(len(live))
    if len(goals) > target:
        consolidated = await _consolidate_goals(goals, target)
        if consolidated:
            goals = consolidated
    if len(goals) > _MAX_GOALS:
        # Overflow del techo de safety: re-consolidación explícita y, como
        # último recurso, corte (siempre logueado, nunca silencioso).
        harder = await _consolidate_goals(goals, target, hard_limit=_MAX_GOALS)
        if harder:
            goals = harder
    if len(goals) > _MAX_GOALS:
        goals = _safety_cut(goals)

    # ¿Cambió el catálogo respecto al persistido? Pre-match con el mismo
    # criterio fuzzy del upsert: decide el ALCANCE de la vinculación. Un
    # catálogo estable solo re-vincula el delta; uno nuevo re-vincula todo.
    def _matches_existing(g: InferredGoal) -> bool:
        stmt = _norm_stmt(g.statement)
        kind_val = _safe_kind(g.kind).value
        for eg in existing_goals:
            if eg.kind.value != kind_val:
                continue
            if (
                fuzz.token_sort_ratio(_norm_stmt(eg.statement), stmt)
                >= _GOALS_DEDUPE_RATIO
            ):
                return True
        return False

    catalog_changed = not existing_goals or any(
        not _matches_existing(g) for g in goals
    )
    link_scope = live if catalog_changed else to_chunk

    seen_codes = {g.code for g in goals}

    goals_brief = "\n".join(
        f"- [{g.code}] ({g.kind}) {g.statement}" for g in goals
    )

    # ------------------------------------------------------------------
    # Fase 2: links por lotes sobre el alcance decidido (la salida escala
    # con el lote, no con el proyecto). Un lote que falla no arrastra al
    # resto: persiste parcial (con un retry antes de rendirse).
    # ------------------------------------------------------------------
    batches = list(_chunk(link_scope, _LINK_BATCH_SIZE))
    sem = asyncio.Semaphore(max(1, DEFAULT_CONCURRENCY))
    batch_done = 0

    async def _link_batch(batch: list) -> GoalLinks:
        nonlocal batch_done
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
            try:
                return await _invoke_unit(
                    GoalLinks,
                    msgs,
                    label=f"links {batch[0].code}..{batch[-1].code}",
                    extra={"max_tokens": GOALS_MAX_TOKENS},
                )
            finally:
                batch_done += 1
                await _report("lotes de links", batch_done, len(batches))

    outcomes = list(
        await asyncio.gather(
            *(_link_batch(b) for b in batches), return_exceptions=True
        )
    )

    # Retry secuencial único de lotes caídos (congestión del gateway): un
    # lote que no completa deja sus ~40 reqs sin link y ANTES nadie estaba
    # obligado a reintentarlo (links_partial era solo un flag).
    batches_retried = 0
    for i, out in enumerate(outcomes):
        if not isinstance(out, BaseException):
            continue
        try:
            outcomes[i] = await _link_batch(batches[i])
            batches_retried += 1
        except Exception:  # noqa: BLE001 — persiste parcial, como antes
            pass

    links_payload: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    failed_batches = 0
    stamp_ids: set[int] = set()
    for i, (batch, out) in enumerate(zip(batches, outcomes)):
        if isinstance(out, BaseException):
            failed_batches += 1
            logger.warning(
                "links lote %s..%s falló (%s); el resto del proyecto persiste",
                batch[0].code,
                batch[-1].code,
                type(out).__name__,
            )
            continue
        stamp_ids.update(it.id for it in batches[i])
        for l in out.links:
            if l.goal_code not in seen_codes:
                continue  # goal inexistente: el upsert lo descartaría
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

    # Fallback de embeddings (opt-in): los reqs del alcance que ni el LLM
    # ni el retry vincularon reciben el goal más similar semánticamente,
    # como sugerencia marcada (detected_by="embedding"). Reutiliza el
    # embedder lazy del pipeline de consolidación.
    if _EMBEDDING_FALLBACK and link_scope:
        try:
            unlinked = _embedding_fallback_links(goals, link_scope, links_payload)
            links_payload.extend(unlinked)
        except Exception:  # noqa: BLE001 — el fallback nunca rompe la etapa
            logger.warning("goals: fallback de embeddings falló (no crítico)")

    # Sello: los reqs de lotes exitosos quedan cubiertos por el catálogo
    # actual; los de lotes fallidos NO se sellan (reintento la próxima).
    # Viaja en la sesión del upsert: un solo commit atómico.
    now = datetime.utcnow()
    scope_by_id = {it.id: it for it in link_scope}
    for rid in stamp_ids:
        obj = scope_by_id.get(rid)
        if obj is not None:
            obj.goals_fingerprint = fps[rid]
            obj.goals_judged_at = now

    # Upsert (no replace): los goals que ya existían conservan id/código y el
    # status de curación humana; los links a mano sobreviven; el PROPOSED no
    # mencionado se marca stale (fila y links quedan). El churn GOAL-XXXX
    # entre corridas era el bug.
    result = await upsert_goals(
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
        "goals_stale": result.get("goals_stale", 0),
        "goals_judged": len(to_chunk),
        "goals_reused": len(live) - len(to_chunk),
        "link_scope": "all" if catalog_changed else "delta",
        "goals_chunks_failed": failed_chunks,
        "goals_partial": failed_chunks > 0,
        "link_batches_failed": failed_batches,
        "link_batches_retried": batches_retried,
        "links_partial": failed_batches > 0,
    }
    logger.info(
        "goals: %d goals, %d links (softgoals=%d, obstacles=%d, delta=%d/%d, "
        "scope=%s, chunks fallidos=%d/%d, lotes fallidos=%d/%d, retried=%d)",
        summary["goals"],
        summary["links"],
        softgoals,
        obstacles,
        len(to_chunk),
        len(live),
        summary["link_scope"],
        failed_chunks,
        len(chunks),
        failed_batches,
        len(batches),
        batches_retried,
    )
    return summary


async def infer_goal_links_incremental(
    session: AsyncSession,
    project_id: int,
    req_codes: list[str],
    *,
    on_progress: Callable[[str, int, int], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Re-infiere links SOLO para los reqs pedidos, contra los goals existentes.

    Camino quirúrgico (sesión 9 de Planitrack): completar la trazabilidad de
    unos pocos requerimientos no justifica re-correr ``infer_goals`` entera
    (reemplazo de cientos de links sanos). El catálogo de goals es el YA
    PERSISTIDO (códigos GOAL-XXXX reales, curación humana incluida) y solo se
    consultan los reqs pedidos. Idempotente: la arista que ya existe no se
    duplica (``add_goal_link``), y un lote caído no arrastra al resto.

    Levanta ``GoalsInferenceError`` si el proyecto aún no tiene goals: correr
    ``infer_goals`` primero. Devuelve {reqs, reqs_missing, links_added,
    links_existing, batches_failed, links_partial}.
    """
    goals = await list_goals(session, project_id)
    if not goals:
        raise GoalsInferenceError(
            "no hay goals en el proyecto: correr infer_goals antes de inferir "
            "links incrementales"
        )
    wanted = [c for c in dict.fromkeys(req_codes or []) if c]
    empty: dict[str, Any] = {
        "reqs": 0,
        "reqs_missing": [],
        "links_added": 0,
        "links_existing": 0,
        "batches_failed": 0,
        "links_partial": False,
    }
    if not wanted:
        return empty

    rows = (
        await session.scalars(
            select(RequirementItem).where(
                RequirementItem.project_id == project_id,
                RequirementItem.code.in_(wanted),
            )
        )
    ).all()
    missing = [c for c in wanted if c not in {it.code for it in rows}]
    # Solo reqs vivos: un rechazado no alimenta trazabilidad nueva (mismo
    # criterio que infer_goals).
    linkable = {
        it.code: it for it in rows if it.status in _LIVE_STATUSES
    }
    items = [linkable[c] for c in wanted if c in linkable]
    if not items:
        empty["reqs_missing"] = missing
        return empty

    async def _report(phase: str, done: int, total: int) -> None:
        if on_progress is None:
            return
        try:
            await on_progress(phase, done, total)
        except Exception:  # noqa: BLE001 — el progreso nunca rompe la etapa
            pass

    goals_brief = "\n".join(
        f"- {g.code} ({g.kind.value}): {g.statement}" for g in goals
    )
    batches = list(_chunk(items, _LINK_BATCH_SIZE))
    sem = asyncio.Semaphore(max(1, DEFAULT_CONCURRENCY))
    batch_done = 0

    async def _link_batch(batch: list) -> GoalLinks:
        nonlocal batch_done
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
            try:
                return await _invoke_unit(
                    GoalLinks,
                    msgs,
                    label=(
                        f"links incrementales {batch[0].code}..{batch[-1].code}"
                    ),
                    extra={"max_tokens": GOALS_MAX_TOKENS},
                )
            finally:
                batch_done += 1
                await _report("lotes de links", batch_done, len(batches))

    outcomes = list(
        await asyncio.gather(
            *(_link_batch(b) for b in batches), return_exceptions=True
        )
    )

    # Retry secuencial único de lotes caídos: la cola de unlinked se cierra
    # en la misma pasada cuando el gateway congestiona (antes quedaba
    # flaggeada y nadie la reintentaba).
    for i, out in enumerate(outcomes):
        if not isinstance(out, BaseException):
            continue
        try:
            outcomes[i] = await _link_batch(batches[i])
        except Exception:  # noqa: BLE001 — persiste parcial
            pass

    goal_id_by_code = {g.code: g.id for g in goals}
    added = 0
    already = 0
    failed_batches = 0
    seen_edges: set[tuple[str, str, str]] = set()
    for batch, out in zip(batches, outcomes):
        if isinstance(out, BaseException):
            failed_batches += 1
            logger.warning(
                "links incrementales lote %s..%s falló (%s); el resto persiste",
                batch[0].code,
                batch[-1].code,
                type(out).__name__,
            )
            continue
        for l in out.links:
            goal_id = goal_id_by_code.get(l.goal_code)
            item = linkable.get(l.req_code)
            if goal_id is None or item is None:
                continue  # código inexistente: anti-alucinación
            edge = (l.goal_code, l.req_code, l.relation)
            if edge in seen_edges:
                continue  # arista duplicada del LLM
            seen_edges.add(edge)
            res = await add_goal_link(
                session,
                project_id,
                goal_id=goal_id,
                req_id=item.id,
                relation=_safe_relation(l.relation),
                rationale=l.rationale,
            )
            if res["created"]:
                added += 1
            else:
                already += 1

    return {
        "reqs": len(items),
        "reqs_missing": missing,
        "links_added": added,
        "links_existing": already,
        "batches_failed": failed_batches,
        "links_partial": failed_batches > 0,
    }


def _valid_goals(raw: list[InferredGoal]) -> list[InferredGoal]:
    """Valida los goals de un chunk: alias repetidos o items sin
    código/statement se descartan (contrato de la versión monolítica)."""
    goals: list[InferredGoal] = []
    seen: set[str] = set()
    for g in raw:
        if not g.code or not g.statement or g.code in seen:
            continue
        seen.add(g.code)
        goals.append(g)
    return goals


def _find_duplicate(statement: str, seen: list[tuple[str, str]]) -> str | None:
    """Código global del goal cuyo statement es casi igual al dado.

    ``seen`` lleva pares (statement, código global). El umbral es alto
    (_GOALS_DEDUPE_RATIO) para fusionar solo reformulaciones evidentes del
    mismo objetivo, no objetivos vecinos legítimos.
    """
    for other, code in seen:
        if fuzz.token_sort_ratio(statement, other) >= _GOALS_DEDUPE_RATIO:
            return code
    return None


def _merge_chunked_goals(chunks: list[list[InferredGoal]]) -> list[InferredGoal]:
    """Merge determinista de los goals de cada chunk.

    Los chunks infieren por separado: los códigos locales colisionan (cada
    uno arranca en ``G1``) y dos chunks pueden emitir el mismo objetivo con
    distinto código. Reglas:

    - Renumeración global secuencial (G1..Gn) en orden de emisión.
    - Dedupe por statement casi igual entre chunks: el duplicado no se emite
      y su código local queda mapeado al canónico (los hijos que lo
      referencian cuelgan del goal que ya existe).
    - parent_code solo se resuelve DENTRO del chunk (un goal de otro chunk
      no es referenciable con códigos locales); el resto se corta, igual que
      el parent fantasma de la versión monolítica.

    El acotado del catálogo NO vive acá: lo hace la consolidación LLM
    (``_consolidate_goals``) con rango objetivo por instrucción; el corte
    duro de safety (``_safety_cut``) es solo el último recurso del caller.
    """
    merged: list[InferredGoal] = []
    seen_statements: list[tuple[str, str]] = []
    emitted: set[str] = set()
    counter = 0
    for chunk in chunks:
        # Paso 1: destino global de cada código local del chunk. Los hijos
        # pueden declararse antes que el padre, así que el remap se cierra
        # antes de emitir.
        remap: dict[str, str] = {}
        for g in chunk:
            canon = _find_duplicate(g.statement, seen_statements)
            if canon is None:
                counter += 1
                canon = f"G{counter}"
                seen_statements.append((g.statement, canon))
            remap[g.code] = canon
        # Paso 2: emitir con parent resuelto (duplicados omitidos).
        for g in chunk:
            code = remap[g.code]
            if code in emitted:
                continue  # duplicado de este u otro chunk
            emitted.add(code)
            parent = remap.get(g.parent_code) if g.parent_code else None
            merged.append(
                g.model_copy(update={"code": code, "parent_code": parent})
            )

    return merged


def _safe_kind(value: str) -> Any:
    for k in GoalKind:
        if k.value == value:
            return k
    return GoalKind.FUNCTIONAL_GOAL


def _safe_relation(value: str) -> Any:
    for r in LinkRelation:
        if r.value == value:
            return r
    return LinkRelation.REALIZES


_LIVE_STATUSES = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)
