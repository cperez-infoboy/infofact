"""Tests for /captura_agente dispatch ordering (Phase 1).

Pins the startswith gotcha fix: /captura_agente must route to the agent-driven
subagent, NOT fall into the /captura branch (which would parse "_agente ..." as
a target_subpath). Also covers the dash variant, user-instruction embedding, and
that the deterministic /captura path stays untouched.
"""
from __future__ import annotations

from backend.routers.chat import _rewrite_command


def test_captura_agente_routes_to_agent_subagent():
    out = _rewrite_command("/captura_agente")
    assert "requirements-capture-agent" in out


def test_captura_agente_with_dash_separator():
    out = _rewrite_command("/captura-agente")
    assert "requirements-capture-agent" in out


def test_user_instructions_are_embedded():
    out = _rewrite_command("/captura_agente dale atencion a restricciones")
    assert 'INSTRUCCIONES DEL USUARIO: "dale atencion a restricciones"' in out


def test_captura_agente_without_text_has_no_instructions_block():
    out = _rewrite_command("/captura_agente")
    assert "INSTRUCCIONES DEL USUARIO" not in out


def test_captura_without_agente_stays_deterministic():
    out = _rewrite_command("/captura")
    assert "requirements-capture-agent" not in out
    assert "run_requirements_capture" in out
    assert "target_subpath" in out


def test_captura_subpath_is_preserved():
    out = _rewrite_command("/captura docs/x")
    assert 'target_subpath="docs/x"' in out
    assert "requirements-capture-agent" not in out


def test_plain_text_passes_through_unchanged():
    msg = "que requerimientos tenemos?"
    assert _rewrite_command(msg) == msg


# --- Fix C: directive content for /captura_agente (bug #2) ---------------

def test_captura_agente_directive_drops_stale_run_requirements_capture():
    out = _rewrite_command("/captura_agente")
    # run_requirements_capture is the deterministic tool; the agent-driven
    # subagent uses ingest_documents instead. The stale reference confused the
    # agent and contributed to bug #2.
    assert "run_requirements_capture" not in out


def test_captura_agente_directive_names_six_stages():
    out = _rewrite_command("/captura_agente")
    assert "ingest_documents" in out
    assert "extract_requirements" in out
    assert "consolidate_requirements" in out
    assert "critique_requirements" in out
    assert "classify_requirements" in out
    assert "commit_capture" in out


def test_captura_agente_directive_forbids_pre_delegation_exploration():
    out = _rewrite_command("/captura_agente")
    # The orchestrator must delegate immediately, not scan the FS first.
    assert "INMEDIATAMENTE" in out


def test_captura_agente_directive_propagates_reset_decision():
    out = _rewrite_command("/captura_agente resetear")
    assert "[DECISION]" in out
    assert 'on_existing="reset"' in out


def test_captura_agente_directive_propagates_append_decision():
    out = _rewrite_command("/captura_agente agregar a los existentes")
    assert "[DECISION]" in out
    assert 'on_existing="append"' in out


def test_captura_deterministic_propagates_reset_decision():
    out = _rewrite_command("/captura resetear")
    assert 'on_existing="reset"' in out
    # The decision keyword must not leak into the subpath.
    assert 'target_subpath="resetear"' not in out
