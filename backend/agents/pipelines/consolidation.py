"""Consolidation: dedup + contradiction detection across extracted requirements.

Two-level dedup (plan §3):
  1. Normalized exact: lowercase + accent-fold + collapse whitespace → trivial
     bucket; one representative per bucket.
  2. Semantic, TWO tiers:
     (A) cosine >= strict_duplicate_threshold → auto-merge (near-verbatim, safe).
     (B) borderline [duplicate_threshold, strict) → LLM duplicate judge. The
         embedding alone cannot separate "same requirement rephrased" from
         "distinct requirements sharing boilerplate framing" — the classic case
         is a split list: 'evidence of endpoint protection' vs 'evidence of
         email security' cluster at ~0.87 cosine but are DISTINCT requirements.
         Only pairs the judge confirms as real duplicates are merged.

Representative selection excludes DANGLING items: a parent intro left pointing
at a list that was split into separate items ("...los siguientes sistemas...")
must never become a cluster representative, or the cluster loses its specifics.
The dangling parent itself survives as a singleton for the critic (step 5) to
rewrite or drop — consolidation's job is to not LOSE real requirements.

Contradictions: among pairs with cosine >= contradict_threshold (same concern),
an LLM judges whether the two statements conflict. Conflicts are PROPOSED, never
auto-resolved — the human decides (plan: "el agente propone, el humano decide").

Consolidation does NOT mutate the input list semantically; it returns a result
with kept representatives, duplicate groups and contradiction pairs. Mutations
(merge/split/link) happen in the store layer with full revision history (§10.2).

Embeddings run HOST-SIDE with sentence-transformers (local model), same rationale
as Docling: client documents are confidential and must not leak to an embeddings
API. Torch is already a dependency. Lazy-imported + singleton.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from backend.agents.llm import build_llm, structured_llm
from backend.agents.pipelines.extraction import RawRequirement

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# Calibrated starting points — the golden set (step 8) pins the real values.
# 0.92 (plan's first guess) is too aggressive: paraphrases of the same
# requirement score ~0.6 with this model, near-verbatim ~0.9+. 0.85 catches
# near-verbatim + strong paraphrases; subtle paraphrases go to the LLM
# contradiction/duplicate judge rather than the embedding gate.
DEFAULT_DUPLICATE_THRESHOLD = 0.85
# Borderline duplicates in [dup, strict) are ambiguous (see module docstring):
# they go to the LLM duplicate judge. >= strict auto-merge without LLM.
DEFAULT_STRICT_DUPLICATE_THRESHOLD = 0.95
DEFAULT_CONTRADICT_THRESHOLD = 0.60
DEFAULT_MAX_CONTRADICT_CANDIDATES = 200
DEFAULT_MAX_DUPLICATE_CANDIDATES = 200

_EMBEDDER = None


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

@dataclass
class DuplicateGroup:
    """Proposed merge: kept_id is the representative; members are merged into it.
    The store's merge_requirements tool performs the actual union of sources."""
    kept_id: str
    member_ids: list[str]
    kept_statement: str


@dataclass
class ContradictionPair:
    a_id: str
    b_id: str
    reason: str
    confidence: float


@dataclass
class ConsolidationResult:
    items: list[RawRequirement]                 # one representative per cluster
    duplicates: list[DuplicateGroup]            # proposed merges (soft)
    contradictions: list[ContradictionPair]     # proposed conflicts (soft)
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Normalization + exact dedup (no LLM, no embeddings)
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Lowercase + accent-fold + strip punctuation + collapse whitespace.
    Only the dedup KEY uses this — the statement itself is never modified."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)  # punctuation -> space
    return re.sub(r"\s+", " ", text).strip()


def exact_dedup(items: list[RawRequirement]) -> list[list[RawRequirement]]:
    """Group by normalized statement. Returns buckets; caller keeps the
    highest-confidence item per bucket as representative."""
    buckets: dict[str, list[RawRequirement]] = {}
    for it in items:
        buckets.setdefault(normalize(it.statement), []).append(it)
    return list(buckets.values())


# ---------------------------------------------------------------------------
# Embeddings (lazy singleton, host-side)
# ---------------------------------------------------------------------------

def _get_embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise RuntimeError(
                "sentence-transformers is required for consolidation. Add it to "
                "requirements.txt."
            ) from exc
        _EMBEDDER = SentenceTransformer(DEFAULT_EMBEDDING_MODEL)
    return _EMBEDDER


