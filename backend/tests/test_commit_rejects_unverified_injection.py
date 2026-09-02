"""Phase 2 safety: commit_capture gate + the holder cannot be used to inject items.

The whole point of splitting the atomic pipeline into staged tools is reasoning
between stages WITHOUT weakening the safety contract. These tests pin the
commit-stage guarantees:

1. commit_capture refuses to persist if any prior stage has not run (the gate),
   and replies ``no_capture_in_progress`` when no holder exists.
2. commit_capture reads ONLY ``run.crit.items`` -- tampering ``run.extracted``
   (or any other field) cannot smuggle an item into the DB. The holder exposes
   no append/add API; every persisted item still originates from
   extract_requirements -> verify_spans.
3. The loop cap still stops a stuck stage.

The existing-data guard (pending_confirmation / reset / append) now runs
UP-FRONT in ``ingest_documents`` (before any parsing), not at commit: see
``test_stage_tools_call_internals.py``. commit_capture takes no ``on_existing``
argument and does not re-ask -- the user already chose at ingest.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

import backend.agents.subagents.requirements_capture_agent as mod
from backend.agents.subagents import capture_run_holder as holder
import backend.database as database
import backend.services.requirements_service as requirements_service

PROJECT_ID = 4301


class _FakeSession:
    """Async context manager standing in for AsyncSessionLocal()."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _isolate_holder(monkeypatch):
    holder.clear_run(PROJECT_ID)
    # No graph context in tests -> commit must still open a session and persist.
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _FakeSession())
    # ingest_documents runs the up-front existing-data guard; default to "no
    # existing requirements" so the happy path proceeds without a DB. A test that
    # wants to exercise the guard re-monkeypatches mod._count_existing itself.
    async def fake_count(project_id):
        return {"requirements": 0, "grouping_plans": 0, "last_code": None}
    monkeypatch.setattr(mod, "_count_existing", fake_count)
    yield
    holder.clear_run(PROJECT_ID)


def _stub_pipeline(monkeypatch) -> None:
    """Populate the holder through all six pre-commit stages without LLM/DB."""
    smap = types.SimpleNamespace(
        document_id="doc1", full_text="hello world", name="doc1.pdf"
    )

    monkeypatch.setattr(mod, "_resolve_target", lambda ws, sub: Path("/tmp/ws/docs"))
    monkeypatch.setattr(mod, "discover_documents", lambda t: [Path("doc1.pdf")])
    async def _parse_cached(d, *, session=None, parser_hint="auto"):
        return (["c1", "c2"], smap)
    monkeypatch.setattr(mod, "parse_document_cached", _parse_cached)

    async def _enrich(s, **kw):
        return None
    monkeypatch.setattr(mod, "enrich_structure_map", _enrich)

    async def _extract_conventions(smap, **kw):
        return types.SimpleNamespace(
            priority_legend=[], priority_field_label="",
            scope_markers=[], glossary=[])
    monkeypatch.setattr(mod, "extract_conventions", _extract_conventions)

    monkeypatch.setattr(
        mod, "merge_conventions",
        lambda per_doc: types.SimpleNamespace(
            priority_legend=[], priority_field_label="",
            scope_markers=[], glossary=[]),
    )

    async def _extract_all(chunks, **kw):
        return [types.SimpleNamespace(id="r1"), types.SimpleNamespace(id="r2")]
    monkeypatch.setattr(mod, "extract_all", _extract_all)

    async def _gap(*a, **kw):
        return []
    monkeypatch.setattr(mod, "gap_pass", _gap)

    async def _implicit(*a, **kw):
        return []
    monkeypatch.setattr(mod, "implicit_pass", _implicit)

    monkeypatch.setattr(mod, "drop_duplicit_implicit", lambda imp, ext: imp)

    async def _consolidate(extracted):
        items = [types.SimpleNamespace(id="r1"), types.SimpleNamespace(id="r2")]
        return types.SimpleNamespace(
            items=items, duplicates=[], contradictions=[], stats={})
    monkeypatch.setattr(mod, "consolidate", _consolidate)

    async def _critique_all(items, **kw):
        return types.SimpleNamespace(
            items=list(items), rejected=[], flagged=[], stats={"kept": 2})
    monkeypatch.setattr(mod, "critique_all", _critique_all)

    async def _classify_all(items, **kw):
        return types.SimpleNamespace(decisions={}, stats={"sub_items": 0})
    monkeypatch.setattr(mod, "classify_all", _classify_all)

    # Fase C: parser_hint_map consulta la DB; stub a {} para no tocarla.
    async def _hint_map(project_id, workspace_root):
        return {}
    monkeypatch.setattr(mod, "parser_hint_map", _hint_map)


