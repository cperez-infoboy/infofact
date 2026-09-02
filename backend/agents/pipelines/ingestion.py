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

import base64
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from backend.config import settings

if TYPE_CHECKING:
    # Forward-only import: DocumentRules lives in extraction.py, which itself
    # imports from ingestion.py. The `from __future__ import annotations` at
    # the top keeps the dataclass field annotation lazy, so this circular dep
    # never resolves at runtime.
    from backend.agents.pipelines.extraction import DocumentRules

logger = logging.getLogger(__name__)

# Docling parses these natively (pdf, office, html, images). CSV/MD/TXT are
# plain text and skip the layout parser (read straight into a single chunk).
# Docling parses these natively (pdf, office, html, images, spreadsheets).
# CSV/MD/TXT are plain text and skip the layout parser (read straight into a
# single chunk). xlsx/xls confirmed supported by Docling InputFormat.
DOCLING_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".html", ".htm",
    ".rtf", ".odt", ".png", ".jpg", ".jpeg", ".tiff",
    ".xlsx", ".xls", ".webp", ".bmp",
}
PLAINTEXT_EXTENSIONS = {".txt", ".md", ".csv"}
DOC_EXTENSIONS = DOCLING_EXTENSIONS | PLAINTEXT_EXTENSIONS

# Char budget for plaintext chunks (~4 chars/token heuristic ≈ the 8000-token
# budget chunk_document uses for Docling). A size guide, not an exact count.
_PLAINTEXT_CHUNK_CHARS = 32_000

# ATX heading (#{1..6} + title); trailing closing #'s are optional.
_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

# Standalone raster images. Docling handles their OCR/layout; when vision is
# available the vision model also describes them semantically.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".webp", ".bmp"}

# Directories that never contain client documents.
_IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".next", "dist", "build", "target", "data",
    # InfoFact internal metadata (grouping plans, capture reports). Must never
    # be parsed as a client document — see backend/agents/pipelines/grouping.py.
    ".infofact",
    # Copias sueltas de planes de agrupado en la raiz del proyecto: threads con
    # la directiva pre-DB (guardar en .infofact/) pueden recrear el dir afuera
    # tras fallar por permisos — jamas debe ingerirse como documento cliente.
    "grouping-plans",
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
    # Per-document conventions discovered by extract_conventions (extraction.py).
    # None until that stage runs; the merged run-level rules live in the
    # orchestrator. The annotation is a forward ref (module uses
    # `from __future__ import annotations`) so the import stays under
    # TYPE_CHECKING and avoids a circular import at runtime.
    conventions: "DocumentRules | None" = None  # type: ignore[name-defined]


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
    """Parse one document into a DoclingDocument (layout-aware).

    When vision is configured, PDFs are converted with
    ``generate_picture_images=True`` so embedded pictures materialize and can be
    described downstream by ``_describe_embedded_pictures``.
    """
    DocumentConverter, _HybridChunker = _resolve_docling()
    logger.info("Converting document: %s", path)
    if _vision_enabled() and path.suffix.lower() == ".pdf":
        converter = _make_vision_converter()
    else:
        converter = DocumentConverter()
    result = converter.convert(path)
    return result.document


# ---------------------------------------------------------------------------
# Vision layer (semantic image description — only runs when configured)
# ---------------------------------------------------------------------------

def _vision_enabled() -> bool:
    """Whether the vision model is available in this deployment."""
    return settings.supports_vision


def _make_vision_converter():
    """DocumentConverter configured to materialize embedded PDF pictures.

    With ``generate_picture_images=True`` each embedded picture becomes a
    drawable image that ``_describe_embedded_pictures`` can read. Lazy import;
    these classes ship with docling.
    """
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    pipeline_options = PdfPipelineOptions()
    pipeline_options.generate_picture_images = True
    pipeline_options.images_scale = settings.vision_images_scale
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
    )


def _pil_to_temp_png(image) -> str:
    """Persist a PIL image to a temporary PNG file and return its path."""
    import os
    import tempfile

    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    image.save(tmp_path, format="PNG")
    return tmp_path


def _picture_page(item) -> int | None:
    """Best-effort page number from a Docling item's provenance (1-based)."""
    prov = getattr(item, "prov", None)
    if not prov:
        return None
    try:
        return getattr(prov[0], "page_no", None)
    except (IndexError, AttributeError):
        return None


