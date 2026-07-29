"""Integration tests for run_requirements_capture's existing-data guard.

Exercises the REAL tool (built via _make_run_capture_tool) against an in-memory
SQLite store, with the heavy pipeline stubbed out. The pure decision policy is
covered by test_existing_gate.py; these tests pin the runtime wiring:

  - block:  requirements exist + on_existing="ask" -> pending_confirmation, the
            pipeline is NOT invoked.
  - append: on_existing="append" -> pipeline runs, existing rows untouched.
  - reset:  on_existing="reset"  -> existing rows are wiped BEFORE the pipeline
            runs (so the next capture starts fresh).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.database
import backend.models.project  # registers tables on Base.metadata
import backend.models.requirement
from backend.agents.subagents import requirements_capture as rc
from backend.models.base import Base
from backend.models.project import Project
from backend.services import requirement_store as store
from backend.services._req_codes import OPAQUE_CODE_RE


def _stub_report() -> SimpleNamespace:
    return SimpleNamespace(
        stats={
            "raw_extracted": 1,
            "after_consolidate": 1,
            "after_critique": 1,
            "sub_items": 0,
        },
        documents=[],
        item_ids=[101],  # the stub "persisted" one item
        duplicates=[],
        contradictions=[],
        flagged=[],
        rejected=[],
    )


@pytest_asyncio.fixture
async def capture_env(monkeypatch, tmp_path):
    """In-memory store with one project + 2 requirements; pipeline stubbed."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # _count_existing and the reset branch import AsyncSessionLocal at call
    # time (from backend.database), so patching the attribute is enough to
    # point the tool at our in-memory engine.
    monkeypatch.setattr(backend.database, "AsyncSessionLocal", session_factory)

    async with session_factory() as session:
        proj = Project(
            user_id=1, name="P", slug="p",
            description="d", phase="requirements",
        )
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        pid = proj.id
        await store.add_requirement(session, pid, statement="r1", created_by="t")
        await store.add_requirement(session, pid, statement="r2", created_by="t")

    calls = {"pipeline": 0}

    async def fake_pipeline(project_id, target, **kwargs):
        calls["pipeline"] += 1
        return _stub_report()

    monkeypatch.setattr(rc, "run_requirements_pipeline", fake_pipeline)

    tool = rc._make_run_capture_tool(pid, Path(str(tmp_path)), "P", "d")
    return tool, calls, pid, session_factory


@pytest.mark.asyncio
async def test_guard_blocks_when_requirements_exist(capture_env):
    tool, calls, pid, _ = capture_env
    result = await tool.ainvoke({"target_subpath": "", "on_existing": "ask"})

    assert result["pending_confirmation"] is True
    assert result["requirements"] == 2
    assert OPAQUE_CODE_RE.match(result["last_code"]), result["last_code"]
    # The pipeline must NOT have run while the user has not decided.
    assert calls["pipeline"] == 0


@pytest.mark.asyncio
async def test_guard_append_runs_without_wiping(capture_env):
    tool, calls, pid, session_factory = capture_env
    result = await tool.ainvoke({"target_subpath": "", "on_existing": "append"})

    assert result["persisted"] == 1
    assert calls["pipeline"] == 1
    # Existing rows survive an append.
    async with session_factory() as session:
        items = await store.list_requirements(session, pid)
    assert len(items) == 2


@pytest.mark.asyncio
async def test_guard_reset_wipes_before_running(capture_env):
    tool, calls, pid, session_factory = capture_env
    result = await tool.ainvoke({"target_subpath": "", "on_existing": "reset"})

    assert result["persisted"] == 1
    assert calls["pipeline"] == 1
    # reset_project_capture wiped the 2 seeded rows before the stubbed pipeline
    # ran (the stub does not persist anything real), so the store is empty.
    async with session_factory() as session:
        items = await store.list_requirements(session, pid)
    assert len(items) == 0
