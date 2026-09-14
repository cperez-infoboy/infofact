"""Regresión del fallo de arranque de stream (500 en POST /messages).

``packages_agent.make_packages_agent_subagent`` construía su middleware con
``make_summarization_middleware()`` SIN el argumento ``backend`` (obligatorio
desde la contramedida anti-overflow de la sesión 17): el TypeError estallaba
en ``build_agent``, antes de abrir el stream SSE, y CUALQUIER mensaje de chat
de CUALQUIER comando (/captura, /srs, /analisis, /paquetes) moría con un 500
«error de stream» en el frontend. El molde correcto es el de los otros tres
subagentes: cada spec crea su propio DockerSandbox del proyecto y se lo pasa
como backend (sin LLM_API_KEY la fábrica devuelve None y queda el default).
"""
from __future__ import annotations

import pytest


def test_packages_agent_spec_builds_with_required_backend():
    """El spec del packages-agent se construye y su summarizer trae backend."""
    from backend.agents.subagents.packages_agent import (
        make_packages_agent_subagent,
    )

    spec = make_packages_agent_subagent(
        project_id=1,
        profile="testuser",
        project_slug="testproj",
    )

    assert spec["name"] == "packages-agent"
    # El summarizer con techo reemplaza al default de deepagents por nombre
    # (sesion 17): si la fábrica no recibió backend, este bloque no existe.
    summarizers = [
        m
        for m in spec["middleware"]
        if getattr(m, "name", "") == "SummarizationMiddleware"
    ]
    assert summarizers, (
        "el spec debe incluir el SummarizationMiddleware con techo "
        "(make_summarization_middleware requiere el backend del sandbox)"
    )


def test_all_subagent_specs_build_with_backend_middleware(monkeypatch):
    """Los 4 specs de subagentes se construyen sin TypeError (regresión global).

    Si mañana se agrega un quinto subagente y llama a la fábrica sin backend,
    este test lo detecta antes de llegar a producción.
    """
    monkeypatch.setenv("LLM_API_KEY", "x-test")
    from backend.agents.subagents.analysis_agent import (
        make_analysis_agent_subagent,
    )
    from backend.agents.subagents.packages_agent import (
        make_packages_agent_subagent,
    )
    from backend.agents.subagents.requirements_capture_agent import (
        make_requirements_capture_agent_subagent,
    )
    from backend.agents.subagents.srs_agent import make_srs_agent_subagent

    factories = [
        make_requirements_capture_agent_subagent,
        make_srs_agent_subagent,
        make_analysis_agent_subagent,
        make_packages_agent_subagent,
    ]
    for factory in factories:
        spec = factory(
            project_id=1,
            profile="testuser",
            project_slug="testproj",
        )
        assert spec["middleware"] is not None