@pytest.fixture
def stage_tools(monkeypatch):
    _stub_pipeline(monkeypatch)
    return mod._make_stage_tools(PROJECT_ID, Path("/tmp/ws"), "Proj", "desc")


async def _run_all_pre_commit(stage_tools) -> None:
    # 0=ingest, 1=conventions, 2=identify_actors, 3=extract, 4=consolidate,
    # 5=critique, 6=classify
    for idx, payload in enumerate(
        [{"target_subpath": ""}, {}, {}, {}, {}, {}, {}]
    ):
        await stage_tools[idx].ainvoke(payload)


# Indices: 0=ingest, 1=conventions, 2=identify_actors, 3=extract,
# 4=consolidate, 5=critique, 6=classify, 7=commit.
COMMIT_IDX = 7
EXTRACT_IDX = 3


@pytest.mark.asyncio
async def test_commit_without_active_run_replies_no_capture(stage_tools):
    out = await stage_tools[COMMIT_IDX].ainvoke({})
    assert out["error"] == "no_capture_in_progress"
    assert "ingest_documents" in out["message"]


@pytest.mark.asyncio
async def test_commit_rejects_when_prior_stages_missing(stage_tools):
    await stage_tools[0].ainvoke({"target_subpath": ""})  # ingest only
    out = await stage_tools[COMMIT_IDX].ainvoke({})
    assert out["error"] == "missing_stages"
    # Pipeline order preserved in the missing list (conventions is index 1).
    assert out["missing"] == [
        "conventions", "extract", "consolidate", "critique", "classify",
    ]


@pytest.mark.asyncio
async def test_commit_persists_crit_items_and_clears_run(stage_tools, monkeypatch):
    captured: dict = {}

    async def fake_persist(session, project_id, items, classification, on_event=None):
        captured["items"] = items
        captured["classification"] = classification
        captured["on_event"] = on_event
        return [101, 102]

    monkeypatch.setattr(requirements_service, "_persist", fake_persist)

    await _run_all_pre_commit(stage_tools)
    run = holder.get_run(PROJECT_ID)
    crit_items = run.crit.items

    out = await stage_tools[COMMIT_IDX].ainvoke({})

    assert out["persisted"] == 2
    # commit passes the holder crit items VERBATIM (identity, not a copy).
    assert captured["items"] is crit_items
    # Successful commit clears the active run (no stale state).
    assert holder.get_run(PROJECT_ID) is None


@pytest.mark.asyncio
async def test_tampering_extracted_cannot_inject_items_into_persist(
    stage_tools, monkeypatch
):
    """The holder is the ONLY item source for commit, and commit reads crit --
    not extracted. Appending to extracted must NOT reach _persist."""
    captured: dict = {}

    async def fake_persist(session, project_id, items, classification, on_event=None):
        captured["items"] = items
        return [201]

    monkeypatch.setattr(requirements_service, "_persist", fake_persist)

    await _run_all_pre_commit(stage_tools)
    run = holder.get_run(PROJECT_ID)
    bogus = types.SimpleNamespace(id="INJECTED-HALLUCINATION")
    run.extracted.append(bogus)  # direct attribute write -- the only "attack"

    await stage_tools[COMMIT_IDX].ainvoke({})

    # crit has 2 items (from consolidate); the injected extracted item is ignored.
    assert len(captured["items"]) == 2
    assert bogus not in captured["items"]


@pytest.mark.asyncio
async def test_loop_cap_stops_a_stuck_stage(stage_tools):
    # Cap is 3: the 4th call of the same stage returns stage_loop_exceeded.
    await stage_tools[0].ainvoke({"target_subpath": ""})
    out = await stage_tools[EXTRACT_IDX].ainvoke({})
    out2 = await stage_tools[EXTRACT_IDX].ainvoke({})
    out3 = await stage_tools[EXTRACT_IDX].ainvoke({})
    out4 = await stage_tools[EXTRACT_IDX].ainvoke({})
    assert out4["error"] == "stage_loop_exceeded"
    assert out4["stage"] == "extract"
    assert out4["calls"] == 4
    assert out4["cap"] == 3
    # The first three calls still returned normal summaries.
    assert "raw_items" in out and "raw_items" in out3
