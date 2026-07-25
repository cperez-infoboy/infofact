"""Classification: type + priority + optional decomposition (plan §4).

Two passes over the survivors of critique:

  Pass A (classify): every item -> (ReqType, Priority, decomposition_needed).
  Pass B (decompose): only items flagged high-level -> operational sub-items.

Design notes:

- Does NOT mutate RawRequirement. Returns decisions + decompositions keyed by
  item id. The requirements_service (Paso 7) maps these onto RequirementItem
  rows (type/priority columns, parent_id/derived flags for sub-items). Keeping
  the stage pure means the DB write happens once, in one place, not scattered.

- Priority normalization is LLM-driven with the MoSCoW table in the system
  prompt, NOT a regex pre-pass. A programmatic synonym table is fragile
  (partial coverage, no nuance); the LLM reads the obligation verb in context
  and maps it. Mandatory/Shall/Obligatorio -> MUST, etc.

- Decomposition is separated from classification on purpose: asking the LLM for
  a variable-length nested list AND a classification in the same structured
  call is exactly where GLM's structured output drifts most. Split passes keep
  each schema flat. Pass B runs over few items (most are already operational).

- ReqType / Priority are the closed enums from backend.models.requirement
  (ISO/IEC 25010 NFRs + IEEE 29148 for type; MoSCoW for priority). Closed set
  => classification is reproducible and comparable across documents.

Decomposed sub-items preserve the parent's source_span/section (attached by
the service when building RequirementItem rows); they do NOT invent detail not
implied by the parent.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from backend.agents.llm import structured_llm
from backend.agents.pipelines._resilience import (
    _TRANSIENT_RETRIES,
    DEFAULT_BATCH_SIZE,
    BATCH_MAX_TOKENS,
    _BATCH_PARSE_RETRIES,
    BatchStats,
)
from backend.agents.pipelines._resilience import is_transient as _is_transient
from backend.agents.pipelines.extraction import RawRequirement
from backend.models.requirement import Priority, ReqType

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 4

_CLASSIFY_ATTEMPTS = 3        # parse-failure retries before the sentinel fallback


# ---------------------------------------------------------------------------
# LLM-facing schemas
# ---------------------------------------------------------------------------

class ClassificationDecision(BaseModel):
    """One item's classification verdict."""
    item_id: str = Field(
        description="Identifier of the requirement this decision applies to.",
    )
    type: ReqType = Field(
        description="Requirement type from the closed taxonomy.",
    )
    priority: Priority = Field(
        description="MoSCoW priority, normalized from the source's obligation "
                    "verb (shall/debe -> MUST, should/deberia -> SHOULD, "
                    "may/podria -> COULD, wont/futuro -> WONT).",
    )
    rationale: str = Field(
        default="",
        description="One line: why this type and priority.",
    )
    decomposition_needed: bool = Field(
        default=False,
        description="True only if the statement is HIGH-LEVEL / vague and "
                    "would benefit from being split into operational, "
                    "testable sub-requirements. False if already operational "
                    "and verifiable as-is.",
    )


class ClassificationBatch(BaseModel):
    """Wrapper for batch-mode classifier output.

    Mirrors `CritiqueBatch`: `_JSON_OBJECT_RE` in llm.py only captures a
    single JSON object, so a list-typed schema needs a named key.
    """
    decisions: list[ClassificationDecision]


class DecomposedItem(BaseModel):
    """One operational sub-requirement derived from a high-level parent."""
    statement: str = Field(
        description="An atomic, operational, verifiable sub-requirement.",
    )
    rationale: str = Field(
        default="",
        description="Why this sub-requirement is implied by the parent.",
    )


