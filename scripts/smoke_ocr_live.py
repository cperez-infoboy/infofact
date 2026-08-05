#!/usr/bin/env python3
"""Smoke E2E del parser GLM-OCR (Fase C) contra la API real de Z.ai.

PURE: asserts directos, no pytest. Construye un PDF "escaneado" sintetico
(texto renderizado como pixeles, SIN capa de texto) con contenido conocido,
y verifica el camino completo:

  1. Heuristica: choose_parser_name(pdf, "auto") == "glm-ocr" (0 text layer).
  2. Parser real: parse_document(pdf, "auto") -> API /layout_parsing -> markdown.
  3. Recuperacion: el markdown contiene los tokens conocidos (fuzzy: >=2 de N).

Debe correr DENTRO del contenedor (tiene LLM_API_KEY + httpx + PIL + pypdfium2):

    docker compose exec -T infofact-webui python scripts/smoke_ocr_live.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont

from backend.agents.parsers.router import (
    _pdf_text_layer_chars,
    choose_parser_name,
    parse_document,
)

KNOWN_LINES = [
    "Requisito SIGSA-HS-001 autenticacion de usuarios",
    "Requisito SIGSA-HS-002 registro de eventos de acceso",
    "El sistema debe validar credenciales contra el directorio activo.",
    "Tabla de requerimientos funcionales del modulo de seguridad.",
]
TOKENS = [
    "sigsa-hs-001", "sigsa-hs-002", "autenticacion",
    "credenciales", "validar", "registro", "directorio",
]


def _make_scanned_pdf(lines: list[str]) -> str:
    """Renderiza texto como pixeles en un PDF (sin capa de texto)."""
    W, H = 1240, 1754  # ~A4 a 150 DPI
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    font = None
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ):
        try:
            font = ImageFont.truetype(path, 34)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    y = 140
    for line in lines:
        draw.text((130, y), line, fill="black", font=font)
        y += 64
    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    img.save(tmp, "PDF", resolution=150.0)
    return tmp


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def main() -> int:
    print("1) PDF escaneado sintetico (sin capa de texto)")
    pdf_path = _make_scanned_pdf(KNOWN_LINES)
    try:
        chars = _pdf_text_layer_chars(Path(pdf_path))
        assert chars == 0, f"esperaba 0 chars de text layer, got {chars}"
        _ok(f"capa de texto vacia (chars={chars}) — es un 'scanneo'")

        print("2) Heuristica del router")
        name = choose_parser_name(Path(pdf_path), "auto")
        assert name == "glm-ocr", f"esperaba glm-ocr (auto), got {name}"
        _ok(f"choose_parser_name(auto) -> {name}")

        print("3) Parser real contra la API GLM-OCR")
        parsed = parse_document(Path(pdf_path), "auto")
        assert parsed.parser_used == "glm-ocr", (
            f"esperaba glm-ocr, got {parsed.parser_used}"
        )
        _ok(f"parser_used = {parsed.parser_used}")

        md = parsed.smap.full_text.lower()
        hits = [t for t in TOKENS if t in md]
        assert len(hits) >= 2, (
            f"OCR no recupero texto suficiente (hits={hits})\n--- markdown ---\n"
            f"{parsed.smap.full_text[:600]}"
        )
        _ok(f"tokens recuperados del OCR: {hits}")

        print(
            f"   chunks={len(parsed.chunks)}  "
            f"sections={len(parsed.smap.sections)}  "
            f"markdown={len(parsed.smap.full_text)} chars"
        )
        if parsed.chunks:
            print(f"   sample chunk[0]: {parsed.chunks[0].text[:160]!r}")

        print("\nFase C E2E live (GLM-OCR): OK")
        return 0
    finally:
        try:
            os.unlink(pdf_path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
