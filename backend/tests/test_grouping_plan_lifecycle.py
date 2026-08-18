"""Tests del ciclo de vida de planes de agrupamiento: lectura enriquecida + archivado.

Pins de la curacion agéntica:
- ``get_plan`` debe devolver por keeper/miembro los campos que la curacion
  necesita (statement, type, priority, explicit_priority, status, fuentes):
  sin ellos el agente improvisa un ``get_requirement`` por item (N+1).
- ``archive_plan`` cierra planes ``proposed`` obsoletos sin tocar items;
  idempotente sobre ``archived``; los ``applied`` son historial y se rechan.
- Las tools ``get_grouping_plan`` / ``archive_grouping_plan`` exponen lo mismo
  al subagente contra la DB.
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
from backend.agents.tools import grouping_tools
from backend.models import (
    Base,
    GroupDecision,
    GroupingGroup,
    GroupingPlan,
    PlanStatus,
    Priority,
    Project,
    ReqStatus,
    ReqType,
    RequirementItem,
)
from backend.services import grouping_store as gstore

SPEC = "/workspaces/p/docs/spec.pdf"
RFQ = "/workspaces/p/other/rfq.docx"


async def _seed(session) -> tuple[int, int, int, int]:
    """Proyecto + 2 items (uno con fuente lista post-merge) + 2 planes.

    Devuelve (project_id, plan_propuesto, plan_aplicado, grupo_del_propuesto).
    """
    proj = Project(user_id=1, name="ciclo", slug="ciclo", description="t")
    session.add(proj)
    await session.flush()

    keeper = RequirementItem(
        project_id=proj.id, code="REQ-001",
        statement="El sistema debe autenticar usuarios via Google OAuth.",
        type=ReqType.FUNCTIONAL, priority=Priority.MUST,
        status=ReqStatus.VALIDATED, confidence=0.9, explicit_priority=True,
        source={"document_id": SPEC, "quote": "autenticacion"},
    )
    member = RequirementItem(
        project_id=proj.id, code="REQ-002",
        statement="Autenticacion de usuarios mediante Google OAuth.",
        type=ReqType.FUNCTIONAL, priority=Priority.SHOULD,
        status=ReqStatus.DRAFT, confidence=0.8, explicit_priority=False,
        # Fuente LISTA (union post-merge): _item_brief dedupea los document_id.
        source=[
            {"document_id": SPEC},
            {"document_id": RFQ},
            {"document_id": SPEC},
        ],
    )
    session.add_all([keeper, member])
    await session.flush()

    proposed = GroupingPlan(project_id=proj.id, status=PlanStatus.PROPOSED)
    applied = GroupingPlan(project_id=proj.id, status=PlanStatus.APPLIED)
    session.add_all([proposed, applied])
    await session.flush()

    group = GroupingGroup(
        plan_id=proposed.id, keeper_id=keeper.id, member_ids=[member.id],
        reason="duplicado semantico", confidence=0.9,
        decision=GroupDecision.PENDING,
    )
    session.add(group)
    await session.commit()
    return proj.id, proposed.id, applied.id, group.id


async def _fresh_db(monkeypatch):
    """Temp SQLite + tools de grouping apuntadas a ella. Devuelve la factory."""
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(grouping_tools, "AsyncSessionLocal", sm)
    return sm, tmp, engine


# --- payload enriquecido ----------------------------------------------------


@pytest.mark.asyncio
async def test_get_plan_carries_curation_fields(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            _pid, plan_id, _applied, _gid = await _seed(session)

        async with sm() as session:
            data = await gstore.get_plan(session, plan_id)

        g = data["groups"][0]
        # Keeper: campos que la curacion necesita sin get_requirement.
        assert g["keeper"]["code"] == "REQ-001"
        assert g["keeper"]["priority"] == "must"
        assert g["keeper"]["explicit_priority"] is True
        assert g["keeper"]["type"] == "functional"
        assert g["keeper"]["status"] == "validated"
        assert g["keeper"]["source_documents"] == [SPEC]
        # Miembro con fuente LISTA: document_id dedupeados (SPEC una vez).
        assert g["members"][0]["source_documents"] == [SPEC, RFQ]
        assert g["members"][0]["priority"] == "should"
        assert g["members"][0]["explicit_priority"] is False
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_get_grouping_plan_tool_reads_persisted_plan(monkeypatch):
    """La tool lee el plan persistido; plan inexistente -> error, no excepcion."""
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            _pid, plan_id, _applied, _gid = await _seed(session)

        tools = {t.name: t for t in grouping_tools.make_grouping_tools(1)}
        assert "get_grouping_plan" in tools
        assert "archive_grouping_plan" in tools

        data = await tools["get_grouping_plan"].ainvoke({"plan_id": plan_id})
        assert data["id"] == plan_id
        assert data["groups"][0]["keeper"]["code"] == "REQ-001"

        missing = await tools["get_grouping_plan"].ainvoke({"plan_id": 999})
        assert "error" in missing
    finally:
        await engine.dispose()
        tmp.cleanup()


# --- archivado ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_plan_closes_proposed_without_touching_items(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            pid, plan_id, _applied, _gid = await _seed(session)

        async with sm() as session:
            result = await gstore.archive_plan(session, plan_id)
        assert result == {"id": plan_id, "status": "archived"}

        # Los items siguen vivos e intactos: archivar no toca el store.
        async with sm() as session:
            items = (await session.scalars(
                select(RequirementItem)
            )).all()
        assert len(items) == 2
        assert all(it.status != ReqStatus.MERGED for it in items)

        # Idempotente: segunda pasada reporta already_archived.
        async with sm() as session:
            again = await gstore.archive_plan(session, plan_id)
        assert again["already_archived"] is True

        # Plan inexistente -> None (la tool lo traduce a error).
        async with sm() as session:
            assert await gstore.archive_plan(session, 999) is None
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_archive_plan_refuses_applied_history(monkeypatch):
    """Los planes applied son historial de auditoria: no se archivan."""
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            _pid, _proposed, applied_id, _gid = await _seed(session)

        async with sm() as session:
            result = await gstore.archive_plan(session, applied_id)
        assert "error" in result

        async with sm() as session:
            row = await session.get(GroupingPlan, applied_id)
        assert row.status == PlanStatus.APPLIED
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_archive_grouping_plan_tool_end_to_end(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            _pid, plan_id, _applied, _gid = await _seed(session)

        tools = {t.name: t for t in grouping_tools.make_grouping_tools(1)}
        result = await tools["archive_grouping_plan"].ainvoke(
            {"plan_id": plan_id}
        )
        assert result["status"] == "archived"

        missing = await tools["archive_grouping_plan"].ainvoke(
            {"plan_id": 999}
        )
        assert "error" in missing
    finally:
        await engine.dispose()
        tmp.cleanup()


# --- list_plans refleja el nuevo estado --------------------------------------


@pytest.mark.asyncio
async def test_list_plans_reports_archived_status(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            pid, plan_id, _applied, _gid = await _seed(session)
            await gstore.archive_plan(session, plan_id)

        async with sm() as session:
            plans = await gstore.list_plans(session, pid)
        statuses = {p["id"]: p["status"] for p in plans}
        assert statuses[plan_id] == "archived"
    finally:
        await engine.dispose()
        tmp.cleanup()
