"""Tests for /captura dispatch (consolidated agent-driven capture).

Pins the startswith gotcha fix: /captura_agente must route to the agent-driven
subagent, NOT fall into the /captura branch (which would parse "_agente ..." as
steering). Also covers the dash variant, user-instruction embedding, and that
/captura now delegates to the SAME agent-driven subagent as /captura_agente
(the deterministic capture path was removed).
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


def test_captura_routes_to_agent_subagent():
    # /captura delegates to the SAME agent-driven subagent as /captura_agente
    # (the deterministic capture path is gone).
    out = _rewrite_command("/captura")
    assert "requirements-capture-agent" in out
    assert "ingest_documents" in out
    assert "run_requirements_capture" not in out


def test_captura_steering_is_embedded():
    # Text after /captura is free user steering (not a structured subpath); it
    # reaches the subagent as INSTRUCCIONES DEL USUARIO.
    out = _rewrite_command("/captura docs/x")
    assert "requirements-capture-agent" in out
    assert 'INSTRUCCIONES DEL USUARIO: "docs/x"' in out


def test_plain_text_passes_through_unchanged():
    msg = "que requerimientos tenemos?"
    assert _rewrite_command(msg) == msg


# --- Directive content for /captura_agente (bug #2) ----------------------


def test_captura_agente_directive_drops_stale_run_requirements_capture():
    out = _rewrite_command("/captura_agente")
    # run_requirements_capture is gone (deterministic capture removed); the
    # agent-driven subagent uses ingest_documents instead.
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


def test_captura_propagates_reset_decision():
    # /captura propagates an explicit reset decision the same way as
    # /captura_agente (both build the agent-driven directive).
    out = _rewrite_command("/captura resetear")
    assert "[DECISION]" in out
    assert 'on_existing="reset"' in out