def embed_texts(texts: list[str]):
    """Return L2-normalized embeddings as a numpy array (rows = texts).
    Normalized so cosine similarity = dot product."""
    import numpy as np  # local: numpy is a transitive dep of torch/sentence-transformers
    model = _get_embedder()
    vecs = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    return np.asarray(vecs, dtype="float32")


# ---------------------------------------------------------------------------
# Clustering (union-find over pairs >= threshold)
# ---------------------------------------------------------------------------

def _cluster_by_similarity(
    items: list[RawRequirement],
    sim,
    threshold: float,
):
    """Union-find over upper-triangle pairs with cosine >= threshold.
    Returns list of clusters (each a list of (index, item)).

    Kept as a public utility (single-threshold clustering). The consolidate
    orchestrator uses the two-tier _cluster_duplicates instead, because a single
    threshold either loses borderline distinct requirements or keeps false dups.
    """
    import numpy as np
    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    iu, ju = np.triu_indices(n, k=1)
    for i, j in zip(iu.tolist(), ju.tolist()):
        if sim[i, j] >= threshold:
            union(i, j)

    clusters: dict[int, list[tuple[int, RawRequirement]]] = {}
    for idx in range(n):
        clusters.setdefault(find(idx), []).append((idx, items[idx]))
    return list(clusters.values())


# Deictic forward references: the statement points to a list/enumeration that
# should follow INSIDE it. When the list was split into separate items (plan §2
# atomicity), the parent intro is left dangling. "etc"/"entre otros" are NOT
# here — those mean non-exhaustive and the statement still stands alone.
_DANGLING_RE = re.compile(
    r"\b(los\s+siguientes|las\s+siguientes|el\s+siguiente|la\s+siguiente|"
    r"the\s+following|como\s+sigue|as\s+follows|a\s+continuaci[oó]n|"
    r"listed\s+below|se\s+enumera|detallad[oa]s?\s+a\b|enumerad[oa]s?)\b",
    re.IGNORECASE,
)


def _has_dangling_reference(statement: str) -> bool:
    """True if the statement references a list/enumeration that is NOT in itself.

    Detects deictic forward references ('los siguientes', 'the following',
    'como sigue', ...) NOT followed by an inline enumeration. Self-contained
    statements are NOT dangling: 'soporta los siguientes formatos: PDF, DOCX'
    has the list inline. A split-list parent intro IS dangling: 'incorpora los
    siguientes sistemas de proteccion basica en su organizacion' (list extracted
    into separate items, nothing follows here).

    Such an item must not become a cluster representative — it would absorb its
    specific children and leave the cluster pointing at a missing list.
    """
    m = _DANGLING_RE.search(statement)
    if not m:
        return False
    tail = statement[m.end():]
    # Inline enumeration after the deictic word -> self-contained, not dangling.
    if re.search(r"\d+\s*[.\):]\s*\S|\b[a-hA-H]\s*[)\.:]\s*\S", tail):
        return False
    after_colon = tail.split(":", 1)
    if len(after_colon) == 2 and "," in after_colon[1]:
        return False
    return True


def _representative(cluster: list[tuple[int, RawRequirement]]) -> RawRequirement:
    """Pick the cluster representative.

    Prefer a NON-dangling item (self-contained statement); among those, highest
    confidence. A dangling item wins only if every member dangles (degenerate
    cluster) — fallback to max confidence so the cluster still resolves.
    """
    non_dangling = [p for p in cluster if not _has_dangling_reference(p[1].statement)]
    pool = non_dangling if non_dangling else cluster
    return max(pool, key=lambda pair: pair[1].confidence)[1]


def _cluster_duplicates(
    items: list[RawRequirement],
    sim,
    strict_threshold: float,
    confirmed_pairs: list[tuple[int, int]],
):
    """Union-find: auto-merge pairs with cosine >= strict_threshold (near-
    verbatim, safe) PLUS the LLM-confirmed borderline pairs. Pairs in the
    borderline band that the judge REJECTED are not unioned → stay distinct."""
    import numpy as np
    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    iu, ju = np.triu_indices(n, k=1)
    for i, j in zip(iu.tolist(), ju.tolist()):
        if sim[i, j] >= strict_threshold:
            union(i, j)
    for i, j in confirmed_pairs:
        union(i, j)

    clusters: dict[int, list[tuple[int, RawRequirement]]] = {}
    for idx in range(n):
        clusters.setdefault(find(idx), []).append((idx, items[idx]))
    return list(clusters.values())


# ---------------------------------------------------------------------------
# Contradiction judgment (LLM, bounded by candidate count)
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field  # noqa: E402


class ContradictionVerdict(BaseModel):
    a_id: str
    b_id: str
    contradicts: bool
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)


