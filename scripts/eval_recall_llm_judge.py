"""Re-evaluate recall/precision with an LLM entailment judge.

The default pipeline metric (cosine similarity @ 0.70) measures lexical /
surface overlap, not semantic containment. That under-counts matches when
the pipeline rewrites or decomposes a requirement: the survivor is correct
but its wording drifts below the threshold and reads as a miss.

Two-stage matching (standard RAG eval pattern):
  1. Cosine pre-filter: top-K golden candidates per survivor AND top-K
     survivors per golden (bidirectional, so a low-similarity golden still
     gets its best survivor judged).
  2. LLM judge: for each (survivor, candidate) pair, decide entailment /
     containment BY MEANING, not wording.

Reads survivors from a pipeline dump markdown so any past run can be
re-evaluated without re-running the pipeline. Reports LLM-judge recall /
precision next to the cosine@0.7 baseline, plus the disagreement pairs:

  - recovered FN: cosine said miss (< thresh) but judge matched  -> the
    pipeline was right, the metric was wrong.
  - cosine FP:   cosine said match (>= thresh) but judge said no  -> the
    metric was over-counting.

Usage:
    .venv/bin/python scripts/eval_recall_llm_judge.py
    .venv/bin/python scripts/eval_recall_llm_judge.py --dump path/to/dump.md
"""
from __future__ import annotations

import argparse
import asyncio
import random
import re
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# resolve backend imports when run from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agents.llm import structured_llm                      # noqa: E402
from backend.agents.pipelines._resilience import is_transient      # noqa: E402
from backend.agents.pipelines.consolidation import embed_texts     # noqa: E402

# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
DEFAULT_DUMP = Path("docs/testing/pipeline_e2e_resultado.md")
XLSX = Path("docs/testing/1. Requerimientos tecnicos- funcionales.xlsx")
TOP_K = 5            # pre-filter candidates per side
CONCURRENCY = 6
THRESH = 0.70        # cosine threshold (mirrors test_pipeline_e2e.py for comparison)
JUDGE_RETRIES = 2
SEED = 42
PROGRESS_EVERY = 200


# --------------------------------------------------------------------------- #
# data loading
# --------------------------------------------------------------------------- #
def load_ground_truth() -> list[str]:
    """Golden descriptions from the xlsx (rows 71+, col 3, both sheets)."""
    import openpyxl
    wb = openpyxl.load_workbook(XLSX, data_only=False)
    descs: list[str] = []
    for sheet in ("NECESIDADES FUNCIONALES", "NECESIDADES TÉCNICAS"):
        ws = wb[sheet]
        for r in ws.iter_rows(min_row=71, max_row=ws.max_row, values_only=True):
            desc = r[3] if len(r) > 3 else None
            if desc and str(desc).strip():
                descs.append(str(desc).strip())
    return descs


_CODE_RE = re.compile(r"^###\s+[✓⚠]\s+(REQ-\d+)", re.MULTILINE)
_STMT_RE = re.compile(r"^\*\*Statement:\*\*\s*(.+)$", re.MULTILINE)


def parse_survivors(dump_path: Path) -> list[tuple[str, str]]:
    """Extract (code, statement) pairs from the pipeline dump markdown."""
    text = dump_path.read_text(encoding="utf-8")
    codes = _CODE_RE.findall(text)
    stmts = _STMT_RE.findall(text)
    if len(codes) != len(stmts):
        raise ValueError(
            f"dump parse mismatch: {len(codes)} codes vs {len(stmts)} statements "
            f"(dump format changed?)"
        )
    return list(zip(codes, stmts))


# --------------------------------------------------------------------------- #
# judge schema + prompt
# --------------------------------------------------------------------------- #
class JudgeVerdict(BaseModel):
    matched: bool = Field(
        description=(
            "True if PIPELINE refers to the SAME real-world requirement as GOLDEN "
            "(any semantic overlap: exact / subset / superset / partial). "
            "False ONLY when they express genuinely different needs."
        )
    )
    relation: Literal["exact", "subset", "superset", "partial", "none"]
    reason: str = Field(description="One short sentence justifying the verdict by meaning.")


