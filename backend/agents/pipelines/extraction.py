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
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from backend.agents.llm import build_llm, structured_llm
from backend.agents.pipelines._quality_rules import PREVENTION_RULES
from backend.agents.pipelines._resilience import DEFAULT_CONCURRENCY
from backend.agents.pipelines.ingestion import Chunk, StructureMap, verification_text

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
    # Per-requirement priority value copied VERBATIM from the source (e.g.
    # "Alta", "P1", "high") when the document carries an explicit priority
    # convention. Empty string when no priority marker is present. The
    # classifier (classification.py) resolves this hint against the run-level
    # DocumentRules.priority_legend to assign MoSCoW. Never inferred from the
    # obligation verb (debe/shall) — that path runs in classify when hint=="".
    priority_hint: str = ""


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
# Document conventions (Component 1 of the CONVENTIONS stage).
#
# Pure factual schema — copies signals VERBATIM from the document with their
# source_span. No MoSCoW resolution here (that happens per-item in classify, so
# the basis lands in `rationale`). Anti-hallucination: any entry without a
# source_span is dropped by the prompt itself; the structured schema enforces
# the verbatim contract on the LLM.
# ---------------------------------------------------------------------------

class LegendEntry(BaseModel):
    """One priority label declared by the document (VERBATIM, unresolved)."""
    label: str = Field(
        description="Priority label copied VERBATIM from the document "
                    "(e.g. 'Alta', 'P1', 'high'). Do NOT resolve to MoSCoW.",
    )
    source_span: str = Field(
        description="VERBATIM quote where this label appears (legend, header, "
                    "or its definition). Must appear in the document exactly.",
    )


class DocumentRules(BaseModel):
    """Run-level conventions extracted from the document corpus.

    All fields are raw factual signals — the resolution to MoSCoW, scope, etc.
    happens per-item in classification.py with these rules as context. Empty
    defaults mean the document does not declare that convention.
    """
    priority_legend: list[LegendEntry] = Field(
        default_factory=list,
        description="Priority labels declared by the document (e.g. a legend "
                    "'Alta = critical, Media = ...'). Empty if none.",
    )
    priority_field_label: str | None = Field(
        default=None,
        description="Name of the per-requirement labeled field that carries "
                    "an explicit priority (e.g. 'Prioridad del requerimiento'). "
                    "None when no such field exists.",
    )
    scope_markers: list[str] = Field(
        default_factory=list,
        description="Verbatim markers of out-of-scope / future items "
                    "(e.g. 'fuera de alcance', 'fase 2'). Empty if none.",
    )
    glossary: dict[str, str] = Field(
        default_factory=dict,
        description="Terms defined in the document mapped to their definition. "
                    "Activates the previously-dead smap.glossary channel.",
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
    "- PRIORITY HINT: if the document carries an explicit priority convention "
    "(a labeled field per requirement, e.g. 'Prioridad del requerimiento: "
    "Alta', or an inline marker like 'P1'), copy that value VERBATIM into "
    "priority_hint. The convention name, when known, is provided in the "
    "user message as PRIORITY_FIELD_LABEL. Copy the value only — do NOT "
    "translate, normalize, or resolve it to MoSCoW. The obligation verbs "
    "('debe', 'shall', 'should') do NOT count as priority hints. Leave "
    "priority_hint empty when no explicit priority marker is attached to "
    "the requirement.\n"
    "- If the chunk is boilerplate (cover, TOC, references, legal) and contains "
    "no requirements, return items=[] and chunk_complete=true.\n"
    + PREVENTION_RULES
    + "Return ONLY the structured object."
)