class ContradictionReport(BaseModel):
    verdicts: list[ContradictionVerdict]


_CONTRADICTION_SYSTEM = (
    "You are a REQUIREMENTS CONSISTENCY JUDGE. For each pair of requirements "
    "from the same project, decide whether they CONTRADICT.\n"
    "Two requirements contradict if satisfying one makes the other unsatisfiable, "
    "or they assert mutually exclusive properties about the same concern "
    "(e.g. 'must use MySQL' vs 'must use PostgreSQL').\n"
    "Merely similar, overlapping or duplicate requirements do NOT contradict — "
    "those are duplicates, handled separately. 'In scope' vs 'out of scope' is "
    "a scope conflict, not a contradiction unless one forbids what the other "
    "mandates.\n"
    "Return one verdict per input pair, echoing a_id and b_id exactly. If a pair "
    "is absent from the input, do not invent it."
)


def _contradiction_candidates(items, sim, threshold: float, cap: int):
    """Pairs (i<j) with cosine >= threshold, capped to the top-`cap` by similarity."""
    import numpy as np
    n = len(items)
    iu, ju = np.triu_indices(n, k=1)
    sims = sim[iu, ju]
    mask = sims >= threshold
    if not mask.any():
        return []
    idx = np.argsort(-sims[mask])[:cap]
    ii = iu[mask][idx]
    jj = ju[mask][idx]
    return [(int(i), int(j)) for i, j in zip(ii.tolist(), jj.tolist())]


async def _judge_contradictions(
    items: list[RawRequirement],
    candidates: list[tuple[int, int]],
) -> list[ContradictionPair]:
    if not candidates:
        return []
    llm = structured_llm(ContradictionReport)
    lines = []
    for n, (i, j) in enumerate(candidates, start=1):
        lines.append(
            f"[{n}] A ({items[i].id}): {items[i].statement}\n"
            f"    B ({items[j].id}): {items[j].statement}"
        )
    user = "Judge each pair below:\n\n" + "\n\n".join(lines)
    report = await llm.ainvoke([("system", _CONTRADICTION_SYSTEM), ("human", user)])
    out: list[ContradictionPair] = []
    for v in report.verdicts:
        if v.contradicts:
            out.append(ContradictionPair(
                a_id=v.a_id, b_id=v.b_id, reason=v.reason, confidence=v.confidence,
            ))
    return out


# ---------------------------------------------------------------------------
# Duplicate judgment (LLM, for borderline pairs the embedding can't resolve)
# ---------------------------------------------------------------------------

class DuplicateVerdict(BaseModel):
    a_id: str
    b_id: str
    is_duplicate: bool
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)


class DuplicateReport(BaseModel):
    verdicts: list[DuplicateVerdict]


_DUPLICATE_SYSTEM = (
    "You are a REQUIREMENTS DUPLICATE JUDGE. For each pair, decide whether they "
    "are the SAME requirement (duplicates) or DISTINCT requirements.\n"
    "Duplicates: one is a paraphrase, restatement, or strict subset of the other "
    "— they demand the SAME capability (e.g. 'auth with user+password' vs "
    "'authenticate using username and password').\n"
    "NOT duplicates: the two share boilerplate framing or belong to the same "
    "concern but demand DIFFERENT capabilities. In particular, items split from "
    "a numbered list are DISTINCT: 'evidence of endpoint protection' vs "
    "'evidence of email security' are two different requirements even though "
    "both are about supplying security evidence. A general intro ('evidence of "
    "the following systems') vs a specific child ('evidence of endpoint "
    "protection') is a parent/child relation, NOT a duplicate — mark "
    "is_duplicate=false.\n"
    "Return one verdict per input pair, echoing a_id and b_id exactly. If a pair "
    "is absent from the input, do not invent it."
)


def _duplicate_candidates(items, sim, lo: float, hi: float, cap: int):
    """Pairs (i<j) with lo <= cosine < hi, capped to top-`cap` by similarity.
    This borderline band is what the embedding cannot resolve alone."""
    import numpy as np
    n = len(items)
    iu, ju = np.triu_indices(n, k=1)
    sims = sim[iu, ju]
    mask = (sims >= lo) & (sims < hi)
    if not mask.any():
        return []
    idx = np.argsort(-sims[mask])[:cap]
    ii = iu[mask][idx]
    jj = ju[mask][idx]
    return [(int(i), int(j)) for i, j in zip(ii.tolist(), jj.tolist())]


