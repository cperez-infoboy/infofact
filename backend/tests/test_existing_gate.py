"""Tests for the run_requirements_capture existing-data guard policy.

The guard prevents a capture from silently appending over an existing capture.
The decision policy is a pure function (_existing_gate) so it is tested in
isolation; the tool wraps it with a DB count + the reset call.
"""
from __future__ import annotations

import pytest

from backend.agents.subagents.requirements_capture import _existing_gate


def test_empty_store_proceeds_on_ask() -> None:
    # No prior capture: nothing to warn about, just run.
    assert _existing_gate(0, "ask") == "proceed"


def test_nonempty_store_blocks_on_ask() -> None:
    # Prior capture exists and the user has not decided yet: block + warn.
    assert _existing_gate(5, "ask") == "block"


def test_nonempty_store_resets_when_confirmed() -> None:
    assert _existing_gate(5, "reset") == "reset"


def test_empty_store_resets_as_proceed() -> None:
    # Nothing to wipe: reset collapses to a plain proceed (no-op wipe).
    assert _existing_gate(0, "reset") == "proceed"


def test_append_always_proceeds() -> None:
    assert _existing_gate(0, "append") == "proceed"
    assert _existing_gate(7, "append") == "proceed"


@pytest.mark.parametrize("bad", ["", "overwrite", "RESETTING", "ask ", "reset_all"])
def test_invalid_on_existing_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        _existing_gate(5, bad)
