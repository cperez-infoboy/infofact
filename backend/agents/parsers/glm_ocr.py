"""Parser GLM-OCR (Fase C): OCR de PDFs escaneados / tablas rotadas via Z.ai.

En PDFs escaneados (sin capa de texto) el router enruta automaticamente a este
parser, porque ahi Docling no recupera texto. En born-digitals con paginas
rotadas, Docling con el converter de produccion (``images_scale=4.0``) SI dispara
RapidOCR y recupera el texto, pero la estructura tabular no es tan fiel como la
de GLM-OCR; por eso para tablas rotadas born-digital conviene forzar
``parser_hint="glm-ocr"`` (la deteccion automatica de rotacion es un gate
diferido, no integrado aun). GLM-OCR (endpoint on-demand ``/api/paas/v4/
layout_parsing``) lee el raster de cada pagina y devuelve ``layout_details`` con
elementos tipados (``label: text|table|image|formula`` + ``bbox_2d`` + ``content``).

Este parser:
  1. Rasteriza cada pagina a PNG (``pypdfium2`` a ``ocr_pdf_dpi``).
  2. Llama a GLM-OCR por pagina (hasta ``ocr_max_pages``).
  3. Arma markdown desde los labels ``text`` / ``table`` / ``formula`` (en orden).
  4. Para cada ``label:image``, recorta el ``bbox_2d`` del raster de la pagina y
     lo describe via ``vision.describe_image`` (Docling no detecta ``PictureItem``
     en rotadas -> el parser es dueno de la imagen en las paginas que procesa).
  5. Chunkuea el markdown (split por headers) y arma un StructureMap sintetico.

Produce markdown + chunks + smap, NO un ``DoclingDocument``: no puede reusar
``chunk_document`` / ``build_structure_map`` (que necesitan un DoclingDocument).

La API es dedicada (``POST /layout_parsing``), NO un chat-completion: se llama
directo con ``httpx`` + ``Authorization: Bearer ${LLM_API_KEY}``.

Si la API falla por pagina, esa pagina se salta (logging warning); si falla
todo, el router (``parse_document``) cae a Docling.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
import tempfile
from pathlib import Path

from backend.config import settings

logger = logging.getLogger(__name__)

_HEADER_RE = re.compile(r"(?m)^(#{1,6})\s+(.+)$")


def parse_with_glm_ocr(path: Path):
    """Parsea un PDF escaneado/rotado con GLM-OCR -> ``ParsedDoc('glm-ocr')``."""
    from backend.agents.parsers.base import ParsedDoc
    from backend.agents.pipelines.ingestion import Chunk, StructureMap

    document_id = str(path)
    page_images = _rasterize_pages(path)
    if not page_images:
        return ParsedDoc(
            chunks=[],
            smap=StructureMap(document_id=document_id),
            parser_used="glm-ocr",
        )

    markdown_parts: list[str] = []
    image_chunks: list[Chunk] = []
    max_images = settings.vision_max_pictures_per_doc
    for page_no, pil_img in page_images:
        data_uri = _pil_to_data_uri(pil_img)
        try:
            data = _call_glm_ocr(data_uri)
        except Exception as exc:  # noqa: BLE001
            logger.warning("glm-ocr pagina %d fallo: %s", page_no, exc)
            continue
        md, image_bboxes = _extract_page(data)
        if md:
            markdown_parts.append(f"<!-- page {page_no} -->\n{md}")
        for bbox in image_bboxes:
            if len(image_chunks) >= max_images:
                logger.info(
                    "glm-ocr: tope de imagenes (%d) alcanzado en %s",
                    max_images, path.name,
                )
                break
            desc = _describe_bbox(pil_img, bbox)
            if desc:
                image_chunks.append(Chunk(
                    text=desc,
                    document_id=document_id,
                    section_path="Imagen (OCR)",
                    element_kinds=("image_description",),
                    page=page_no,
                ))

    full_text = "\n\n".join(markdown_parts)
    chunks = _chunk_markdown(full_text, document_id)
    chunks.extend(image_chunks)
    smap = _synthetic_smap(full_text, document_id, page_count=len(page_images))
    return ParsedDoc(chunks=chunks, smap=smap, parser_used="glm-ocr")


# ---------------------------------------------------------------------------
# Rasterizacion
# ---------------------------------------------------------------------------

def _rasterize_pages(path: Path) -> list[tuple[int, object]]:
    """Devuelve ``[(page_no_1based, PIL.Image)]`` hasta ``ocr_max_pages``."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    scale = settings.ocr_pdf_dpi / 72.0
    out: list[tuple[int, object]] = []
    for i in range(min(len(pdf), settings.ocr_max_pages)):
        try:
            pil = pdf[i].render(scale=scale).to_pil()
            out.append((i + 1, pil))
        except Exception as exc:  # noqa: BLE001
            logger.warning("rasterize pagina %d fallo: %s", i + 1, exc)
    return out


def _pil_to_data_uri(pil_img) -> str:
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


# ---------------------------------------------------------------------------
# GLM-OCR API
# ---------------------------------------------------------------------------

def _call_glm_ocr(data_uri: str) -> dict:
    """POST ``/layout_parsing`` -> JSON crudo de GLM-OCR."""
    import httpx

    url = f"{settings.llm_ocr_base_url.rstrip('/')}/layout_parsing"
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    payload = {"model": settings.llm_ocr_model, "file": data_uri}
    resp = httpx.post(url, json=payload, headers=headers, timeout=180.0)
    resp.raise_for_status()
    return resp.json()


