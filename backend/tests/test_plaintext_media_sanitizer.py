"""Sanitización de data-URIs en plaintext (lección sesión 14).

Un .md exportado por pandoc puede traer megabytes de base64 en líneas únicas
sin saltos: el chunker por líneas no puede partirlos y al extractor llegan
chunks de hasta ~350K chars, la extracción devuelve 0 items. El sanitizador
reemplaza cada data-URI por un placeholder corto, materializa el binario en
el directorio de media y agrega chunks ``image_description`` (misma
convención de la ruta Docling que ``verification_text`` ya appendea para
verify_spans).
"""
from __future__ import annotations

import base64
from pathlib import Path

from backend.agents.pipelines.extraction import (
    RawRequirement,
    verify_spans,
)
from backend.agents.pipelines.ingestion import (
    StructureMap,
    _chunk_plaintext_text,
    _parse_plaintext,
    _sanitize_plaintext_media,
    _split_plaintext,
)

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"A" * 500


def _md_with_data_uris(*sizes: int) -> str:
    uris = "\n\n".join(
        f"[image{i + 1}]: <data:image/png;base64,"
        + base64.b64encode(FAKE_PNG + b"B" * n).decode()
        + ">"
        for i, n in enumerate(sizes)
    )
    return (
        "# Doc\n\nRequerimiento: el sistema debe navegar offline.\n\n"
        + uris
        + "\n\n## Seccion 2\n\nOtro requerimiento: tolerancia de desvio 30m.\n"
    )


def _write_md(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "doc_con_media.md"
    p.write_text(text, encoding="utf-8")
    return p


def _ensure_vision_fields(monkeypatch, ingestion) -> None:
    """Garantiza supports_vision True sin tocar la property de solo lectura.

    Los campos reales ya traen key+modelo del .env del entorno; si faltaran
    (CI sin secretos), los fija para que la property dé True.
    """
    if not ingestion.settings.supports_vision:
        monkeypatch.setattr(
            ingestion.settings, "llm_api_key", "test-key", raising=False
        )
        monkeypatch.setattr(
            ingestion.settings, "llm_vision_model", "test-vision", raising=False
        )


def test_data_uri_single_line_defeats_line_chunker():
    """Pin del modo de falla de sesión 14: la data-URI es una línea única que
    _split_plaintext no puede partir aunque supere el presupuesto."""
    huge = base64.b64encode(FAKE_PNG + b"B" * 200_000).decode()
    text = f"# Doc\n\n[image1]: <data:image/png;base64,{huge}>\n\nfin\n"
    sizes = [len(t) for _, t in _split_plaintext(text, "doc.md")]
    assert max(sizes) > 32_000


def test_sanitize_bounds_chunks_and_strips_base64(tmp_path):
    p = _write_md(tmp_path, _md_with_data_uris(200_000, 100_000))
    parsed = _parse_plaintext(p)
    sizes = [len(c.text) for c in parsed.chunks]
    assert max(sizes) <= 32_000
    assert "data:image" not in parsed.smap.full_text
    assert "[imagen: image1" in parsed.smap.full_text


def test_sanitize_materializes_media_dir(tmp_path):
    p = _write_md(tmp_path, _md_with_data_uris(1_000, 2_000))
    _parse_plaintext(p)
    media = sorted((p.parent / ".infofact-media" / p.stem).glob("image*.png"))
    assert [f.name for f in media] == ["image1.png", "image2.png"]
    assert media[0].stat().st_size > 500


def test_sanitize_without_vision_still_sanitizes(tmp_path, monkeypatch):
    """Sin modelo de visión, el placeholder queda igual y la ingesta no se
    rompe: degradación elegante."""
    from backend.agents.pipelines import ingestion

    # supports_vision es una property de solo lectura: se apaga vaciando los
    # campos que la determinan (key + modelo de visión).
    monkeypatch.setattr(ingestion.settings, "llm_vision_api_key", None, raising=False)
    monkeypatch.setattr(ingestion.settings, "llm_api_key", None, raising=False)
    monkeypatch.setattr(ingestion.settings, "llm_vision_model", None, raising=False)
    p = _write_md(tmp_path, _md_with_data_uris(1_000))
    parsed = _parse_plaintext(p)
    assert "data:image" not in parsed.smap.full_text
    assert not [
        c for c in parsed.chunks
        if c.element_kinds and "image_description" in c.element_kinds
    ]


def test_sanitize_vision_failure_degrades(tmp_path, monkeypatch):
    from backend.agents.pipelines import ingestion

    def boom(img_path):
        raise RuntimeError("vision down")

    _ensure_vision_fields(monkeypatch, ingestion)
    monkeypatch.setattr(
        "backend.agents.vision.describe_image", boom, raising=False
    )
    p = _write_md(tmp_path, _md_with_data_uris(1_000))
    parsed = _parse_plaintext(p)  # no debe lanzar
    assert "data:image" not in parsed.smap.full_text


def test_sanitize_cap_respects_vision_max_pictures(tmp_path, monkeypatch):
    from backend.agents.pipelines import ingestion

    calls: list[str] = []

    def fake_describe(img_path):
        calls.append(img_path.name)
        return f"descripcion de {img_path.name}"

    _ensure_vision_fields(monkeypatch, ingestion)
    monkeypatch.setattr(
        "backend.agents.vision.describe_image", fake_describe, raising=False
    )
    monkeypatch.setattr(
        ingestion.settings, "vision_max_pictures_per_doc", 2, raising=False
    )
    p = _write_md(tmp_path, _md_with_data_uris(10, 10, 10, 10, 10))
    parsed = _parse_plaintext(p)
    assert len(calls) == 2  # cap de visión
    descriptions = [
        c for c in parsed.chunks
        if c.element_kinds and "image_description" in c.element_kinds
    ]
    assert len(descriptions) == 2


def test_image_chunk_feeds_span_verification(tmp_path, monkeypatch):
    """Un requerimiento citado desde una descripción de imagen verifica contra
    el haystack: verification_text appendea los chunks image_description."""
    from backend.agents.pipelines import ingestion
    from backend.agents.pipelines.ingestion import verification_text

    def fake_describe(img_path):
        return (
            "Diagrama de flujo: si el camion se desvia mas de 30 metros el "
            "sistema debe recalcular la ruta offline."
        )

    _ensure_vision_fields(monkeypatch, ingestion)
    monkeypatch.setattr(
        "backend.agents.vision.describe_image", fake_describe, raising=False
    )
    p = _write_md(tmp_path, _md_with_data_uris(1_000))
    parsed = _parse_plaintext(p)

    smap = StructureMap(
        document_id=parsed.smap.document_id,
        sections=[],
        full_text=parsed.smap.full_text,
    )
    haystack = verification_text(parsed.chunks, smap)
    raw = RawRequirement(
        statement="Recalcular la ruta offline cuando el camion se desvia",
        source_span="el sistema debe recalcular la ruta offline",
        section="Imagen embebida image1",
        confidence=0.9,
        document_id=parsed.smap.document_id,
    )
    verify_spans([raw], {parsed.smap.document_id: haystack})
    assert raw.span_verified is True
