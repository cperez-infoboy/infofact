"""Ingestion pipeline: parse client documents into structured chunks + a map.

Runs host-side (FastAPI process), reading workspace files from
``settings.workspaces_host_root``. Docling is the layout-aware parser; tables and
lists come out as structure, not flat text. The heavy import (torch + DocLayNet /
TableFormer models, ~1-2 GB) is lazy: this module imports fine without docling
installed, and conversion fails with a clear message at call time if it is
missing.

Why host-side (not inside the per-user agent container):
  - Docling + torch + models are heavy; duplicating them per container is
    wasteful (one model cache on the host serves everyone).
  - Parsing is read-only and runs no user code, so it needs no isolation
    (CLAUDE.md Decision 1 — requirements phase does not execute shell/code).
  - Mirrors how web_search / fetch_url already run host-side.
  - Results go to the DB (host-side), consistent with the SQLite-not-on-NFS rule.

Transport-agnostic: every public function takes absolute paths already resolved
by the caller (requirements_service / the capture subagent). If the workspace
moves (container path vs host path), only the caller's path resolution changes —
the pipeline does not.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Docling parses these natively (pdf, office, html, images). CSV/MD/TXT are
# plain text and skip the layout parser (read straight into a single chunk).
# Docling parses these natively (pdf, office, html, images, spreadsheets).
# CSV/MD/TXT are plain text and skip the layout parser (read straight into a
# single chunk). xlsx/xls confirmed supported by Docling InputFormat.
DOCLING_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".html", ".htm",
    ".rtf", ".odt", ".png", ".jpg", ".jpeg", ".tiff",
    ".xlsx", ".xls",
}
PLAINTEXT_EXTENSIONS = {".txt", ".md", ".csv"}
DOC_EXTENSIONS = DOCLING_EXTENSIONS | PLAINTEXT_EXTENSIONS

# Directories that never contain client documents.
_IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".next", "dist", "build", "target", "data",
}

# Labels Docling attaches to text items that represent document headings.
_HEADING_LABELS = {"title", "section_header", "heading"}


# ---------------------------------------------------------------------------
# Data shapes (plain dataclasses — no Pydantic here; extraction.py owns the
# LLM-facing schemas).
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    """One element-aware slice of a document fed to the extractor."""
    text: str
    document_id: str                       # absolute path of the source document
    section_path: str                      # breadcrumb "Cap.3 > 3.2 > 3.2.1"
    element_kinds: tuple[str, ...] = ()    # {table, list_item, paragraph, ...}
    page: int | None = None
    index: int = 0


@dataclass
class TableRef:
    """A table detected by the parser (structure, not text)."""
    document_id: str
    index: int
    caption: str | None
    row_count: int
    col_count: int
    page: int | None


@dataclass
class SectionNode:
    """One heading in the document skeleton.

    The deterministic fields (id/title/level/page) come from the parser here.
    The LLM-enriched fields (summary/req_likelihood/is_boilerplate) are filled
    later by extraction.py pass 1 — the index is always parser-built, never
    LLM-built, so annexes missing from the TOC are still covered.
    """
    id: str
    title: str
    level: int
    page: int | None
    summary: str = ""
    req_likelihood: str = "medium"     # high | medium | low | none
    is_boilerplate: bool = False


@dataclass
class StructureMap:
    """Parser-built skeleton of a document (no LLM)."""
    document_id: str
    sections: list[SectionNode] = field(default_factory=list)
    tables: list[TableRef] = field(default_factory=list)
    glossary: dict[str, str] = field(default_factory=dict)   # LLM-filled later
    full_text: str = ""                # markdown export — input for verify_spans
    page_count: int = 0


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _is_ignorable(path: Path) -> bool:
    """True if path sits under a build/vcs/deps directory we never scan."""
    return any(part in _IGNORED_DIRS for part in path.parts)


def discover_documents(target: Path) -> list[Path]:
    """Deterministic walk returning every client document under ``target``.

    ``target`` may be a single file or a directory (scanned recursively).
    Deciding which files to process is NOT left to the LLM: it is slow,
    non-deterministic, and risks dropping coverage. The capture subagent calls
    this and forwards the filtered list to the pipeline.
    """
    if not target.exists():
        logger.warning("discover_documents: target does not exist: %s", target)
        return []
    if target.is_file():
        return [target] if target.suffix.lower() in DOC_EXTENSIONS else []
    found: list[Path] = []
    for p in sorted(target.rglob("*")):
        # Relativize before the ignore check: _IGNORED_DIRS is meant to skip
        # build/vcs dirs INSIDE the scanned tree, not host ancestors. Without
        # this, a workspace at ``./data/workspaces/...`` has ``data`` as an
        # ancestor part, so every file is dropped and discover returns [].
        if _is_ignorable(p.relative_to(target)):
            continue
        if p.is_file() and p.suffix.lower() in DOC_EXTENSIONS:
            found.append(p)
    return found


# ---------------------------------------------------------------------------
# Conversion + chunking (Docling — lazy import)
# ---------------------------------------------------------------------------

def _resolve_docling():
    """Lazy import; torch + layout/table models load only when parsing runs."""
    try:
        from docling.document_converter import DocumentConverter
        from docling.chunking import HybridChunker
        return DocumentConverter, HybridChunker
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "docling is not installed. Add 'docling' to requirements.txt and "
            "rebuild the image (it pulls torch + layout/table models)."
        ) from exc


def convert_document(path: Path):
    """Parse one document into a DoclingDocument (layout-aware)."""
    DocumentConverter, _HybridChunker = _resolve_docling()
    logger.info("Converting document: %s", path)
    result = DocumentConverter().convert(path)
    return result.document


def _meta(chunk):
    return getattr(chunk, "meta", None)


def _breadcrumbs(chunk) -> str:
    """Best-effort section path from chunk metadata (headings/captions)."""
    meta = _meta(chunk)
    if meta is None:
        return ""
    headings = getattr(meta, "headings", None) or []
    captions = getattr(meta, "captions", None) or []
    parts = [str(h) for h in headings] + [str(c) for c in captions]
    return " > ".join(parts)


def _element_kinds(chunk) -> tuple[str, ...]:
    """Labels of the DoclingDocument items a chunk spans (table, list_item...)."""
    meta = _meta(chunk)
    if meta is None:
        return ()
    items = getattr(meta, "doc_items", None) or []
    kinds: list[str] = []
    for it in items:
        label = getattr(it, "label", None) or getattr(it, "name", None)
        if label:
            kinds.append(str(label))
    return tuple(dict.fromkeys(kinds))   # dedupe, preserve order


def _first_page(items) -> int | None:
    """First page number from a list of DoclingDocument items' provenance."""
    for it in items:
        prov = getattr(it, "prov", None) or []
        for p in prov:
            page_no = getattr(p, "page_no", None)
            if page_no is not None:
                return int(page_no)
    return None