_IMPLICIT_SYSTEM = (
    "TASK: infer IMPLICIT requirements from the excerpt.\n"
    "Implicit = not stated verbatim, but necessary for the system to satisfy the "
    "stated requirements, or mandated by domain/standard context.\n"
    "- LANGUAGE: write every statement in the SAME LANGUAGE as the source_span. Do NOT translate, summarize in another language, or normalize to English. A Spanish source yields a Spanish statement; an English source yields an English statement. Preserve original domain terminology verbatim.\n"
    "For each item set: statement, rationale (why it is implied), source_span "
    "(the span that IMPLIES it — mandatory even if indirect), section, "
    "confidence (0..1).\n"
    + PREVENTION_RULES
    + "Do NOT invent requirements without a textual anchor. If none, return items=[]."
)

_ANNOTATOR_SYSTEM = (
    "You annotate a document's section skeleton (built by a parser, not you). "
    "For each section give: a one-line summary, req_likelihood "
    "(high|medium|low|none — how likely it carries actual requirements), and "
    "is_boilerplate (cover, TOC, glossary, references, legal). Also extract any "
    "glossary: terms explicitly defined in the document mapped to their "
    "definition. Return ONLY the structured object."
)

_CONVENTIONS_SYSTEM = (
    "You discover the CLIENT'S OWN document conventions (the rules the client "
    "embedded in the document itself). You read the document text and copy each "
    "signal VERBATIM with its source_span. Do NOT interpret, normalize, or "
    "resolve anything to MoSCoW — the downstream classifier does that with "
    "your raw output as context.\n"
    "Rules:\n"
    "- LANGUAGE: copy every label, marker, and glossary term VERBATIM in the "
    "SAME LANGUAGE as the source. Do NOT translate, summarize, or normalize. "
    "A Spanish document yields Spanish labels; an English document yields "
    "English labels.\n"
    "- PRIORITY LEGEND: any legend, key, or definition that declares priority "
    "labels (e.g. 'Alta = mandatory', 'P1 = critical', 'high = must'). Copy "
    "each distinct label as one LegendEntry with its source_span. If the "
    "document has no priority legend, return an empty list.\n"
    "- PRIORITY FIELD LABEL: the NAME of the per-requirement labeled field "
    "that carries an explicit priority, when one exists (e.g. 'Prioridad del "
    "requerimiento', 'Priority', 'Criticité'). Copy the field name VERBATIM. "
    "Set to null when requirements do not carry a per-item priority field.\n"
    "- SCOPE MARKERS: verbatim phrases that mark items as out-of-scope or "
    "future (e.g. 'fuera de alcance', 'fase 2', 'out of scope'). Copy each "
    "distinct marker once. Empty list when none.\n"
    "- GLOSSARY: terms explicitly defined in the document (e.g. in a "
    "'Definiciones' / 'Glossary' section) mapped to their definition. Copy "
    "the term and definition VERBATIM. Empty dict when the document has no "
    "glossary.\n"
    "- Anti-hallucination: every label, marker, and glossary entry MUST be "
    "copied from the document text. Do NOT invent conventions the document "
    "does not state. When unsure, return the empty value — never guess.\n"
    "Return ONLY the structured object."
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
    rules: DocumentRules | None = None,
    rules_block: str = "",
) -> ChunkExtraction:
    """Extract explicit requirements from one chunk.

    ``rules`` (optional) carries run-level conventions. When
    ``rules.priority_field_label`` is set, it is surfaced in the user message
    so the extractor knows which labeled field to read for ``priority_hint``.
    ``rules_block`` (optional) is the persistent PROJECT_RULES block; it goes
    BEFORE the chunk text so long chunks cannot bury it (lost-in-the-middle).
    Defaults preserve the historical behavior.
    """
    llm = _structured_llm(ChunkExtraction)
    lines = [
        f"{_project_header(project_name, project_description)}",
        f"SECTION: {chunk.section_path or '(unknown)'}",
    ]
    if rules and rules.priority_field_label:
        lines.append(
            f"PRIORITY_FIELD_LABEL: {rules.priority_field_label}"
        )
    if rules_block:
        lines.append(rules_block)
    lines.append(f"CHUNK:\n{chunk.text}")
    user = "\n".join(lines)
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
    concurrency: int = DEFAULT_CONCURRENCY,
    rules: DocumentRules | None = None,
    rules_block: str = "",
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> list[RawRequirement]:
    """Extract across all chunks (multi-document safe), then verify spans.

    doc_texts maps document_id -> full parsed text (for span verification); one
    entry per source document. Concurrency is bounded to respect provider rate
    limits. Failed chunks are logged and skipped, never crash the batch.

    ``rules`` (optional) is threaded into ``extract_chunk`` so the extractor
    can populate ``RawRequirement.priority_hint`` from the document's labeled
    priority field. Default None preserves the historical verb-only behavior.
    """
    sem = asyncio.Semaphore(max(1, concurrency))
    total = len(chunks)
    done = 0

    async def _one(c: Chunk) -> list[RawRequirement]:
        nonlocal done
        async with sem:
            try:
                res = await extract_chunk(
                    c,
                    project_name=project_name,
                    project_description=project_description,
                    rules=rules,
                    rules_block=rules_block,
                )
                return res.items
            except Exception:
                logger.exception(
                    "extract_chunk failed: %s idx %d", c.document_id, c.index,
                )
                return []
            finally:
                done += 1
                if on_progress and (done % 5 == 0 or done == total):
                    await on_progress(done, total)

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
    concurrency: int = DEFAULT_CONCURRENCY,
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
        doc_texts={smap.document_id: verification_text(chunks, smap)},
        project_name=project_name,
        project_description=project_description,
        concurrency=concurrency,
    )


