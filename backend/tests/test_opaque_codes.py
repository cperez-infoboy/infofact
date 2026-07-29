"""Tests for opaque REQ-XXXX code allocation (Workstream B).

Codes are Crockford base32, 4 chars, unique per project, allocated via
``gen_opaque_code``. Sequential REQ-NNN was replaced because gaps after
merge/delete read as lost requirements — opaque codes carry no count meaning.

The editing surface (``requirement_store._next_code``) and the pipeline
(``requirements_service._persist``) both delegate to the same helper, so these
tests pin the shared contract.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.models.project  # registers tables on Base.metadata
import backend.models.requirement
from backend.models.base import Base
from backend.models.project import Project
from backend.services import requirement_store as store
from backend.services._req_codes import OPAQUE_CODE_RE, gen_opaque_code


@pytest_asyncio.fixture
async def store_env():
    """In-memory store with one project; returns (session_factory, pid)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        proj = Project(
            user_id=1, name="P", slug="p",
            description="d", phase="requirements",
        )
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        pid = proj.id

    return session_factory, pid


@pytest.mark.asyncio
async def test_format_is_crockford_b32(store_env):
    """Every code matches ^REQ-[0-9A-HJ-KM-NP-TV-Z]{4}$ and avoids I/L/O/U."""
    session_factory, pid = store_env
    async with session_factory() as session:
        reserved: set[str] = set()
        for _ in range(50):
            code = await gen_opaque_code(session, pid, reserved=reserved)
            assert OPAQUE_CODE_RE.match(code), code
        for code in reserved:
            seg = code.split("-", 1)[1]
            assert all(ch not in seg for ch in "ILOU"), seg


@pytest.mark.asyncio
async def test_uniqueness_past_birthday_threshold(store_env):
    """1200 codes > ~1177 birthday-collision point for a 4-char Crockford space.

    Without the reserved/existing check collisions would be near-certain; the
    check guarantees zero duplicates in a single batch.
    """
    session_factory, pid = store_env
    async with session_factory() as session:
        reserved: set[str] = set()
        for _ in range(1200):
            await gen_opaque_code(session, pid, reserved=reserved)
        assert len(reserved) == 1200


@pytest.mark.asyncio
async def test_retry_skips_colliding_codes(store_env, monkeypatch):
    """First two draws collide with a reserved code; the third succeeds.

    Proves the retry loop advances instead of failing on the first collision.
    """
    import backend.services._req_codes as mod

    session_factory, pid = store_env
    reserved: set[str] = {"REQ-AAAA"}
    # Per-character sequence: "AAAA", "AAAA", then "BBBB" (12 choice calls).
    vals = iter(["A"] * 8 + ["B"] * 4)
    monkeypatch.setattr(mod.secrets, "choice", lambda _alpha: next(vals))

    async with session_factory() as session:
        code = await gen_opaque_code(session, pid, reserved=reserved)
    assert code == "REQ-BBBB"


@pytest.mark.asyncio
async def test_add_requirement_assigns_opaque_code(store_env):
    """The editing surface (add_requirement) delegates to gen_opaque_code."""
    session_factory, pid = store_env
    async with session_factory() as session:
        item = await store.add_requirement(
            session, pid, statement="r", created_by="t"
        )
    assert OPAQUE_CODE_RE.match(item.code), item.code


@pytest.mark.asyncio
async def test_list_ordered_by_insertion_not_code(store_env):
    """list_requirements returns insertion order (by id), not code order.

    Opaque codes are random, so code-order would not match insertion order.
    """
    session_factory, pid = store_env
    inserted_ids: list[int] = []
    async with session_factory() as session:
        for stmt in ("alpha", "bravo", "charlie"):
            item = await store.add_requirement(
                session, pid, statement=stmt, created_by="t"
            )
            inserted_ids.append(item.id)
        items = await store.list_requirements(session, pid)
    assert [it.id for it in items] == inserted_ids
