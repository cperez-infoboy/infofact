"""Tests for requirement list ordering — family grouping (parent + children).

Verifies that list_requirements returns children grouped right after their
parent, not all dumped at the end of the list. Without family-grouped
ordering, the flat ORDER BY id creates the visual illusion that all derived
items belong to the last parent (because _persist creates all parents first
in Pass 1, then all children in Pass 2).
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.models.project  # register tables on Base.metadata
import backend.models.requirement
from backend.models.base import Base
from backend.models.project import Project
from backend.models.requirement import (
    Priority, ReqStatus, ReqType, RequirementItem,
)
from backend.services.requirement_store import list_requirements


async def _make_session():
    """Create an in-memory SQLite DB with all tables and a project row."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    session = Session()
    proj = Project(
        user_id=1, name="Test", slug="test",
        description="ordering test", phase="requirements",
    )
    session.add(proj)
    await session.commit()
    await session.refresh(proj)
    return session, proj.id, engine


def _req(pid: int, code: str, *, derived: bool = False, parent_id: int | None = None):
    return RequirementItem(
        project_id=pid, code=code, statement=code,
        type=ReqType.FUNCTIONAL, priority=Priority.MUST,
        status=ReqStatus.DRAFT, derived=derived, parent_id=parent_id,
    )


@pytest.mark.asyncio
async def test_children_grouped_under_parent():
    """Children appear right after their parent, not at the end of the list."""
    session, pid, engine = await _make_session()
    try:
        # Simulate _persist insertion order: all parents first, then all children.
        p1 = _req(pid, "P1")
        p2 = _req(pid, "P2")
        p3 = _req(pid, "P3")
        session.add_all([p1, p2, p3])
        await session.flush()

        # Children created AFTER all parents (mirrors _persist Pass 2).
        c1a = _req(pid, "C1A", derived=True, parent_id=p1.id)
        c1b = _req(pid, "C1B", derived=True, parent_id=p1.id)
        c3a = _req(pid, "C3A", derived=True, parent_id=p3.id)
        session.add_all([c1a, c1b, c3a])
        await session.commit()

        rows = await list_requirements(session, pid)
        codes = [r.code for r in rows]

        # Family-grouped: P1, C1A, C1B, P2, P3, C3A
        # NOT flat-id: P1, P2, P3, C1A, C1B, C3A
        assert codes == ["P1", "C1A", "C1B", "P2", "P3", "C3A"], (
            f"Expected family-grouped order, got: {codes}"
        )
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_parent_without_children_stays_in_order():
    """A parent with no children doesn't break the ordering of others."""
    session, pid, engine = await _make_session()
    try:
        p1 = _req(pid, "P1")
        p2 = _req(pid, "P2")
        p3 = _req(pid, "P3")
        session.add_all([p1, p2, p3])
        await session.flush()

        c1 = _req(pid, "C1", derived=True, parent_id=p1.id)
        c3 = _req(pid, "C3", derived=True, parent_id=p3.id)
        session.add_all([c1, c3])
        await session.commit()

        rows = await list_requirements(session, pid)
        codes = [r.code for r in rows]

        assert codes == ["P1", "C1", "P2", "P3", "C3"], (
            f"Expected interleaved families, got: {codes}"
        )
    finally:
        await session.close()
        await engine.dispose()