def filter_boilerplate_chunks(
    chunks: list[Chunk], smaps: list[StructureMap]
) -> list[Chunk]:
    """Drop chunks under sections the annotator flagged boilerplate or with no
    requirement likelihood (cover, TOC, glossary, references, legal).

    Used to skip the IMPLICIT pass over sections that cannot carry implicit
    requirements, cutting LLM calls. A chunk is dropped when its
    ``section_path`` breadcrumb contains the title of a boilerplate / none
    section of its OWN document. Returns the input unchanged when no section is
    flagged, so documents without annotations behave exactly as before.
    """
    bad_titles: dict[str, set[str]] = {}
    for smap in smaps:
        # Defensive: a smap without the sections annotation (a test mock, or a
        # document whose enrich_structure_map pass did not run) contributes no
        # bad titles — all its chunks pass through unchanged.
        sections = getattr(smap, "sections", None) or []
        doc_id = getattr(smap, "document_id", None)
        bad = {
            s.title for s in sections
            if getattr(s, "is_boilerplate", False)
            or getattr(s, "req_likelihood", None) == "none"
        }
        if bad and doc_id is not None:
            bad_titles.setdefault(doc_id, set()).update(bad)
    if not bad_titles:
        return list(chunks)
    out: list[Chunk] = []
    for c in chunks:
        bad_for_doc = bad_titles.get(c.document_id)
        if not bad_for_doc:
            out.append(c)
            continue
        path = c.section_path or ""
        if not any(title and title in path for title in bad_for_doc):
            out.append(c)
    return out


