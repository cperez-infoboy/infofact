"""patch_commit (refinamiento): la copia de filas previas re-asocia por NOMBRES.

El bug que motiva estos tests (sub-proyectos invisibles en el árbol del
visor): al refinar sin re-ejecutar la etapa de sub-proyectos, la copia usaba
``subproject_to_dict`` que emite ``project_code`` viejo, pero
``create_analysis`` solo re-asocia vía ``project_name`` → la versión nueva
persistía sub-proyectos con ``project_code=None``. Mismo patrón que el bug de
relaciones del MER. Se pinea el contrato del payload copiado:

- sub-proyectos con proyecto previo llevan ``project_name`` (no el código);
- los huérfanos (``project_code=None``) no lo llevan, pero siguen viajando;
- contratos copiados con NOMBRES de sub-proyecto en ambos extremos.
"""
from __future__ import annotations

import types

import pytest

import backend.agents.subagents.analysis_agent as mod
import backend.services.analysis_store as analysis_store_mod
import backend.services.requirement_store as req_store_mod
import backend.services.srs_store as srs_store_mod
from backend.agents.subagents import analysis_run_holder as holder
from backend.agents.subagents.analysis_run_holder import STAGE_NFR
from backend.models.requirement import ReqStatus, ReqType

PROJECT_ID = 4410


class _FakeSession:
    """Sesión con get() para prev_doc; el resto de accesos va por stubs."""

    def __init__(self, prev_doc):
        self._prev_doc = prev_doc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, _model, _pk):
        return self._prev_doc


def _prev_doc() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=999,
        version=3,
        srs_version=None,
        mer_diagram="erDiagram",
        nfr_analysis={
            "decisions": [],
            "stack": [],
            "data_consistency": "",
            "patterns": "",
        },
        process_diagrams=[],
        component_diagram="graph TD",
        system_architecture_diagram="",
        system_architecture_description="",
        infrastructure_diagram="",
        infrastructure_description="",
    )


def _prev_rows():
    proj = types.SimpleNamespace(
        id=1,
        project_id=PROJECT_ID,
        analysis_id=999,
        code="PROJ-001",
        name="Gestión de Flota",
        description="Subdominio de operación de flota.",
        domain_type="core",
        bounded_contexts=["Fleet"],
        entity_codes=["ENT-0001"],
        traced_req_codes=["REQ-0001"],
    )
    sub_assoc = types.SimpleNamespace(
        id=2,
        project_id=PROJECT_ID,
        analysis_id=999,
        code="SUB-001",
        name="seguimiento-svc",
        responsibility="Posición GPS en tiempo real.",
        stack={"backend": "FastAPI"},
        bounded_contexts=["Fleet"],
        nfr_codes=["REQ-0001"],
        entity_codes=["ENT-0001"],
        project_code="PROJ-001",
    )
    sub_orphan = types.SimpleNamespace(
        id=3,
        project_id=PROJECT_ID,
        analysis_id=999,
        code="SUB-002",
        name="reportes-svc",
        responsibility="Reportes periódicos.",
        stack={},
        bounded_contexts=[],
        nfr_codes=[],
        entity_codes=[],
        project_code=None,
    )
    contract = types.SimpleNamespace(
        id=4,
        project_id=PROJECT_ID,
        analysis_id=999,
        from_subproject_code="SUB-001",
        to_subproject_code="SUB-002",
        contract_type=types.SimpleNamespace(value="openapi"),
        name="POST /alerts",
        spec="",
        description=None,
    )
    return proj, [sub_assoc, sub_orphan], [contract]


@pytest.mark.asyncio
async def test_patch_commit_copies_subprojects_with_project_name(monkeypatch):
    holder.clear_run(PROJECT_ID)
    try:
        prev_doc = _prev_doc()
        proj, subs, contracts = _prev_rows()
        monkeypatch.setattr(
            mod, "AsyncSessionLocal", lambda: _FakeSession(prev_doc)
        )

        async def _empty(session, *args, **kwargs):
            return []

        async def fake_list_requirements(session, project_id, include_deleted=False):
            return [
                types.SimpleNamespace(
                    code="REQ-0001",
                    status=ReqStatus.VALIDATED,
                    type=ReqType.FUNCTIONAL,
                )
            ]

        async def fake_list_goals(session, project_id):
            return []

        async def fake_list_goal_links(session, project_id):
            return []

        captured: dict = {}

        async def fake_create(session, project_id, payload):
            captured["payload"] = payload
            return types.SimpleNamespace(
                version=4,
                status=types.SimpleNamespace(value="candidate"),
                requirement_count=1,
            )

        monkeypatch.setattr(req_store_mod, "list_requirements", fake_list_requirements)
        monkeypatch.setattr(srs_store_mod, "list_goals", fake_list_goals)
        monkeypatch.setattr(srs_store_mod, "list_goal_links", fake_list_goal_links)
        monkeypatch.setattr(analysis_store_mod, "list_domain_entities", _empty)
        monkeypatch.setattr(analysis_store_mod, "list_domain_relationships", _empty)
        monkeypatch.setattr(analysis_store_mod, "list_adrs", _empty)

        async def fake_list_sub_projects(session, analysis_id):
            return subs

        async def fake_list_contracts(session, analysis_id):
            return contracts

        async def fake_list_projects(session, analysis_id):
            return [proj]

        monkeypatch.setattr(
            analysis_store_mod, "list_sub_projects", fake_list_sub_projects
        )
        monkeypatch.setattr(analysis_store_mod, "list_contracts", fake_list_contracts)
        monkeypatch.setattr(analysis_store_mod, "list_projects", fake_list_projects)
        monkeypatch.setattr(analysis_store_mod, "create_analysis", fake_create)

        # Refinamiento: solo NFR re-ejecutada (sin resultado en holder), el
        # resto se copia de la versión previa.
        run = holder.get_or_create_run(PROJECT_ID, project_name="Proj")
        run.previous_analysis_id = 999
        run.calls[STAGE_NFR] = 1

        tools = mod._make_stage_tools(PROJECT_ID, "Proj", "desc")
        patch_commit = next(t for t in tools if t.name == "patch_commit")
        out = await patch_commit.ainvoke({})

        assert "error" not in out, out
        assert out["version"] == 4

        payload = captured["payload"]
        # Sub-proyectos copiados por nombre con la asociación traducida a
        # project_name (create_analysis re-asigna el código PROJ nuevo).
        by_name = {sp["name"]: sp for sp in payload["sub_projects"]}
        assert len(by_name) == 2
        assert by_name["seguimiento-svc"]["project_name"] == "Gestión de Flota"
        # El huérfano sigue viajando, sin asociación inventada.
        assert by_name["reportes-svc"].get("project_name") is None
        # Contratos copiados con nombres en ambos extremos.
        assert payload["contracts"][0]["from_subproject"] == "seguimiento-svc"
        assert payload["contracts"][0]["to_subproject"] == "reportes-svc"
        # Proyectos copiados completos.
        assert payload["projects"][0]["name"] == "Gestión de Flota"
    finally:
        holder.clear_run(PROJECT_ID)