def _chunk_page(chunk) -> int | None:
    meta = _meta(chunk)
    if meta is None:
        return None
    return _first_page(getattr(meta, "doc_items", None) or [])


def _enforce_atomic(chunks: list[Chunk]) -> list[Chunk]:
    """Guard against tables / list-requirements split across chunk boundaries.

    HybridChunker keeps tables atomic by contract (repeat_table_header=True, see
    Docling chunking concepts). This is defense in depth: at the text level we
    cannot cleanly reassemble a split table without the structured items, so we
    only flag suspiciously short table chunks for review instead of merging.
    """
    for i, c in enumerate(chunks):
        if "table" in c.element_kinds and len(c.text) < 40:
            logger.warning(
                "Suspiciously short table chunk %d in %s — possible split; "
                "review chunker config.", i, c.document_id,
            )
    return chunks


def _build_tokenizer(max_tokens: int):
    """tiktoken-based tokenizer — lightweight, no HF model download.

    o200k_base approximates modern LLM tokenizers (GPT-4o family). The token
    count is only a size guide for chunking, so a near-exact match to the
    extractor LLM (e.g. GLM) is not required; if it ever is, swap the encoding.
    """
    try:
        import tiktoken
        from docling_core.transforms.chunker.tokenizer.openai import (
            OpenAITokenizer,
        )
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "tiktoken is required for chunking. Add 'tiktoken' to "
            "requirements.txt."
        ) from exc
    encoding = tiktoken.get_encoding("o200k_base")
    return OpenAITokenizer(tokenizer=encoding, max_tokens=max_tokens)