async def _judge_duplicates(
    items: list[RawRequirement],
    candidates: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Judge borderline duplicate candidates. Returns the index pairs confirmed
    as REAL duplicates (to be unioned). Distinct pairs are left alone so both
    requirements survive."""
    if not candidates:
        return []
    llm = structured_llm(DuplicateReport)
    lines = []
    for n, (i, j) in enumerate(candidates, start=1):
        lines.append(
            f"[{n}] A ({items[i].id}): {items[i].statement}\n"
            f"    B ({items[j].id}): {items[j].statement}"
        )
    user = "Judge each pair below:\n\n" + "\n\n".join(lines)
    report = await llm.ainvoke([("system", _DUPLICATE_SYSTEM), ("human", user)])
    id_to_idx = {items[i].id: i for i in range(len(items))}
    confirmed: list[tuple[int, int]] = []
    for v in report.verdicts:
        if v.is_duplicate and v.a_id in id_to_idx and v.b_id in id_to_idx:
            confirmed.append((id_to_idx[v.a_id], id_to_idx[v.b_id]))
    return confirmed


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def consolidate(
    items: list[RawRequirement],
    *,
    duplicate_threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
    strict_duplicate_threshold: float = DEFAULT_STRICT_DUPLICATE_THRESHOLD,
    contradict_threshold: float = DEFAULT_CONTRADICT_THRESHOLD,
    max_contradict_candidates: int = DEFAULT_MAX_CONTRADICT_CANDIDATES,
    max_duplicate_candidates: int = DEFAULT_MAX_DUPLICATE_CANDIDATES,
) -> ConsolidationResult:
    """Exact dedup → two-tier semantic dedup → contradiction candidates → judge.

    Two-tier dedup:
      (A) cosine >= strict_duplicate_threshold  → auto-merge (near-verbatim).
      (B) [duplicate_threshold, strict)          → LLM judge; merge only confirmed.
    Borderline pairs the judge rejects stay DISTINCT (the split-list case).

    Assigns stable ids (raw-<n>) to any item missing one so references in
    DuplicateGroup / ContradictionPair survive serialization.
    """
    if not items:
        return ConsolidationResult(items=[], duplicates=[], contradictions=[])

    for i, it in enumerate(items):
        if not it.id:
            it.id = f"raw-{i}"

    # 1. exact dedup → one representative per normalized bucket
    buckets = exact_dedup(items)
    reps: list[RawRequirement] = [
        max(b, key=lambda it: it.confidence) for b in buckets
    ]
    stats = {"input": len(items), "after_exact": len(reps)}

    # 2. embeddings + similarity matrix
    vectors = embed_texts([r.statement for r in reps])
    sim = vectors @ vectors.T

    # 3. duplicate clusters → proposed merges (two tiers, see docstring).
    dup_candidates = _duplicate_candidates(
        reps, sim, duplicate_threshold, strict_duplicate_threshold,
        max_duplicate_candidates,
    )
    confirmed_dup_pairs = await _judge_duplicates(reps, dup_candidates)
    clusters = _cluster_duplicates(
        reps, sim, strict_duplicate_threshold, confirmed_dup_pairs,
    )
    kept: list[RawRequirement] = []
    duplicates: list[DuplicateGroup] = []
    for cluster in clusters:
        rep = _representative(cluster)
        kept.append(rep)
        if len(cluster) > 1:
            duplicates.append(DuplicateGroup(
                kept_id=rep.id,
                member_ids=[m.id for _, m in cluster],
                kept_statement=rep.statement,
            ))
    stats["dup_candidates_borderline"] = len(dup_candidates)
    stats["dup_confirmed"] = len(confirmed_dup_pairs)
    stats["after_semantic"] = len(kept)
    stats["duplicate_groups"] = len(duplicates)

    # 4. contradiction candidates (cosine >= contradict_threshold) → LLM judge
    candidates = _contradiction_candidates(
        reps, sim, contradict_threshold, max_contradict_candidates,
    )
    contradictions = await _judge_contradictions(reps, candidates)
    stats["contradict_candidates"] = len(candidates)
    stats["contradictions"] = len(contradictions)

    logger.info(
        "consolidate: %d in -> %d after exact -> %d after semantic, "
        "%d dup groups (%d borderline judged, %d confirmed), %d contradictions",
        stats["input"], stats["after_exact"], stats["after_semantic"],
        stats["duplicate_groups"], stats["dup_candidates_borderline"],
        stats["dup_confirmed"], stats["contradictions"],
    )
    return ConsolidationResult(
        items=kept, duplicates=duplicates, contradictions=contradictions, stats=stats,
    )