class DecompositionResult(BaseModel):
    parts: list[DecomposedItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_CLASSIFY_RUBRIC = (
    "TYPE taxonomy (closed set, pick exactly one):\n"
    "- functional        : a capability/behaviour the system must do.\n"
    "- performance        : speed, throughput, latency, response time, capacity.\n"
    "- security           : authn/authz, confidentiality, integrity, audit.\n"
    "- usability          : UX, accessibility, learnability, UI constraints.\n"
    "- reliability        : uptime, fault tolerance, recoverability, MTBF.\n"
    "- maintainability    : modularity, testability, portability, tech-debt limits.\n"
    "- compliance         : regulatory, legal, standards (GDPR, ISO, etc.).\n"
    "- constraint         : technology / environment / platform choices.\n"
    "- process            : deliverables, methodology, project-management demands.\n"
    "- data               : data model, persistence, integrations, data quality.\n\n"
    "PRIORITY (MoSCoW). Map the source obligation verb to one of:\n"
    "- MUST   : mandatory. Source says 'shall', 'must', 'debe', 'obligatorio', "
    "'required', 'mandatory'.\n"
    "- SHOULD : desired/important. 'should', 'deberia', 'preferred', "
    "'deseable', 'recommended'.\n"
    "- COULD  : optional. 'may', 'could', 'podria', 'optional', 'nice to have'.\n"
    "- WONT   : out of scope / future. 'wont', 'future', 'out of scope', "
    "'futuro', 'no aplica'.\n"
    "If the document carries its own priority legend, honour that mapping. "
    "If no priority signal is present, default MUST for explicit requirements "
    "and SHOULD for implicit/assumption ones.\n\n"
    "DECOMPOSITION:\n"
    "- Set decomposition_needed=True ONLY when the statement is high-level or "
    "vague (e.g. 'the system must be secure', 'must be usable') AND its "
    "priority is MUST or SHOULD. Low-level, already-testable statements stay "
    "False.\n"
    "- Never set True just because the statement is long; length != vagueness.\n"
)

# `_CLASSIFY_SYSTEM` (historical literal) had no dedicated "Rules:" block; the
# constant exists for symmetry with `_CRITIC_RULES` so the batch variant can
# append shared rules later without touching the byte-identical single-item
# composition below.
_CLASSIFY_RULES = ""

# IMPORTANT: keep this composed form BYTE-IDENTICAL to the historical literal —
# the per-item smoke (batch_size=1) validates that this path did not drift.
_CLASSIFY_SYSTEM = (
    "You are a REQUIREMENTS CLASSIFIER for a software SRS. For each "
    "requirement you assign a TYPE and a PRIORITY, and decide whether it is "
    "high-level enough to need DECOMPOSITION. Base every judgment on the "
    "statement + its source_span (a verbatim quote from the client document).\n\n"
    + _CLASSIFY_RUBRIC
    + _CLASSIFY_RULES
    + "Return ONLY the structured object."
)

_CLASSIFY_BATCH_SYSTEM = (
    "You are a REQUIREMENTS CLASSIFIER for a software SRS. You are given "
    "MULTIPLE requirements, each identified by its ITEM_ID. For EACH item, "
    "assign a TYPE and a PRIORITY, and decide whether it needs DECOMPOSITION. "
    "You MUST return one decision per item_id — never omit an item.\n\n"
    + _CLASSIFY_RUBRIC
    + _CLASSIFY_RULES
    + '\nReturn ONLY the structured object: {"decisions": [{"item_id": "...", ...}, ...]}.'
)

_DECOMPOSE_SYSTEM = (
    "You decompose ONE high-level requirement into operational, testable "
    "sub-requirements. Each sub-requirement must be atomic (one capability) "
    "and individually verifiable.\n\n"
    "Rules:\n"
    "- LANGUAGE: every generated sub-statement MUST be in the SAME LANGUAGE as the parent statement. Do NOT translate.\n"
    "- Derive ONLY what is implied by the parent statement + its source_span. "
    "Do NOT invent capabilities, technologies, or thresholds not present or "
    "clearly implied by the parent.\n"
    "- PRESERVE every technical specific of the parent (named technologies, "
    "numbers, thresholds) in the relevant sub-item.\n"
    "- Aim for 2-5 sub-items. If the parent is already operational and needs "
    "no split, return parts=[] (empty list).\n"
    "- Do NOT repeat the parent verbatim as a single part — that adds nothing.\n"
    "Return ONLY the structured object."
)


# ---------------------------------------------------------------------------\
# Sentinels (parse-failure / rate-limit fallbacks)
# ---------------------------------------------------------------------------
#
# ClassificationDecision has no free-form `reasons` dict (unlike CritiqueVerdict),
# so the sentinel marks itself by prefixing `rationale` with `[parse_error]` or
# `[rate_limited]`. `classify_all` counts these prefixes for stats. Conservative
# values: type=CONSTRAINT (catch-all that does not bias downstream filters),
# priority=MUST (so the item is never silently deprioritized), and
# decomposition_needed=False (avoid cascade failures through Pass B on an item
# we could not even parse).

_PARSE_ERROR_PREFIX = "[parse_error]"
_RATE_LIMITED_PREFIX = "[rate_limited]"


def _parse_failure_decision(
    item_id: str, attempts: int, exc: Exception
) -> ClassificationDecision:
    """Sentinel for items whose LLM response could not be parsed.

    Mirrors `critique._parse_failure_verdict`: retry absorbs transient
    malformations; once every attempt fails we flag for human review instead
    of letting one bad response abort the whole `classify_all` gather.
    """
    return ClassificationDecision(
        item_id=item_id,
        type=ReqType.CONSTRAINT,
        priority=Priority.MUST,
        rationale=(
            f"{_PARSE_ERROR_PREFIX} Classification LLM returned malformed/"
            f"unparseable JSON after {attempts} attempts "
            f"({type(exc).__name__}); manual review required."
        ),
        decomposition_needed=False,
    )


def _rate_limit_decision(
    item_id: str, fails: int, exc: Exception
) -> ClassificationDecision:
    """Sentinel for items that exhausted transient-error retries."""
    return ClassificationDecision(
        item_id=item_id,
        type=ReqType.CONSTRAINT,
        priority=Priority.MUST,
        rationale=(
            f"{_RATE_LIMITED_PREFIX} Z.ai returned 429/transient after {fails} "
            f"backoffs ({type(exc).__name__}); retry this item later."
        ),
        decomposition_needed=False,
    )


# ---------------------------------------------------------------------------
# Pass A: classify
# ---------------------------------------------------------------------------

async def _classify_item(
    item: RawRequirement,
    *,
    attempts: int = _CLASSIFY_ATTEMPTS,
    transient_retries: int = _TRANSIENT_RETRIES,
) -> ClassificationDecision:
    """One LLM classification call over one item.

    Two retry paths (mirrors `critique._judge_item`):
    - Transient (429, 5xx, timeout, conn error): exponential backoff up to
      `transient_retries`, then a `[rate_limited]` sentinel.
    - Parse (truncated/malformed JSON, ValidationError): up to `attempts`
      quick retries, then a `[parse_error]` sentinel.
    One bad response must not abort the whole batch.
    """
    llm = structured_llm(ClassificationDecision)
    user = (
        f"STATEMENT: {item.statement}\n"
        f"SOURCE_SPAN: {item.source_span}\n"
        f"SECTION: {item.section}\n"
        f"EXPLICIT: {item.explicit}\n"
    )
    msgs = [("system", _CLASSIFY_SYSTEM), ("human", user)]
    parse_fails = 0
    transient_fails = 0
    while True:
        try:
            v = await llm.ainvoke(msgs)
            # Post-hoc override: model-emitted item_id is unreliable in
            # per-item mode; use the item's actual id for routing.
            return v.model_copy(update={"item_id": item.id})
        except Exception as exc:  # noqa: BLE001 — split below
            if _is_transient(exc):
                transient_fails += 1
                if transient_fails >= transient_retries:
                    return _rate_limit_decision(item.id, transient_fails, exc)
                wait = min(2 ** transient_fails, 60)  # exp backoff, cap 60s
                logger.warning(
                    "classify transient %s for %s; backoff %.1fs (%d/%d)",
                    type(exc).__name__, item.id, wait,
                    transient_fails, transient_retries,
                )
                await asyncio.sleep(wait)
            else:
                parse_fails += 1
                if parse_fails >= attempts:
                    return _parse_failure_decision(item.id, parse_fails, exc)
                logger.warning(
                    "classify parse failed for %s (%s); retry (%d/%d)",
                    item.id, exc, parse_fails, attempts,
                )


async def _per_item_fallback_classify(
    items: list[RawRequirement],
) -> list[ClassificationDecision]:
    """Run `_classify_item` concurrently for each item.

    Mirrors `critique._per_item_fallback`. Used when the batch call fails or
    when the batch response omits items.
    """
    if not items:
        return []
    return list(await asyncio.gather(*[_classify_item(it) for it in items]))


async def _classify_batch(
    items: list[RawRequirement],
    *,
    max_tokens: int = BATCH_MAX_TOKENS,
) -> tuple[list[ClassificationDecision], BatchStats]:
    """Batch-mode classifier pass over M items in one LLM call.

    Mirrors `critique._judge_batch` (plan §13.D). Decision tree:
    - len == 0 → empty + zero stats.
    - len == 1 → delegate to `_classify_item` (smoke path + loose last chunk).
    - Otherwise build a batch user message with ITEM_ID/STATEMENT/.../EXPLICIT
      blocks and call the batch-typed structured LLM.

    Retry policy + omission detection identical to `_judge_batch`.
    """
    stats = BatchStats()
    if not items:
        return [], stats
    if len(items) == 1:
        only = items[0]
        d = await _classify_item(only)
        return [d], stats

    # Build the batch user message.
    blocks: list[str] = []
    for it in items:
        lines = [
            f"ITEM_ID: {it.id}",
            f"STATEMENT: {it.statement}",
            f"SOURCE_SPAN: {it.source_span}",
            f"SECTION: {it.section}",
            f"EXPLICIT: {it.explicit}",
        ]
        blocks.append("\n".join(lines))
    user = "\n---\n".join(blocks)
    msgs = [("system", _CLASSIFY_BATCH_SYSTEM), ("human", user)]

    llm = structured_llm(ClassificationBatch)
    parse_fails = 0
    transient_fails = 0
    batch: ClassificationBatch | None = None
    while True:
        try:
            batch = await llm.ainvoke(msgs, max_tokens=max_tokens)
            stats.batch_calls += 1
            break
        except Exception as exc:  # noqa: BLE001 — split below
            if _is_transient(exc):
                transient_fails += 1
                if transient_fails >= _TRANSIENT_RETRIES:
                    logger.warning(
                        "classify batch transient exhausted after %d retries; "
                        "falling back to per-item for %d items",
                        transient_fails, len(items),
                    )
                    fallback = await _per_item_fallback_classify(items)
                    stats.fallback += len(fallback)
                    return fallback, stats
                wait = min(2 ** transient_fails, 60)
                logger.warning(
                    "classify batch transient %s; backoff %.1fs (%d/%d)",
                    type(exc).__name__, wait, transient_fails, _TRANSIENT_RETRIES,
                )
                await asyncio.sleep(wait)
            else:
                parse_fails += 1
                if parse_fails >= _BATCH_PARSE_RETRIES:
                    logger.warning(
                        "classify batch parse failed after %d retries; "
                        "falling back to per-item for %d items",
                        parse_fails, len(items),
                    )
                    fallback = await _per_item_fallback_classify(items)
                    stats.fallback += len(fallback)
                    return fallback, stats
                logger.warning(
                    "classify batch parse failed (%s); retry (%d/%d)",
                    exc, parse_fails, _BATCH_PARSE_RETRIES,
                )

    # Success — detect omissions via item_id.
    by_id: dict[str, ClassificationDecision] = {
        d.item_id: d for d in batch.decisions
    }
    missing = [it for it in items if it.id not in by_id]
    if missing:
        logger.warning(
            "classify batch omitted %d/%d items; routing to per-item",
            len(missing), len(items),
        )
        omitted = await _per_item_fallback_classify(missing)
        stats.omitted += len(missing)
        for it, d in zip(missing, omitted):
            by_id[it.id] = d

    # Assemble in input order; override item_id post-hoc.
    out: list[ClassificationDecision] = []
    for it in items:
        d = by_id.get(it.id)
        if d is None:
            d = _parse_failure_decision(
                it.id, 0, RuntimeError("missing from batch response"),
            )
        else:
            d = d.model_copy(update={"item_id": it.id})
        out.append(d)
    return out, stats


# ---------------------------------------------------------------------------
# Pass B: decompose
# ---------------------------------------------------------------------------

async def _decompose_item(item: RawRequirement) -> list[DecomposedItem]:
    """Decompose one high-level item into operational sub-items.

    Returns [] when the LLM decides no split is warranted, OR when the LLM
    call fails after exhausting retries (graceful degradation: the item
    keeps its classification but is not decomposed; Pass B does not crash
    the whole `classify_all` gather). Mirrors the retry+sentinel pattern of
    `_classify_item` but with `[]` as the sentinel because there is no
    `DecompositionResult`-shaped failure object to surface.
    """
    llm = structured_llm(DecompositionResult)
    user = (
        f"PARENT_STATEMENT: {item.statement}\n"
        f"PARENT_SOURCE_SPAN: {item.source_span}\n"
        f"SECTION: {item.section}\n"
    )
    msgs = [("system", _DECOMPOSE_SYSTEM), ("human", user)]
    parse_fails = 0
    transient_fails = 0
    while True:
        try:
            res = await llm.ainvoke(msgs)
            return list(res.parts)
        except Exception as exc:  # noqa: BLE001 — split below
            if _is_transient(exc):
                transient_fails += 1
                if transient_fails >= _TRANSIENT_RETRIES:
                    logger.warning(
                        "decompose transient exhausted for %s after %d retries "
                        "(%s); leaving item without decomposition",
                        item.id, transient_fails, type(exc).__name__,
                    )
                    return []
                wait = min(2 ** transient_fails, 60)
                logger.warning(
                    "decompose transient %s for %s; backoff %.1fs (%d/%d)",
                    type(exc).__name__, item.id, wait,
                    transient_fails, _TRANSIENT_RETRIES,
                )
                await asyncio.sleep(wait)
            else:
                parse_fails += 1
                if parse_fails >= _CLASSIFY_ATTEMPTS:
                    logger.warning(
                        "decompose parse failed for %s after %d attempts "
                        "(%s); leaving item without decomposition",
                        item.id, parse_fails, type(exc).__name__,
                    )
                    return []
                logger.warning(
                    "decompose parse failed for %s (%s); retry (%d/%d)",
                    item.id, exc, parse_fails, _CLASSIFY_ATTEMPTS,
                )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    decisions: dict[str, ClassificationDecision] = field(default_factory=dict)
    decompositions: dict[str, list[DecomposedItem]] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)