_JUDGE_SYSTEM = """You are a REQUIREMENTS MATCHING JUDGE.

You receive two requirement statements:
- PIPELINE: extracted by an automated pipeline from a client document.
- GOLDEN: from the human-annotated ground truth of the same document.

Decide whether PIPELINE refers to the SAME real-world requirement as GOLDEN.
"Same" includes any semantic overlap:
- exact:   same requirement, same scope.
- subset:  PIPELINE is a piece / atomic decomposition of GOLDEN (GOLDEN broader).
- superset: PIPELINE broadens GOLDEN (PIPELINE broader).
- partial: they share the same core need but each adds distinct aspects.

Set matched=true for exact / subset / superset / partial.
Set matched=false (relation=none) ONLY when they express genuinely different
needs with no overlap in the requirement they express.

Judge by MEANING, not wording. Paraphrase, atomic decomposition, and different
vocabulary MUST NOT cause a false "no". But do not conflate distinct needs that
merely share a domain word (for example "login" and "logout" are different).

Return ONLY the structured object."""


def _judge_user(pipeline_stmt: str, golden_stmt: str) -> str:
    return f"PIPELINE:\n{pipeline_stmt}\n\nGOLDEN:\n{golden_stmt}"


# --------------------------------------------------------------------------- #
# judge execution
# --------------------------------------------------------------------------- #
async def _judge_pair(runnable, pipeline_stmt: str, golden_stmt: str) -> JudgeVerdict:
    msgs = [("system", _JUDGE_SYSTEM), ("human", _judge_user(pipeline_stmt, golden_stmt))]
    last: Exception | None = None
    for attempt in range(JUDGE_RETRIES + 1):
        try:
            return await runnable.ainvoke(msgs)
        except Exception as exc:  # noqa: BLE001 — retry any failure, conservative default after
            last = exc
            if is_transient(exc):
                await asyncio.sleep(min(2 ** attempt, 30))
                continue
            # parse error: retry once more, then default
            if attempt < JUDGE_RETRIES:
                await asyncio.sleep(1.0)
                continue
            break
    # exhausted: do NOT inflate recall — default to no-match, log the failure
    return JudgeVerdict(
        matched=False, relation="none",
        reason=f"judge-error: {type(last).__name__ if last else 'unknown'}",
    )


