"""Tests for image-description inclusion in span verification.

Image-derived requirements were incorrectly marked UNVERIFIED because the
image description text was absent from doc_texts (which only contained
smap.full_text — Docling's markdown export, without vision-generated content).
"""

from backend.agents.pipelines.extraction import RawRequirement, verify_spans
from backend.agents.pipelines.ingestion import (
    Chunk,
    StructureMap,
    verification_text,
)


# ---------------------------------------------------------------------------
# verification_text helper
# ---------------------------------------------------------------------------

def test_verification_text_includes_image_description():
    """Image description text must appear in the verification haystack."""
    chunks = [
        Chunk(text="Body paragraph", document_id="doc1", section_path="1.1"),
        Chunk(
            text="El sistema debe soportar autenticacion LDAP",
            document_id="doc1",
            section_path="Imagen embebida",
            element_kinds=("image_description",),
        ),
    ]
    smap = StructureMap(document_id="doc1", full_text="Texto del documento")
    result = verification_text(chunks, smap)
    assert "Texto del documento" in result
    assert "El sistema debe soportar autenticacion LDAP" in result


def test_verification_text_excludes_non_image_chunks():
    """Only image_description chunks are appended, not regular chunks."""
    chunks = [
        Chunk(text="Body paragraph", document_id="doc1", section_path="1.1"),
    ]
    smap = StructureMap(document_id="doc1", full_text="Texto base")
    result = verification_text(chunks, smap)
    assert "Body paragraph" not in result
    assert "Texto base" in result


def test_verification_text_empty_smap():
    """Standalone image: smap.full_text is empty, description is the only text."""
    chunks = [
        Chunk(
            text="Diagrama de arquitectura con tres capas",
            document_id="img1",
            section_path="diagrama.png",
            element_kinds=("image_description",),
        ),
    ]
    smap = StructureMap(document_id="img1", full_text="")
    result = verification_text(chunks, smap)
    assert "Diagrama de arquitectura con tres capas" in result


# ---------------------------------------------------------------------------
# verify_spans integration
# ---------------------------------------------------------------------------

def test_verify_spans_passes_for_image_description():
    """A requirement sourced from an image description passes verification."""
    description = (
        "El diagrama muestra que el sistema debe procesar transacciones "
        "concurrentes con bloqueo optimista"
    )
    chunks = [
        Chunk(
            text=description,
            document_id="doc1",
            section_path="Imagen embebida",
            element_kinds=("image_description",),
        ),
    ]
    smap = StructureMap(document_id="doc1", full_text="Documento sin requisitos")
    doc_texts = {"doc1": verification_text(chunks, smap)}

    items = [
        RawRequirement(
            statement="El sistema debe usar bloqueo optimista",
            source_span="el sistema debe procesar transacciones concurrentes con bloqueo optimista",
            section="Imagen embebida",
            confidence=0.9,
            document_id="doc1",
        ),
    ]
    verify_spans(items, doc_texts)
    assert items[0].span_verified is True
    assert items[0].status != "unverified"


def test_verify_spans_fails_without_image_description_in_haystack():
    """Without the fix (plain smap.full_text), the same requirement fails."""
    description = (
        "El diagrama muestra que el sistema debe procesar transacciones "
        "concurrentes con bloqueo optimista"
    )
    smap = StructureMap(document_id="doc1", full_text="Documento sin requisitos")
    doc_texts = {"doc1": smap.full_text}  # Old behavior: no image text

    items = [
        RawRequirement(
            statement="El sistema debe usar bloqueo optimista",
            source_span="el sistema debe procesar transacciones concurrentes con bloqueo optimista",
            section="Imagen embebida",
            confidence=0.9,
            document_id="doc1",
        ),
    ]
    verify_spans(items, doc_texts)
    assert items[0].span_verified is False
    assert items[0].status == "unverified"
