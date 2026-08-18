"""Pines del prompt del subagente de captura: ubicacion de datos + agrupamiento.

El prompt era 100% de captura; cuando llegaba la delegacion de /agrupar el
modelo improvisaba el preambulo completo (orientacion, salud, exploracion).
"""
from __future__ import annotations

from backend.agents.no_fs_tools import NoFilesystemToolsMiddleware
from backend.agents.subagents.requirements_capture_agent import (
    REQUIREMENTS_CAPTURE_AGENT_PROMPT,
    make_requirements_capture_agent_subagent,
)


def test_prompt_states_data_location():
    p = REQUIREMENTS_CAPTURE_AGENT_PROMPT
    assert "base de datos del backend" in p
    assert ".db" in p and ".sqlite" in p
    assert "nunca los busques en archivos" in p


def test_prompt_grouping_section_goes_straight_to_review():
    p = REQUIREMENTS_CAPTURE_AGENT_PROMPT
    assert "NO es una captura" in p
    assert "review_grouping" in p and "DIRECTAMENTE" in p
    assert "check_capture_health" in p  # lo que debe OMITIR para agrupar


def test_capture_spec_hides_filesystem_tools():
    spec = make_requirements_capture_agent_subagent(
        project_id=1, profile="p", project_slug="demo"
    )
    assert any(
        isinstance(m, NoFilesystemToolsMiddleware) for m in spec["middleware"]
    )
