#!/usr/bin/env python3
"""Smoke: router de parsers (Fase C) — heuristica de routing, sin API.

PURE: asserts directos, no pytest. Stubea los lectores de capa de texto y
``ocr_available`` para testear la logica del router sin PDFs reales ni la API
de GLM-OCR. Verifica ademas que ``parse_document`` despacha plaintext end-to-end
(sin Docling).

Uso: .venv/bin/python scripts/smoke_ocr_router.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agents.parsers import router


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def main() -> int:
    # Stubear los lectores de PDF + disponibilidad de OCR para testear la logica.
    router._pdf_page_count = lambda p: 10

    # --- 1. Heuristica por capa de texto --------------------------------
    print("1) choose_parser_name (heuristica auto)")
    router.ocr_available = lambda: True

    router._pdf_text_layer_chars = lambda p: 100  # 10 chars/pag < 500 -> escaneado
    assert router.choose_parser_name(Path("scan.pdf"), "auto") == "glm-ocr", (
        "PDF escaneado -> glm-ocr"
    )
    _ok("PDF escaneado (10 chars/pag) -> glm-ocr")

    router._pdf_text_layer_chars = lambda p: 10000  # 1000 chars/pag -> born-digital
    assert router.choose_parser_name(Path("born.pdf"), "auto") == "docling", (
        "PDF born-digital -> docling"
    )
    _ok("PDF born-digital (1000 chars/pag) -> docling")

    assert router.choose_parser_name(Path("nota.txt"), "auto") == "plaintext"
    _ok(".txt -> plaintext")
    assert router.choose_parser_name(Path("doc.docx"), "auto") == "docling"
    _ok(".docx -> docling (born-digital, nunca OCR)")

    # --- 2. Hint explicito pisa la heuristica ---------------------------
    print("2) hint explicito")
    router._pdf_text_layer_chars = lambda p: 100  # seria glm-ocr en auto
    assert router.choose_parser_name(Path("scan.pdf"), "docling") == "docling"
    _ok("hint=docling pisa la heuristica")
    assert router.choose_parser_name(Path("born.pdf"), "glm-ocr") == "glm-ocr"
    _ok("hint=glm-ocr fuerza OCR")

    # --- 3. OCR no disponible -> siempre docling (fallback seguro) -------
    print("3) OCR no disponible (fallback)")
    router.ocr_available = lambda: False
    assert router.choose_parser_name(Path("scan.pdf"), "auto") == "docling"
    _ok("auto cae a docling cuando OCR no disponible")
    assert router.choose_parser_name(Path("scan.pdf"), "glm-ocr") == "docling"
    _ok("hint=glm-ocr cae a docling cuando OCR no disponible")

    # --- 4. parse_document despacha plaintext end-to-end (sin Docling) --
    print("4) parse_document (plaintext, sin Docling)")
    router.ocr_available = lambda: True  # restaurar
    tmp = Path(tempfile.mktemp(suffix=".txt"))
    tmp.write_text(
        "El sistema debe autenticar usuarios.\n\nDebe loggear eventos de acceso.",
        encoding="utf-8",
    )
    parsed = router.parse_document(tmp, "auto")
    assert parsed.parser_used == "plaintext", (
        f"esperaba plaintext, got {parsed.parser_used}"
    )
    assert len(parsed.chunks) == 1, f"plaintext -> 1 chunk, got {len(parsed.chunks)}"
    assert "autenticar" in parsed.chunks[0].text
    _ok("parse_document(.txt) -> ParsedDoc('plaintext') con contenido")
    tmp.unlink()

    # --- 5. Rotacion en born-digital -----------------------------------
    print("5) choose_parser_name (rotacion born-digital)")
    router.ocr_available = lambda: True
    router._pdf_page_count = lambda p: 10
    router._pdf_text_layer_chars = lambda p: 10000  # born-digital

    router._pdf_rotation_flags = lambda p: [True] * 10  # todas rotadas
    assert router.choose_parser_name(Path("born_rot.pdf"), "auto") == "glm-ocr", (
        "PDF born-digital con todas rotadas -> glm-ocr"
    )
    _ok("PDF born-digital con todas rotadas -> glm-ocr")

    router._pdf_rotation_flags = lambda p: [False] * 10  # sin rotacion
    assert router.choose_parser_name(Path("born_ok.pdf"), "auto") == "docling", (
        "PDF born-digital sin rotacion -> docling"
    )
    _ok("PDF born-digital sin rotacion -> docling")

    # Escaneado sigue ganando por capa de texto aunque no haya angulos rotados.
    router._pdf_text_layer_chars = lambda p: 100  # escaneado
    router._pdf_rotation_flags = lambda p: [False] * 10
    assert router.choose_parser_name(Path("scan.pdf"), "auto") == "glm-ocr"
    _ok("PDF escaneado (sin angulos rotados) -> glm-ocr por capa de texto")

    # --- 6. Hibrido (algunas paginas rotadas, no todas) -----------------
    print("6) choose_parser_name (hibrido)")
    router._pdf_text_layer_chars = lambda p: 10000  # born-digital
    router._pdf_page_count = lambda p: 5
    router._pdf_rotation_flags = lambda p: [False, True, False, False, True]  # 2/5
    assert router.choose_parser_name(Path("mix.pdf"), "auto") == "hybrid", (
        "born-digital con algunas paginas rotadas -> hybrid"
    )
    _ok("born-digital con algunas (no todas) rotadas -> hybrid")

    # --- 7. _parse_hybrid mergea por pagina con re-mapeo ----------------
    print("7) _parse_hybrid (merge Docling no-rotadas + GLM-OCR rotadas)")
    from backend.agents.parsers import glm_ocr as glm_mod
    from backend.agents.parsers.base import ParsedDoc
    from backend.agents.pipelines import ingestion as ingestion_mod
    from backend.agents.pipelines.ingestion import Chunk, SectionNode, StructureMap

    router._pdf_page_count = lambda p: 4
    router._pdf_rotation_flags = lambda p: [False, True, False, True]  # pags 2,4

    splits: dict[str, list[int]] = {}

    def fake_split(path, indices):
        splits["normal" if 0 in indices else "rotado"] = list(indices)
        return Path(f"/tmp/fake_{'normal' if 0 in indices else 'rotado'}.pdf")

    def fake_docling(sub):
        return ParsedDoc(
            chunks=[
                Chunk(text="nativa pag1", document_id=str(sub), section_path="", page=1, index=0),
                Chunk(text="nativa pag3", document_id=str(sub), section_path="", page=2, index=1),
            ],
            smap=StructureMap(
                document_id=str(sub),
                sections=[SectionNode(id="s1", title="S1", level=1, page=1)],
                full_text="NATIVA",
                page_count=2,
            ),
            parser_used="docling",
        )

    def fake_ocr(sub):
        return ParsedDoc(
            chunks=[
                Chunk(text="ocr pag2", document_id=str(sub), section_path="", page=1, index=0),
                Chunk(text="ocr pag4", document_id=str(sub), section_path="", page=2, index=1),
            ],
            smap=StructureMap(document_id=str(sub), full_text="OCR"),
            parser_used="glm-ocr",
        )

    router._split_pdf = fake_split
    ingestion_mod._parse_docling = fake_docling
    glm_mod.parse_with_glm_ocr = fake_ocr

    out = router.parse_document(Path("/tmp/doc.pdf"), "auto", _name="hybrid")
    assert out.parser_used == "hybrid", out.parser_used
    _ok("parse_document(_name=hybrid) -> ParsedDoc('hybrid')")

    pages = sorted(c.page for c in out.chunks)
    assert pages == [1, 2, 3, 4], f"paginas re-mapeadas: {pages}"
    _ok(f"chunks re-mapeados a paginas originales {pages}")

    assert [c.index for c in out.chunks] == [0, 1, 2, 3], [c.index for c in out.chunks]
    _ok("indices de chunks renumerados 0..3")

    assert splits["normal"] == [0, 2], splits
    assert splits["rotado"] == [1, 3], splits
    _ok(f"split: no-rotadas={splits['normal']} rotadas={splits['rotado']}")

    texts = " ".join(c.text for c in out.chunks)
    assert "nativa" in texts and "ocr" in texts, texts
    _ok("merge combina chunks nativos (Docling) + ocr (GLM-OCR)")

    assert out.smap.full_text == "NATIVA\n\nOCR", out.smap.full_text
    _ok(f"full_text = {out.smap.full_text!r} (nativa + ocr)")
    assert out.smap.page_count == 4
    _ok("page_count conserva el total del documento (4)")

    print("\nFase C router smoke: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