def _describe_standalone_image(path: Path, document_id: str) -> list[Chunk]:
    """Describe a standalone raster image with the vision model."""
    try:
        from backend.agents.vision import describe_image

        description = describe_image(path)
    except Exception as exc:  # vision must never break ingestion
        logger.warning("Vision describe failed for %s: %s", path, exc)
        return []
    if not description or not description.strip():
        return []
    return [Chunk(
        text=description,
        document_id=document_id,
        section_path=path.name,
        element_kinds=("image_description",),
    )]


def _describe_embedded_pictures(doc, document_id: str) -> list[Chunk]:
    """Materialize and semantically describe pictures embedded in a document."""
    try:
        from docling_core.types.doc import PictureItem
        from backend.agents.vision import MIN_IMAGE_EDGE, describe_image
    except ImportError as exc:
        logger.warning("Picture types unavailable for vision: %s", exc)
        return []

    max_pictures = settings.vision_max_pictures_per_doc
    out: list[Chunk] = []
    count = 0
    for item, _level in doc.iterate_items(traverse_pictures=True):
        if not isinstance(item, PictureItem):
            continue
        if count >= max_pictures:
            logger.info(
                "Vision picture cap (%d) reached for %s; skipping the rest",
                max_pictures, document_id,
            )
            break
        try:
            image = item.get_image(doc)
        except Exception as exc:
            logger.debug("get_image failed for an embedded picture: %s", exc)
            continue
        if image is None:
            continue
        # Z.ai rejects images below its minimum edge; skip tiny icons silently
        # so an icon-heavy document does not flood the log with warnings.
        # images_scale ups the rasterized resolution; raise the minimum-edge
        # floor proportionally so upscaled logos/icons are still skipped (a 64px
        # icon at scale 4.0 is 256px but carries no new content). This keeps the
        # picture cap for real diagrams instead of spending it on noise.
        if min(image.size) < MIN_IMAGE_EDGE * settings.vision_images_scale:
            continue
        tmp_path = _pil_to_temp_png(image)
        try:
            description = describe_image(Path(tmp_path))
        except Exception as exc:
            logger.warning("Vision describe failed for an embedded picture: %s", exc)
            continue
        finally:
            import os
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        if not description or not description.strip():
            continue
        out.append(Chunk(
            text=description,
            document_id=document_id,
            section_path="Imagen embebida",
            element_kinds=("image_description",),
            page=_picture_page(item),
        ))
        count += 1
    return out


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


def _table_header(lines: list[str]) -> str | None:
    """First line when the block looks like a delimited table, else None.

    Signature: first two lines share the same count of commas / pipes / tabs
    (CSV, markdown pipe table, TSV). Deliberately cheap — it only decides
    whether to repeat one header row on hard splits.
    """
    if len(lines) < 2:
        return None

    def counts(line: str) -> tuple[int, int, int]:
        return (line.count(","), line.count("|"), line.count("\t"))

    head, second = counts(lines[0]), counts(lines[1])
    if head == second and any(head):
        return lines[0]
    return None


def _hard_split_lines(piece: str) -> list[str]:
    """Last-resort split of an oversized piece by line (keeps lines whole).

    When the piece looks tabular, the header row is repeated at the top of
    each continuation chunk so the extractor never sees orphaned data rows
    without column context.
    """
    lines = piece.splitlines()
    header = _table_header(lines)
    out: list[str] = []
    cur: list[str] = []
    size = 0
    for line in lines:
        add = len(line) + 1
        if cur and size + add > _PLAINTEXT_CHUNK_CHARS:
            out.append("\n".join(cur))
            cur, size = ([header] if header else []), ((len(header) + 1) if header else 0)
        cur.append(line)
        size += add
    if cur:
        out.append("\n".join(cur))
    return out


