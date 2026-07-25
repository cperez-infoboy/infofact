"""Smoke test for classification (step 6). No LLM key — _classify_item and
_decompose_item are patched with canned decisions to exercise both passes,
the decomposition gate, and the batch stats.

Per-item tests use batch_size=1 so _classify_batch delegates to _classify_item
and the historical call-count assertions still hold. Batch tests append new
scenarios (happy path, omission, parse failure) per plan §13.H.
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agents.pipelines.classification import (
    classify_all,
    ClassificationDecision,
    ClassificationBatch,
    DecomposedItem,
)
from backend.agents.pipelines.extraction import RawRequirement
from backend.models.requirement import Priority, ReqType
import backend.agents.pipelines.classification as clf

_REAL_CLASSIFY = clf._classify_item   # capture before any patch; sentinel test needs the real one


def _plain_decision(item_id: str = "x") -> ClassificationDecision:
    return ClassificationDecision(
        item_id=item_id,
        type=ReqType.FUNCTIONAL,
        priority=Priority.MUST,
        rationale="plain functional must",
        decomposition_needed=False,
    )


# --- Pass A only (no item needs decomposition) ---
async def _plain(item):
    return _plain_decision(item.id)


clf._classify_item = _plain
clf._decompose_item = lambda *a, **k: asyncio.sleep(0, result=[])  # never called

items = [
    RawRequirement(statement=f"req {i}", source_span="span", section="s",
                   confidence=0.9, id=f"c{i}")
    for i in range(4)
]
res = asyncio.run(classify_all(items, decompose=True, batch_size=1))
assert res.stats["input"] == 4
assert res.stats["classified"] == 4
assert res.stats["decomposed_items"] == 0, "no item flagged -> 0 decomposed"
assert res.stats["sub_items"] == 0
assert res.stats["priority_dist"] == {"must": 4}
assert res.stats["type_dist"] == {"functional": 4}
assert res.decisions["c2"].priority == Priority.MUST
print("Pass A (classify only) OK:", res.stats)


# --- Pass A + B (one high-level item triggers decomposition) ---
_decisions = {
    "d0": ClassificationDecision(
        item_id="d0",
        type=ReqType.SECURITY, priority=Priority.SHOULD,
        rationale="high-level security",
        decomposition_needed=True,
    ),
    "d1": ClassificationDecision(
        item_id="d1",
        type=ReqType.PERFORMANCE, priority=Priority.MUST,
        rationale="operational latency",
        decomposition_needed=False,
    ),
}


async def _mixed(item):
    return _decisions[item.id]


async def _two_parts(item):
    return [
        DecomposedItem(statement=f"{item.id} auth sub", rationale="authn"),
        DecomposedItem(statement=f"{item.id} crypto sub", rationale="cipher"),
    ]


clf._classify_item = _mixed
clf._decompose_item = _two_parts

items2 = [
    RawRequirement(statement="El sistema debe ser seguro.", source_span="seguro",
                   section="s", confidence=0.9, id="d0"),
    RawRequirement(statement="latencia p95 < 200ms", source_span="p95",
                   section="s", confidence=0.9, id="d1"),
]
res2 = asyncio.run(classify_all(items2, decompose=True, batch_size=1))
assert res2.stats["classified"] == 2
assert res2.stats["decomposed_items"] == 1, "only d0 flagged"
assert res2.stats["sub_items"] == 2
assert "d0" in res2.decompositions
assert "d1" not in res2.decompositions, "d1 not flagged -> not decomposed"
assert len(res2.decompositions["d0"]) == 2
assert res2.decisions["d1"].type == ReqType.PERFORMANCE
assert res2.stats["priority_dist"] == {"should": 1, "must": 1}
assert res2.stats["type_dist"] == {"security": 1, "performance": 1}
print("Pass A+B (decompose flagged subset) OK:", res2.stats)


# --- decompose=False skips Pass B entirely ---
clf._classify_item = _mixed
calls = {"n": 0}


async def _counting_decompose(item):
    calls["n"] += 1
    return []


clf._decompose_item = _counting_decompose
res3 = asyncio.run(classify_all(items2, decompose=False, batch_size=1))
assert res3.stats["decomposed_items"] == 0
assert res3.stats["sub_items"] == 0
assert calls["n"] == 0, "decompose=False must not call _decompose_item"
print("decompose=False skips Pass B OK (0 decompose calls)")


# --- empty input short-circuit ---
res4 = asyncio.run(classify_all([]))
assert res4.stats["input"] == 0
assert res4.decisions == {}
print("empty input OK")


# --- enum round-trip via Pydantic (schema accepts closed enums) ---
d = ClassificationDecision(item_id="x", type=ReqType.COMPLIANCE, priority=Priority.WONT)
assert d.type == ReqType.COMPLIANCE
assert d.priority.value == "wont"
print("enum round-trip OK (compliance/wont)")


# --- parse-failure sentinel (regression: real _classify_item + structured_llm
#     that always raises). One malformed/truncated GLM JSON must NOT abort the
#     batch. ---
clf._classify_item = _REAL_CLASSIFY      # restore the real classifier (retry + sentinel)
_pf_calls = {"n": 0}


class _FailingRunnable:
    """Stand-in for StructuredRunnable whose LLM truncates/malforms every call."""

    async def ainvoke(self, msgs, config=None, **kwargs):
        _pf_calls["n"] += 1
        raise ValueError("simulated truncated JSON: EOF while parsing object")


def _failing_structured_llm(schema, *, temperature: float = 0.0):
    return _FailingRunnable()


clf.structured_llm = _failing_structured_llm
pf_items = [
    RawRequirement(statement="req uno", source_span="span", section="s",
                   confidence=0.9, id="pf1"),
    RawRequirement(statement="req dos", source_span="span", section="s",
                   confidence=0.9, id="pf2"),
]
pf_res = asyncio.run(classify_all(pf_items, decompose=False, concurrency=2,
                                  batch_size=1))
assert pf_res.stats["classified"] == 2, "sentinel items survive (not dropped)"
assert pf_res.stats["parse_error"] == 2, pf_res.stats
assert pf_res.stats["rate_limited"] == 0, pf_res.stats
assert pf_res.decisions["pf1"].rationale.startswith("[parse_error]"), (
    pf_res.decisions["pf1"].rationale
)
assert pf_res.decisions["pf1"].type == ReqType.CONSTRAINT
assert pf_res.decisions["pf1"].priority == Priority.MUST
assert pf_res.decisions["pf1"].decomposition_needed is False, (
    "parse-failure sentinel must NOT trigger Pass B cascade"
)
# default _CLASSIFY_ATTEMPTS=3, 2 items -> 6 LLM calls
assert _pf_calls["n"] == 6, (
    f"expected 3 attempts x 2 items = 6 LLM calls, got {_pf_calls['n']}"
)
print(
    "classify_all parse-failure sentinel OK:", pf_res.stats,
    "(retries:", _pf_calls["n"], "LLM calls for 2 items)",
)


# --- rate-limit sentinel: _classify_item must distinguish 429 from parse_error ---
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


clf.structured_llm = _rate_limit_structured_llm
rl_items = [
    RawRequirement(statement="req rl uno", source_span="span", section="s",
                   confidence=0.9, id="rl1"),
]
# _TRANSIENT_RETRIES=8 exp backoffs sum to ~3 min; collapse asyncio.sleep for smoke.
_orig_sleep = clf.asyncio.sleep


async def _no_sleep(*a, **kw):
    pass


clf.asyncio.sleep = _no_sleep
try:
    rl_res = asyncio.run(classify_all(rl_items, decompose=False, concurrency=2,
                                      batch_size=1))
finally:
    clf.asyncio.sleep = _orig_sleep
assert rl_res.stats["classified"] == 1, "rate-limited items survive (not dropped)"
assert rl_res.stats["rate_limited"] == 1, rl_res.stats
assert rl_res.stats["parse_error"] == 0, rl_res.stats
assert rl_res.decisions["rl1"].rationale.startswith("[rate_limited]"), (
    rl_res.decisions["rl1"].rationale
)
# default _TRANSIENT_RETRIES=8, 1 item -> 8 calls
assert _rl_calls["n"] == 8, (
    f"expected 8 transient retries x 1 item = 8 LLM calls, got {_rl_calls['n']}"
)
print(
    "classify_all rate-limit sentinel OK:", rl_res.stats,
    "(retries:", _rl_calls["n"], "LLM calls for 1 item)",
)


# ---------------------------------------------------------------------------
# Batch tests (plan §13.H)
# ---------------------------------------------------------------------------

clf._classify_item = _REAL_CLASSIFY  # restore for the batch path


class _DualRunnable:
    """Distinguishes batch vs per-item by counting ITEM_ID: occurrences.

    Modes:
    - "happy": batch returns a decision per item_id.
    - "omit_last": batch drops the last item_id.
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
        decisions = []
        omit_last = self.mode == "omit_last"
        for i, item_id in enumerate(ids):
            if omit_last and i == len(ids) - 1:
                continue
            decisions.append(_plain_decision(item_id))
        return ClassificationBatch(decisions=decisions)

    def _per_item(self, item_id):
        self.per_item_calls += 1
        return _plain_decision(item_id)


