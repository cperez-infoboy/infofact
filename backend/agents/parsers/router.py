"""Router de parsers (Fase C): decide upfront que parser usa cada documento.

Heuristica simple, sin LLM:
  1. Born-digital (.docx/.pptx/.html/.txt/.md/.csv/imagenes) -> Docling (capa de
     texto nativa, perfecto y barato). Solo ``.pdf`` es candidato a OCR.
  2. PDFs por capa de texto (``pypdfium2``): si chars/pagina <
     ``ocr_text_layer_threshold`` -> escaneado -> **GLM-OCR** (si disponible).
  3. PDFs born-digital con paginas rotadas (tabla landscape, pagina girada):
     ``_pdf_rotation_flags`` detecta, por pagina, angulos a 90/270 grados
     (``FPDFText_GetCharAngle`` via pypdfium2).
       - ninguna rotada -> Docling.
       - todas rotadas   -> GLM-OCR (todo el documento).
       - algunas rotadas -> **hybrid**: Docling sobre las no rotadas + GLM-OCR
         sobre las rotadas, mergeado por pagina (preserva la fidelidad nativa del
         texto born-digital y lee bien las tablas rotadas).
  4. Hint explicito del usuario: ``glm-ocr`` | ``docling`` | ``auto`` (default);
     siempre pisa la heuristica.

La deteccion de rotacion se hace ANTES de parsear, desde el angulo de cada
caracter del text layer. Validada en scripts/spike_rotation_detect.py.

Seguridad: si GLM-OCR o el mergeo hybrid fallan, ``parse_document`` cae a
Docling sobre el documento entero. El parseo nunca rompe la captura.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from backend.config import settings

logger = logging.getLogger(__name__)

# Umbral (% de caracteres a 90/270 grados) y tope de muestreo por pagina,
# trasladados de scripts/spike_rotation_detect.py. Promovibles a config.py.
_ROTATION_THRESHOLD_PCT = 30.0
_ROTATION_SAMPLE_CAP = 20_000


def _pdf_page_count(path: Path) -> int:
    try:
        import pypdfium2 as pdfium

        return len(pdfium.PdfDocument(str(path)))
    except Exception:  # noqa: BLE001 -- PDF ilegible -> no OCR
        return 0


def _pdf_text_layer_chars(path: Path) -> int:
    """Total de caracteres en la capa de texto del PDF (0 si no se puede leer)."""
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        total = 0
        for i in range(len(pdf)):
            tp = pdf[i].get_textpage()
            if hasattr(tp, "get_text_range"):
                total += len(tp.get_text_range())
        return total
    except Exception:  # noqa: BLE001
        return 0


def ocr_available() -> bool:
    """Si el parser GLM-OCR puede usarse (API key presente y no deshabilitado)."""
    return bool(settings.llm_api_key) and settings.ocr_enabled


def _cardinal_angle(deg: float) -> int | str:
    """Clasifica un angulo en el cardinal mas cercano (0/90/180/270/other)."""
    d = deg % 360
    if d > 315 or d <= 45:
        return 0
    if d <= 135:
        return 90
    if d <= 225:
        return 180
    if d <= 315:
        return 270
    return "other"


def _pdf_rotation_flags(path: Path) -> list[bool]:
    """Por pagina (0-indexada): True si >= ``_ROTATION_THRESHOLD_PCT`` % de sus
    caracteres estan a 90/270 grados.

    Reusa ``FPDFText_GetCharAngle`` via pypdfium2 (cero dependencias nuevas; ya
    esta en el stack por Docling). Validado en scripts/spike_rotation_detect.py
    sobre sigsa_srs.pdf: paginas con tabla landscape rotada 90 grados marcan
    ~100 %. Barato (~ms por pagina).
    """
    try:
        import math
        import pypdfium2 as pdfium

        raw = pdfium.raw
        pdf = pdfium.PdfDocument(str(path))
        flags: list[bool] = []
        for idx in range(len(pdf)):
            tp = pdf[idx].get_textpage()
            n = raw.FPDFText_CountChars(tp)
            if n == 0:
                flags.append(False)
                continue
            bins = {0: 0, 90: 0, 180: 0, 270: 0, "other": 0}
            for i in range(min(n, _ROTATION_SAMPLE_CAP)):
                try:
                    deg = math.degrees(float(raw.FPDFText_GetCharAngle(tp, i)))
                except Exception:  # noqa: BLE001
                    bins["other"] += 1
                    continue
                bins[_cardinal_angle(deg)] += 1
            rotated = bins[90] + bins[270]
            denom = (bins[0] + bins[90] + bins[180] + bins[270]) or 1
            flags.append(100.0 * rotated / denom >= _ROTATION_THRESHOLD_PCT)
        return flags
    except Exception:  # noqa: BLE001 -- PDF ilegible -> no detectamos rotacion
        return []


def _pdf_has_rotated_pages(path: Path) -> bool:
    """True si alguna pagina esta rotada (wrapper de ``_pdf_rotation_flags``)."""
    return any(_pdf_rotation_flags(path))


def _split_pdf(path: Path, page_indices: list[int]) -> Path:
    """Escribe un PDF temporal con solo ``page_indices`` (0-based) de ``path``.

    Preserva la capa de texto (``FPDF_ImportPagesByIndex`` via
    ``PdfDocument.import_pages``), asi Docling sigue leyendo el texto nativo
    sobre el sub-PDF. El llamador debe borrar el archivo temporal.
    """
    import pypdfium2 as pdfium

    src = pdfium.PdfDocument(str(path))
    dst = pdfium.PdfDocument.new()
    dst.import_pages(src, pages=list(page_indices))
    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    dst.save(tmp)
    return Path(tmp)


def choose_parser_name(path: Path, hint: str = "auto") -> str:
    """Devuelve ``'plaintext'`` | ``'docling'`` | ``'glm-ocr'`` | ``'hybrid'``.

    Determinista y barato: para PDFs lee la capa de texto una sola vez
    (~ms con pypdfium2, 0 costo LLM) y, si es born-digital, los angulos de los
    caracteres. El hint explicito pisa la heuristica.
    """
    from backend.agents.pipelines.ingestion import PLAINTEXT_EXTENSIONS

    suffix = path.suffix.lower()
    if suffix in PLAINTEXT_EXTENSIONS:
        return "plaintext"
    if hint == "docling":
        return "docling"
    if hint == "glm-ocr":
        return "glm-ocr" if ocr_available() else "docling"
    # auto: PDFs escaneados, o born-digitals con paginas rotadas, van a OCR.
    if suffix == ".pdf" and ocr_available():
        pages = _pdf_page_count(path)
        if pages > 0:
            chars = _pdf_text_layer_chars(path)
            ratio = chars / pages
            if ratio < settings.ocr_text_layer_threshold:
                logger.info(
                    "router: %s -> glm-ocr (capa de texto %.0f chars/pag < %d)",
                    path.name, ratio, settings.ocr_text_layer_threshold,
                )
                return "glm-ocr"
            rotation = _pdf_rotation_flags(path)
            rotated = sum(1 for r in rotation if r)
            if rotated == 0:
                return "docling"
            if rotated >= pages:
                logger.info(
                    "router: %s -> glm-ocr (born-digital, %d/%d pag rotadas)",
                    path.name, rotated, pages,
                )
                return "glm-ocr"
            logger.info(
                "router: %s -> hybrid (born-digital, %d/%d pag rotadas)",
                path.name, rotated, pages,
            )
            return "hybrid"
    return "docling"


def _remap_page(page, mapping: dict[int, int]):
    """Mapea pagina del sub-PDF (1-based) a pagina original; None pasa directo."""
    if page is None:
        return None
    try:
        return mapping.get(int(page), page)
    except (TypeError, ValueError):
        return page


def _parse_hybrid(path: Path):
    """Docling sobre paginas no rotadas + GLM-OCR sobre rotadas, mergeado.

    Devuelve un ``ParsedDoc('hybrid')`` con chunks/sections/tables re-mapeados a
    las paginas originales del PDF completo (cada parser trabaja sobre un
    sub-PDF cuyas paginas son 1..N; se mapean de vuelta a la pagina original).

    ``full_text`` concatena el texto nativo (paginas no rotadas) seguido del OCR
    (paginas rotadas). NO esta en orden estricto de pagina (Docling no segmenta
    su markdown por pagina), pero contiene todo el texto limpio. Los chunks SI
    conservan la pagina original correcta, que es lo que usa la captura para
    ``source_span`` y la trazabilidad.
    """
    from backend.agents.parsers import glm_ocr
    from backend.agents.parsers.base import ParsedDoc
    from backend.agents.pipelines import ingestion
    from backend.agents.pipelines.ingestion import StructureMap

    pages = _pdf_page_count(path)
    rotation = _pdf_rotation_flags(path)
    if len(rotation) < pages:
        rotation = rotation + [False] * (pages - len(rotation))
    rotated_indices = [i for i, r in enumerate(rotation) if r]
    normal_indices = [i for i in range(pages) if not rotation[i]]
    # sub-PDF page (1-based) -> original page (1-based)
    rotated_map = {sub + 1: orig + 1 for sub, orig in enumerate(rotated_indices)}
    normal_map = {sub + 1: orig + 1 for sub, orig in enumerate(normal_indices)}

    document_id = str(path)
    chunks = []
    sections = []
    tables = []
    full_parts: list[str] = []

    if normal_indices:
        sub = _split_pdf(path, normal_indices)
        try:
            d = ingestion._parse_docling(sub)
        finally:
            sub.unlink(missing_ok=True)
        for c in d.chunks:
            c.page = _remap_page(c.page, normal_map)
            c.document_id = document_id
            chunks.append(c)
        for s in d.smap.sections:
            s.page = _remap_page(s.page, normal_map)
            sections.append(s)
        for t in d.smap.tables:
            t.page = _remap_page(t.page, normal_map)
            tables.append(t)
        if d.smap.full_text:
            full_parts.append(d.smap.full_text)

    if rotated_indices:
        sub = _split_pdf(path, rotated_indices)
        try:
            o = glm_ocr.parse_with_glm_ocr(sub)
        finally:
            sub.unlink(missing_ok=True)
        for c in o.chunks:
            c.page = _remap_page(c.page, rotated_map)
            c.document_id = document_id
            chunks.append(c)
        for s in o.smap.sections:
            s.page = _remap_page(s.page, rotated_map)
            sections.append(s)
        if o.smap.full_text:
            full_parts.append(o.smap.full_text)

    # Indices consecutivos: no rotados primero, rotados despues.
    for i, c in enumerate(chunks):
        c.index = i

    smap = StructureMap(
        document_id=document_id,
        sections=sections,
        tables=tables,
        full_text="\n\n".join(p for p in full_parts if p),
        page_count=pages,
    )
    return ParsedDoc(chunks=chunks, smap=smap, parser_used="hybrid")


def parse_document(path: Path, hint: str = "auto", *, _name: str | None = None):
    """Despacha al parser elegido y devuelve un ``ParsedDoc``.

    Si GLM-OCR o el mergeo hybrid fallan (API, timeout, respuesta malformada,
    split de PDF), cae a Docling sobre el documento entero: nunca deja al
    llamador sin parseo. ``_name`` permite reutilizar la decision del router (lo
    usa ``parse_cache`` para no releer la capa de texto dos veces).
    """
    from backend.agents.pipelines.ingestion import _parse_docling, _parse_plaintext

    name = _name or choose_parser_name(path, hint)
    if name == "plaintext":
        return _parse_plaintext(path)
    if name == "hybrid":
        try:
            return _parse_hybrid(path)
        except Exception as exc:  # noqa: BLE001 -- hybrid nunca rompe la captura
            logger.warning("hybrid fallo (%s); cayendo a docling entero", exc)
            return _parse_docling(path)
    if name == "glm-ocr":
        try:
            from backend.agents.parsers.glm_ocr import parse_with_glm_ocr

            return parse_with_glm_ocr(path)
        except Exception as exc:  # noqa: BLE001 -- OCR nunca rompe la captura
            logger.warning("glm-ocr fallo (%s); cayendo a docling", exc)
            return _parse_docling(path)
    return _parse_docling(path)
