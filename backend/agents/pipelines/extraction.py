"""Extraction: turn document chunks into raw requirements.

LLM-facing Pydantic schemas, per-chunk structured extraction with a MANDATORY
verbatim source_span, a programmatic span-verification gate (no LLM), a gap pass
that re-scans high-likelihood sections which yielded nothing, an implicit pass
that surfaces assumptions, and a structure-map enrichment pass (the only place
the LLM touches the parser-built skeleton).

Design rules enforced here (see plan section 2):
  - source_span is mandatory and must be a VERBATIM quote; items whose span is
    not found in the parsed text are marked span_verified=False / status
    'unverified' — kept for review, never silently accepted.
  - One idea per statement; the critic (step 5) splits any that slip through.
  - Explicit and implicit requirements are extracted in SEPARATE passes so
    assumptions do not inflate recall.

Structured output uses function_calling (ChatOpenAI default). Any
OpenAI-compatible endpoint that supports tool calling works (default target:
Z.ai GLM). If a provider lacks tool calling, switch with_structured_output to
method='json_schema' or 'json_mode'.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel, Field

from backend.agents.llm import build_llm, structured_llm
from backend.agents.pipelines.ingestion import Chunk, StructureMap

logger = logging.getLogger(__name__)

SPAN_VERIFY_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# LLM-facing schemas
# ---------------------------------------------------------------------------

class RawRequirement(BaseModel):
    """One requirement as extracted from a chunk, before consolidation."""
    statement: str = Field(
        description="A single atomic requirement. One idea only; never join "
                    "two requirements with 'and'/'y'.",
    )
    source_span: str = Field(
        description="VERBATIM quote copied from the chunk that supports the "
                    "statement. Must appear in the chunk exactly. Mandatory.",
    )
    section: str = Field(
        description="The section / annex / table heading the requirement "
                    "comes from (use the breadcrumb).",
    )
    page: int | None = Field(
        default=None, description="Page number if known from the chunk.",
    )
    explicit: bool = Field(
        default=True,
        description="True if stated verbatim; False if inferred (assumption).",
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Self-rated extraction confidence, 0..1.",
    )
    # Pipeline-filled (never produced by the LLM):
    id: str = ""                    # pipeline identity (raw-<n>); empty until assigned
    document_id: str = ""
    rationale: str = ""           # set by the implicit pass
    span_verified: bool = False   # set by verify_spans
    status: str = "draft"         # draft | unverified | validated | ...


class ChunkExtraction(BaseModel):
    """Extraction result for one chunk."""
    items: list[RawRequirement]
    chunk_complete: bool = Field(
        description="True if the entire chunk was processed (not truncated).",
    )


class ImplicitRequirement(BaseModel):
    statement: str
    rationale: str = Field(description="Why this is implied by the source.")
    source_span: str = Field(
        description="The span that IMPLIES it — mandatory even if indirect.",
    )
    section: str
    page: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class ImplicitExtraction(BaseModel):
    items: list[ImplicitRequirement]


class SectionAnnotation(BaseModel):
    id: str
    summary: str = Field(description="One-line summary of the section.")
    req_likelihood: Literal["high", "medium", "low", "none"]
    is_boilerplate: bool = Field(
        description="True for cover, TOC, glossary, references, legal boilerplate.",
    )


class StructureMapAnnotation(BaseModel):
    sections: list[SectionAnnotation]
    glossary: dict[str, str] = Field(
        default_factory=dict,
        description="Terms defined in the document and their definitions.",
    )


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_EXTRACTOR_SYSTEM = (
    "You are a REQUIREMENTS EXTRACTOR for software development. Given a document "
    "chunk (with its section breadcrumb and project context), extract EXPLICIT "
    "requirements stated in the text.\n"
    "Rules:\n"
    "- LANGUAGE: write every statement in the SAME LANGUAGE as the source_span. Do NOT translate, summarize in another language, or normalize to English. A Spanish source yields a Spanish statement; an English source yields an English statement. Preserve original domain terminology verbatim.\n"
    "- One idea per statement. If a sentence joins two requirements with "
    "'and'/'y', split them into separate items.\n"
    "- The statement must capture EVERY technical detail present in the "
    "source_span: named technologies (e.g. Kubernetes, WebSockets, PostgreSQL), "
    "quantitative values, thresholds, and parenthetical specifics. Never "
    "paraphrase these away — if the source mentions them, the statement MUST "
    "include them. 'Atomic' means one requirement, not a truncated one.\n"
    "- Preserve the EXACT obligation verb of the source (e.g. 'suministrar "
    "evidencia', 'garantizar', 'permitir', 'cumplir'). Do NOT collapse two "
    "verbs into a simpler one — if the source says 'debe suministrar evidencia "
    "que incorpora X', the obligation is to SUPPLY EVIDENCE of X, not merely "
    "to incorporate X. The statement must reflect the real obligation.\n"
    "- A numbered or bulleted list where each entry is a distinct obligation "
    "produces ONE item per entry (each with its own source_span pointing at "
    "the list). Additionally, any trailing note, caveat, cadence, or condition "
    "attached to the list (e.g. 'Nota: ... debe presentarse anualmente', "
    "'siempre que', 'salvo') is a SEPARATE requirement — emit it as its own "
    "item with explicit=false if it is an implicit condition, or explicit=true "
    "if it is directly stated.\n"
    "- source_span is a VERBATIM quote copied from the chunk (never a "
    "paraphrase). It MUST appear in the chunk exactly as written.\n"
    "- Do NOT infer implicit requirements here; those are a separate pass.\n"
    "- If the chunk is boilerplate (cover, TOC, references, legal) and contains "
    "no requirements, return items=[] and chunk_complete=true.\n"
    "Return ONLY the structured object."
)

_IMPLICIT_SYSTEM = (
    "TASK: infer IMPLICIT requirements from the excerpt.\n"
    "Implicit = not stated verbatim, but necessary for the system to satisfy the "
    "stated requirements, or mandated by domain/standard context.\n"
    "- LANGUAGE: write every statement in the SAME LANGUAGE as the source_span. Do NOT translate, summarize in another language, or normalize to English. A Spanish source yields a Spanish statement; an English source yields an English statement. Preserve original domain terminology verbatim.\n"
    "For each item set: statement, rationale (why it is implied), source_span "
    "(the span that IMPLIES it — mandatory even if indirect), section, "
    "confidence (0..1).\n"
    "Do NOT invent requirements without a textual anchor. If none, return items=[]."
)

_ANNOTATOR_SYSTEM = (
    "You annotate a document's section skeleton (built by a parser, not you). "
    "For each section give: a one-line summary, req_likelihood "
    "(high|medium|low|none — how likely it carries actual requirements), and "
    "is_boilerplate (cover, TOC, glossary, references, legal). Also extract any "
    "glossary: terms explicitly defined in the document mapped to their "
    "definition. Return ONLY the structured object."
)


def _project_header(project_name: str, project_description: str) -> str:
    desc = project_description.strip() or "(no description provided)"
    return f"PROJECT: {project_name}\nDESCRIPTION: {desc}"


def _structured_llm(schema: type[BaseModel]):
    # Fence-tolerant (Z.ai GLM wraps JSON in ```json fences); see backend.agents.llm.
    return structured_llm(schema)


# ---------------------------------------------------------------------------
# Span verification (programmatic, no LLM)
# ---------------------------------------------------------------------------

def verify_spans(
    items: list[RawRequirement],
    doc_texts: dict[str, str],
    *,
    threshold: float = SPAN_VERIFY_THRESHOLD,
) -> None:
    """Anti-hallucination gate.

    Marks span_verified on each item by fuzzy-matching its source_span against
    the document's parsed text. Items whose span is not found become
    status='unverified' (kept for review, never silently accepted). Independent
    of the LLM, so it doubles as a CI guard.
    """
    try:
        from rapidfuzz import fuzz
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "rapidfuzz is required for span verification. Add 'rapidfuzz' to "
            "requirements.txt."
        ) from exc

    lowered = {doc_id: text.lower() for doc_id, text in doc_texts.items()}
    for it in items:
        haystack = lowered.get(it.document_id, "")
        # rapidfuzz returns scores on a 0-100 scale; normalize to 0-1.
        ratio = fuzz.partial_ratio(it.source_span.lower(), haystack) / 100.0 if haystack else 0.0
        it.span_verified = ratio >= threshold
        if not it.span_verified:
            it.status = "unverified"


# ---------------------------------------------------------------------------
# Extraction passes
# ---------------------------------------------------------------------------

async def extract_chunk(
    chunk: Chunk,
    *,
    project_name: str,
    project_description: str,
) -> ChunkExtraction:
    """Extract explicit requirements from one chunk."""
    llm = _structured_llm(ChunkExtraction)
    user = (
        f"{_project_header(project_name, project_description)}\n"
        f"SECTION: {chunk.section_path or '(unknown)'}\n\n"
        f"CHUNK:\n{chunk.text}"
    )
    result = await llm.ainvoke([("system", _EXTRACTOR_SYSTEM), ("human", user)])
    for it in result.items:
        it.document_id = chunk.document_id
        if it.page is None:
            it.page = chunk.page
    return result


async def extract_all(
    chunks: list[Chunk],
    *,
    doc_texts: dict[str, str],
    project_name: str,
    project_description: str,
    concurrency: int = 4,
) -> list[RawRequirement]:
    """Extract across all chunks (multi-document safe), then verify spans.

    doc_texts maps document_id -> full parsed text (for span verification); one
    entry per source document. Concurrency is bounded to respect provider rate
    limits. Failed chunks are logged and skipped, never crash the batch.
    """
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(c: Chunk) -> list[RawRequirement]:
        async with sem:
            try:
                res = await extract_chunk(
                    c,
                    project_name=project_name,
                    project_description=project_description,
                )
                return res.items
            except Exception:
                logger.exception(
                    "extract_chunk failed: %s idx %d", c.document_id, c.index,
                )
                return []

    batches = await asyncio.gather(*(_one(c) for c in chunks))
    flat: list[RawRequirement] = [it for sub in batches for it in sub]
    verify_spans(flat, doc_texts)
    logger.info(
        "extract_all: %d chunks -> %d items (%d unverified)",
        len(chunks), len(flat), sum(1 for i in flat if not i.span_verified),
    )
    return flat


async def gap_pass(
    chunks: list[Chunk],
    smap: StructureMap,
    extracted: list[RawRequirement],
    *,
    project_name: str,
    project_description: str,
    concurrency: int = 4,
) -> list[RawRequirement]:
    """Re-extract chunks under high/medium-likelihood sections that yielded 0 items.

    Catches requirements missed on the first pass because of a bad chunk boundary
    or a dense section. Uses the same extractor schema. Runs enrich_structure_map
    first is the caller's responsibility (gap_pass relies on req_likelihood).
    """
    covered = {it.section for it in extracted}
    empty_high = {
        s.title for s in smap.sections
        if s.req_likelihood in ("high", "medium") and s.title and s.title not in covered
    }
    if not empty_high:
        return []
    candidates = [
        c for c in chunks
        if c.section_path and any(s in c.section_path for s in empty_high)
    ]
    if not candidates:
        return []
    logger.info(
        "gap_pass: %d empty high/medium sections -> %d candidate chunks",
        len(empty_high), len(candidates),
    )
    return await extract_all(
        candidates,
        doc_texts={smap.document_id: smap.full_text},
        project_name=project_name,
        project_description=project_description,
        concurrency=concurrency,
    )


async def implicit_pass(
    chunks: list[Chunk],
    *,
    doc_texts: dict[str, str],
    project_name: str,
    project_description: str,
    concurrency: int = 4,
) -> list[RawRequirement]:
    """Separate pass for IMPLICIT requirements (assumptions), tagged explicit=False."""
    llm = _structured_llm(ImplicitExtraction)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(c: Chunk) -> list[RawRequirement]:
        async with sem:
            try:
                user = (
                    f"{_project_header(project_name, project_description)}\n"
                    f"SECTION: {c.section_path or '(unknown)'}\n\n"
                    f"EXCERPT:\n{c.text}"
                )
                res = await llm.ainvoke(
                    [("system", _IMPLICIT_SYSTEM), ("human", user)]
                )
                out: list[RawRequirement] = []
                for im in res.items:
                    out.append(RawRequirement(
                        statement=im.statement,
                        source_span=im.source_span,
                        section=im.section,
                        page=im.page or c.page,
                        explicit=False,
                        confidence=im.confidence,
                        rationale=im.rationale,
                        document_id=c.document_id,
                        status="draft",
                    ))
                return out
            except Exception:
                logger.exception(
                    "implicit_pass failed: %s idx %d", c.document_id, c.index,
                )
                return []

    batches = await asyncio.gather(*(_one(c) for c in chunks))
    flat = [it for sub in batches for it in sub]
    verify_spans(flat, doc_texts)
    return flat


async def enrich_structure_map(
    smap: StructureMap,
    *,
    project_name: str,
    project_description: str,
) -> None:
    """LLM annotates the parser-built skeleton (summary, req_likelihood,
    is_boilerplate, glossary). Mutates smap in place. The index itself stays
    parser-built; the LLM only enriches it — so annexes missing from the TOC
    are still covered by the gap pass.
    """
    if not smap.sections:
        return
    llm = _structured_llm(StructureMapAnnotation)
    tree = "\n".join(
        f"[{s.level}] {s.id} (p.{s.page}) :: {s.title}" for s in smap.sections
    )
    user = (
        f"{_project_header(project_name, project_description)}\n"
        f"DOCUMENT SECTIONS (parser-extracted):\n{tree}"
    )
    ann = await llm.ainvoke([("system", _ANNOTATOR_SYSTEM), ("human", user)])
    by_id = {s.id: s for s in ann.sections}
    for node in smap.sections:
        a = by_id.get(node.id)
        if a:
            node.summary = a.summary
            node.req_likelihood = a.req_likelihood
            node.is_boilerplate = a.is_boilerplate
    smap.glossary.update(ann.glossary)