def chunk_document(doc, document_id: str, *, max_tokens: int = 8000) -> list[Chunk]:
    """Element-aware chunking. Tables and list-requirements stay atomic.

    Docling 2.x moved the token budget off the chunker and onto the tokenizer;
    ``max_tokens`` is therefore set on the tokenizer, not passed to
    ``HybridChunker`` directly.
    """
    _DocumentConverter, HybridChunker = _resolve_docling()
    chunker = HybridChunker(tokenizer=_build_tokenizer(max_tokens))
    out: list[Chunk] = []
    for i, c in enumerate(chunker.chunk(doc)):
        out.append(Chunk(
            text=c.text,
            document_id=document_id,
            section_path=_breadcrumbs(c),
            element_kinds=_element_kinds(c),
            page=_chunk_page(c),
            index=i,
        ))
    return _enforce_atomic(out)


def _chunk_plaintext(path: Path, document_id: str) -> list[Chunk]:
    """Plaintext files (.txt/.md/.csv) bypass the layout parser."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return []
    return [Chunk(text=text, document_id=document_id, section_path=path.name)]


# ---------------------------------------------------------------------------
# Structural map (deterministic — parser only, no LLM)
# ---------------------------------------------------------------------------

def _heading_level(item) -> int:
    """Depth of a section header (1-based). Falls back to 1 when unknown."""
    level = getattr(item, "level", None)
    if isinstance(level, int):
        return level
    return 1


def _item_page(item) -> int | None:
    return _first_page([item])


def _table_ref(document_id: str, index: int, table) -> TableRef:
    data = getattr(table, "data", None)
    rows = getattr(data, "num_rows", 0) if data else 0
    cols = getattr(data, "num_cols", 0) if data else 0
    caption = getattr(table, "caption", None)
    if caption and not isinstance(caption, str):
        caption = getattr(caption, "text", None)
    return TableRef(
        document_id=document_id,
        index=index,
        caption=caption,
        row_count=int(rows),
        col_count=int(cols),
        page=_item_page(table),
    )


def build_structure_map(doc, *, document_id: str) -> StructureMap:
    """Build the document skeleton FROM THE PARSER (deterministic, no LLM).

    The LLM only *annotates* this skeleton later (extraction.py pass 1: summary,
    req_likelihood, is_boilerplate, glossary). Building the index from the parser
    — not from the document's TOC — is what lets the gap pass reach annexes and
    footnotes that the TOC never mentions.
    """
    sections: list[SectionNode] = []
    for item in getattr(doc, "texts", []):
        label = getattr(item, "label", "") or ""
        if label in _HEADING_LABELS:
            title = (getattr(item, "text", "") or "").strip()
            sections.append(SectionNode(
                id=title[:80] or f"section-{len(sections)}",
                title=title,
                level=_heading_level(item),
                page=_item_page(item),
            ))

    tables = [
        _table_ref(document_id, i, t)
        for i, t in enumerate(getattr(doc, "tables", []))
    ]

    full_text = ""
    export = getattr(doc, "export_to_markdown", None)
    if callable(export):
        try:
            full_text = export()
        except Exception:  # pragma: no cover - parser-specific
            logger.exception("export_to_markdown failed for %s", document_id)

    return StructureMap(
        document_id=document_id,
        sections=sections,
        tables=tables,
        glossary={},
        full_text=full_text,
        page_count=len(getattr(doc, "pages", {}) or {}),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def ingest_document(path: Path) -> tuple[list[Chunk], StructureMap]:
    """Full ingestion of one document: element-aware chunks + structural map.

    Plaintext skips Docling; everything else goes through the layout parser.
    """
    document_id = str(path)
    suffix = path.suffix.lower()
    if suffix in PLAINTEXT_EXTENSIONS:
        chunks = _chunk_plaintext(path, document_id)
        smap = StructureMap(
            document_id=document_id,
            sections=[SectionNode(id=path.name, title=path.name, level=1,
                                  page=None)],
            full_text=chunks[0].text if chunks else "",
        )
        return chunks, smap

    doc = convert_document(path)
    chunks = chunk_document(doc, document_id=document_id)
    smap = build_structure_map(doc, document_id=document_id)
    return chunks, smap