def _dual_structured_llm(runnable):
    def _factory(schema, *, temperature: float = 0.0):
        return runnable
    return _factory


# --- Batch test 1: happy path, 10 items M=5 → 2 batch calls, 0 fallback ---
happy = _DualRunnable("happy")
clf.structured_llm = _dual_structured_llm(happy)
clf._decompose_item = lambda *a, **k: asyncio.sleep(0, result=[])  # no decomposition
happy_items = [
    RawRequirement(statement=f"req {i}", source_span="span", section="s",
                   confidence=0.9, id=f"h{i}")
    for i in range(10)
]
happy_res = asyncio.run(classify_all(happy_items, decompose=False, batch_size=5,
                                     concurrency=2))
assert happy_res.stats["classified"] == 10, happy_res.stats
assert happy_res.stats["batch_calls"] == 2, (
    f"expected 2 batch calls for 10 items at M=5, got {happy_res.stats['batch_calls']}"
)
assert happy_res.stats["per_item_fallback"] == 0, happy_res.stats
assert happy_res.stats["batch_omitted"] == 0, happy_res.stats
assert happy.batch_calls == 2, happy.batch_calls
assert happy.per_item_calls == 0, happy.per_item_calls
print("batch happy path OK:", happy_res.stats)


# --- Batch test 2: omission → batch_omitted >= 1, per-item routing for missing ---
omit = _DualRunnable("omit_last")
clf.structured_llm = _dual_structured_llm(omit)
omit_items = [
    RawRequirement(statement=f"req {i}", source_span="span", section="s",
                   confidence=0.9, id=f"o{i}")
    for i in range(5)
]
omit_res = asyncio.run(classify_all(omit_items, decompose=False, batch_size=5,
                                    concurrency=2))
