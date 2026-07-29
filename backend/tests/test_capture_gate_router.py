"""Tests for the router-level existing-data guard (bug #2 fix).

Pins the unbypassable pre-flight: a capture command (``/captura`` or
``/captura_agente``) run over existing data WITHOUT an explicit decision must
short-circuit with a confirmation, regardless of what the agent would do with
``on_existing``. This closes the bypass where the agent-driven subagent chose
``on_existing="append"`` on its own and never asked.

Also covers the decision-keyword detection (conservative: ambiguous steering
does NOT count as a decision) and the subpath/decision splitting used by the
deterministic ``/captura`` directive.
"""
from __future__ import annotations

import pytest

from backend.agents.subagents import requirements_capture as rc
from backend.routers import chat


def _patch_count(monkeypatch, existing: dict):
    async def fake_count(_project_id: int) -> dict:
        return existing

    monkeypatch.setattr(rc, "_count_existing", fake_count)


# --- _is_capture_command -------------------------------------------------

def test_is_capture_command_recognizes_both_commands():
    assert chat._is_capture_command("/captura")
    assert chat._is_capture_command("/captura_agente")
    assert chat._is_capture_command("/captura-agente")
    assert chat._is_capture_command("/captura docs/x")
    assert chat._is_capture_command("/captura_agente resetear")


def test_is_capture_command_rejects_non_capture():
    assert not chat._is_capture_command("que requerimientos hay?")
    assert not chat._is_capture_command("/agrupar")
    assert not chat._is_capture_command("")


# --- _user_existing_decision --------------------------------------------

def test_user_existing_decision_reset_markers():
    assert chat._user_existing_decision("/captura_agente resetear") == "reset"
    assert chat._user_existing_decision("/captura resetear enfocate en X") == "reset"
    assert chat._user_existing_decision("empezar de cero") == "reset"


def test_user_existing_decision_append_markers():
    assert chat._user_existing_decision("agregar a los existentes") == "append"
    assert chat._user_existing_decision("mantener los existentes") == "append"
    assert chat._user_existing_decision("append") == "append"


def test_user_existing_decision_ambiguous_is_none():
    # Steering that mentions "agregar" without a clear append decision must NOT
    # bypass the guard.
    assert chat._user_existing_decision("agregar detalle a las restricciones") is None
    assert chat._user_existing_decision("enfocate en los no funcionales") is None
    assert chat._user_existing_decision("") is None


# --- _extract_decision_and_subpath --------------------------------------

def test_extract_decision_strips_reset_keyword():
    sub, decision = chat._extract_decision_and_subpath("resetear")
    assert sub == ""
    assert decision == "reset"


def test_extract_decision_preserves_real_subpath():
    sub, decision = chat._extract_decision_and_subpath("docs/x")
    assert sub == "docs/x"
    assert decision is None


def test_extract_decision_decision_plus_subpath():
    sub, decision = chat._extract_decision_and_subpath("docs/x resetear")
    assert sub == "docs/x"
    assert decision == "reset"


# --- _capture_gate_message ----------------------------------------------

@pytest.mark.asyncio
async def test_capture_gate_none_when_no_existing(monkeypatch):
    _patch_count(monkeypatch, {"requirements": 0, "grouping_plans": 0, "last_code": None})
    assert await chat._capture_gate_message(1, "/captura_agente") is None


@pytest.mark.asyncio
async def test_capture_gate_blocks_when_existing_and_no_decision(monkeypatch):
    _patch_count(
        monkeypatch,
        {"requirements": 533, "grouping_plans": 2, "last_code": "REQ-7K3F"},
    )
    msg = await chat._capture_gate_message(1, "/captura_agente")
    assert msg is not None
    assert "533" in msg
    assert "REQ-7K3F" in msg
    assert "resetear" in msg
    assert "agregar a los existentes" in msg


@pytest.mark.asyncio
async def test_capture_gate_blocks_deterministic_captura_too(monkeypatch):
    _patch_count(
        monkeypatch,
        {"requirements": 10, "grouping_plans": 0, "last_code": "REQ-ABCD"},
    )
    msg = await chat._capture_gate_message(1, "/captura")
    assert msg is not None
    assert "10" in msg


@pytest.mark.asyncio
async def test_capture_gate_lets_through_when_decision_present(monkeypatch):
    _patch_count(
        monkeypatch,
        {"requirements": 533, "grouping_plans": 2, "last_code": "REQ-7K3F"},
    )
    # Explicit decision => no gate, even with existing data.
    assert await chat._capture_gate_message(1, "/captura_agente resetear") is None
    assert (
        await chat._capture_gate_message(1, "/captura_agente agregar a los existentes")
        is None
    )


@pytest.mark.asyncio
async def test_capture_gate_ignores_non_capture_commands(monkeypatch):
    _patch_count(monkeypatch, {"requirements": 999, "grouping_plans": 0, "last_code": None})
    assert await chat._capture_gate_message(1, "hola") is None
    assert await chat._capture_gate_message(1, "/agrupar") is None
