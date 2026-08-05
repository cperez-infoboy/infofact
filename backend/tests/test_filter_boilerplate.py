"""Unit tests for extraction.filter_boilerplate_chunks (Phase-2 call reduction).

Pure data-shape test: the helper drops chunks whose section_path falls under a
section the annotator flagged boilerplate or req_likelihood=="none", cutting
implicit_pass LLM calls over cover/TOC/glossary sections. Scoping is per
document_id so a boilerplate title in one doc never drops chunks of another.
"""
from __future__ import annotations

from backend.agents.pipelines import extraction as exc
from backend.agents.pipelines.ingestion import Chunk, SectionNode, StructureMap


def _chunk(doc: str, section: str) -> Chunk:
    return Chunk(
        text="x", document_id=doc, section_path=section, page=1, index=0,
    )


def _sec(
    title: str, *, boilerplate: bool = False, likelihood: str = "medium"
) -> SectionNode:
    return SectionNode(
        id=title, title=title, level=1, page=1,
        is_boilerplate=boilerplate, req_likelihood=likelihood,
    )


def _smap(doc: str, sections: list[SectionNode]) -> StructureMap:
    return StructureMap(document_id=doc, sections=sections)


def test_no_flagged_sections_returns_all_chunks_unchanged():
    chunks = [_chunk("d1", "Intro"), _chunk("d1", "Body")]
    smaps = [_smap("d1", [_sec("Intro"), _sec("Body")])]
    out = exc.filter_boilerplate_chunks(chunks, smaps)
    assert [c.section_path for c in out] == ["Intro", "Body"]


def test_drops_chunks_under_boilerplate_section():
    chunks = [
        _chunk("d1", "Cover Page"),
        _chunk("d1", "Glossary > Terms"),
        _chunk("d1", "Requirements > Auth"),
    ]
    smaps = [_smap("d1", [
        _sec("Cover Page", boilerplate=True),
        _sec("Glossary", boilerplate=True),
        _sec("Requirements"),
    ])]
    out = exc.filter_boilerplate_chunks(chunks, smaps)
    assert [c.section_path for c in out] == ["Requirements > Auth"]


def test_drops_chunks_under_none_likelihood_section():
    chunks = [_chunk("d1", "References"), _chunk("d1", "Funcional")]
    smaps = [_smap("d1", [
        _sec("References", likelihood="none"),
        _sec("Funcional", likelihood="high"),
    ])]
    out = exc.filter_boilerplate_chunks(chunks, smaps)
    assert [c.section_path for c in out] == ["Funcional"]


def test_scoping_is_per_document():
    # "Cover" is boilerplate only in d1; a d2 chunk whose path contains "Cover"
    # must NOT be dropped.
    chunks = [_chunk("d1", "Cover"), _chunk("d2", "Cover Letter")]
    smaps = [_smap("d1", [_sec("Cover", boilerplate=True)])]
    out = exc.filter_boilerplate_chunks(chunks, smaps)
    assert [c.section_path for c in out] == ["Cover Letter"]


def test_smaps_without_sections_are_tolerated():
    """A smap lacking the sections annotation (a test mock, or a doc whose
    enrich pass hasn't run) must not crash the filter — it contributes no bad
    titles, so all chunks pass through unchanged."""
    import types

    chunks = [_chunk("d1", "Body"), _chunk("d1", "More")]
    mock_smap = types.SimpleNamespace(document_id="d1")  # no .sections attr
    out = exc.filter_boilerplate_chunks(chunks, [mock_smap])
    assert [c.section_path for c in out] == ["Body", "More"]
