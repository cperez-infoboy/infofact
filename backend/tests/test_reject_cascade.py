"""Tests for auto-detach cascade on parent rejection.

When a parent requirement is rejected, its children (items with parent_id
pointing to it) must be promoted to independent items: parent_id=None,
derived=False. Only REJECTED triggers this — SUPERSEDED (split) and MERGED
keep parent_id for their own lifecycle semantics.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.models.project  # register tables on Base.metadata
import backend.models.requirement
from backend.models.base import Base
from backend.models.project import Project
from backend.models.requirement import (
    Priority,
    ReqStatus,
    ReqType,
    RequirementItem,
    RequirementRevision,
)
from backend.services import requirement_store as store


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite with all tables and one project."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        proj = Project(
            user_id=1, name="T", slug="t",
            description="d", phase="requirements",
        )
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        yield session, proj.id

    await engine.dispose()


def _req(pid: int, code: str, *, parent_id: int | None = None):
    return RequirementItem(
        project_id=pid, code=code, statement=code,
        type=ReqType.FUNCTIONAL, priority=Priority.MUST,
        status=ReqStatus.DRAFT, derived=parent_id is not None,
        parent_id=parent_id,
    )


async def _revision_reasons(session, req_id: int) -> list[str]:
    rows = await session.scalars(
        select(RequirementRevision.change_reason)
        .where(RequirementRevision.req_id == req_id)
        .order_by(RequirementRevision.version.desc())
    )
    return list(rows)


# ---------------------------------------------------------------------------
# 1. Children are detached (parent_id cleared, derived=False)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reject_detaches_children(db):
    """Rejecting a parent promotes children to independent items."""
    session, pid = db

    parent = _req(pid, "P1")
    session.add(parent)
    await session.flush()

    c1 = _req(pid, "C1", parent_id=parent.id)
    c2 = _req(pid, "C2", parent_id=parent.id)
    session.add_all([c1, c2])
    await session.commit()

    await store.reject_requirement(session, parent.id, reason="out of scope")

    # Reload children from DB
    for child in [c1, c2]:
        await session.refresh(child)
        assert child.parent_id is None, f"{child.code} parent_id not cleared"
        assert child.derived is False, f"{child.code} derived not cleared"


# ---------------------------------------------------------------------------
# 2. Each detached child gets a revision with the detach reason
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reject_appends_child_revisions(db):
    """Detached children get an auditable revision entry."""
    session, pid = db

    parent = _req(pid, "P1")
    session.add(parent)
    await session.flush()

    child = _req(pid, "C1", parent_id=parent.id)
    session.add(child)
    await session.commit()

    await store.reject_requirement(session, parent.id, reason="out of scope")

    reasons = await _revision_reasons(session, child.id)
    assert any(r == f"parent_rejected:{parent.code}" for r in reasons), (
        f"Expected 'parent_rejected:{parent.code}' in revisions: {reasons}"
    )


# ---------------------------------------------------------------------------
# 3. Rejecting a childless item works as before (no spurious work)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reject_no_children_no_error(db):
    """Rejecting an item without children behaves like the old code."""
    session, pid = db

    item = _req(pid, "LONE")
    session.add(item)
    await session.commit()

    result = await store.reject_requirement(session, item.id, reason="dup")

    assert result.status == ReqStatus.REJECTED


# ---------------------------------------------------------------------------
# 4. Negative test: split (SUPERSEDED) does NOT detach children
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_split_supersede_does_not_detach(db):
    """split_requirement sets SUPERSEDED on the parent — children keep parent_id."""
    session, pid = db

    original = await store.add_requirement(
        session, pid, statement="compound: X and Y", created_by="test",
    )
    children = await store.split_requirement(
        session, original.id, ["Part X", "Part Y"], reason="non-atomic",
    )
    assert len(children) == 2

    # Reload original to confirm SUPERSEDED (not REJECTED)
    await session.refresh(original)
    assert original.status == ReqStatus.SUPERSEDED

    # Children must still point to the (SUPERSEDED) parent
    for child in children:
        await session.refresh(child)
        assert child.parent_id == original.id, (
            f"{child.code} was detached by split — must keep parent_id"
        )
        assert child.derived is True
