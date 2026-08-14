"""Each analysis stage tool calls the correct internal pipeline function.

Pins that the six staged tools (MER, NFR, processes, ADRs, sub-projects,
commit) call the right pipeline function per stage and store its typed output
on the per-project ``AnalysisRun``. Each pipeline function is stubbed and we
assert the tool updates the holder and returns a compact summary.

Also pins the guard contract: generate_processes requires MER, generate_adrs
requires NFR, discover_projects requires MER + ADRs, propose_subprojects
requires MER + ADRs + projects, generate_architecture requires MER + NFRs +
subprojects, and commit refuses unless all seven pre-commit stages are done.

No DB / no LLM / no graph context: the emitters are no-ops outside a LangGraph
run (see ``_make_emitters``).

Stage tool indices:
  0=generate_mer, 1=analyze_nfrs, 2=generate_processes, 3=generate_adrs,
  4=discover_projects, 5=propose_subprojects, 6=generate_architecture,
  7=commit_analysis.
"""
from __future__ import annotations

import types

import pytest

import backend.agents.pipelines.adr_pipeline as adr_mod
import backend.agents.pipelines.architecture_pipeline as architecture_mod
import backend.agents.pipelines.mer_pipeline as mer_mod
import backend.agents.pipelines.nfr_pipeline as nfr_mod
import backend.agents.pipelines.process_pipeline as process_mod
import backend.agents.pipelines.project_pipeline as project_mod
import backend.agents.pipelines.subproject_pipeline as subproject_mod
import backend.agents.subagents.analysis_agent as mod
import backend.services.analysis_store as analysis_store_mod
import backend.services.requirement_store as req_store_mod
import backend.services.srs_store as srs_store_mod
from backend.agents.pipelines.adr_pipeline import AdrResult
from backend.agents.pipelines.architecture_pipeline import ArchitectureResult
from backend.agents.pipelines.mer_pipeline import MerResult
from backend.agents.pipelines.nfr_pipeline import NfrResult
from backend.agents.pipelines.process_pipeline import ProcessResult
from backend.agents.pipelines.project_pipeline import ProjectResult
from backend.agents.pipelines.subproject_pipeline import SubProjectResult
from backend.agents.subagents import analysis_run_holder as holder
from backend.agents.subagents.analysis_run_holder import (
    STAGE_ADR,
    STAGE_ARCHITECTURE,
    STAGE_MER,
    STAGE_NFR,
    STAGE_PROCESS,
    STAGE_PROJECTS,
    STAGE_SUBPROJECT,
)
from backend.models.requirement import ReqStatus, ReqType

PROJECT_ID = 4401


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


