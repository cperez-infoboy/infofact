"""Each stage tool calls the correct internal pipeline function.

Pins that the seven staged tools call the same hardened function per stage --
the guardrails (verify_spans, dedup thresholds, critique sentinel) live inside
those functions and are reached verbatim. Each pipeline function is stubbed
and we assert the tool stores its typed output on the per-project
``CaptureRun`` and returns a compact summary. No DB / no LLM / no graph
context: the emitters are no-ops outside a LangGraph run (see
``_make_emitters``).

Stage tool indices (after the CONVENTIONS stage insertion):
  0=ingest, 1=conventions, 2=extract, 3=consolidate, 4=critique, 5=classify,
  6=commit.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

import backend.agents.subagents.requirements_capture_agent as mod
from backend.agents.subagents import capture_run_holder as holder

PROJECT_ID = 4201


@pytest.fixture(autouse=True)
def _isolate_holder():
    holder.clear_run(PROJECT_ID)
    yield
    holder.clear_run(PROJECT_ID)


@pytest.fixture
def calls():
    """Shared call-recorder so a test can assert which stubs ran."""
    return {}


def _stub_pipeline(monkeypatch, calls: dict) -> None:
    """Replace every pipeline function the stage tools call with a recorder."""
    smap = types.SimpleNamespace(
        document_id="doc1", full_text="hello world", name="doc1.pdf"
    )

    def fake_resolve(ws, subpath):
        calls["resolve"] = True
        return Path("/tmp/ws/docs")

    def fake_discover(target):
        calls["discover"] = True
        return [Path("doc1.pdf")]

    async def fake_ingest(doc, *, session=None, parser_hint="auto"):
        calls["ingest_document"] = True
        calls["ingest_parser_hint"] = parser_hint
        return [["chunk-1", "chunk-2"], smap]

    async def fake_enrich(s, **kw):
        calls["enrich_structure_map"] = True

    async def fake_extract_conventions(smap, **kw):
        calls["extract_conventions"] = True
        return types.SimpleNamespace(
            priority_legend=[], priority_field_label="",
            scope_markers=[], glossary=[])

    def fake_merge_conventions(per_doc):
        calls["merge_conventions"] = True
        return types.SimpleNamespace(
            priority_legend=[], priority_field_label="",
            scope_markers=[], glossary=[])

    async def fake_extract_all(chunks, **kw):
        calls["extract_all"] = True
        return [types.SimpleNamespace(id="r1"), types.SimpleNamespace(id="r2")]

    async def fake_gap_pass(*a, **kw):
        calls["gap_pass"] = True
        return []

    async def fake_implicit_pass(*a, **kw):
        calls["implicit_pass"] = True
        return [types.SimpleNamespace(id="i1")]

    def fake_drop_duplicit(implicits, extracted):
        calls["drop_duplicit_implicit"] = True
        return implicits

    async def fake_consolidate(extracted):
        calls["consolidate"] = True
        return types.SimpleNamespace(
            items=[types.SimpleNamespace(id="r1"), types.SimpleNamespace(id="r2")],
            duplicates=[types.SimpleNamespace(
                kept_id="r1", member_ids=["r2"], kept_statement="x")],
            contradictions=[types.SimpleNamespace(
                a_id="r1", b_id="r2", reason="conflict", confidence=0.9)],
        )

    async def fake_critique_all(items, **kw):
        calls["critique_all"] = True
        calls["critique_rules"] = kw.get("rules")
        return types.SimpleNamespace(
            items=list(items), rejected=[], flagged=[], stats={"kept": 2})

    async def fake_classify_all(items, **kw):
        calls["classify_all"] = True
        calls["classify_rules"] = kw.get("rules")
        return types.SimpleNamespace(
            decisions={"r1": object()}, stats={"sub_items": 1})

    monkeypatch.setattr(mod, "_resolve_target", fake_resolve)
    monkeypatch.setattr(mod, "discover_documents", fake_discover)
    monkeypatch.setattr(mod, "parse_document_cached", fake_ingest)
    monkeypatch.setattr(mod, "enrich_structure_map", fake_enrich)
    monkeypatch.setattr(mod, "extract_conventions", fake_extract_conventions)
    monkeypatch.setattr(mod, "merge_conventions", fake_merge_conventions)
    monkeypatch.setattr(mod, "extract_all", fake_extract_all)
    monkeypatch.setattr(mod, "gap_pass", fake_gap_pass)
    monkeypatch.setattr(mod, "implicit_pass", fake_implicit_pass)
    monkeypatch.setattr(mod, "drop_duplicit_implicit", fake_drop_duplicit)
    monkeypatch.setattr(mod, "consolidate", fake_consolidate)
    monkeypatch.setattr(mod, "critique_all", fake_critique_all)
    monkeypatch.setattr(mod, "classify_all", fake_classify_all)
    # ingest_documents now runs the up-front existing-data guard; stub it to
    # "no existing requirements" so the happy path proceeds with no DB.
    async def fake_count(project_id):
        return {"requirements": 0, "grouping_plans": 0, "last_code": None}
    monkeypatch.setattr(mod, "_count_existing", fake_count)
    # Fase C: parser_hint_map consulta la DB por hints por-documento; stub a {}
    # para que ingest_documents no toque la DB real en los tests.
    async def fake_hint_map(project_id, workspace_root):
        return {}
    monkeypatch.setattr(mod, "parser_hint_map", fake_hint_map)


@pytest.fixture
def stage_tools(monkeypatch, calls):
    _stub_pipeline(monkeypatch, calls)
    return mod._make_stage_tools(PROJECT_ID, Path("/tmp/ws"), "Proj", "desc")


@pytest.mark.asyncio
async def test_ingest_calls_ingestion_and_stores_chunks(stage_tools, calls):
    out = await stage_tools[0].ainvoke({"target_subpath": ""})
    run = holder.get_run(PROJECT_ID)
    assert run is not None
    assert run.all_chunks == ["chunk-1", "chunk-2"]
    assert run.docs == [Path("doc1.pdf")]
    assert run.doc_texts == {"doc1": "hello world"}
    assert out["total_chunks"] == 2
    assert "ingest" in out["stages_done"]
    assert calls.get("ingest_document") is True
    assert calls.get("enrich_structure_map") is True


@pytest.mark.asyncio
async def test_conventions_calls_extract_and_merge(stage_tools, calls):
    """Stage 1 (conventions) wires extract_conventions + merge_conventions and
    stores DocumentRules on the holder."""
    await stage_tools[0].ainvoke({"target_subpath": ""})
    out = await stage_tools[1].ainvoke({})
    run = holder.get_run(PROJECT_ID)
    assert run.document_rules is not None
    assert "conventions" in out["stages_done"]
    assert out["priority_legend_entries"] == 0
    assert out["scope_markers"] == 0
    assert calls.get("extract_conventions") is True
    assert calls.get("merge_conventions") is True


@pytest.mark.asyncio
async def test_extract_calls_extract_all_gap_implicit_and_drops_duplicit(
    stage_tools, calls
):
    await stage_tools[0].ainvoke({"target_subpath": ""})
    out = await stage_tools[2].ainvoke({})  # index 2 = extract
    run = holder.get_run(PROJECT_ID)
    assert len(run.extracted) == 3  # 2 explicit + 1 implicit kept
    assert out["raw_items"] == 3
    assert "extract" in out["stages_done"]
    # verify_spans lives INSIDE extract_all -- calling the stage reaches it.
    assert calls.get("extract_all") is True
    assert calls.get("gap_pass") is True
    assert calls.get("implicit_pass") is True
    assert calls.get("drop_duplicit_implicit") is True


@pytest.mark.asyncio
async def test_consolidate_calls_consolidate_and_counts_conflicts(stage_tools, calls):
    await stage_tools[0].ainvoke({"target_subpath": ""})
    await stage_tools[2].ainvoke({})  # extract (skip conventions)
    out = await stage_tools[3].ainvoke({})  # index 3 = consolidate
    run = holder.get_run(PROJECT_ID)
    assert run.cons is not None
    assert len(run.cons.items) == 2
    assert out["duplicates"] == 1
    assert out["contradictions"] == 1
    assert "consolidate" in out["stages_done"]
    assert calls.get("consolidate") is True


@pytest.mark.asyncio
async def test_critique_calls_critique_all_and_reports_verdict(stage_tools, calls):
    # Run ingest + conventions + extract + consolidate (indices 0..3).
    for idx, payload in enumerate(
        [{"target_subpath": ""}, {}, {}, {}]
    ):
        await stage_tools[idx].ainvoke(payload)
    out = await stage_tools[4].ainvoke({})  # index 4 = critique
    run = holder.get_run(PROJECT_ID)
    assert run.crit is not None
    assert out["kept"] == 2
    assert out["rejected"] == 0
    assert out["flagged"] == 0
    assert "critique" in out["stages_done"]
    assert calls.get("critique_all") is True
    # rules are threaded through (run.document_rules from conventions stage).
    assert calls.get("critique_rules") is not None


@pytest.mark.asyncio
async def test_classify_calls_classify_all(stage_tools, calls):
    # Run ingest + conventions + extract + consolidate + critique (0..4).
    for idx, payload in enumerate(
        [{"target_subpath": ""}, {}, {}, {}, {}]
    ):
        await stage_tools[idx].ainvoke(payload)
    out = await stage_tools[5].ainvoke({})  # index 5 = classify
    run = holder.get_run(PROJECT_ID)
    assert run.cls is not None
    assert out["classified"] == 1
    assert out["sub_items"] == 1
    assert "classify" in out["stages_done"]
    assert calls.get("classify_all") is True
    # rules are threaded through (run.document_rules from conventions stage).
    assert calls.get("classify_rules") is not None


@pytest.mark.asyncio
async def test_ingest_gates_existing_data_before_parsing(
    stage_tools, monkeypatch, calls
):
    """Up-front guard: existing requirements + on_existing='ask' returns
    pending_confirmation BEFORE any Docling parse (no holder, no work)."""
    async def fake_count(project_id):
        return {"requirements": 3, "grouping_plans": 1, "last_code": "REQ-7K3F"}

    monkeypatch.setattr(mod, "_count_existing", fake_count)

    out = await stage_tools[0].ainvoke({"target_subpath": "", "on_existing": "ask"})

    assert out["pending_confirmation"] is True
    assert out["requirements"] == 3
    assert out["grouping_plans"] == 1
    assert out["last_code"] == "REQ-7K3F"
    # No holder created -> the capture did not start.
    assert holder.get_run(PROJECT_ID) is None
    # No parsing happened: the guard fired before discover/ingest ran.
    assert "discover" not in calls
    assert "ingest_document" not in calls


@pytest.mark.asyncio
async def test_ingest_reset_deletes_existing_then_parses(
    stage_tools, monkeypatch, calls
):
    """on_existing='reset' deletes the prior capture BEFORE parsing, then still
    parses and starts the holder."""
    import backend.database as database
    import backend.services.requirements_service as requirements_service

    reset_seen: list = []

    async def fake_reset(session, project_id):
        reset_seen.append(project_id)

    async def fake_count(project_id):
        return {"requirements": 2, "grouping_plans": 0, "last_code": "REQ-9AAA"}

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(requirements_service, "reset_project_capture", fake_reset)
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(mod, "_count_existing", fake_count)

    out = await stage_tools[0].ainvoke({"target_subpath": "", "on_existing": "reset"})

    assert reset_seen == [PROJECT_ID]  # destructive delete ran before parsing
    assert out["total_chunks"] == 2  # parse still ran and stored 2 chunks
    run = holder.get_run(PROJECT_ID)
    assert run is not None  # holder created after the reset
    assert "ingest" in out["stages_done"]
    assert calls.get("ingest_document") is True