async def implicit_pass(
    chunks: list[Chunk],
    *,
    doc_texts: dict[str, str],
    project_name: str,
    project_description: str,
    concurrency: int = DEFAULT_CONCURRENCY,
    batch_size: int = 3,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> list[RawRequirement]:
    """Separate pass for IMPLICIT requirements (assumptions), tagged explicit=False.

    Chunks are batched by document (up to batch_size per LLM call) to reduce
    the total number of calls. document_id is correct within each batch since
    all chunks share the same document.
    """
    if not chunks:
        return []
    llm = _structured_llm(ImplicitExtraction)
    sem = asyncio.Semaphore(max(1, concurrency))

    # Group chunks by document_id, then batch within each document.
    by_doc: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.document_id, []).append(c)
    batches: list[list[Chunk]] = []
    for doc_chunks in by_doc.values():
        for i in range(0, len(doc_chunks), batch_size):
            batches.append(doc_chunks[i:i + batch_size])

    total = len(batches)
    done = 0

    async def _one_batch(batch: list[Chunk]) -> list[RawRequirement]:
        nonlocal done
        async with sem:
            try:
                excerpts = "\n\n---\n\n".join(
                    f"[SECTION: {c.section_path or '(unknown)'}]\n{c.text}"
                    for c in batch
                )
                user = (
                    f"{_project_header(project_name, project_description)}\n\n"
                    f"EXCERPTS:\n{excerpts}"
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
                        page=im.page or batch[0].page,
                        explicit=False,
                        confidence=im.confidence,
                        rationale=im.rationale,
                        document_id=batch[0].document_id,
                        status="draft",
                    ))
                return out
            except Exception:
                logger.exception(
                    "implicit_pass batch failed: %s",
                    batch[0].document_id if batch else "?",
                )
                return []
            finally:
                done += 1
                if on_progress and (done % 3 == 0 or done == total):
                    await on_progress(done, total)

    results = await asyncio.gather(*(_one_batch(b) for b in batches))
    flat = [it for sub in results for it in sub]
    verify_spans(flat, doc_texts)
    logger.info(
        "implicit_pass: %d chunks in %d batches -> %d items",
        len(chunks), total, len(flat),
    )
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


# Maximum characters of smap.full_text fed to extract_conventions. The markdown
# export is usually well under the LLM context window, but we cap defensively
# to keep the conventions call bounded on huge documents. The head is kept
# verbatim — definitional sections (legends, glossaries, scope statements)
# typically live near the top of an SRS / RFP, so a head truncation preserves
# the most informative part.
_CONVENTIONS_TEXT_BUDGET = 24_000


def _truncate_for_conventions(text: str) -> str:
    """Head-biased truncation of the document markdown for the conventions call.

    Definitional sections (priority legend, glossary, scope) usually sit near
    the front of a client document, so truncating the tail is the safe side.
    The budget is a character count (approx 6-8k tokens) well inside any
    modern LLM context window.
    """
    if len(text) <= _CONVENTIONS_TEXT_BUDGET:
        return text
    return text[:_CONVENTIONS_TEXT_BUDGET]


async def extract_conventions(
    smap: StructureMap,
    *,
    project_name: str,
    project_description: str,
) -> DocumentRules:
    """One LLM call per document that discovers the client's own conventions.

    Reads ``smap.full_text`` (the markdown export that ``build_structure_map``
    already produces) and asks the LLM to copy verbatim any priority legend,
    per-requirement priority field label, scope markers, and glossary entries
    the document declares. Returns a :class:`DocumentRules`.

    Graceful degradation: any failure (LLM error, parse error, missing text)
    returns an empty ``DocumentRules()``. This stage NEVER breaks the pipeline
    — when no conventions are discovered, downstream consumers fall back to
    the verb-based behavior (current day-1 path).
    """
    if not smap.full_text or not smap.full_text.strip():
        return DocumentRules()

    llm = _structured_llm(DocumentRules)
    body = _truncate_for_conventions(smap.full_text)
    user = (
        f"{_project_header(project_name, project_description)}\n"
        f"DOCUMENT TEXT (markdown export):\n{body}"
    )
    try:
        rules = await llm.ainvoke(
            [("system", _CONVENTIONS_SYSTEM), ("human", user)]
        )
    except Exception:
        logger.exception(
            "extract_conventions failed for %s; returning empty rules "
            "(pipeline continues with verb-based defaults)",
            smap.document_id,
        )
        return DocumentRules()
    # Cache the per-doc rules on the smap so the orchestrator can read them
    # without re-running the stage. Mutation in place, mirroring
    # enrich_structure_map's contract.
    smap.conventions = rules
    return rules


