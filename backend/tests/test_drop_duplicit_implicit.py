"""Tests for drop_duplicit_implicit (false-implicit dedup by source_span)."""
from __future__ import annotations

from backend.agents.pipelines.consolidation import drop_duplicit_implicit
from backend.agents.pipelines.extraction import RawRequirement


def _req(statement: str, span: str, explicit: bool) -> RawRequirement:
    return RawRequirement(
        statement=statement,
        source_span=span,
        section="s",
        explicit=explicit,
        confidence=0.9,
    )


def test_drops_implicit_sharing_span_with_explicit() -> None:
    explicits = [
        _req("Explorador debe ser Mozilla o Chrome.", "Explorador: Mozilla o Chrome.", True)
    ]
    implicits = [
        _req("compatible con navegadores Mozilla y Chrome", "Explorador: Mozilla o Chrome.", False),
        _req("asuncion distinta", "otra cita del documento", False),
    ]
    kept = drop_duplicit_implicit(implicits, explicits)
    assert len(kept) == 1
    assert kept[0].source_span == "otra cita del documento"


def test_normalizes_span_before_matching() -> None:
    # Case + accent folding must collapse both spans into the same bucket.
    explicits = [_req("foo", "Explorador: Mozilla o Chrome.", True)]
    implicits = [_req("bar", "explorador: mozilla o chrome.", False)]
    kept = drop_duplicit_implicit(implicits, explicits)
    assert kept == []


def test_keeps_implicit_with_empty_span() -> None:
    explicits = [_req("foo", "span-explicito", True)]
    implicits = [_req("asuncion sin cita textual", "", False)]
    kept = drop_duplicit_implicit(implicits, explicits)
    assert len(kept) == 1


def test_empty_explicits_keeps_all_implicits() -> None:
    implicits = [_req("a", "s1", False), _req("b", "s2", False)]
    assert drop_duplicit_implicit(implicits, []) == implicits
