"""Motor de calidad del SRS: análisis de requerimientos (INCOSE + smells + EARS).

Doble motor (patrón del ``critique`` del pipeline de captura):

FASE A — pre-checks PROGRAMÁTICOS deterministas (``detected_by="programmatic"``,
costo cero, cero alucinación): modal faltante, negación, combinadores (y/o, /),
términos vagos, pronombres, absolutos, exceso de longitud, target medible
faltante en NFR, y detección de plantilla EARS.

FASE B — evaluación LLM (``detected_by="agent"``, vía ``structured_llm``):
ambigüedad semántica, gaps de cuantificación, reescrituras EARS,
inconsistencias de conjunto y requerimientos faltantes. Mismo esquema de
resiliencia que ``critique``: transient backoff + sentinel, y preservación de
idioma (las sugerencias quedan en el idioma del enunciado; los mensajes en
español neutro).

La salida son hallazgos listos para ``srs_store.replace_findings``.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.llm import disable_thinking_body, structured_llm
from backend.agents.pipelines._resilience import (
    DEFAULT_BATCH_SIZE,
    _BATCH_PARSE_RETRIES,
    invoke_structured_resilient,
    is_transient,
)
from backend.agents.pipelines._quality_rules import programmatic_findings_for_text
from backend.models.requirement import ReqStatus
from backend.models.srs import (
    FindingDimension,
    FindingScope,
    FindingSeverity,
    RequirementFinding,
)
from backend.services.requirement_store import list_requirements

logger = logging.getLogger(__name__)

# Concurrencia de batches LLM: 8 (precedente de extraction; el backoff
# jitterado absorbe los 429 residuales del proxy). Env-tunable.
DEFAULT_CONCURRENCY = int(os.environ.get("INFOFACT_CONCURRENCY", "8"))
_JUDGE_ATTEMPTS = 3
# Emisión de progreso: cada N batches completados (y en el último).
_PROGRESS_EVERY = 10
# Las reglas deterministas ITEM-level viven en ``_quality_rules`` (módulo
# compartido con el pipeline de captura, shift-left de calidad). Ver ahí:
# ``programmatic_findings_for_text``, ``detect_ears_pattern``, ``VAGUE_TERMS``.


# Reglas que llevan el campo ``ears_pattern`` en el dict de hallazgo (paridad
# con la forma histórica heterogénea de programmatic_findings).
_RULES_WITH_EARS_PATTERN = frozenset({"incose.modal_missing", "ears.missing_condition"})


def programmatic_findings(item) -> list[dict[str, Any]]:
    """Pre-checks deterministas sobre un RequirementItem. Cero LLM.

    Wrapper delgado sobre ``_quality_rules.programmatic_findings_for_text``:
    conserva el contrato histórico (dicts listos para
    ``srs_store.replace_findings``) de modo que el comportamiento de ``/srs``
    no cambie. La fuente única de las reglas ITEM-level vive en el módulo
    compartido, reutilizado por el pipeline de captura (shift-left de calidad).
    """
    req_type = item.type.value if item.type else None
    out: list[dict[str, Any]] = []
    for f in programmatic_findings_for_text(item.statement or "", req_type=req_type):
        d: dict[str, Any] = {
            "scope": FindingScope.ITEM,
            "req_id": item.id,
            "dimension": _DIM_MAP[f.dimension],
            "rule_id": f.rule_id,
            "severity": _SEV_MAP[f.severity],
            "message": f.message,
            "suggestion": f.suggestion,
            "detected_by": "programmatic",
        }
        if f.rule_id in _RULES_WITH_EARS_PATTERN:
            d["ears_pattern"] = f.ears_pattern
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# LLM evaluation (ambigüedad semántica + reescrituras EARS)
# ---------------------------------------------------------------------------


class LlmFinding(BaseModel):
    # rule_id CERRADO (vocabulario canonico): el juez no inventa identificadores.
    # Antes el LLM eligia el string libre y la base acumulo ~1200 rule_ids
    # distintos (SEM-AMB-01, AMB-TERM, LLM_AMBIGUITY...) describiendo los mismos
    # 6 defectos; con la identidad dispar el merge re-creaba las filas en cada
    # corrida (inserted/deleted masivos), la curacion fixed/waived se perdia y
    # el backlog nunca mostraba convergencia. El schema estructurado fuerza la
    # eleccion y el dedupe por (req_id, rule_id) queda estable entre corridas.
    rule_id: Literal[
        "sem.ambiguous",
        "sem.quantification",
        "sem.ears_rewrite",
        "sem.incomplete",
        "sem.missing_requirement",
        "sem.set_inconsistency",
    ] = "sem.ambiguous"
    dimension: Literal[
        "ambiguity",
        "requirement_smell",
        "ears_violation",
        "incose_rule",
        "missing_req",
    ]
    severity: Literal["blocker", "major", "minor", "info"]
    message: str
    suggestion: str | None = None
    ears_pattern: str | None = None


class ItemQualityVerdict(BaseModel):
    item_id: str
    findings: list[LlmFinding] = Field(default_factory=list)


class QualityBatch(BaseModel):
    # Wrapper para que StructuredRunnable (que captura un único {}) lo pueda
    # serializar (mismo truco que CritiqueBatch).
    verdicts: list[ItemQualityVerdict]


_DIM_MAP = {
    "ambiguity": FindingDimension.AMBIGUITY,
    "requirement_smell": FindingDimension.REQUIREMENT_SMELL,
    "ears_violation": FindingDimension.EARS_VIOLATION,
    "incose_rule": FindingDimension.INCOSE_RULE,
    "missing_req": FindingDimension.MISSING_REQ,
}
_SEV_MAP = {
    "blocker": FindingSeverity.BLOCKER,
    "major": FindingSeverity.MAJOR,
    "minor": FindingSeverity.MINOR,
    "info": FindingSeverity.INFO,
}

# Juez con PRESUPUESTO y umbral de materialidad: sin acotacion, "find defects"
# generaba 1-3 hallazgos por enunciado (Planitrack2.0: 1.633 majors + 2.225
# minors abiertos con solo 17 FIXED) y cada cura re-juzgada excavaba hallazgos
# cada vez mas finos sobre el texto re-escrito. "Material" = cambia como se
# implementaria o testearia el requerimiento; el matiz de estilo no es defecto.
_QUALITY_RUBRIC = (
    "For EACH requirement, report AT MOST the 2 most MATERIAL quality defects "
    "the programmatic checks cannot catch. A defect is MATERIAL when it changes "
    "how an implementer would build or test the requirement (a word/phrase two "
    "teams would implement differently, a missing object/actor/target, a "
    "condition that cannot be verified). Stylistic nuances, tone and wording "
    "preferences are NOT defects. Focus on:\n"
    "- ambiguity: a word/phrase with more than one interpretation in context "
    "(e.g. 'soporte' = help vs. maintenance; 'alto' = high vs. stop).\n"
    "- ears: if the requirement is conditional but does not follow an EARS "
    "template (When/While/Where/If-then), propose a rewrite in the SAME "
    "LANGUAGE as the statement.\n"
    "- incose: incompleteness (missing actor, missing object, missing target).\n"
    "Do NOT repeat the obvious smells a regex already catches (modal missing, "
    "negation, slash, vague single word, pronoun, absolute, length). Only "
    "report SEMANTIC issues. If nothing material, return an EMPTY findings "
    "list for it — an empty list is a valid, preferred verdict for clean "
    "requirements."
)

_QUALITY_RULES = (
    "Rules:\n"
    "- rule_id: choose the single canonical id matching the defect "
    "(sem.ambiguous=word with multiple interpretations; "
    "sem.quantification=missing quantity/limit/threshold; "
    "sem.ears_rewrite=conditional not in EARS shape; "
    "sem.incomplete=missing actor/object/target; "
    "sem.missing_requirement=absent requirement this text reveals; "
    "sem.set_inconsistency=contradicts another requirement in the set). "
    "NEVER invent new ids.\n"
    "- One finding per rule_id per requirement (the first, most material one).\n"
    "- LANGUAGE: every message MUST be in neutral Spanish. Every suggestion "
    "MUST stay in the SAME LANGUAGE as the requirement's statement — never "
    "translate it.\n"
    "- Keep each message to one short clause (max ~20 words).\n"
    "- A suggestion is the corrected statement text only — no labels, no "
    "markdown, no echoing the original.\n"
    "- Return ONLY the structured object."
)

_QUALITY_SYSTEM = (
    "You are a REQUIREMENTS QUALITY ANALYST for a software SRS (ISO/IEC/IEEE "
    "29148 + INCOSE Guide + EARS). You review requirements and report only the "
    "SEMANTIC defects a programmatic checker cannot detect.\n\n"
    + _QUALITY_RUBRIC
    + "\n"
    + _QUALITY_RULES
)


# Presupuesto del juez: máximo de hallazgos SEMANTICOS por ítem (y regla
# única por ítem vía dedupe). El prompt pide "los 2 más material" y este cap
# mecánico lo respalda: sin él, un juez exhaustivo generaba hallazgos cada vez
# más finos sobre el mismo enunciado y el backlog crecía en cada cura.
_MAX_FINDINGS_PER_ITEM = 2


def _verdict_to_findings(v: ItemQualityVerdict, req_id: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_rules: set[str] = set()
    for lf in v.findings:
        if lf.rule_id in seen_rules:
            # Unica restriccion de la tabla: (req_id, rule_id). El juez podria
            # emitir 2 ambiguedades del mismo req; conservamos la primera (el
            # orden de emision refleja su materialidad).
            continue
        seen_rules.add(lf.rule_id)
        if len(out) >= _MAX_FINDINGS_PER_ITEM:
            break
        dim = _DIM_MAP.get(lf.dimension, FindingDimension.AMBIGUITY)
        sev = _SEV_MAP.get(lf.severity, FindingSeverity.MINOR)
        out.append(
            {
                "scope": FindingScope.ITEM,
                "req_id": req_id,
                "dimension": dim,
                "rule_id": lf.rule_id,
                "severity": sev,
                "message": lf.message,
                "suggestion": lf.suggestion,
                "ears_pattern": lf.ears_pattern,
                "detected_by": "agent",
            }
        )
    return out


async def _judge_batch(
    items: list,  # list[RequirementItem]
) -> dict[int, list[dict[str, Any]]]:
    """Una llamada LLM por batch. Devuelve {req_id: [finding_dict, ...]}.

    Resiliencia (espejo de critique._judge_batch): transient backoff ->
    fallback per-ítem; parse retries -> fallback per-ítem. Un truncado
    (JSON cortado por presupuesto, finish_reason=length) se degrada a
    thinking desactivado antes de rendirse al fallback: mismo remedio que
    ``goals_engine`` (incidente v6 de Planitrack2.0 — 3 generaciones
    full-price con thinking activo y ~7 minutos por episodio de fallback).
    Un fallo no aborta todo.
    """
    if not items:
        return {}

    async def _judge_one(it) -> list[dict[str, Any]]:
        msgs = [
            ("system", _QUALITY_SYSTEM),
            ("human", f"ITEM_ID: {it.id}\nTYPE: {it.type.value}\nSTATEMENT: {it.statement}"),
        ]
        try:
            v = await invoke_structured_resilient(
                lambda **kw: structured_llm(ItemQualityVerdict, **kw),
                msgs,
                context_label=f"quality/item:{it.id}",
                max_parse=_JUDGE_ATTEMPTS,
                thinking_off_body=disable_thinking_body(),
            )
        except Exception as exc:  # noqa: BLE001 — sentinel, no aborta
            reason = "rate_limited" if is_transient(exc) else "parse_error"
            logger.warning(
                "quality LLM eval unavailable for %s (%s)", it.id, reason
            )
            # Sentinel directo (dict, sin el desvío por ItemQualityVerdict):
            # su dimensión no existe en el Literal del esquema LLM y el intento
            # histórico de construirla como LlmFinding crashaba con
            # ValidationError antes de llegar a la DB (bug latente).
            return [
                {
                    "scope": FindingScope.ITEM,
                    "req_id": it.id,
                    "dimension": FindingDimension.EVAL_UNAVAILABLE,
                    "rule_id": "llm.eval_unavailable",
                    "severity": FindingSeverity.MINOR,
                    "message": (
                        "No se pudo completar la evaluación LLM de este ítem "
                        f"({reason}); revisión manual recomendada."
                    ),
                    "suggestion": None,
                    "ears_pattern": None,
                    "detected_by": "agent",
                }
            ]
        return _verdict_to_findings(
            v.model_copy(update={"item_id": str(it.id)}), it.id
        )

    # Batch path.
    blocks = []
    for it in items:
        blocks.append(
            f"ITEM_ID: {it.id}\nTYPE: {it.type.value}\nSTATEMENT: {it.statement}"
        )
    user = "\n---\n".join(blocks)
    msgs = [("system", _QUALITY_SYSTEM), ("human", user)]
    try:
        batch: QualityBatch = await invoke_structured_resilient(
            lambda **kw: structured_llm(QualityBatch, **kw),
            msgs,
            context_label="quality/batch",
            max_parse=_BATCH_PARSE_RETRIES,
            thinking_off_body=disable_thinking_body(),
        )
    except Exception:  # noqa: BLE001 — fallback per-ítem
        logger.warning(
            "quality batch failed; per-item fallback for %d items", len(items)
        )
        results = await asyncio.gather(*[_judge_one(it) for it in items])
        return {it.id: r for it, r in zip(items, results)}

    by_id = {v.item_id: v for v in batch.verdicts}
    out: dict[int, list[dict[str, Any]]] = {}
    for it in items:
        v = by_id.get(str(it.id))
        if v is None:
            v = by_id.get(it.id)  # por si el modelo no citó como str
        if v is None:
            results = await asyncio.gather(_judge_one(it))
            out[it.id] = results[0]
        else:
            out[it.id] = _verdict_to_findings(v, it.id)
    return out


# Tope del detalle de blockers embebido en el summary (la lista completa vive
# en los findings; el detalle es para que la tool del agente los reporte TODOS
# de una vez sin paginar).
_BLOCKERS_DETAIL_CAP = 40

# Sentinel de Fase B: la evaluación LLM no pudo completarse. Identifica tanto
# los ítems a reintentar en la próxima corrida como los que NO deben sellarse.
_SENTINEL_RULE_ID = "llm.eval_unavailable"


def quality_fingerprint(statement: str | None, type_value: str | None) -> str:
    """Huella de lo único que el juez LLM ve de un ítem: (statement, type).

    Dos corridas sobre el mismo enunciado producen el mismo fingerprint: es la
    clave del análisis delta (reutilizar el veredicto persistido en vez de
    re-juzgar). Cambios de status/priority NO cuentan — no alteran el juicio.
    """
    payload = "\x1f".join([(statement or "").strip(), type_value or ""])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _item_fingerprint(item) -> str:
    return quality_fingerprint(
        item.statement, item.type.value if item.type else None
    )


def _row_to_finding_dict(f: RequirementFinding) -> dict[str, Any]:
    """RequirementFinding persistido -> dict de tanda (misma forma que Fase A/B)."""
    return {
        "scope": f.scope,
        "req_id": f.req_id,
        "dimension": f.dimension,
        "rule_id": f.rule_id,
        "severity": f.severity,
        "message": f.message,
        "suggestion": f.suggestion,
        "ears_pattern": f.ears_pattern,
        "detected_by": f.detected_by,
    }


async def _sentinel_req_ids(
    session: AsyncSession, project_id: int
) -> set[int]:
    """Ítems cuya última corrida LLM cayó al sentinel (reintentar siempre)."""
    rows = await session.execute(
        select(RequirementFinding.req_id).where(
            RequirementFinding.project_id == project_id,
            RequirementFinding.rule_id == _SENTINEL_RULE_ID,
        )
    )
    return {rid for (rid,) in rows.all() if rid is not None}


async def analyze_quality(
    session: AsyncSession,
    project_id: int,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    *,
    incremental: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Analiza la calidad de los requerimientos vivos: Fase A total + Fase B delta.

    Devuelve (summary, findings) donde ``findings`` es el inventario COMPLETO
    del proyecto, listo para ``srs_store.merge_findings``:

    - Fase A programática corre sobre TODOS los vivos (determinista, costo
      cero): el estado de curación lo preserva el merge por (req_id, rule_id).
    - Fase B LLM solo re-juzga los ítems nuevos/editados
      (``quality_fingerprint`` distinto o NULL) y los que cayeron al sentinel
      en la corrida anterior. Los ítems intactos conservan su veredicto LLM
      persistido (reutilización): el inventario combina Fase A + Fase B +
      reutilizados, así los blockers reportados son completos SIN re-juzgar
      el corpus entero. Esto corta el ciclo «cura N blockers → el re-juez
      descubre N nuevos en ítems nunca revisados»: lo intacto no se re-toca.
    - ``incremental=False`` fuerza el re-juez completo (primera corrida tras
      un deploy con motor cambiado, o diagnóstico).

    El summary incluye ``blockers_detail``: TODOS los blockers del inventario
    en forma compacta (req, rule, mensaje) para que la tool del agente los
    informe de una vez, y ``fresh_judged_req_ids``: los ítems que esta corrida
    re-juzgó sin sentinel (el merge los sella con su fingerprint EN LA MISMA
    transacción que persiste hallazgos — si el run aborta antes del commit no
    hay sello y la próxima corrida los re-juzga).

    ``on_progress(done, total)`` (opcional, awaited) reporta el avance de los
    batches LLM: se emite cada ``_PROGRESS_EVERY`` lotes y en el último; el
    caller decide el canal (SSE ``srs.progress`` desde el tool del agente).
    """
    items = await list_requirements(session, project_id, include_deleted=True)
    live = [it for it in items if it.status in _LIVE_STATUSES]

    fingerprints = {it.id: _item_fingerprint(it) for it in live}
    code_map = {it.id: it.code for it in live}

    # Fase A: programática (instantánea, determinista) sobre todos los vivos.
    findings: list[dict[str, Any]] = []
    for it in live:
        findings.extend(programmatic_findings(it))

    # Delta de Fase B: qué ítems necesitan juicio LLM fresco.
    if incremental:
        retry_ids = await _sentinel_req_ids(session, project_id)
        to_judge = [
            it
            for it in live
            if it.quality_fingerprint is None
            or it.quality_fingerprint != fingerprints[it.id]
            or it.id in retry_ids
        ]
    else:
        to_judge = live
    judged_ids = {it.id for it in to_judge}
    kept_ids = {it.id for it in live if it.id not in judged_ids}

    # Fase B: LLM en batches concurrentes SOLO sobre el delta.
    sem = asyncio.Semaphore(DEFAULT_CONCURRENCY)
    batches = [
        to_judge[i : i + DEFAULT_BATCH_SIZE]
        for i in range(0, len(to_judge), DEFAULT_BATCH_SIZE)
    ]
    total = len(batches)
    done = 0
    llm_by_req: dict[int, list[dict[str, Any]]] = {}

    async def _run(batch):
        nonlocal done
        async with sem:
            result = await _judge_batch(batch)
        done += 1
        if on_progress is not None and (
            done % _PROGRESS_EVERY == 0 or done == total
        ):
            try:
                await on_progress(done, total)
            except Exception:  # noqa: BLE001 — el progreso nunca rompe la etapa
                pass
        return result

    batch_results = await asyncio.gather(*[_run(b) for b in batches])
    for br in batch_results:
        llm_by_req.update(br)
    for req_id, fs in llm_by_req.items():
        findings.extend(fs)

    # Reutilización: los ítems intactos conservan su veredicto LLM previo.
    if kept_ids:
        rows = await session.scalars(
            select(RequirementFinding).where(
                RequirementFinding.project_id == project_id,
                RequirementFinding.req_id.in_(kept_ids),
                RequirementFinding.detected_by == "agent",
                RequirementFinding.rule_id != _SENTINEL_RULE_ID,
            )
        )
        for f in rows:
            findings.append(_row_to_finding_dict(f))

    # Ítems con veredicto fresco y sin sentinel: candidatos a sello (el merge
    # sella en su misma transacción; un abort pre-commit deja el sello fuera).
    fresh_judged = [
        it.id
        for it in to_judge
        if not any(
            fd.get("rule_id") == _SENTINEL_RULE_ID
            for fd in llm_by_req.get(it.id, [])
        )
    ]

    # Resumen (sobre el inventario COMPLETO, no solo el delta).
    by_severity: dict[str, int] = {}
    by_dimension: dict[str, int] = {}
    items_with_findings = set()
    blockers: list[dict[str, str]] = []
    for f in findings:
        sev = f["severity"].value
        dim = f["dimension"].value
        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_dimension[dim] = by_dimension.get(dim, 0) + 1
        if f.get("req_id"):
            items_with_findings.add(f["req_id"])
        if sev == "blocker":
            blockers.append(
                {
                    "req": code_map.get(f.get("req_id")),
                    "rule": f.get("rule_id"),
                    "message": (f.get("message") or "")[:140],
                }
            )
    blockers.sort(key=lambda b: (b["rule"] or "", b["req"] or ""))
    capped = blockers[:_BLOCKERS_DETAIL_CAP]

    summary = {
        "total_findings": len(findings),
        "by_severity": by_severity,
        "by_dimension": by_dimension,
        "items_with_findings": len(items_with_findings),
        "items_analyzed": len(live),
        "items_judged": len(to_judge),
        "items_reused": len(kept_ids),
        "blockers": len(blockers),
        "blockers_detail": capped,
        "blockers_detail_truncated": len(blockers) > len(capped),
        # Ítems cuya evaluación LLM cayó al sentinel (degradación explícita:
        # el reporte deja de esconder cobertura de evaluación faltante).
        "llm_eval_unavailable": sum(
            1 for f in findings if f.get("rule_id") == _SENTINEL_RULE_ID
        ),
        # Contrato con merge_findings: sellar fingerprints de estos ítems en
        # la misma transacción que persiste la tanda.
        "fresh_judged_req_ids": fresh_judged,
    }
    logger.info(
        "quality: %d items (%d re-juzgados, %d reutilizados) -> %d findings "
        "(blockers=%d)",
        len(live), len(to_judge), len(kept_ids), len(findings), len(blockers),
    )
    return summary, findings


# Reutiliza el mismo criterio de "vivo" que el builder.
_LIVE_STATUSES = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)
