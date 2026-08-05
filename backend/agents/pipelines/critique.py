"""Critique: generator-critic loop over extracted requirements (plan §3).

Per-item quality review with a structured LLM verdict on 3 dimensions:
  - fidelity:   does source_span support the statement? (pass/fix/reject)
  - atomic:     exactly one requirement? (pass/fix)
  - verifiable: can an acceptance criterion/test be written? (pass/flag)

Plus a programmatic pre-check for DANGLING references (statement points at a
list that was split into separate items) — no LLM needed for that case. The
critic proposes a rewrite for fidelity/atomic fixes; the loop applies it and
re-checks (max DEFAULT_MAX_ITER refinements, after which diminishing returns
dominate — Self-Refine, Madaan et al.).

Scope split with consolidation (Paso 4): duplicate + contradiction detection
live THERE (batch, embedding candidates + LLM judge). Critique focuses on
per-item quality. neighbors_by_id optionally feeds nearby items as CONTEXT
only (the critic does not re-run dedup). The store layer (§10) consumes
verdicts and maps them to tools (split/merge/rewrite/approve).

Kamoi et al. (TACL 2024): LLM self-correction works when feedback is anchored
to verifiable criteria — so the critic judges against source_span text, not
subjective taste.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from backend.agents.llm import structured_llm
from backend.agents.pipelines._resilience import is_transient as _is_transient
from backend.agents.pipelines._resilience import (
    _TRANSIENT_RETRIES,
    DEFAULT_BATCH_SIZE,
    BATCH_MAX_TOKENS,
    _BATCH_PARSE_RETRIES,
    BatchStats,
    DEFAULT_CONCURRENCY,
    transient_backoff_seconds,
)
from backend.agents.pipelines._quality_rules import (
    ProgrammaticFinding,
    programmatic_findings_for_text,
)
from backend.agents.pipelines.consolidation import _has_dangling_reference
from backend.agents.pipelines.extraction import DocumentRules, RawRequirement

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITER = 1       # max refinement rounds (total judges = 1 + refinements)
_JUDGE_ATTEMPTS = 3        # parse-failure retries before the sentinel fallback


class CritiqueVerdict(BaseModel):
    # item_id identifies which item this verdict belongs to. Required so the
    # batch mode can reconnect verdicts to items; in per-item mode it is
    # overridden post-hoc with the item's actual id.
    item_id: str
    # nature is judged FIRST: a non-requirement (template fill instruction,
    # codification legend, proposal-evaluation criterion) is out of scope for
    # the SRS even if it is atomic, verifiable, and faithful to the source.
    nature: Literal["requirement", "meta_instruction", "definition", "evaluation"]
    fidelity: Literal["pass", "fix", "reject"]
    atomic: Literal["pass", "fix"]
    verifiable: Literal["pass", "flag"]
    reasons: dict[str, str] = Field(default_factory=dict)
    suggested_rewrite: str | None = None


class CritiqueBatch(BaseModel):
    """Wrapper for batch-mode critic output.

    `_JSON_OBJECT_RE = r"\\{.*\\}"` in llm.py captures a single JSON object, so
    a list[T] schema would not round-trip through StructuredRunnable. This
    wrapper holds the list under a named key so the existing parser works.
    """
    verdicts: list[CritiqueVerdict]


_CRITIC_RUBRIC = (
    "- nature: is this a REQUIREMENT OF THE SYSTEM being built, or part of the "
    "CLIENT DOCUMENT's own machinery? Classify:\n"
    "    requirement      = a capability or condition the SOFTWARE must provide "
    "(functional or non-functional).\n"
    "    meta_instruction = tells the bidder how to FILL or structure the document "
    "itself ('mark with X', 'complete the column', 'describe how'). KEY SIGNAL: the ACTOR is the bidder/offeror completing the PROPOSAL, not the system or its end users in operation. This holds EVEN IF phrased as an obligation ('the bidder must mark...', 'the offeror shall describe...', 'indicate whether...'). Obligation form alone does NOT make a statement a requirement.\n"
    "    definition       = a legend or codification inside the document ('support "
    "type A means...', 'priority 1 = critical').\n"
    "    evaluation       = a proposal-evaluation or administrative criterion "
    "('the proposal must include architecture...', 'attach evidence', 'this cost "
    "enters the economic offer'). Worked examples for nature: [1] 'The bidder shall mark with an X whether the offered solution covers each listed need.' -> nature=meta_instruction, fidelity=reject. The ACTION is marking the proposal form and the bidder is the ACTOR; the embedded claim that the solution covers needs is the document's own coverage grid, not a testable system capability. [2] 'Indicate which support type (A/B/C/NS) applies to each feature.' -> nature=meta_instruction, fidelity=reject (filling the proposal's classification column). [3] 'The system shall encrypt sensitive data such as bank details at rest.' -> nature=requirement (the system is the actor; encryption is a testable capability).\n"
    "    If nature != requirement, set fidelity=reject and state why in "
    "reasons.nature. Do NOT propose a rewrite or assess atomicity/verifiability "
    "for non-requirements — the item is out of scope, not broken.\n"
    "- fidelity: Does the source_span support the statement?\n"
    "    pass   = fully supported, captures the span's meaning.\n"
    "    fix    = supported but INCOMPLETE or ambiguous — the statement dropped "
    "a technical specific, an obligation verb, or a constraint present in the "
    "span. Provide suggested_rewrite that reincorporates the missing detail "
    "while staying atomic.\n"
    "    reject = the statement is NOT supported by the span (hallucinated or "
    "contradicts it).\n"
    "- atomic: exactly one requirement?\n"
    "    pass = single capability.\n"
    "    fix  = contains >1 requirement joined (needs split). Explain in "
    "reasons.atomic; suggested_rewrite (if any) covers ONE of the split parts.\n"
    "- verifiable: can an acceptance criterion / test be written?\n"
    "    pass = observable, testable.\n"
    "    flag = untestable as stated (e.g. 'must be fast', 'user-friendly'). "
    "Never reject for this — flag for human quantification.\n"
)

_CRITIC_RULES = (
    "Rules:\n"
    "- LANGUAGE: the suggested_rewrite MUST stay in the SAME LANGUAGE as the item's statement. Never translate a statement while refining it.\n"
    "- In any suggested_rewrite, PRESERVE every technical specific (named "
    "technologies, numbers, thresholds) and the EXACT obligation verb from the "
    "source_span. Do not paraphrase them away.\n"
    "- Prefer 'fix' over 'reject' when the statement is mostly right; reserve "
    "'reject' for genuine hallucination.\n"
    "- Base every judgment on the source_span text provided, not on outside "
    "knowledge.\n"
    "- Keep each reasons value to ONE short clause (max ~15 words). In "
    "suggested_rewrite, output ONLY the corrected statement text — never echo "
    "labels, markdown, or the original verbatim."
)

_CRITIC_SYSTEM = (
    "You are a REQUIREMENTS CRITIC for a software SRS. You review ONE "
    "requirement against its source_span — a VERBATIM quote from the client "
    "document that must back the statement. Apply these checks:\n\n"
    + _CRITIC_RUBRIC
    + "\n"
    + _CRITIC_RULES
    + "\nReturn ONLY the structured object."
)

_CRITIC_BATCH_SYSTEM = (
    "You are a REQUIREMENTS CRITIC for a software SRS. You are given MULTIPLE "
    "requirements, each identified by its ITEM_ID. For EACH item, apply the "
    "checks below independently and return one verdict per item_id. You MUST "
    "return a verdict for EVERY item_id provided — never omit an item, even if "
    "the verdict is all-pass.\n\n"
    + _CRITIC_RUBRIC
    + "\n"
    + _CRITIC_RULES
    + '\nReturn ONLY the structured object: {"verdicts": [{"item_id": "...", ...}, ...]}.'
)


def _format_conventions_block(rules: DocumentRules) -> str:
    """Build the DOCUMENT_CONVENTIONS USER-message block for the critic.

    Surface the document's own legend + glossary as KNOWN signals so the
    critic's ``nature`` filter does NOT reject them as ``definition`` or
    ``meta_instruction`` (which would force ``fidelity=reject`` and drop the
    item). Returns an empty string when there is nothing to surface (so the
    historical user message stays untouched for documents without rules).
    """
    if not rules or (
        not rules.priority_legend
        and not rules.glossary
        and not rules.scope_markers
        and not rules.priority_field_label
    ):
        return ""
    parts: list[str] = [
        "DOCUMENT_CONVENTIONS (KNOWN — do NOT classify as "
        "definition/meta_instruction these are the document's own signals):"
    ]
    if rules.priority_field_label:
        parts.append(
            f"- Priority field label: {rules.priority_field_label!r} "
            "(per-requirement labeled field that carries an explicit priority)."
        )
    if rules.priority_legend:
        legend = ", ".join(
            f"{e.label!r} (@ {e.source_span!r})" for e in rules.priority_legend
        )
        parts.append(f"- Priority legend: {legend}.")
    if rules.scope_markers:
        markers = ", ".join(repr(m) for m in rules.scope_markers)
        parts.append(f"- Scope markers (out-of-scope / future): {markers}.")
    if rules.glossary:
        terms = "; ".join(
            f"{term!r} = {definition!r}"
            for term, definition in rules.glossary.items()
        )
        parts.append(f"- Glossary: {terms}.")
    return "\n".join(parts)


def _parse_failure_verdict(
    item_id: str, attempts: int, exc: Exception
) -> CritiqueVerdict:
    """Sentinel verdict for items whose LLM response could not be parsed.

    Z.ai GLM occasionally truncates JSON mid-object (max_tokens hit) or wraps it
    oddly, so the greedy `_JSON_OBJECT_RE` captures an incomplete object and
    pydantic raises ValidationError. Retrying absorbs transient malformations;
    when every attempt fails we flag the item for human review instead of
    letting one bad response abort the whole `critique_all` gather.
    """
    return CritiqueVerdict(
        item_id=item_id,
        nature="requirement",  # unknown — assume requirement; human reviews
        fidelity="fix",
        atomic="pass",
        verifiable="flag",
        reasons={
            "parse_error": (
                f"Critic LLM returned malformed/unparseable JSON after "
                f"{attempts} attempts ({type(exc).__name__}); manual review "
                f"required."
            ),
        },
        suggested_rewrite=None,
    )


def _rate_limit_verdict(
    item_id: str, fails: int, exc: Exception,
) -> CritiqueVerdict:
    """Sentinel verdict for items that exhausted transient-error retries.

    Same shape as `_parse_failure_verdict` (short-circuits the refinement
    loop, lands in `flagged`) but `reasons` distinguishes the cause so stats
    can separate API-driven failures from critic-quality failures.
    """
    return CritiqueVerdict(
        item_id=item_id,
        nature="requirement",
        fidelity="fix",
        atomic="pass",
        verifiable="flag",
        reasons={
            "rate_limited": (
                f"Z.ai returned 429/transient after {fails} backoffs "
                f"({type(exc).__name__}); retry this item later."
            ),
        },
        suggested_rewrite=None,
    )


async def _judge_item(
    item: RawRequirement,
    neighbors: list[RawRequirement] | None = None,
    *,
    attempts: int = _JUDGE_ATTEMPTS,
    transient_retries: int = _TRANSIENT_RETRIES,
    rules: DocumentRules | None = None,
) -> CritiqueVerdict:
    """Single LLM critic pass over one item.

    Two retry paths:
    - Transient (429, 5xx, timeout, conn error): exponential backoff up to
      `transient_retries`, then a `rate_limited` sentinel — the API, not the
      critic, is at fault.
    - Parse (truncated/malformed JSON, ValidationError): up to `attempts`
      quick retries, then a `parse_error` sentinel.
    One bad response must not abort the whole batch.

    ``rules`` (optional) surfaces the document's own legend/glossary as KNOWN
    signals in the user message so the ``nature`` filter does not reject them
    as ``definition`` / ``meta_instruction``. Default None preserves the
    historical byte-identical user message.
    """
    llm = structured_llm(CritiqueVerdict)
    lines = [
        f"STATEMENT: {item.statement}",
        f"SOURCE_SPAN: {item.source_span}",
        f"SECTION: {item.section}",
    ]
    if neighbors:
        nbr = "; ".join(f"{n.id}: {n.statement}" for n in neighbors[:3])
        lines.append(f"NEARBY_ITEMS (context only, do NOT re-detect duplicates): {nbr}")
    conventions_block = _format_conventions_block(rules) if rules else ""
    if conventions_block:
        lines.append(conventions_block)
    user = "\n".join(lines)
    msgs = [("system", _CRITIC_SYSTEM), ("human", user)]
    parse_fails = 0
    transient_fails = 0
    while True:
        try:
            v = await llm.ainvoke(msgs)
            # Post-hoc override: the model-emitted item_id is unreliable in
            # per-item mode. Use the item's actual id so downstream stitching
            # never misses.
            return v.model_copy(update={"item_id": item.id})
        except Exception as exc:  # noqa: BLE001 — split below
            if _is_transient(exc):
                transient_fails += 1
                if transient_fails >= transient_retries:
                    return _rate_limit_verdict(item.id, transient_fails, exc)
                wait = transient_backoff_seconds(transient_fails)
                logger.warning(
                    "critic transient %s for %s; backoff %.1fs (%d/%d)",
                    type(exc).__name__, item.id, wait,
                    transient_fails, transient_retries,
                )
                await asyncio.sleep(wait)
            else:
                parse_fails += 1
                if parse_fails >= attempts:
                    return _parse_failure_verdict(item.id, parse_fails, exc)
                logger.warning(
                    "critic parse failed for %s (%s); retry (%d/%d)",
                    item.id, exc, parse_fails, attempts,
                )


async def _per_item_fallback(
    items: list[RawRequirement],
    neighbors_by_id: dict[str, list[RawRequirement]] | None = None,
    *,
    rules: DocumentRules | None = None,
) -> list[CritiqueVerdict]:
    """Run `_judge_item` concurrently for each item.

    Each item keeps its own retry+sentinel budget — the batch failure does not
    propagate. Used when the batch call fails (transient or parse exhausted)
    or when the batch response omits items.
    """
    if not items:
        return []

    async def _one(it: RawRequirement) -> CritiqueVerdict:
        return await _judge_item(
            it, (neighbors_by_id or {}).get(it.id), rules=rules
        )

    return list(await asyncio.gather(*[_one(it) for it in items]))


async def _judge_batch(
    items: list[RawRequirement],
    neighbors_by_id: dict[str, list[RawRequirement]] | None = None,
    *,
    max_tokens: int = BATCH_MAX_TOKENS,
    rules: DocumentRules | None = None,
) -> tuple[list[CritiqueVerdict], BatchStats]:
    """Batch-mode critic pass over M items in one LLM call.

    Decision tree (plan §13.D):
    - len == 0 → empty result + zero stats.
    - len == 1 → delegate to `_judge_item` (smoke path + loose last chunk).
      `batch_calls` stays 0 so the per-item call count assertions hold.
    - Otherwise build a batch user message with ITEM_ID/STATEMENT/... blocks
      and call the batch-typed structured LLM.

    Retry policy mirrors `_judge_item`:
    - Transient: exponential backoff (cap 60s) up to `_TRANSIENT_RETRIES`,
      then per-item fallback for ALL items in this batch.
    - Parse: up to `_BATCH_PARSE_RETRIES`, then per-item fallback.

    On success, detect omissions by `item_id` (the model may drop items from
    the middle of the batch — lost-in-the-middle on the OUTPUT side) and
    route the omitted items through `_judge_item` via `_per_item_fallback`.
    `item_id` is overridden post-hoc with the item's actual id so downstream
    stitching never misses.
    """
    stats = BatchStats()
    if not items:
        return [], stats
    if len(items) == 1:
        only = items[0]
        v = await _judge_item(
            only, (neighbors_by_id or {}).get(only.id), rules=rules
        )
        return [v], stats

    # Build the batch user message. Per-item blocks separated by `\n---\n`.
    # The DOCUMENT_CONVENTIONS block is appended ONCE after the last item so
    # the critic sees every item under the same known-signals context.
    blocks: list[str] = []
    for it in items:
        nbrs = (neighbors_by_id or {}).get(it.id)
        lines = [
            f"ITEM_ID: {it.id}",
            f"STATEMENT: {it.statement}",
            f"SOURCE_SPAN: {it.source_span}",
            f"SECTION: {it.section}",
        ]
        if nbrs:
            nbr = "; ".join(f"{n.id}: {n.statement}" for n in nbrs[:3])
            lines.append(
                f"NEARBY_ITEMS (context only, do NOT re-detect duplicates): {nbr}"
            )
        blocks.append("\n".join(lines))
    user = "\n---\n".join(blocks)
    conventions_block = _format_conventions_block(rules) if rules else ""
    if conventions_block:
        user = user + "\n---\n" + conventions_block
    msgs = [("system", _CRITIC_BATCH_SYSTEM), ("human", user)]

    llm = structured_llm(CritiqueBatch)
    parse_fails = 0
    transient_fails = 0
    batch: CritiqueBatch | None = None
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
                        "critic batch transient exhausted after %d retries; "
                        "falling back to per-item for %d items",
                        transient_fails, len(items),
                    )
                    fallback = await _per_item_fallback(
                        items, neighbors_by_id, rules=rules
                    )
                    stats.fallback += len(fallback)
                    return fallback, stats
                wait = transient_backoff_seconds(transient_fails)
                logger.warning(
                    "critic batch transient %s; backoff %.1fs (%d/%d)",
                    type(exc).__name__, wait, transient_fails, _TRANSIENT_RETRIES,
                )
                await asyncio.sleep(wait)
            else:
                parse_fails += 1
                if parse_fails >= _BATCH_PARSE_RETRIES:
                    logger.warning(
                        "critic batch parse failed after %d retries; "
                        "falling back to per-item for %d items",
                        parse_fails, len(items),
                    )
                    fallback = await _per_item_fallback(
                        items, neighbors_by_id, rules=rules
                    )
                    stats.fallback += len(fallback)
                    return fallback, stats
                logger.warning(
                    "critic batch parse failed (%s); retry (%d/%d)",
                    exc, parse_fails, _BATCH_PARSE_RETRIES,
                )

    # Success — detect omissions via item_id.
    by_id: dict[str, CritiqueVerdict] = {v.item_id: v for v in batch.verdicts}
    missing = [it for it in items if it.id not in by_id]
    if missing:
        logger.warning(
            "critic batch omitted %d/%d items; routing to per-item",
            len(missing), len(items),
        )
        omitted_verdicts = await _per_item_fallback(
            missing, neighbors_by_id, rules=rules
        )
        stats.omitted += len(missing)
        for it, v in zip(missing, omitted_verdicts):
            by_id[it.id] = v

    # Assemble in input order; override item_id post-hoc.
    out: list[CritiqueVerdict] = []
    for it in items:
        v = by_id.get(it.id)
        if v is None:
            # Edge case: still missing — synthesize a parse-failure sentinel.
            v = _parse_failure_verdict(
                it.id, 0, RuntimeError("missing from batch response"),
            )
        else:
            v = v.model_copy(update={"item_id": it.id})
        out.append(v)
    return out, stats


def _is_clean(v: CritiqueVerdict) -> bool:
    """True when the verdict needs no further action."""
    return (
        v.nature == "requirement"
        and v.fidelity == "pass"
        and v.atomic == "pass"
        and v.verifiable == "pass"
        and v.suggested_rewrite is None
    )


def _dangling_verdict(item: RawRequirement) -> CritiqueVerdict:
    """Programmatic verdict for a dangling parent intro (no LLM needed)."""
    return CritiqueVerdict(
        item_id=item.id,
        nature="requirement",
        fidelity="fix",
        atomic="pass",
        verifiable="flag",
        reasons={
            "fidelity": (
                "Statement references a list/enumeration (e.g. 'los siguientes') "
                "that was split into separate items. It is incomplete as a "
                "standalone requirement — merge into its children or drop "
                "(covered by them)."
            ),
            "verifiable": "Cannot test until the statement is self-contained.",
        },
        suggested_rewrite=None,  # cannot auto-rewrite without the child list
    )


def _apply_quality_flags(
    verdict: CritiqueVerdict, flags: list[ProgrammaticFinding]
) -> CritiqueVerdict:
    """Fusiona las señales deterministas de calidad en el veredicto del crítico.

    Shift-left (plan §Capa 2): los pre-checks programáticos ITEM-level
    (combinadores, términos vagos/absolutos, etc.) se aplican de forma
    AUTORITATIVA sobre el veredicto del crítico, sin depender de que el LLM los
    detecte. Cero llamadas LLM extra (la crítica ya se ejecutaba por ítem).

    - ``smell.combinator`` fuerza ``atomic="fix"`` (respalda el split que el
      crítico ya puede proponer).
    - ``smell.vague_term`` / ``smell.absolute`` / ``incose.unmeasurable_nfr``
      fuerzan ``verifiable="flag"`` (la cuantificación queda para el humano o
      el crítico LLM).
    - Las reglas informativas (negación, pronombre, modal, longitud, EARS) se
      registran en ``reasons["quality"]`` sin alterar las dimensiones.
    Sin flags -> veredicto sin tocar (idempotente).
    """
    if not flags:
        return verdict

    rule_ids = {f.rule_id for f in flags}
    atomic = "fix" if "smell.combinator" in rule_ids else verdict.atomic
    verifiable = (
        "flag"
        if rule_ids & {"smell.vague_term", "smell.absolute", "incose.unmeasurable_nfr"}
        else verdict.verifiable
    )

    reasons = dict(verdict.reasons)
    reasons["quality"] = "; ".join(f"{f.rule_id}: {f.message}" for f in flags)
    return verdict.model_copy(update={"atomic": atomic, "verifiable": verifiable, "reasons": reasons})


async def critique_item(
    item: RawRequirement,
    *,
    neighbors: list[RawRequirement] | None = None,
    max_iter: int = DEFAULT_MAX_ITER,
    _initial_verdict: CritiqueVerdict | None = None,
    rules: DocumentRules | None = None,
) -> tuple[RawRequirement | None, CritiqueVerdict]:
    """Generator-critic loop over one item.

    Returns (kept_item_or_None, verdict). kept is None when fidelity=reject.
    The statement may be rewritten in place (model_copy) when the critic
    proposes and confirms a fix across refinement rounds.

    `_initial_verdict` (optional): a pre-computed verdict that skips the first
    `_judge_item` call. When the batch layer already produced a verdict for
    this item, pass it here so the loop only does the refinement work (0 extra
    calls for a clean verdict, 1 extra call when the verdict asks for a fix).
    Default `None` preserves the historical per-item path byte-for-byte.

    ``rules`` (optional) threads the document conventions into every judge
    call so the ``nature`` filter does not flag the doc's legend/glossary as
    ``definition`` / ``meta_instruction``.
    """
    # Programmatic pre-check: dangling reference -> flag without LLM.
    if _has_dangling_reference(item.statement):
        return item, _dangling_verdict(item)

    current = item
    quality_flags = programmatic_findings_for_text(current.statement)
    verdict = _initial_verdict or await _judge_item(
        current, neighbors, rules=rules
    )
    verdict = _apply_quality_flags(verdict, quality_flags)
    refinements = 0
    while (not _is_clean(verdict)
           and verdict.fidelity != "reject"
           and verdict.suggested_rewrite
           and refinements < max_iter):
        current = current.model_copy(update={"statement": verdict.suggested_rewrite})
        verdict = await _judge_item(current, neighbors, rules=rules)
        # Recomputa las señales sobre el enunciado reescrito: si la
        # reescritura corrigió el defecto, la señal desaparece (idempotente).
        verdict = _apply_quality_flags(
            verdict, programmatic_findings_for_text(current.statement)
        )
        refinements += 1

    if verdict.fidelity == "reject":
        return None, verdict
    return current, verdict


@dataclass
class CritiqueResult:
    items: list[RawRequirement]                        # survivors (rewritten if fix applied)
    verdicts: dict[str, CritiqueVerdict] = field(default_factory=dict)
    rejected: list[str] = field(default_factory=list)   # fidelity=reject
    flagged: list[str] = field(default_factory=list)    # residual fix/flag after loop
    stats: dict = field(default_factory=dict)


async def critique_all(
    items: list[RawRequirement],
    *,
    neighbors_by_id: dict[str, list[RawRequirement]] | None = None,
    max_iter: int = DEFAULT_MAX_ITER,
    concurrency: int = DEFAULT_CONCURRENCY,
    batch_size: int = DEFAULT_BATCH_SIZE,
    rules: DocumentRules | None = None,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> CritiqueResult:
    """Run the critic over all items concurrently. Returns survivors + verdicts.

    Pipeline (plan §13.E):
    1. Chunk items into batches of `batch_size`.
    2. Per batch, pre-filter dangling items programmatically (no LLM) — they
       go straight to `critique_item` and land flagged.
    3. The rest go through `_judge_batch` (one LLM call per batch when M>1;
       per-item shortcut when M==1 — the smoke path).
    4. Each first-verdict is fed into `critique_item(_initial_verdict=v)` so
       the refinement loop only does the extra work when needed.
    5. Results stitched in input order; `verdicts` dict keyed by `it.id`.

    neighbors_by_id (optional): nearby items per id, for context only. Duplicate
    and contradiction detection itself lives in consolidation (Paso 4).
    """
    if not items:
        return CritiqueResult(items=[])

    for i, it in enumerate(items):
        if not it.id:
            it.id = f"raw-{i}"

    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    sem = asyncio.Semaphore(concurrency)
    agg = BatchStats()
    total = len(items)
    done = 0

    async def _one_batch(batch: list[RawRequirement]):
        nonlocal done
        # Pre-filter dangling programmatically (no LLM).
        dangling = [
            it for it in batch if _has_dangling_reference(it.statement)
        ]
        to_judge = [
            it for it in batch if not _has_dangling_reference(it.statement)
        ]
        results: list[tuple[RawRequirement, RawRequirement | None, CritiqueVerdict]] = []

        # Phase 1: ONE batch LLM call. The semaphore slot is held ONLY for the
        # batch call (not the per-item refinement below), so the `concurrency`
        # slots stay free for other batches' batch calls instead of blocking
        # behind up to `batch_size` sequential refinement calls each.
        first_verdicts: list[CritiqueVerdict] = []
        if to_judge:
            async with sem:
                fv, bstats = await _judge_batch(
                    to_judge, neighbors_by_id, rules=rules,
                )
            agg.batch_calls += bstats.batch_calls
            agg.omitted += bstats.omitted
            agg.fallback += bstats.fallback
            first_verdicts = fv

        # Phase 2: per-item refinement. Each critique_item acquires the
        # semaphore individually, so refinement runs concurrently across items
        # (clean verdicts do 0 LLM calls; non-clean do up to max_iter). Peak
        # concurrency stays bounded by `sem` — it is now a shared LLM-call
        # budget across all batches' batch calls AND refinements (the correct
        # model for a single upstream rate limit).
        async def _refine(it: RawRequirement, v: CritiqueVerdict):
            nbrs = (neighbors_by_id or {}).get(it.id)
            async with sem:
                return await critique_item(
                    it, neighbors=nbrs, max_iter=max_iter,
                    _initial_verdict=v, rules=rules,
                )

        if to_judge:
            refined = await asyncio.gather(
                *[_refine(it, v) for it, v in zip(to_judge, first_verdicts)]
            )
            for it, (kept, final_v) in zip(to_judge, refined):
                done += 1
                if done % 10 == 0 or done == total:
                    logger.info("critique progress: %d/%d", done, total)
                    if on_progress:
                        await on_progress(done, total)
                results.append((it, kept, final_v))

        # Dangling: no LLM (critique_item short-circuits to _dangling_verdict),
        # so no semaphore needed.
        for it in dangling:
            kept, v = await critique_item(it, max_iter=max_iter, rules=rules)
            done += 1
            if done % 10 == 0 or done == total:
                logger.info("critique progress: %d/%d", done, total)
                if on_progress:
                    await on_progress(done, total)
            results.append((it, kept, v))

        return results

    batch_results = await asyncio.gather(*[_one_batch(b) for b in batches])

    # Stitch results in input order.
    flat: dict[str, tuple[RawRequirement | None, CritiqueVerdict]] = {}
    for batch_res in batch_results:
        for it, kept, v in batch_res:
            flat[it.id] = (kept, v)

    survivors: list[RawRequirement] = []
    verdicts: dict[str, CritiqueVerdict] = {}
    rejected: list[str] = []
    flagged: list[str] = []
    n_rewritten = 0
    for it in items:
        kept, v = flat[it.id]
        verdicts[it.id] = v
        if kept is None:
            rejected.append(it.id)
            continue
        if kept.statement != it.statement:
            n_rewritten += 1
        survivors.append(kept)
        if not _is_clean(v):
            flagged.append(it.id)

    n_rate_limited = sum(1 for v in verdicts.values() if "rate_limited" in v.reasons)
    n_parse_error = sum(1 for v in verdicts.values() if "parse_error" in v.reasons)
    n_not_req = sum(1 for v in verdicts.values() if v.nature != "requirement")
    stats = {
        "input": len(items),
        "kept": len(survivors),
        "rejected": len(rejected),
        "flagged": len(flagged),
        "rewritten": n_rewritten,
        "rate_limited": n_rate_limited,
        "parse_error": n_parse_error,
        "not_a_requirement": n_not_req,
        "batch_calls": agg.batch_calls,
        "batch_omitted": agg.omitted,
        "per_item_fallback": agg.fallback,
    }
    logger.info(
        "critique: %d in -> %d kept, %d rejected, %d flagged "
        "(rate_limited=%d, parse_error=%d, nature_reject=%d), %d rewritten "
        "[batch: calls=%d, omitted=%d, per_item_fallback=%d]",
        stats["input"], stats["kept"], stats["rejected"],
        stats["flagged"], stats["rate_limited"], stats["parse_error"],
        stats["not_a_requirement"], stats["rewritten"],
        agg.batch_calls, agg.omitted, agg.fallback,
    )
    return CritiqueResult(
        items=survivors, verdicts=verdicts, rejected=rejected,
        flagged=flagged, stats=stats,
    )
