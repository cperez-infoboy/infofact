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


def test_prompt_grouping_section_covers_curation():
    """Curacion de planes pendientes: tambien es agrupamiento, sin captura.

    Una delegacion libre ("resuelve los agrupamientos pendientes") no tiene
    forma de /agrupar; sin esta seccion el subagente improvisaba preambulo
    de captura (orientacion, check_capture_health).
    """
    p = REQUIREMENTS_CAPTURE_AGENT_PROMPT
    assert "curacion de planes" in p
    assert "set_group_decision" in p and "edit_group" in p
    assert "re-extraigas ni re-captures" in p


def test_prompt_grouping_section_reads_plans_without_n_plus_1():
    """Curar con el payload del plan, sin get_requirement por item ni regenerar.

    El payload de get_grouping_plan ya trae enunciados/prioridades/fuentes;
    sin este pin el agente re-llama get_requirement por codigo (N+1) o
    regenera el plan con review_grouping solo para volver a verlo.
    """
    p = REQUIREMENTS_CAPTURE_AGENT_PROMPT
    assert "get_grouping_plan(plan_id)" in p
    assert "sin llamar `get_requirement` por item" in p
    assert "regeneres un plan" in p
    assert "archive_grouping_plan" in p


def test_spec_description_delegates_curation_without_capture():
    """La description guia la prosa del orquestador al delegar.

    Antes decia solo "Tambien cubre la edicion, el agrupamiento..." y el
    orquestador narraba "lanzo el subagente de captura" para curar planes.
    """
    spec = make_requirements_capture_agent_subagent(
        project_id=1, profile="p", project_slug="demo"
    )
    d = spec["description"]
    assert "revisar planes pendientes" in d
    assert "NO ejecutan captura" in d


def test_capture_spec_hides_filesystem_tools():
    spec = make_requirements_capture_agent_subagent(
        project_id=1, profile="p", project_slug="demo"
    )
    assert any(
        isinstance(m, NoFilesystemToolsMiddleware) for m in spec["middleware"]
    )
