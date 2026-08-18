"""Tests de set_relation_status: formalizar relaciones documentadas sin winner.

Pin del gap que el subagente reporto en la curacion real: las relaciones
``depends_on`` documentadas quedaban ``proposed`` para siempre porque el unico
camino a un estado terminal era ``resolve_conflict`` — semantica de
contradiccion que exige un ``winner`` y REESCRIBE la nota. ``confirmed`` ya
existia en el enum (nunca se seteaba); esta es la via para usarlo.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.agents.tools.requirements_tools import make_requirements_tools
from backend.models import (
    Base,
    Priority,
    Project,
    RelationKind,
    RelationStatus,
    ReqType,
    RequirementItem,
    RequirementRelation,
)
from backend.services import requirement_store as store

NOTE = "caso especifico -> capacidad general; span MARCO ENT-07/REC-13"


async def _seed(session) -> tuple[int, int]:
    """Proyecto + 2 items + 1 relacion depends_on proposed. (pid, rel_id)."""
    proj = Project(user_id=1, name="rel", slug="rel", description="t")
    session.add(proj)
    await session.flush()
    a = RequirementItem(
        project_id=proj.id, code="REQ-A", statement="Alcance de efectivo.",
        type=ReqType.FUNCTIONAL, priority=Priority.MUST,
    )
    b = RequirementItem(
        project_id=proj.id, code="REQ-B", statement="Campos del modulo.",
        type=ReqType.FUNCTIONAL, priority=Priority.SHOULD,
    )
    session.add_all([a, b])
    await session.flush()
    rel = RequirementRelation(
        from_id=b.id, to_id=a.id, kind=RelationKind.DEPENDS_ON,
        status=RelationStatus.PROPOSED, note=NOTE, detected_by="agent",
    )
    session.add(rel)
    await session.commit()
    return proj.id, rel.id


async def _fresh_db(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(
        "backend.agents.tools.requirements_tools.AsyncSessionLocal", sm
    )
    return sm, tmp, engine


# --- store -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_preserves_note_and_appends(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            _pid, rel_id = await _seed(session)

        async with sm() as session:
            rel = await store.set_relation_status(
                session, rel_id, RelationStatus.CONFIRMED,
                note="aprobado por el usuario",
            )
        assert rel.status is RelationStatus.CONFIRMED
        # La nota original queda intacta; la nueva se AGREGA (resolve_conflict
        # la reescribe con winner=...; loser=...).
        assert rel.note.startswith(NOTE)
        assert rel.note.endswith("aprobado por el usuario")
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_confirm_without_note_leaves_note_untouched(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            _pid, rel_id = await _seed(session)
        async with sm() as session:
            rel = await store.set_relation_status(
                session, rel_id, RelationStatus.CONFIRMED
            )
        assert rel.note == NOTE
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_reopen_back_to_proposed(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            _pid, rel_id = await _seed(session)
        async with sm() as session:
            await store.set_relation_status(
                session, rel_id, RelationStatus.CONFIRMED
            )
            rel = await store.set_relation_status(
                session, rel_id, RelationStatus.PROPOSED
            )
        assert rel.status is RelationStatus.PROPOSED
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_resolved_is_rejected_pointing_to_resolve_conflict(monkeypatch):
    """RESOLVED exige winner: rechazarlo evita distorsionar la auditoria."""
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            _pid, rel_id = await _seed(session)
        async with sm() as session:
            with pytest.raises(ValueError, match="resolve_conflict"):
                await store.set_relation_status(
                    session, rel_id, RelationStatus.RESOLVED
                )
            # La relacion queda intacta tras el rechazo.
            rel = (
                await session.scalars(select(RequirementRelation))
            ).first()
            assert rel.status is RelationStatus.PROPOSED
            assert rel.note == NOTE
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_missing_relation_raises_keyerror(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            with pytest.raises(KeyError):
                await store.set_relation_status(
                    session, 999, RelationStatus.CONFIRMED
                )
    finally:
        await engine.dispose()
        tmp.cleanup()


# --- tool ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_confirm_and_invalid_status(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            pid, rel_id = await _seed(session)

        tools = {t.name: t for t in make_requirements_tools(pid)}
        assert "set_relation_status" in tools

        ok = await tools["set_relation_status"].ainvoke({
            "relation_id": rel_id, "status": "confirmed",
        })
        assert ok["status"] == "confirmed"
        assert ok["note"] == NOTE

        # 'resolved' se rechaza con derivacion a resolve_conflict.
        rejected = await tools["set_relation_status"].ainvoke({
            "relation_id": rel_id, "status": "resolved",
        })
        assert "error" in rejected
        assert "resolve_conflict" in rejected["error"]

        missing = await tools["set_relation_status"].ainvoke({
            "relation_id": 999, "status": "confirmed",
        })
        assert "error" in missing
    finally:
        await engine.dispose()
        tmp.cleanup()