def _split_plaintext(text: str, fallback_path: str) -> list[tuple[str, str]]:
    """Split plaintext into ``(section_path, text)`` chunks under a char budget.

    Splits on markdown ATX headings (outside fenced code blocks) and blank
    lines, then greedily packs the pieces up to ``_PLAINTEXT_CHUNK_CHARS``.
    A large .md returned whole would force the extractor to emit every
    requirement in a single structured output (truncation risk) and hurt
    recall mid-file (lost-in-the-middle). Pieces larger than the budget (e.g.
    a CSV or a long table with no blank lines) are hard-split by line.
    """
    headings: list[tuple[int, str]] = []  # breadcrumb of (level, title)
    pieces: list[tuple[str, str]] = []
    buf: list[str] = []
    buf_crumb = fallback_path
    in_fence = False

    def crumb() -> str:
        return " > ".join(t for _, t in headings) if headings else fallback_path

    def flush() -> None:
        nonlocal buf, buf_crumb
        if buf:
            pieces.append((buf_crumb, "\n".join(buf)))
            buf = []
            buf_crumb = fallback_path

    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else _ATX_HEADING_RE.match(stripped)
        if match:
            flush()
            level, title = len(match.group(1)), match.group(2).strip()
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, title))
            buf = [stripped]
            buf_crumb = crumb()
            continue
        if not stripped:
            flush()
            continue
        if not buf:
            buf_crumb = crumb()
        buf.append(raw.rstrip())

    flush()

    # Greedy packing under the char budget. A chunk never ends on a heading:
    # trailing heading pieces carry over so a section title is never separated
    # from its content (the next extractor call would see a bare title).
    def _is_heading_piece(text: str) -> bool:
        return "\n" not in text and _ATX_HEADING_RE.match(text.strip()) is not None

    chunks: list[tuple[str, str]] = []
    cur: list[tuple[str, str]] = []  # (crumb, text) pieces being packed
    size = 0
    for piece_crumb, piece in pieces:
        parts = (
            [piece]
            if len(piece) <= _PLAINTEXT_CHUNK_CHARS
            else _hard_split_lines(piece)
        )
        for part in parts:
            if cur and size + len(part) + 1 > _PLAINTEXT_CHUNK_CHARS:
                carry: list[tuple[str, str]] = []
                while cur and _is_heading_piece(cur[-1][1]):
                    carry.insert(0, cur.pop())
                if cur:
                    chunks.append((cur[0][0], "\n".join(t for _, t in cur)))
                cur = carry
                size = sum(len(t) + 1 for _, t in carry)
            cur.append((piece_crumb, part))
            size += len(part) + 1
    if cur:
        chunks.append((cur[0][0], "\n".join(t for _, t in cur)))
    return chunks


def _chunk_plaintext(path: Path, document_id: str) -> list[Chunk]:
    """Plaintext files (.txt/.md/.csv) bypass the layout parser.

    The text is split by ``_split_plaintext`` instead of returned whole so a
    large file reaches the extractor as several bounded chunks.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    return _chunk_plaintext_text(text, document_id, path.name)


def _chunk_plaintext_text(
    text: str, document_id: str, fallback_name: str
) -> list[Chunk]:
    """Chunk already-read plaintext (shared by _parse_plaintext sanitizing)."""
    if not text.strip():
        return []
    return [
        Chunk(text=t, document_id=document_id, section_path=crumb, index=i)
        for i, (crumb, t) in enumerate(_split_plaintext(text, fallback_name))
    ]


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


def verification_text(chunks: list[Chunk], smap: StructureMap) -> str:
    """Full text for span verification, including vision-generated descriptions.

    Image descriptions are not part of Docling's markdown export, so they must
    be appended separately for verify_spans to match source_spans from
    image-derived requirements.
    """
    parts = [smap.full_text] if smap.full_text else []
    parts.extend(
        c.text for c in chunks
        if getattr(c, "element_kinds", None)
        and "image_description" in c.element_kinds
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _scan_headings(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(level, title)`` for each ATX heading outside fenced blocks.

    Fence tracking is line-granular (``` toggles); good enough for documents
    the pipeline sees and keeps ``#`` comments inside code fences from being
    misread as section headings.
    """
    in_fence = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _ATX_HEADING_RE.match(stripped)
        if match:
            yield len(match.group(1)), match.group(2).strip()


def _plaintext_sections(text: str, fallback_title: str) -> list[SectionNode]:
    """SectionNodes from markdown headings; single-section fallback."""
    sections = [
        SectionNode(id=title[:80], title=title, level=level, page=None)
        for level, title in _scan_headings(text)
    ]
    if not sections:
        return [SectionNode(id=fallback_title, title=fallback_title, level=1,
                            page=None)]
    return sections


_DATA_URI_RE = re.compile(
    r"\[?(\w+)\]?:\s*<data:image/[a-z+]+;base64,[A-Za-z0-9+/=]+>"
)

_MEDIA_DIR_NAME = ".infofact-media"


def _media_dir_for(path: Path) -> Path:
    """Media dir next to the document (workspace-relative)."""
    return path.parent / _MEDIA_DIR_NAME / path.stem