async def classify_all(
    items: list[RawRequirement],
    *,
    decompose: bool = True,
    concurrency: int = DEFAULT_CONCURRENCY,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> ClassificationResult:
    """Classify every item; optionally decompose the high-level ones.

    Pipeline (plan §13.E):
    - Pass A (classify): chunked into batches of `batch_size`; one LLM call
      per batch when M>1 (per-item shortcut when M==1 — smoke path).
    - Pass B (decompose): per-item over the subset flagged
      `decomposition_needed`. Batching Pass B is out of scope (small subset).

    Returns decisions for all items and decompositions for the subset flagged
    decomposition_needed. Sub-items inherit the parent's source_span/section at
    persistence time (service layer), not here.
    """
    if not items:
        return ClassificationResult(stats={"input": 0})

    for i, it in enumerate(items):
        if not it.id:
            it.id = f"raw-{i}"

    batches = [
        items[i:i + batch_size] for i in range(0, len(items), batch_size)
    ]
    sem = asyncio.Semaphore(concurrency)
    agg = BatchStats()

    async def _one_batch(batch: list[RawRequirement]):
        async with sem:
            first_decisions, bstats = await _classify_batch(batch)
            agg.batch_calls += bstats.batch_calls
            agg.omitted += bstats.omitted
            agg.fallback += bstats.fallback
            # Return list of (item, decision) tuples; stitch in input order later.
            return list(zip(batch, first_decisions))

    batch_results = await asyncio.gather(*[_one_batch(b) for b in batches])
    # Flatten preserving input order via dict.
    flat: dict[str, ClassificationDecision] = {}
    for batch_res in batch_results:
        for it, d in batch_res:
            flat[it.id] = d
    decisions: dict[str, ClassificationDecision] = {
        it.id: flat[it.id] for it in items
    }

    decompositions: dict[str, list[DecomposedItem]] = {}
    n_decomposed = 0
    n_sub = 0
    if decompose:
        to_decompose = [
            it for it in items if decisions[it.id].decomposition_needed
        ]

        async def _one_dec(it: RawRequirement) -> list[DecomposedItem]:
            async with sem:
                return await _decompose_item(it)

        dec_results = await asyncio.gather(*[_one_dec(it) for it in to_decompose])
        for it, parts in zip(to_decompose, dec_results):
            if parts:
                decompositions[it.id] = parts
                n_decomposed += 1
                n_sub += len(parts)

    # Priority/type distribution for quick eyeballing.
    prio_dist: dict[str, int] = {}
    type_dist: dict[str, int] = {}
    for d in decisions.values():
        prio_dist[d.priority.value] = prio_dist.get(d.priority.value, 0) + 1
        type_dist[d.type.value] = type_dist.get(d.type.value, 0) + 1

    # Count sentinel-marked decisions so callers can tell API-driven failures
    # from clean classifications (mirrors `critique_all` rate_limited/parse_error).
    n_parse_error = sum(
        1 for d in decisions.values()
        if d.rationale.startswith(_PARSE_ERROR_PREFIX)
    )
    n_rate_limited = sum(
        1 for d in decisions.values()
        if d.rationale.startswith(_RATE_LIMITED_PREFIX)
    )

    stats = {
        "input": len(items),
        "classified": len(decisions),
        "decomposed_items": n_decomposed,
        "sub_items": n_sub,
        "priority_dist": prio_dist,
        "type_dist": type_dist,
        "parse_error": n_parse_error,
        "rate_limited": n_rate_limited,
        "batch_calls": agg.batch_calls,
        "batch_omitted": agg.omitted,
        "per_item_fallback": agg.fallback,
    }
    logger.info(
        "classification: %d items -> %d decomposed (%d sub-items); priorities=%s "
        "(parse_error=%d, rate_limited=%d) "
        "[batch: calls=%d, omitted=%d, per_item_fallback=%d]",
        stats["input"], n_decomposed, n_sub, prio_dist,
        n_parse_error, n_rate_limited,
        agg.batch_calls, agg.omitted, agg.fallback,
    )
    return ClassificationResult(
        decisions=decisions, decompositions=decompositions, stats=stats,
    )
