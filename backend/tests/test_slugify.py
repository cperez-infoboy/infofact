"""Tests for backend.services.slugify.slugify."""
from __future__ import annotations

import pytest

from backend.services.slugify import slugify


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Mi Proyecto", "mi-proyecto"),
        ("Proyecto 1", "proyecto-1"),
        ("API Gateway v2", "api-gateway-v2"),
        ("Already-clean", "already-clean"),
        ("under_score_ok", "under_score_ok"),
        ("multi   spaces", "multi-spaces"),
        ("trim-dashes-", "trim-dashes"),
        ("-leading", "leading"),
    ],
)
def test_basic_slug(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Diseño", "diseno"),
        ("Información", "informacion"),
        ("Ñandú", "nandu"),
        ("Garçon", "garcon"),
        ("Ünicode", "unicode"),
    ],
)
def test_accents_stripped(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("UPPERCASE", "uppercase"),
        ("CamelCase", "camelcase"),
        ("MixEdCaSe", "mixedcase"),
    ],
)
def test_uppercase(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


@pytest.mark.parametrize(
    ("raw",),
    [
        ("",),
        ("   ",),
        ("!",),
        ("....",),
        ("- - -",),
        ("@@@",),
    ],
)
def test_empty_or_punct_raises(raw: str) -> None:
    with pytest.raises(ValueError):
        slugify(raw)


def test_single_char_raises() -> None:
    """A 1-char slug violates the minimum length of 2."""
    with pytest.raises(ValueError):
        slugify("a")


def test_two_chars_ok() -> None:
    assert slugify("ab") == "ab"


def test_too_long_truncates_to_48() -> None:
    raw = "a" * 200
    out = slugify(raw)
    assert len(out) <= 48
    assert out == "a" * 48


def test_truncate_respects_min_len_after_strip() -> None:
    """Truncation must not leave a too-short remainder after rstrip('-')."""
    raw = ("a" * 47) + "-" * 100
    out = slugify(raw)
    assert 2 <= len(out) <= 48


def test_special_chars_collapse() -> None:
    assert slugify("a!@#b$%^c&*d") == "a-b-c-d"


def test_output_matches_valid_pattern() -> None:
    import re

    pattern = re.compile(r"^[a-z0-9_-]{2,48}$")
    samples = [
        "Mi Proyecto!",
        "Diseño Web 2.0",
        "A" * 100,
        "x@y#z",
        "__init__",
    ]
    for s in samples:
        assert pattern.fullmatch(slugify(s)), s