def _sanitize_plaintext_media(
    path: Path, text: str, document_id: str
) -> tuple[str, list[Chunk]]:
    """Replace embedded base64 data-URIs with placeholders + vision chunks.

    A pandoc-exported .md can carry megabytes of base64 in single lines (no
    newline), which defeats the line-based plaintext chunking: chunks up to
    ~350K chars reach the extractor and it returns 0 items (session-14
    failure). Each data-URI is swapped for a short placeholder, the binary is
    materialized under the media dir, and the image becomes an
    ``image_description`` chunk via the vision model — the same convention
    the Docling embedded-pictures path uses (``verification_text`` already
    appends those chunks for span checks). Vision failures degrade to the
    placeholder only: ingestion never breaks.
    """
    from backend.agents.vision import describe_image

    media_dir = _media_dir_for(path)
    out: list[Chunk] = []
    seen = 0

    def _sub(m: re.Match) -> str:
        nonlocal seen
        seen += 1
        ref = m.group(1)
        size_kb = (len(m.group(0)) * 3) // 4 // 1024
        img_path = media_dir / f"{ref}.png"
        try:
            media_dir.mkdir(parents=True, exist_ok=True)
            raw = base64.b64decode(m.group(0).split("base64,", 1)[1], validate=False)
            img_path.write_bytes(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo materializar %s de %s: %s", ref, path.name, exc)
        description = ""
        cap = settings.vision_max_pictures_per_doc
        if settings.supports_vision and seen <= cap:
            try:
                description = describe_image(img_path) or ""
                description = " ".join(description.split())[:2000]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Vision fallo para %s/%s: %s", path.name, ref, exc)
        if description:
            out.append(Chunk(
                text=description,
                document_id=document_id,
                section_path=f"Imagen embebida {ref}",
                element_kinds=("image_description",),
            ))
        return f"\n[imagen: {ref} ~{size_kb} KB — ver descripción al final del documento]\n"

    clean = _DATA_URI_RE.sub(_sub, text)
    if seen:
        logger.info(
            "plaintext media: %d data-URIs reemplazadas en %s (%d descripciones)",
            seen, path.name, len(out),
        )
    return clean, out


def _parse_plaintext(path: Path):
    """Rama plaintext como ``ParsedDoc`` (para el router, Fase C)."""
    from backend.agents.parsers.base import ParsedDoc

    document_id = str(path)
    # Data-URIs first: the verbatim file can carry megabytes of base64 in
    # single lines, which defeats the line-based chunker (session-14).
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    full_text, image_chunks = _sanitize_plaintext_media(path, raw_text, document_id)
    chunks = _chunk_plaintext_text(full_text, document_id, path.name)
    chunks.extend(image_chunks)
    smap = StructureMap(
        document_id=document_id,
        sections=_plaintext_sections(full_text, path.name),
        full_text=full_text,
    )
    return ParsedDoc(chunks=chunks, smap=smap, parser_used="plaintext")


def _parse_docling(path: Path):
    """Rama Docling como ``ParsedDoc`` (para el router, Fase C).

    Reproduce exactamente la logica previa de ``ingest_document`` (convert +
    chunk + vision + structure map); el router la elige para born-digital y como
    fallback de GLM-OCR.
    """
    from backend.agents.parsers.base import ParsedDoc

    document_id = str(path)
    suffix = path.suffix.lower()
    doc = convert_document(path)
    chunks = chunk_document(doc, document_id=document_id)
    if _vision_enabled():
        if suffix in IMAGE_EXTENSIONS:
            chunks.extend(_describe_standalone_image(path, document_id))
        else:
            chunks.extend(_describe_embedded_pictures(doc, document_id))
    smap = build_structure_map(doc, document_id=document_id)
    return ParsedDoc(chunks=chunks, smap=smap, parser_used="docling")


def ingest_document(path: Path, *, parser_hint: str = "auto") -> tuple[list[Chunk], StructureMap]:
    """Full ingestion of one document: element-aware chunks + structural map.

    Delega al router de parsers (Fase C): Docling por defecto, GLM-OCR para PDFs
    escaneados / tablas rotadas (si esta habilitado). ``parser_hint``
    (``auto`` | ``docling`` | ``glm-ocr``) pisa la heuristica. Mantiene la firma
    2-tuple para ``smoke_ingestion.py`` y compatibilidad con call sites previos.
    """
    from backend.agents.parsers.router import parse_document

    parsed = parse_document(path, parser_hint)
    return parsed.chunks, parsed.smap