def _strip_html(s: str) -> str:
    """Quita tags HTML, decodifica entidades y colapsa blancos.

    GLM-OCR entrega el ``content`` de text/formula como HTML liviano
    (``<div align="center">...</div>``). Lo reducimos a texto plano legible para
    que el pipeline de extraccion y el retrieval puedan operar sobre el."""
    import html as htmlmod

    s = re.sub(r"<[^>]+>", "\n", s)
    s = htmlmod.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def _html_table_to_md(s: str) -> str:
    """Convierte ``<table><tr><td>...</td></tr></table>`` a tabla markdown.

    GLM-OCR entrega las tablas como HTML; el pipeline de captura/retrieval las
    necesita como texto lineal. Cae a ``_strip_html`` si no hay filas parseables.
    """
    import html as htmlmod

    rows = re.findall(r"<tr>(.*?)</tr>", s, re.DOTALL | re.IGNORECASE)
    parsed: list[list[str]] = []
    for r in rows:
        cells = [
            htmlmod.unescape(re.sub(r"<[^>]+>", "", c)).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.DOTALL | re.IGNORECASE)
        ]
        if cells:
            parsed.append(cells)
    if not parsed:
        return _strip_html(s)
    width = max(len(row) for row in parsed)
    pad = lambda row: (list(row) + [""] * width)[:width]
    lines = [
        "| " + " | ".join(pad(parsed[0])) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in parsed[1:]:
        lines.append("| " + " | ".join(pad(row)) + " |")
    return "\n".join(lines)


def _extract_page(data: dict) -> tuple[str, list[list[int]]]:
    """Devuelve (markdown, bboxes de imagenes) desde la respuesta de una pagina.

    ``layout_details`` es una LISTA DE PAGINAS (cada pagina = lista de
    elementos); como GLM-OCR procesa una pagina por llamada, aplanamos. El
    ``content`` de text/table viene como HTML (``<div>``, ``<table>``): lo
    convertimos a markdown legible. Los ``label:image`` se omiten del markdown y
    su ``bbox_2d`` se devuelve para cropear. Safety net: si no hay elementos,
    cae a ``md_results`` (el markdown que arma la propia API).
    """
    elements: list[dict] = []
    details = data.get("layout_details")
    if isinstance(details, list):
        for entry in details:
            if isinstance(entry, list):
                elements.extend(e for e in entry if isinstance(e, dict))
            elif isinstance(entry, dict):
                elements.append(entry)
    parts: list[str] = []
    image_bboxes: list[list[int]] = []
    for el in elements:
        label = (el.get("label") or "").lower()
        content = el.get("content") or ""
        if label == "table":
            md = _html_table_to_md(content)
            if md.strip():
                parts.append(md.strip())
        elif label in ("text", "formula", "title", "header"):
            text = _strip_html(content)
            if text:
                parts.append(text)
        elif label == "image":
            bbox = el.get("bbox_2d") or el.get("bbox")
            if isinstance(bbox, list) and len(bbox) >= 4:
                image_bboxes.append([int(x) for x in bbox[:4]])
    if not parts:
        # Safety net: la API ensambla su propio markdown en md_results.
        md_res = data.get("md_results")
        if isinstance(md_res, str) and md_res.strip():
            return _strip_html(md_res), image_bboxes
    return "\n\n".join(parts), image_bboxes


def _describe_bbox(pil_img, bbox: list[int]) -> str:
    """Recorta el ``bbox`` del raster de la pagina y lo describe con vision.

    ``bbox_2d`` de GLM-OCR esta en coordenadas de pixel de la imagen renderizada
    (a ``ocr_pdf_dpi``), asi que el crop es directo sobre el raster."""
    try:
        from backend.agents.vision import describe_image
    except ImportError:
        return ""
    x0, y0, x1, y1 = bbox
    w, h = pil_img.size
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return ""
    crop = pil_img.crop((x0, y0, x1, y1))
    if min(crop.size) < 64:
        return ""
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        crop.save(tmp, format="PNG")
        return describe_image(Path(tmp))
    except Exception as exc:  # noqa: BLE001
        logger.warning("describe_bbox fallo: %s", exc)
        return ""
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Markdown -> chunks + StructureMap sinteticos
# ---------------------------------------------------------------------------

def _chunk_markdown(markdown: str, document_id: str) -> list:
    """Chunkuea markdown por headers (cada seccion es un chunk).

    Sin headers, cae a parrafos (split por linea en blanco)."""
    from backend.agents.pipelines.ingestion import Chunk

    if not markdown.strip():
        return []
    headers = list(_HEADER_RE.finditer(markdown))
    if not headers:
        paras = [p.strip() for p in re.split(r"\n\s*\n", markdown) if p.strip()]
        return [
            Chunk(text=p, document_id=document_id, section_path="", index=i)
            for i, p in enumerate(paras)
        ]
    chunks: list[Chunk] = []
    if headers[0].start() > 0:
        pre = markdown[: headers[0].start()].strip()
        if pre:
            chunks.append(
                Chunk(text=pre, document_id=document_id, section_path="", index=0)
            )
    for i, h in enumerate(headers):
        start = h.start()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(markdown)
        section_text = markdown[start:end].strip()
        if section_text:
            chunks.append(Chunk(
                text=section_text,
                document_id=document_id,
                section_path=h.group(2).strip(),
                index=len(chunks),
            ))
    return chunks


def _synthetic_smap(markdown: str, document_id: str, *, page_count: int):
    from backend.agents.pipelines.ingestion import SectionNode, StructureMap

    sections = [
        SectionNode(
            id=m.group(2).strip()[:80] or f"section-{i}",
            title=m.group(2).strip(),
            level=len(m.group(1)),
            page=None,
        )
        for i, m in enumerate(_HEADER_RE.finditer(markdown))
    ]
    return StructureMap(
        document_id=document_id,
        sections=sections,
        tables=[],
        glossary={},
        full_text=markdown,
        page_count=page_count,
    )
