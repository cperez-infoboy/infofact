"""Smoke test for consolidation (step 4). No LLM key needed — the contradiction
judge is skipped; only embeddings + clustering + exact dedup are exercised."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agents.pipelines.consolidation import (
    consolidate,
    embed_texts,
    exact_dedup,
    normalize,
    DEFAULT_DUPLICATE_THRESHOLD,
)
from backend.agents.pipelines.extraction import RawRequirement

# --- normalize / exact dedup (pure Python) ---
assert normalize("El  Sístéma   debe.") == "el sistema debe"
b1 = exact_dedup([
    RawRequirement(statement="El sistema debe autenticar usuarios.", source_span="x", section="s", confidence=0.9),
    RawRequirement(statement="El sistema debe autenticar usuarios!", source_span="x", section="s", confidence=0.7),
    RawRequirement(statement="El sistema debe registrar ventas.", source_span="x", section="s", confidence=0.8),
])
assert len(b1) == 2, f"expected 2 buckets, got {len(b1)}"
print("normalize + exact_dedup OK (accent-fold + whitespace + dup bucketing)")

# --- embeddings + cosine (downloads model on first run) ---
print("loading embedding model (first run downloads ~120MB)...")
statements = [
    "El sistema debe autenticar a los usuarios mediante usuario y clave.",
    "El sistema debe autenticar a los usuarios con usuario y clave.",      # near-verbatim dup
    "El sistema debe usar una base de datos PostgreSQL.",                  # different concern
    "El informe mensual debe enviarse por correo electronico.",            # unrelated
    "Los usuarios deben iniciar sesion con su usuario y contrasena.",      # paraphrase (calibration probe)
]
vecs = embed_texts(statements)
import numpy as np
sim = vecs @ vecs.T
print("similarity matrix (rounded):")
for row in sim:
    print("  " + "  ".join(f"{v:.2f}" for v in row))

assert sim[0, 1] > DEFAULT_DUPLICATE_THRESHOLD, (
    f"near-verbatim pair must clear dup threshold: {sim[0,1]:.3f}"
)
assert sim[0, 3] < 0.5, f"unrelated pair must be low: {sim[0,3]:.3f}"
print(f"near-verbatim(0,1)={sim[0,1]:.3f} >= {DEFAULT_DUPLICATE_THRESHOLD}  OK")
print(f"unrelated(0,3)={sim[0,3]:.3f} < 0.5  OK")
print(f"paraphrase(0,4)={sim[0,4]:.3f}  (calibration probe: needs LLM judge, not the embedding gate)")

# --- consolidate end-to-end (without LLM: patch both judges) ---
import backend.agents.pipelines.consolidation as cons
async def _noop_judge(items, candidates):
    print(f"(contradiction judge skipped: {len(candidates)} candidates)")
    return []
cons._judge_contradictions = _noop_judge
async def _noop_dup_judge(items, candidates):
    print(f"(duplicate judge skipped: {len(candidates)} candidates)")
    return []
cons._judge_duplicates = _noop_dup_judge

items = [
    RawRequirement(statement=statements[i], source_span="x", section="s", confidence=0.9 - i * 0.05)
    for i in range(len(statements))
]
import asyncio
res = asyncio.run(consolidate(items))
print("stats:", res.stats)
assert res.stats["after_exact"] == 5, "no exact dups among these"
assert len(res.duplicates) == 1, f"expected 1 dup group (near-verbatim), got {len(res.duplicates)}"
assert len(res.items) == 4, f"expected 4 kept (5 - 1 merged), got {len(res.items)}"
print(f"consolidate OK: 5 in -> {len(res.items)} kept, {len(res.duplicates)} dup group(s)")
