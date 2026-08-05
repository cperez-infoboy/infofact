#!/usr/bin/env python3
"""
Spike: detección de páginas/tablas rotadas en PDFs nacidos digitales.

Hipótesis del router (Fase C del plan de ingesta mejorada):
  La rotación de una tabla es detectable ANTES de parsear, desde el ángulo
  de cada carácter del text layer. Se usa FPDFText_GetCharAngle vía pypdfium2,
  que ya está en el stack por Docling (cero dependencias nuevas).

Por página imprime: total de caracteres, histograma de ángulos cardinales,
% rotado y bounding-box de los caracteres rotados (dónde en la página).

Con --docling: además ejecuta Docling y compara caracteres extraídos vs
disponibles, para confirmar que Docling pierde texto donde hay rotación.

Uso:
  .venv/bin/python scripts/spike_rotation_detect.py <pdf> [--docling] [--threshold 30]
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field

import pypdfium2 as pdfium


@dataclass
class PageRotation:
    page: int
    char_count: int
    text_len: int
    bins: dict = field(default_factory=dict)   # {0:.., 90:.., 180:.., 270:.., "other":..}
    rotated_pct: float = 0.0
    rotated_bbox: tuple | None = None          # (x0, y0, x1, y1) en puntos PDF


def _cardinal(deg: float) -> int | str:
    """Clasifica un ángulo en el cardenal más cercano (0/90/180/270)."""
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


def analyze_page(raw, tp, page_idx: int, sample_cap: int = 20000) -> PageRotation:
    """Histograma de ángulos + bbox de caracteres rotados para una página."""
    n = raw.FPDFText_CountChars(tp)
    txt = tp.get_text_range() if hasattr(tp, "get_text_range") else ""
    bins = {0: 0, 90: 0, 180: 0, 270: 0, "other": 0}
    xs0 = ys0 = xs1 = ys1 = None
    for i in range(min(n, sample_cap)):
        try:
            deg = math.degrees(float(raw.FPDFText_GetCharAngle(tp, i)))
        except Exception:
            bins["other"] += 1
            continue
        k = _cardinal(deg)
        bins[k] += 1
        if k in (90, 270):
            # Acumula el bbox de los caracteres rotados para ver la región afectada.
            try:
                x0, y0, x1, y1 = raw.FPDFText_GetCharBox(tp, i)
                xs0 = x0 if xs0 is None else min(xs0, x0)
                ys0 = y0 if ys0 is None else min(ys0, y0)
                xs1 = x1 if xs1 is None else max(xs1, x1)
                ys1 = y1 if ys1 is None else max(ys1, y1)
            except Exception:
                pass
    rotated = bins[90] + bins[270]
    denom = (bins[0] + bins[90] + bins[180] + bins[270]) or 1
    return PageRotation(
        page=page_idx,
        char_count=n,
        text_len=len(txt),
        bins=bins,
        rotated_pct=100.0 * rotated / denom,
        rotated_bbox=(xs0, ys0, xs1, ys1) if xs0 is not None else None,
    )


def detect_rotation(path: str, sample_cap: int) -> list[PageRotation]:
    raw = pdfium.raw
    pdf = pdfium.PdfDocument(path)
    return [analyze_page(raw, pdf[idx].get_textpage(), idx, sample_cap) for idx in range(len(pdf))]


def docling_chars_per_page(path: str) -> tuple[dict[int, int], object]:
    """Corre Docling y devuelve (chars por página 1-indexed, documento)."""
    import logging
    for name in ("docling", "docling.models", "docling.pipeline"):
        logging.getLogger(name).setLevel(logging.ERROR)
    from collections import defaultdict
    from docling.document_converter import DocumentConverter

    doc = DocumentConverter().convert(path).document
    by_page: dict[int, int] = defaultdict(int)
    try:
        items = list(doc.iter_items())
    except Exception:
        items = list(getattr(doc, "texts", [])) + list(getattr(doc, "tables", []))
    for item in items:
        for prov in (getattr(item, "prov", None) or []):
            pno = getattr(prov, "page_no", None) or (
                prov.get("page_no") if isinstance(prov, dict) else None
            )
            if pno is None:
                continue
            txt = getattr(item, "text", None)
            if txt:
                by_page[pno] += len(txt)
    return by_page, doc


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("pdf", help="ruta al PDF")
    ap.add_argument("--threshold", type=float, default=30.0,
                    help="%% rotado para marcar página como rotada (default 30)")
    ap.add_argument("--docling", action="store_true",
                    help="corre Docling y compara texto extraído vs disponible")
    ap.add_argument("--sample-cap", type=int, default=20000,
                    help="tope de caracteres a inspeccionar por página (default 20000)")
    args = ap.parse_args()

    pages = detect_rotation(args.pdf, args.sample_cap)
    rotated = [p for p in pages if p.rotated_pct >= args.threshold]

    print(f"PDF: {args.pdf}")
    print(f"Total páginas: {len(pages)} | umbral rotación: {args.threshold:.0f}%\n")
    print(f"{'pag':>4} {'chars':>7} {'0°':>7} {'90°':>5} {'180°':>5} {'270°':>6} {'rot%':>6}  bbox_rotados")
    for p in pages:
        flag = " <-- ROTADA" if p.rotated_pct >= args.threshold else ""
        if p.rotated_bbox is not None:
            x0, y0, x1, y1 = p.rotated_bbox
            bb = f"({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f})"
        else:
            bb = "(n/a)"
        print(
            f"{p.page:>4} {p.char_count:>7} {p.bins[0]:>7} {p.bins[90]:>5} "
            f"{p.bins[180]:>5} {p.bins[270]:>6} {p.rotated_pct:>5.1f}%  {bb}{flag}"
        )

    print(f"\nVeredicto: {len(rotated)} página(s) rotada(s): {[r.page for r in rotated]}")

    if args.docling:
        print("\n=== Docling: texto extraído vs disponible en el text layer ===")
        dcp, doc = docling_chars_per_page(args.pdf)
        print(f"Docling detectó {len(doc.tables)} tabla(s).")
        print(f"{'pag':>4} {'pdf_chars':>10} {'docling_chars':>14} {'recuperado':>10}")
        for p in pages:
            dc = dcp.get(p.page + 1, 0)  # Docling usa páginas 1-indexed
            rec = f"{100 * dc / p.char_count:.1f}%" if p.char_count else "-"
            loss = "  <-- Docling pierde texto" if (p.char_count and dc < 0.5 * p.char_count) else ""
            print(f"{p.page:>4} {p.char_count:>10} {dc:>14} {rec:>10}{loss}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