class _FakeSession:
    """Async context manager standing in for AsyncSessionLocal()."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _fake_item(
    code: str = "REQ-0001",
    status=ReqStatus.VALIDATED,
    type_=ReqType.FUNCTIONAL,
):
    return types.SimpleNamespace(code=code, status=status, type=type_)


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _isolate_holder(monkeypatch):
    holder.clear_run(PROJECT_ID)
    # AsyncSessionLocal is imported at the top of analysis_agent — patch there.
    monkeypatch.setattr(mod, "AsyncSessionLocal", lambda: _FakeSession())
    yield
    holder.clear_run(PROJECT_ID)


@pytest.fixture
def calls():
    """Shared call-recorder so a test can assert which stubs ran."""
    return {}


def _stub_pipelines(monkeypatch, calls: dict) -> None:
    """Replace every pipeline function the stage tools call with a recorder.

    Pipeline functions are imported lazily INSIDE each tool function body, so
    we patch them at their source modules (not on ``mod``).
    """

    async def fake_list_requirements(session, project_id, include_deleted=False):
        calls["list_requirements"] = True
        return [_fake_item()]

    async def fake_list_goals(session, project_id):
        return []

    async def fake_list_goal_links(session, project_id):
        return []

    async def fake_generate_mer(items, **kw):
        calls["generate_mer"] = True
        return MerResult(mermaid="erDiagram")

    async def fake_analyze_nfrs(nfrs, **kw):
        calls["analyze_nfrs"] = True
        return NfrResult()

    async def fake_generate_processes(functional, *, mer_result=None, **kw):
        calls["generate_processes"] = True
        return ProcessResult()

    async def fake_generate_adrs(nfr_result, mer_result, **kw):
        calls["generate_adrs"] = True
        return AdrResult()

    async def fake_propose_subprojects(
        *, mer_result=None, adr_result=None, nfr_result=None, **kw
    ):
        calls["propose_subprojects"] = True
        return SubProjectResult()

    async def fake_discover_projects(*args, **kw):
        calls["discover_projects"] = True
        return ProjectResult(stats={})

    async def fake_generate_architecture(*args, **kw):
        calls["generate_architecture"] = True
        return ArchitectureResult(stats={})

    monkeypatch.setattr(req_store_mod, "list_requirements", fake_list_requirements)
    monkeypatch.setattr(srs_store_mod, "list_goals", fake_list_goals)
    monkeypatch.setattr(srs_store_mod, "list_goal_links", fake_list_goal_links)
    monkeypatch.setattr(mer_mod, "generate_mer", fake_generate_mer)
    monkeypatch.setattr(nfr_mod, "analyze_nfrs", fake_analyze_nfrs)
    monkeypatch.setattr(process_mod, "generate_processes", fake_generate_processes)
    monkeypatch.setattr(adr_mod, "generate_adrs", fake_generate_adrs)
    monkeypatch.setattr(project_mod, "discover_projects", fake_discover_projects)
    monkeypatch.setattr(
        subproject_mod, "propose_subprojects", fake_propose_subprojects
    )
    monkeypatch.setattr(
        architecture_mod, "generate_architecture", fake_generate_architecture
    )


@pytest.fixture
def stage_tools(monkeypatch, calls):
    _stub_pipelines(monkeypatch, calls)
    return mod._make_stage_tools(PROJECT_ID, "Proj", "desc")


# --------------------------------------------------------------------------- #
# Happy-path: pipeline call + holder update                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_generate_mer_calls_pipeline_and_stores_result(stage_tools, calls):
    out = await stage_tools[0].ainvoke({})
    run = holder.get_run(PROJECT_ID)
    assert run is not None
    assert run.mer_result is not None
    assert STAGE_MER in run.stages_done
    assert out["stage"] == "mer"
    assert "stages_done" in out
    assert calls.get("generate_mer") is True


@pytest.mark.asyncio
async def test_analyze_nfrs_calls_pipeline_and_stores_result(stage_tools, calls):
    # analyze_nfrs requires a run to already exist (unlike generate_mer which
    # creates one tolerantly).
    run = holder.get_or_create_run(PROJECT_ID, project_name="Proj")
    out = await stage_tools[1].ainvoke({})
    assert run.nfr_result is not None
    assert STAGE_NFR in run.stages_done
    assert out["stage"] == "nfr"
    assert calls.get("analyze_nfrs") is True


@pytest.mark.asyncio
async def test_discover_projects_calls_pipeline_and_stores_result(stage_tools, calls):
    run = holder.get_or_create_run(PROJECT_ID, project_name="Proj")
    run.stages_done.update({STAGE_MER, STAGE_ADR})
    out = await stage_tools[4].ainvoke({})
    assert run.project_result is not None
    assert STAGE_PROJECTS in run.stages_done
    assert out["stage"] == "projects"
    assert calls.get("discover_projects") is True


@pytest.mark.asyncio
async def test_generate_architecture_calls_pipeline_and_stores_result(
    stage_tools, calls
):
    run = holder.get_or_create_run(PROJECT_ID, project_name="Proj")
    run.stages_done.update({STAGE_MER, STAGE_NFR, STAGE_SUBPROJECT})
    out = await stage_tools[6].ainvoke({})
    assert run.architecture_result is not None
    assert STAGE_ARCHITECTURE in run.stages_done
    assert out["stage"] == "architecture"
    assert calls.get("generate_architecture") is True


# --------------------------------------------------------------------------- #
# Guard tests: prerequisite stages                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_generate_processes_requires_mer_done(stage_tools, calls):
    holder.get_or_create_run(PROJECT_ID, project_name="Proj")
    out = await stage_tools[2].ainvoke({})
    assert out["error"] == "missing_stages"
    assert out["missing"] == [STAGE_MER]
    assert "generate_processes" not in calls


@pytest.mark.asyncio
async def test_generate_adrs_requires_nfr_done(stage_tools, calls):
    holder.get_or_create_run(PROJECT_ID, project_name="Proj")
    out = await stage_tools[3].ainvoke({})
    assert out["error"] == "missing_stages"
    assert out["missing"] == [STAGE_NFR]
    assert "generate_adrs" not in calls


@pytest.mark.asyncio
async def test_discover_projects_requires_mer_and_adr_done(stage_tools, calls):
    holder.get_or_create_run(PROJECT_ID, project_name="Proj")
    out = await stage_tools[4].ainvoke({})
    assert out["error"] == "missing_stages"
    assert out["missing"] == [STAGE_MER, STAGE_ADR]
    assert "discover_projects" not in calls


@pytest.mark.asyncio
async def test_propose_subprojects_requires_mer_adr_and_projects_done(
    stage_tools, calls
):
    holder.get_or_create_run(PROJECT_ID, project_name="Proj")
    out = await stage_tools[5].ainvoke({})
    assert out["error"] == "missing_stages"
    assert STAGE_MER in out["missing"]
    assert STAGE_ADR in out["missing"]
    assert STAGE_PROJECTS in out["missing"]
    assert "propose_subprojects" not in calls


@pytest.mark.asyncio
async def test_generate_architecture_requires_mer_nfr_and_subproject_done(
    stage_tools, calls
):
    holder.get_or_create_run(PROJECT_ID, project_name="Proj")
    out = await stage_tools[6].ainvoke({})
    assert out["error"] == "missing_stages"
    assert STAGE_MER in out["missing"]
    assert STAGE_NFR in out["missing"]
    assert STAGE_SUBPROJECT in out["missing"]
    assert "generate_architecture" not in calls


# --------------------------------------------------------------------------- #
# Commit gate                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_commit_refuses_when_stages_missing(stage_tools, calls):
    run = holder.get_or_create_run(PROJECT_ID, project_name="Proj")
    run.stages_done.add(STAGE_MER)  # only MER done
    out = await stage_tools[7].ainvoke({})
    assert out["error"] == "missing_stages"
    assert STAGE_NFR in out["missing"]
    assert STAGE_PROCESS in out["missing"]
    assert STAGE_PROJECTS in out["missing"]
    assert STAGE_SUBPROJECT in out["missing"]
    assert STAGE_ARCHITECTURE in out["missing"]
    # Run is NOT cleared on refusal.
    assert holder.get_run(PROJECT_ID) is not None


@pytest.mark.asyncio
async def test_commit_persists_and_clears_run(stage_tools, calls, monkeypatch):
    run = holder.get_or_create_run(PROJECT_ID, project_name="Proj")
    run.stages_done = {
        STAGE_MER,
        STAGE_NFR,
        STAGE_PROCESS,
        STAGE_ADR,
        STAGE_PROJECTS,
        STAGE_SUBPROJECT,
        STAGE_ARCHITECTURE,
    }

    captured: dict = {}

    async def fake_create(session, project_id, payload):
        captured["payload"] = payload
        return types.SimpleNamespace(
            version=1,
            status=types.SimpleNamespace(value="candidate"),
            requirement_count=0,
        )

    monkeypatch.setattr(analysis_store_mod, "create_analysis", fake_create)

    out = await stage_tools[7].ainvoke({})

    assert out["stage"] == "commit"
    assert out["version"] == 1
    assert out["status"] == "candidate"
    assert captured["payload"] is not None
    # Successful commit clears the active run (no stale state).
    assert holder.get_run(PROJECT_ID) is None


# --------------------------------------------------------------------------- #
# Loop cap                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_loop_cap_stops_a_stuck_stage(stage_tools, calls):
    # Cap is 3: the 4th call of generate_mer returns stage_loop_exceeded.
    await stage_tools[0].ainvoke({})
    await stage_tools[0].ainvoke({})
    await stage_tools[0].ainvoke({})
    out4 = await stage_tools[0].ainvoke({})
    assert out4["error"] == "stage_loop_exceeded"
    assert out4["stage"] == "mer"
    assert out4["calls"] == 4
    assert out4["cap"] == 3