class ActorCandidate(BaseModel):
    """Un actor evidenciado por un documento (rol humano o sistema externo)."""

    name: str = Field(
        description="Canonical role in the document's language, singular "
        "(e.g. 'Coordinador de terreno').",
    )
    synonyms: list[str] = Field(
        default_factory=list,
        description="Other names the document uses for the same role.",
    )
    channel: str | None = Field(
        default=None,
        description="'humano' for a human role, 'sistema_externo' for "
        "external systems/services.",
    )
    rationale: str | None = Field(
        default=None,
        description="At most 12 words citing the evidence.",
    )


class ActorCatalog(BaseModel):
    """Salida de la pasada de actores por documento (vacía si no evidencia)."""

    actors: list[ActorCandidate] = Field(default_factory=list)


_ACTORS_SYSTEM = (
    "You are a requirements analyst identifying the ACTORS of a software "
    "system (UML actors: human roles and external systems that interact with "
    "the system under specification).\n\n"
    "You receive ONE project document. Extract the actors it EVIDENCES:\n"
    "- name: canonical role in the SAME LANGUAGE as the document, singular "
    "(e.g. 'Coordinador de terreno').\n"
    "- synonyms: other names the document uses for the same role.\n"
    "- channel: 'humano' for a human role; 'sistema_externo' for external "
    "systems or integration services.\n"
    "- rationale: at most 12 words citing the evidence.\n\n"
    "Rules:\n"
    "- Only actors evidenced by the document text. Never invent roles.\n"
    "- Do NOT emit 'usuario'/'user' as an actor: that generic label is "
    "exactly the vagueness this catalog fixes. Resolve it to the concrete "
    "role the document describes; if the document names no concrete roles, "
    "return an empty list.\n"
    "- Do NOT emit the system under specification itself as an actor.\n"
    "- LANGUAGE: names, synonyms and rationale stay in the document's "
    "language. Never translate.\n"
    "- Return ONLY the structured object."
)


async def extract_actors(
    smap: StructureMap,
    *,
    project_name: str,
    project_description: str,
) -> ActorCatalog:
    """Una llamada LLM por documento que identifica los actores que evidencia.

    Espejo de ``extract_conventions`` (misma degradación grácil): lee
    ``smap.full_text`` y devuelve un :class:`ActorCatalog`; cualquier fallo
    (LLM, parse, texto ausente) devuelve un catálogo vacío — esta etapa NUNCA
    rompe el pipeline. Sin catálogo, los consumidores siguen con el léxico
    base de ``detect_actor``.
    """
    if not smap.full_text or not smap.full_text.strip():
        return ActorCatalog()

    try:
        llm = _structured_llm(ActorCatalog)
        body = _truncate_for_conventions(smap.full_text)
        user = (
            f"{_project_header(project_name, project_description)}\n"
            f"DOCUMENT TEXT (markdown export):\n{body}"
        )
        return await llm.ainvoke(
            [("system", _ACTORS_SYSTEM), ("human", user)]
        )
    except Exception:
        logger.exception(
            "extract_actors failed for %s; returning empty catalog "
            "(pipeline continues with the base actor lexicon)",
            smap.document_id,
        )
        return ActorCatalog()


def merge_conventions(per_doc: list[DocumentRules]) -> DocumentRules:
    """Deterministic merge of per-document rules into one run-level object.

    Concatenates list/dict fields (legend, scope markers, glossary) and picks
    the first non-None ``priority_field_label``. Pure data merge — no
    deduplication, no inference. Order follows the input list (document order
    at the caller) so the merged rules stay traceable to their source doc.
    """
    if not per_doc:
        return DocumentRules()
    merged_legend: list[LegendEntry] = []
    merged_scope: list[str] = []
    merged_glossary: dict[str, str] = {}
    priority_field_label: str | None = None
    for rules in per_doc:
        merged_legend.extend(rules.priority_legend)
        merged_scope.extend(rules.scope_markers)
        merged_glossary.update(rules.glossary)
        if priority_field_label is None and rules.priority_field_label:
            priority_field_label = rules.priority_field_label
    return DocumentRules(
        priority_legend=merged_legend,
        priority_field_label=priority_field_label,
        scope_markers=merged_scope,
        glossary=merged_glossary,
    )
