"""Smoke: /captura_agente staged capture end-to-end (LLM + DB stubbed).

Runs the six staged tools (ingest -> extract -> consolidate -> critique ->
classify -> commit) of the agent-driven capture directly, with the pipeline
functions and the DB layer stubbed. Asserts the holder flows each stage output
into the next, commit persists exactly the crit items, and the commit gate
rejects a partial run. No LLM, no container, no real DB -- a wiring smoke for
Phase 2 that complements backend/tests/test_stage_tools_call_internals.py and
backend/tests/test_commit_rejects_unverified_injection.py.

Run: .venv/bin/python scripts/smoke_captura_agente_e2e.py
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.agents.subagents.requirements_capture_agent as mod
from backend.agents.subagents import capture_run_holder as holder
import backend.database as database
import backend.services.requirements_service as requirements_service

PROJECT_ID = 99001

_FAILED = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global _FAILED
    mark = "OK  " if ok else "FAIL"
    line = f"  {mark}  {label}"
    if not ok:
        _FAILED += 1
        if detail:
            line += f" -> {detail}"
    print(line)


class _FakeSession:
    """Async context manager standing in for AsyncSessionLocal()."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _install_stubs() -> dict:
    """Stub every pipeline fn + the DB layer; return a dict capturing _persist."""
    smap = types.SimpleNamespace(
        document_id="doc1", full_text="hello world", name="rfp.pdf"
    )

    mod._resolve_target = lambda ws, sub: Path("/tmp/ws/docs")
    mod.discover_documents = lambda t: [Path("rfp.pdf")]
    async def _parse_cached(d, *, session=None, parser_hint="auto"):
        return (["c1", "c2", "c3"], smap)
    mod.parse_document_cached = _parse_cached
    # Fase C: parser_hint_map consulta la DB por hints por-documento; stub {}
    # para que ingest_documents no toque la DB real en el smoke.
    async def _hint_map(project_id, workspace_root):
        return {}
    mod.parser_hint_map = _hint_map

    async def _enrich(s, **kw):
        return None

    mod.enrich_structure_map = _enrich

    async def _extract_conventions(smap, **kw):
        return types.SimpleNamespace(
            priority_legend=[], priority_field_label=None,
            scope_markers=[], glossary={})

    mod.extract_conventions = _extract_conventions

    def _merge_conventions(per_doc):
        return types.SimpleNamespace(
            priority_legend=[], priority_field_label=None,
            scope_markers=[], glossary={})

    mod.merge_conventions = _merge_conventions

    async def _extract_all(chunks, **kw):
        return [types.SimpleNamespace(id=f"r{i}") for i in range(4)]

    mod.extract_all = _extract_all

    async def _gap(*a, **kw):
        return []

    mod.gap_pass = _gap

    async def _implicit(*a, **kw):
        return []

    mod.implicit_pass = _implicit
    mod.drop_duplicit_implicit = lambda imp, ext: imp

    async def _consolidate(extracted):
        return types.SimpleNamespace(
            items=list(extracted),
            duplicates=[types.SimpleNamespace(
                kept_id="r0", member_ids=["r1"], kept_statement="dup")],
            contradictions=[],
        )

    mod.consolidate = _consolidate

    async def _critique_all(items, **kw):
        return types.SimpleNamespace(
            items=list(items), rejected=[], flagged=[],
            stats={"kept": len(items)},
        )

    mod.critique_all = _critique_all

    async def _classify_all(items, **kw):
        return types.SimpleNamespace(decisions={}, stats={"sub_items": 2})

    mod.classify_all = _classify_all

    async def _count(project_id):
        return {"requirements": 0, "grouping_plans": 0, "last_code": None}

    mod._count_existing = _count

    captured: dict = {}

    async def _persist(session, project_id, items, classification, on_event=None):
        captured["items"] = items
        return [1000 + i for i in range(len(items))]

    requirements_service._persist = _persist
    database.AsyncSessionLocal = lambda: _FakeSession()
    return captured


async def main() -> None:
    print("== /captura_agente e2e staged (stubbed) ==")
    holder.clear_run(PROJECT_ID)
    captured = _install_stubs()
    tools = mod._make_stage_tools(PROJECT_ID, Path("/tmp/ws"), "Proj", "desc")

    out_ingest = await tools[0].ainvoke({"target_subpath": ""})
    run = holder.get_run(PROJECT_ID)
    check("ingest stores chunks", run.all_chunks == ["c1", "c2", "c3"], out_ingest)
    check("ingest reports total_chunks", out_ingest.get("total_chunks") == 3, out_ingest)

    out_conv = await tools[1].ainvoke({})  # discover_conventions
    check("conventions stores rules", holder.get_run(PROJECT_ID).document_rules is not None, out_conv)

    out_ext = await tools[2].ainvoke({})
    check("extract stores raw items", len(holder.get_run(PROJECT_ID).extracted) == 4, out_ext)

    out_cons = await tools[3].ainvoke({})
    check("consolidate proposes 1 duplicate", out_cons.get("duplicates") == 1, out_cons)

    out_crit = await tools[4].ainvoke({})
    check("critique reports kept=count", out_crit.get("kept") == 4, out_crit)

    out_cls = await tools[5].ainvoke({})
    check("classify reports sub_items", out_cls.get("sub_items") == 2, out_cls)

    out_commit = await tools[6].ainvoke({})
    check("commit persisted count", out_commit.get("persisted") == 4, out_commit)
    check("commit persisted crit items", len(captured.get("items", [])) == 4, captured)
    check("commit cleared active run", holder.get_run(PROJECT_ID) is None)

    # Commit gate: a fresh partial run (ingest only) must be rejected.
    holder.clear_run(PROJECT_ID)
    await tools[0].ainvoke({"target_subpath": ""})
    out_gate = await tools[6].ainvoke({})
    check("commit gate rejects missing stages", out_gate.get("error") == "missing_stages", out_gate)
    check("gate lists missing in pipeline order",
          out_gate.get("missing") == ["conventions", "extract", "consolidate", "critique", "classify"], out_gate)

    # Up-front existing-data guard (now in ingest_documents): existing reqs +
    # on_existing="ask" -> pending_confirmation BEFORE parsing (no holder).
    holder.clear_run(PROJECT_ID)
    async def _count_some(project_id):
        return {"requirements": 5, "grouping_plans": 2, "last_code": "REQ-7K3F"}
    mod._count_existing = _count_some
    out_pre = await tools[0].ainvoke({"target_subpath": "", "on_existing": "ask"})
    check("ingest up-front guard blocks", out_pre.get("pending_confirmation") is True, out_pre)
    check("guard reports counts + last_code",
          out_pre.get("requirements") == 5 and out_pre.get("last_code") == "REQ-7K3F", out_pre)
    check("guard creates no holder", holder.get_run(PROJECT_ID) is None)

    holder.clear_run(PROJECT_ID)
    print("=" * 52)
    if _FAILED:
        print(f"  FAIL: {_FAILED} chequeo(s) fallaron")
        sys.exit(1)
    print("  OK: secuencia staged completa verificada")


if __name__ == "__main__":
    asyncio.run(main())