async def judge_all(
    pairs: list[tuple[int, int, str, str, float]],
    on_progress,
) -> list[tuple[int, int, float, JudgeVerdict]]:
    runnable = structured_llm(JudgeVerdict)
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async def _one(pair):
        nonlocal done
        s_idx, g_idx, s_stmt, g_stmt, cos = pair
        async with sem:
            v = await _judge_pair(runnable, s_stmt, g_stmt)
        done += 1
        if done % PROGRESS_EVERY == 0:
            on_progress(done, len(pairs))
        return (s_idx, g_idx, cos, v)

    return await asyncio.gather(*[_one(p) for p in pairs])


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path, default=DEFAULT_DUMP,
                        help="pipeline dump markdown to re-evaluate")
    args = parser.parse_args()

    import numpy as np

    survivors = parse_survivors(args.dump)
    gt = load_ground_truth()
    print(f"dump:    {args.dump}")
    print(f"survivors: {len(survivors)} | golden: {len(gt)}")

    print("embedding ...")
    s_vecs = embed_texts([s for _, s in survivors])
    g_vecs = embed_texts(gt)
    sim = s_vecs @ g_vecs.T  # rows = survivors, cols = golden

    # ---- cosine baseline (identical to test_pipeline_e2e.py) ----
    best_per_gt = sim.max(axis=0)
    best_per_ex = sim.max(axis=1)
    cos_recall = float((best_per_gt >= THRESH).sum()) / len(gt)
    cos_precision = float((best_per_ex >= THRESH).sum()) / len(survivors)
    print(f"cosine@{THRESH}: recall={cos_recall:.1%}, precision={cos_precision:.1%}")

    # ---- bidirectional cosine pre-filter ----
    # survivor -> top-K golden
    pair_set: set[tuple[int, int]] = set()
    for i in range(len(survivors)):
        for j in np.argsort(sim[i])[-TOP_K:]:
            pair_set.add((i, int(j)))
    # golden -> top-K survivors (covers low-similarity goldens whose best
    # survivor ranks them outside that survivor's own top-K)
    for j in range(len(gt)):
        for i in np.argsort(sim[:, j])[-TOP_K:]:
            pair_set.add((int(i), j))

    pairs = [
        (i, j, survivors[i][1], gt[j], float(sim[i, j]))
        for (i, j) in pair_set
    ]
    print(f"judge pairs: {len(pairs)} (bidirectional top-{TOP_K}), concurrency={CONCURRENCY}")

    def on_progress(done, total):
        print(f"  judged {done}/{total}")

    results = await judge_all(pairs, on_progress)

    # ---- aggregate ----
    matched_golden: set[int] = set()
    matched_survivor: set[int] = set()
    recovered_fn: list[tuple[int, int, float, JudgeVerdict]] = []   # cosine miss, judge match
    cosine_fp: list[tuple[int, int, float, JudgeVerdict]] = []      # cosine match, judge no
    judge_errors = 0

    for s_idx, g_idx, cos, v in results:
        if "judge-error" in v.reason:
            judge_errors += 1
        if v.matched:
            matched_golden.add(g_idx)
            matched_survivor.add(s_idx)
            # survivor's BEST cosine to any golden was below thresh -> metric missed it
            if best_per_ex[s_idx] < THRESH:
                recovered_fn.append((s_idx, g_idx, cos, v))
        else:
            # this was the survivor's top cosine match and looked like a hit
            if cos >= THRESH and abs(cos - best_per_ex[s_idx]) < 1e-6:
                cosine_fp.append((s_idx, g_idx, cos, v))

    llm_recall = len(matched_golden) / len(gt)
    llm_precision = len(matched_survivor) / len(survivors)

    print()
    print("=== LLM-JUDGE vs COSINE ===")
    print(f"  recall:     {len(matched_golden)}/{len(gt)} = {llm_recall:.1%}   "
          f"(cosine: {cos_recall:.1%}, delta {llm_recall - cos_recall:+.1%})")
    print(f"  precision:  {len(matched_survivor)}/{len(survivors)} = {llm_precision:.1%}   "
          f"(cosine: {cos_precision:.1%}, delta {llm_precision - cos_precision:+.1%})")
    print(f"  recovered FN (cosine miss -> judge match): {len(recovered_fn)}")
    print(f"  cosine FP   (cosine match -> judge no):    {len(cosine_fp)}")
    print(f"  judge errors (defaulted to no-match):      {judge_errors}")

    # ---- validation sample for human spot-check ----
    random.seed(SEED)
    sample = random.sample(results, min(30, len(results)))
    print()
    print("=== JUDGE SAMPLE (30 random pairs — spot-check the judge) ===")
    for s_idx, g_idx, cos, v in sample:
        flag = "MATCH" if v.matched else "no   "
        print(f"  [{flag} {v.relation:8s} cos={cos:.2f}] S={survivors[s_idx][0]} "
              f"{survivors[s_idx][1][:68]!r}")
        print(f"      G[{g_idx:>3}]: {gt[g_idx][:68]!r}")

    # ---- recovered matches (the recall delta — most informative) ----
    print()
    print(f"=== RECOVERED (cosine<{THRESH} but judge=match) — first 15, lowest cosine first ===")
    for s_idx, g_idx, cos, v in sorted(recovered_fn, key=lambda x: x[2])[:15]:
        print(f"  cos={cos:.2f} {v.relation:8s} S={survivors[s_idx][0]} "
              f"{survivors[s_idx][1][:62]!r}")
        print(f"      G[{g_idx:>3}]: {gt[g_idx][:62]!r}")
        print(f"      reason: {v.reason[:90]}")


if __name__ == "__main__":
    asyncio.run(main())
