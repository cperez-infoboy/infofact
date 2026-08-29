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
import logging
import os
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field
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
    rule_id: str
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

_QUALITY_RUBRIC = (
    "For EACH requirement, find QUALITY DEFECTS the programmatic checks cannot "
    "catch. Focus on:\n"
    "- ambiguity: a word/phrase with more than one interpretation in context "
    "(e.g. 'soporte' = help vs. maintenance; 'alto' = high vs. stop).\n"
    "- ears: if the requirement is conditional but does not follow an EARS "
    "template (When/While/Where/If-then), propose a rewrite in the SAME "
    "LANGUAGE as the statement.\n"
    "- incose: incompleteness (missing actor, missing object, missing target).\n"
    "Do NOT repeat the obvious smells a regex already catches (modal missing, "
    "negation, slash, vague single word, pronoun, absolute, length). Only "
    "report SEMANTIC issues. If the requirement is clean, return an empty "
    "findings list for it."
)

_QUALITY_RULES = (
    "Rules:\n"
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


def _verdict_to_findings(v: ItemQualityVerdict, req_id: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for lf in v.findings:
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


async def analyze_quality(
    session: AsyncSession,
    project_id: int,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Analiza la calidad de todos los requerimientos vivos del proyecto.

    Devuelve (summary, findings) donde ``findings`` son dict listos para
    ``srs_store.replace_findings``. Combina Fase A (programática) + Fase B (LLM).
    ``on_progress(done, total)`` (opcional, awaited) reporta el avance de los
    batches LLM: se emite cada ``_PROGRESS_EVERY`` lotes y en el último; el
    caller decide el canal (SSE ``srs.progress`` desde el tool del agente).
    """
    items = await list_requirements(session, project_id, include_deleted=True)
    live = [it for it in items if it.status in _LIVE_STATUSES]

    # Fase A: programática (instantánea, determinista).
    findings: list[dict[str, Any]] = []
    for it in live:
        findings.extend(programmatic_findings(it))

    # Fase B: LLM en batches concurrentes.
    sem = asyncio.Semaphore(DEFAULT_CONCURRENCY)
    batches = [live[i:i + DEFAULT_BATCH_SIZE] for i in range(0, len(live), DEFAULT_BATCH_SIZE)]
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

    # Resumen.
    by_severity: dict[str, int] = {}
    by_dimension: dict[str, int] = {}
    items_with_findings = set()
    blockers = 0
    for f in findings:
        sev = f["severity"].value
        dim = f["dimension"].value
        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_dimension[dim] = by_dimension.get(dim, 0) + 1
        if f.get("req_id"):
            items_with_findings.add(f["req_id"])
        if sev == "blocker":
            blockers += 1

    summary = {
        "total_findings": len(findings),
        "by_severity": by_severity,
        "by_dimension": by_dimension,
        "items_with_findings": len(items_with_findings),
        "items_analyzed": len(live),
        "blockers": blockers,
        # Ítems cuya evaluación LLM cayó al sentinel (degradación explícita:
        # el reporte deja de esconder cobertura de evaluación faltante).
        "llm_eval_unavailable": sum(
            1 for f in findings if f.get("rule_id") == "llm.eval_unavailable"
        ),
    }
    logger.info(
        "quality: %d items -> %d findings (blockers=%d)",
        len(live), len(findings), blockers,
    )
    return summary, findings


# Reutiliza el mismo criterio de "vivo" que el builder.
_LIVE_STATUSES = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)
