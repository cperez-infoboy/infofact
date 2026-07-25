"""Smoke test for critique (step 5). No LLM key — _judge_item is patched with
canned verdicts to exercise the loop, reject path, dangling pre-check and batch.

Per-item tests use batch_size=1 so _judge_batch delegates to _judge_item and
the historical call-count assertions still hold. Batch tests append new
scenarios (happy path, omission, parse failure) per plan §13.H.
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agents.pipelines.critique import (
    critique_item,
    critique_all,
    _has_dangling_reference,
    CritiqueVerdict,
    CritiqueBatch,
)
from backend.agents.pipelines.extraction import RawRequirement
import backend.agents.pipelines.critique as crt

_REAL_JUDGE = crt._judge_item   # capture before any patch; sentinel test needs the real one


def _pass_verdict(item_id: str = "x") -> CritiqueVerdict:
    return CritiqueVerdict(
        item_id=item_id,
        nature="requirement",
        fidelity="pass", atomic="pass", verifiable="pass",
        reasons={}, suggested_rewrite=None,
    )


# --- dangling detection (programmatic, no LLM) ---
assert _has_dangling_reference(
    "...incorpora los siguientes sistemas de proteccion basica en su organizacion."
), "split-list parent intro must dangle"
assert not _has_dangling_reference(
    "Soporta los siguientes formatos: PDF, DOCX, XLSX."
), "inline-enumeration statement must NOT dangle"
assert not _has_dangling_reference("El sistema debe usar PostgreSQL.")
print("dangling detection OK (parent dangles, inline list + plain do not)")


# --- critique_item rewrite loop (patched judge: fix-then-pass) ---
async def _fix_then_pass(item, neighbors=None):
    if "corta" in item.statement:  # pre-rewrite: statement is incomplete
        return CritiqueVerdict(
            item_id=item.id,
            nature="requirement",
            fidelity="fix", atomic="pass", verifiable="pass",
            reasons={"fidelity": "dropped the specific DB + version"},
            suggested_rewrite="El sistema debe usar PostgreSQL version 15.",
        )
    return _pass_verdict(item.id)


crt._judge_item = _fix_then_pass
item = RawRequirement(
    statement="El sistema debe usar una base de datos corta.",
    source_span="base de datos PostgreSQL version 15",
    section="s", confidence=0.9, id="r1",
)
kept, v = asyncio.run(critique_item(item))
assert kept is not None, "fixable item must be kept"
assert kept.statement == "El sistema debe usar PostgreSQL version 15.", kept.statement
assert v.fidelity == "pass"
print("critique_item rewrite loop OK:", kept.statement)


# --- reject path (hallucination) ---
async def _reject(item, neighbors=None):
    return CritiqueVerdict(
        item_id=item.id,
        nature="requirement",
        fidelity="reject", atomic="pass", verifiable="pass",
        reasons={"fidelity": "statement not supported by span"}, suggested_rewrite=None,
    )


crt._judge_item = _reject
bad = RawRequirement(
    statement="hallucinated requirement", source_span="otra cosa",
    section="s", confidence=0.9, id="r2",
)
kept2, v2 = asyncio.run(critique_item(bad))
assert kept2 is None, "rejected item must return None"
assert v2.fidelity == "reject"
print("critique_item reject path OK (kept=None)")


# --- dangling path (programmatic, judge NOT called) ---
calls = {"n": 0}


async def _counting_pass(item, neighbors=None):
    calls["n"] += 1
    return _pass_verdict(item.id)


crt._judge_item = _counting_pass
dangling = RawRequirement(
    statement="El aliado debe incorporar los siguientes sistemas de proteccion basica en su organizacion.",
    source_span="lista completa con 5 items",
    section="s", confidence=0.9, id="r3",
)
kept3, v3 = asyncio.run(critique_item(dangling))
assert kept3 is not None, "dangling item survives (flagged, not dropped)"
assert v3.fidelity == "fix"
assert v3.suggested_rewrite is None
assert calls["n"] == 0, "dangling pre-check must skip the LLM judge entirely"
print("critique_item dangling path OK (no LLM call, flagged fix)")


# --- critique_all per-item (batch_size=1, all pass) ---
async def _pass(item, neighbors=None):
    return _pass_verdict(item.id)


crt._judge_item = _pass
items = [
    RawRequirement(statement=f"req {i}", source_span="span", section="s",
                   confidence=0.9, id=f"b{i}")
    for i in range(5)
]
# batch_size=1 → _judge_batch delegates to _judge_item (patched). Historical
# behavior + counts preserved.
res = asyncio.run(critique_all(items, batch_size=1))
assert res.stats["kept"] == 5
assert res.stats["rejected"] == 0
assert res.stats["flagged"] == 0
print("critique_all (batch_size=1) OK:", res.stats)


# --- parse-failure sentinel (regression: real _judge_item + structured_llm that
#     always raises). One malformed/truncated GLM JSON must NOT abort the batch. ---
crt._judge_item = _REAL_JUDGE          # restore the real judge (retry + sentinel)
_pf_calls = {"n": 0}


class _FailingRunnable:
    """Stand-in for StructuredRunnable whose LLM truncates/malforms every call."""

    async def ainvoke(self, msgs, config=None, **kwargs):
        _pf_calls["n"] += 1
        raise ValueError("simulated truncated JSON: EOF while parsing object")


def _failing_structured_llm(schema, *, temperature: float = 0.0):
    return _FailingRunnable()


crt.structured_llm = _failing_structured_llm
pf_items = [
    RawRequirement(statement="req uno", source_span="span", section="s",
                   confidence=0.9, id="pf1"),
    RawRequirement(statement="req dos", source_span="span", section="s",
                   confidence=0.9, id="pf2"),
]
# batch_size=1 → per-item path; the batch-typed structured_llm is NOT invoked,
# so the count stays at _JUDGE_ATTEMPTS=3 x 2 items = 6 (historical baseline).
pf_res = asyncio.run(critique_all(pf_items, concurrency=2, batch_size=1))
assert pf_res.stats["kept"] == 2, "sentinel items survive (flagged, not dropped)"
assert pf_res.stats["flagged"] == 2, "parse-failure items must be flagged"
assert pf_res.stats["rejected"] == 0
assert "parse_error" in pf_res.verdicts["pf1"].reasons, pf_res.verdicts["pf1"].reasons
# default _JUDGE_ATTEMPTS=3, 2 items, no refinement (sentinel has no rewrite) -> 6 calls
assert _pf_calls["n"] == 6, (
    f"expected 3 attempts x 2 items = 6 LLM calls, got {_pf_calls['n']}"
)
print(
    "critique_all parse-failure sentinel OK:", pf_res.stats,
    "(retries:", _pf_calls["n"], "LLM calls for 2 items)",
)


# --- rate-limit sentinel: _judge_item must distinguish 429 from parse_error ---
_rl_calls = {"n": 0}


class _RateLimitRunnable:
    """Stand-in whose LLM keeps hitting 429.

    Uses a dynamically-named exception so _is_transient's defensive
    type-name branch fires without importing a real openai.RateLimitError
    (which needs a fabricated httpx.Response to construct).
    """

    async def ainvoke(self, msgs, config=None, **kwargs):
        _rl_calls["n"] += 1
        exc_cls = type("RateLimitError", (Exception,), {})
        raise exc_cls("429 rate limit reached")


def _rate_limit_structured_llm(schema, *, temperature: float = 0.0):
    return _RateLimitRunnable()


crt.structured_llm = _rate_limit_structured_llm
rl_items = [
    RawRequirement(statement="req rl uno", source_span="span", section="s",
                   confidence=0.9, id="rl1"),
]
# _TRANSIENT_RETRIES=8 exp backoffs sum to ~3 min; collapse asyncio.sleep for smoke.
_orig_sleep = crt.asyncio.sleep


async def _no_sleep(*a, **kw):
    pass


crt.asyncio.sleep = _no_sleep
try:
    rl_res = asyncio.run(critique_all(rl_items, concurrency=2, batch_size=1))
finally:
    crt.asyncio.sleep = _orig_sleep
assert rl_res.stats["kept"] == 1, "rate-limited items survive (flagged, not dropped)"
assert rl_res.stats["flagged"] == 1, "rate-limited items must be flagged"
assert rl_res.stats["rejected"] == 0
assert "rate_limited" in rl_res.verdicts["rl1"].reasons, rl_res.verdicts["rl1"].reasons
# default _TRANSIENT_RETRIES=8, 1 item, no refinement -> 8 calls
assert _rl_calls["n"] == 8, (
    f"expected 8 transient retries x 1 item = 8 LLM calls, got {_rl_calls['n']}"
)
assert rl_res.stats["rate_limited"] == 1, rl_res.stats
assert rl_res.stats["parse_error"] == 0, rl_res.stats
print(
    "critique_all rate-limit sentinel OK:", rl_res.stats,
    "(retries:", _rl_calls["n"], "LLM calls for 1 item)",
)


# ---------------------------------------------------------------------------
# Batch tests (plan §13.H)
# ---------------------------------------------------------------------------

# Restore real judge + structured_llm so _judge_batch uses the real per-item
# fallback path when items are omitted or the batch call fails.
crt._judge_item = _REAL_JUDGE


class _DualRunnable:
    """Distinguishes batch vs per-item by counting ITEM_ID: occurrences.

    Modes:
    - "happy": batch returns a verdict per item_id.
    - "omit_last": batch drops the last item_id (simulates lost-in-the-middle).
    - "fail_batch": batch call raises ValueError on every attempt.
    """

    def __init__(self, mode: str):
        self.mode = mode
        self.batch_calls = 0
        self.per_item_calls = 0

    async def ainvoke(self, msgs, config=None, **kwargs):
        user = next(m[1] for m in msgs if m[0] == "human")
        ids = re.findall(r"ITEM_ID: (\S+)", user)
        if len(ids) > 1:
            return self._batch(ids)
        else:
            return self._per_item(ids[0] if ids else "x")

    def _batch(self, ids):
        self.batch_calls += 1
        if self.mode == "fail_batch":
            raise ValueError("simulated batch parse failure")
        verdicts = []
        omit_last = self.mode == "omit_last"
        for i, item_id in enumerate(ids):
            if omit_last and i == len(ids) - 1:
                continue
            verdicts.append(_pass_verdict(item_id))
        return CritiqueBatch(verdicts=verdicts)

    def _per_item(self, item_id):
        self.per_item_calls += 1
        return _pass_verdict(item_id)


def _dual_structured_llm(runnable):
    def _factory(schema, *, temperature: float = 0.0):
        return runnable
    return _factory


# --- Batch test 1: happy path, 10 items M=5 → 2 batch calls, 0 fallback ---
crt.asyncio.sleep = _no_sleep  # just in case any backoff fires; harmless when nothing raises
happy = _DualRunnable("happy")
crt.structured_llm = _dual_structured_llm(happy)
happy_items = [
    RawRequirement(statement=f"req {i}", source_span="span", section="s",
                   confidence=0.9, id=f"h{i}")
    for i in range(10)
]
happy_res = asyncio.run(critique_all(happy_items, batch_size=5, concurrency=2))
assert happy_res.stats["kept"] == 10, happy_res.stats
assert happy_res.stats["rejected"] == 0, happy_res.stats
assert happy_res.stats["batch_calls"] == 2, (
    f"expected 2 batch calls for 10 items at M=5, got {happy_res.stats['batch_calls']}"
)
assert happy_res.stats["per_item_fallback"] == 0, happy_res.stats
assert happy_res.stats["batch_omitted"] == 0, happy_res.stats
assert happy.batch_calls == 2, happy.batch_calls
assert happy.per_item_calls == 0, happy.per_item_calls
print("batch happy path OK:", happy_res.stats)


# --- Batch test 2: omission (model drops last verdict) → batch_omitted >= 1 ---
omit = _DualRunnable("omit_last")
crt.structured_llm = _dual_structured_llm(omit)
omit_items = [
    RawRequirement(statement=f"req {i}", source_span="span", section="s",
                   confidence=0.9, id=f"o{i}")
    for i in range(5)
]
omit_res = asyncio.run(critique_all(omit_items, batch_size=5, concurrency=2))
assert omit_res.stats["kept"] == 5, omit_res.stats
assert omit_res.stats["batch_calls"] == 1, omit_res.stats
assert omit_res.stats["batch_omitted"] >= 1, omit_res.stats
# Omitted item went through per-item fallback.
assert omit.per_item_calls >= 1, omit.per_item_calls
print("batch omission path OK:", omit_res.stats)


# --- Batch test 3: batch parse failure → all items via per-item fallback ---
fail = _DualRunnable("fail_batch")
crt.structured_llm = _dual_structured_llm(fail)
fail_items = [
    RawRequirement(statement=f"req {i}", source_span="span", section="s",
                   confidence=0.9, id=f"f{i}")
    for i in range(5)
]
fail_res = asyncio.run(critique_all(fail_items, batch_size=5, concurrency=2))
assert fail_res.stats["kept"] == 5, fail_res.stats
assert fail_res.stats["per_item_fallback"] == 5, (
    f"expected 5 per-item fallbacks after batch parse failure, got "
    f"{fail_res.stats['per_item_fallback']}"
)
# Batch retries: _BATCH_PARSE_RETRIES=2 before fallback.
assert fail.batch_calls == 2, fail.batch_calls
assert fail.per_item_calls == 5, fail.per_item_calls
print("batch parse-failure fallback OK:", fail_res.stats)

crt.asyncio.sleep = _orig_sleep  # restore in case anything follows

print("\nALL critique smoke checks PASSED")
