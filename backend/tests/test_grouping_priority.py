"""Tests for priority-aware keeper selection in grouping (explicit > MoSCoW > confidence).

Pins the rule the user asked for: when duplicates are grouped, the item whose
priority was EXPLICITLY declared by the client wins as keeper — even if a
duplicate's verb-inferred MoSCoW is "higher". The signal is the
``explicit_priority`` column (set from the pipeline's ``priority_hint``).
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.agents.pipelines import grouping


def _item(code, prio, explicit_prio, conf):
    return SimpleNamespace(
        code=code,
        priority=SimpleNamespace(value=prio),
        explicit_priority=explicit_prio,
        confidence=conf,
    )


def test_keeper_prefers_explicit_priority_over_higher_moscow():
    """An explicitly-prioritized item wins over an inferred one even when the
    inferred MoSCoW is higher AND its confidence is higher."""
    explicit_media = _item("REQ-A", "should", explicit_prio=True, conf=0.7)
    inferred_must = _item("REQ-B", "must", explicit_prio=False, conf=0.9)

    keeper = min([explicit_media, inferred_must], key=grouping._keeper_key)
    assert keeper.code == "REQ-A"


def test_keeper_among_two_explicit_picks_higher_moscow():
    """When both are explicit, the higher MoSCoW rank wins (then confidence)."""
    explicit_alta = _item("REQ-C", "must", explicit_prio=True, conf=0.5)
    explicit_media = _item("REQ-D", "should", explicit_prio=True, conf=0.9)

    keeper = min([explicit_alta, explicit_media], key=grouping._keeper_key)
    assert keeper.code == "REQ-C"


def test_keeper_no_explicit_falls_back_to_moscow_then_confidence():
    """When neither is explicit, higher MoSCoW wins (the historical verb-based intent)."""
    inferred_must = _item("REQ-E", "must", explicit_prio=False, conf=0.5)
    inferred_should = _item("REQ-F", "should", explicit_prio=False, conf=0.9)

    keeper = min([inferred_must, inferred_should], key=grouping._keeper_key)
    assert keeper.code == "REQ-E"


def test_keeper_confidence_ties_break_same_explicit_same_moscow():
    """Same explicit-ness + same MoSCoW -> higher confidence wins."""
    a = _item("REQ-G", "must", explicit_prio=True, conf=0.8)
    b = _item("REQ-H", "must", explicit_prio=True, conf=0.5)

    keeper = min([a, b], key=grouping._keeper_key)
    assert keeper.code == "REQ-G"