assert omit_res.stats["classified"] == 5, omit_res.stats
assert omit_res.stats["batch_calls"] == 1, omit_res.stats
assert omit_res.stats["batch_omitted"] >= 1, omit_res.stats
assert omit.per_item_calls >= 1, omit.per_item_calls
print("batch omission path OK:", omit_res.stats)


# --- Batch test 3: batch parse failure → all items via per-item fallback ---
fail = _DualRunnable("fail_batch")
clf.structured_llm = _dual_structured_llm(fail)
fail_items = [
    RawRequirement(statement=f"req {i}", source_span="span", section="s",
                   confidence=0.9, id=f"f{i}")
    for i in range(5)
]
fail_res = asyncio.run(classify_all(fail_items, decompose=False, batch_size=5,
                                    concurrency=2))
assert fail_res.stats["classified"] == 5, fail_res.stats
assert fail_res.stats["per_item_fallback"] == 5, (
    f"expected 5 per-item fallbacks after batch parse failure, got "
    f"{fail_res.stats['per_item_fallback']}"
)
assert fail.batch_calls == 2, fail.batch_calls      # _BATCH_PARSE_RETRIES = 2
assert fail.per_item_calls == 5, fail.per_item_calls
print("batch parse-failure fallback OK:", fail_res.stats)


# --- Batch test 4: _decompose_item failure doesn't crash classify_all.
# Patch structured_llm so decompose calls fail; _classify_item is patched to
# flag every item for decomposition. The hardened _decompose_item returns []
# (graceful degradation: items classified but not decomposed). ---
clf._classify_item = _REAL_CLASSIFY

class _ClassifyOK_DecomposeFail:
    """Returns a valid decision for per-item classify; raises for decompose."""

    async def ainvoke(self, msgs, config=None, **kwargs):
        user = next(m[1] for m in msgs if m[0] == "human")
        # Decompose user msg has PARENT_STATEMENT; classify has STATEMENT.
        if "PARENT_STATEMENT" in user:
            raise ValueError("simulated decompose failure")
        # Per-item classify path.
        ids = re.findall(r"ITEM_ID: (\S+)", user)
        item_id = ids[0] if ids else "x"
        return ClassificationDecision(
            item_id=item_id,
            type=ReqType.SECURITY, priority=Priority.SHOULD,
            rationale="high-level security",
            decomposition_needed=True,
        )


clf.structured_llm = lambda schema, **kw: _ClassifyOK_DecomposeFail()
clf.asyncio.sleep = _no_sleep
dec_items = [
    RawRequirement(statement=f"high-level req {i}", source_span="span",
                   section="s", confidence=0.9, id=f"dec{i}")
    for i in range(3)
]
dec_res = asyncio.run(classify_all(dec_items, decompose=True, batch_size=1,
                                   concurrency=2))
clf.asyncio.sleep = _orig_sleep
assert dec_res.stats["classified"] == 3, dec_res.stats
# All items flagged decomposition_needed, but decompose failed gracefully.
assert dec_res.stats["decomposed_items"] == 0, dec_res.stats
assert dec_res.stats["sub_items"] == 0, dec_res.stats
print("_decompose_item hardening OK (no crash, items stay classified):",
      dec_res.stats)


print("\nALL classification smoke checks PASSED")
