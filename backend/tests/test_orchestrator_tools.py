"""Orchestrator tool surface: the rules harness must be agent-facing.

Sesión 11 de planitrack2-0 (2026-08-31): el usuario preguntó al orquestador
si tenía herramientas para almacenar convenciones/observaciones; respondió
"no tengo una herramienta dedicada" — y tenía razón: ``make_project_rules_tools``
quedó registrada solo dentro de los tres subagentes. Estos tests fijan la
superficie del orquestador para que el harness no pueda volver a caerse
silenciosamente del agente de chat.
"""
from backend.services.agent_service import (
    _project_rules_capability_block,
    make_orchestrator_tools,
)


def _tool_names(tools) -> set[str]:
    return {t.name for t in tools}


def test_orchestrator_tools_include_rules_harness():
    names = _tool_names(make_orchestrator_tools(project_id=7))
    assert {
        "add_project_rule",
        "list_project_rules",
        "retire_project_rule",
    } <= names


def test_orchestrator_tools_keep_web_and_read_surface():
    names = _tool_names(make_orchestrator_tools(project_id=7))
    assert {"web_search", "fetch_url"} <= names


def test_orchestrator_tools_without_project_stay_web_only():
    names = _tool_names(make_orchestrator_tools(project_id=None))
    assert "add_project_rule" not in names
    assert {"web_search", "fetch_url"} <= names


def test_capability_block_names_tools_and_scopes():
    block = _project_rules_capability_block()
    assert "add_project_rule" in block
    assert "list_project_rules" in block
    assert "capture | analysis | srs | all" in block
