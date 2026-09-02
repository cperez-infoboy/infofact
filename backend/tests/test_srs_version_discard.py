"""Tests de descarte de versiones del SRS (soft discard, estado DISCARDED).

Sesión 53: el agente pidió al usuario descartar v9-v11 (prosa horneada con
«notas provisionales») para que el próximo refresco siembre desde la v8
limpia, pero no existía ninguna vía: ni store, ni router, ni tool del agente,
ni botón en la UI. El descarte es SOFT (nuevo estado ``DISCARDED``, la fila
queda) por dos razones:

- La numeración NUNCA se reutiliza (``create_srs`` numera con max+1 sobre
  todas las filas): con hard delete la próxima versión volvería a llamarse v9
  y chocaría con toda la conversación previa que menciona a la v9 envenenada.
- ``get_latest_srs`` filtra las descartadas: la «última» pasa a ser la
  versión previa, que es exactamente la base que el seed necesita.

LOCKED no se puede descartar: es el snapshot inmutable que consume la fase 2.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.agents.subagents import srs_agent, srs_run_holder as holder
from backend.models import Base, Priority, Project, ReqType, RequirementItem
from backend.models.srs import SrsStatus
from backend.services import srs_store


async def _fresh_db(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(srs_agent, "AsyncSessionLocal", sm)
    return sm, tmp, engine


async def _mk_project(sm) -> int:
    async with sm() as session:
        proj = Project(user_id=1, name="srs", slug="srs", description="t")
        session.add(proj)
        await session.commit()
        return proj.id


async def _mk_version(sm, pid: int, *, narrative: dict | None = None) -> int:
    """Persiste una versión más del SRS del proyecto; devuelve su número."""
    async with sm() as session:
        row = await srs_store.create_srs(session, pid, {
            "narrative": narrative or {"intro.purpose": f"prosa v{narrative}"},
            "markdown": "# srs",
            "quality_summary": {"total_findings": 0, "blockers": 0},
            "coverage": {"totals": {"live": 2}},
            "requirement_codes": ["REQ-A", "REQ-B"],
            "requirement_count": 2,
        })
        return row.version


def _tools(pid: int) -> dict:
    return {t.name: t for t in srs_agent._make_stage_tools(pid, "srs", "t")}


@pytest.mark.asyncio
async def test_discard_is_soft_and_latest_falls_back(monkeypatch):
    """Descartar la última deja la fila con estado ``discarded`` y la
    «última» pasa a ser la versión previa (base del próximo seed)."""
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        await _mk_version(sm, pid)
        await _mk_version(sm, pid)
        last = await _mk_version(sm, pid)

        async with sm() as session:
            discarded = await srs_store.discard_srs_version(session, pid, last)
            assert discarded.status is SrsStatus.DISCARDED

            latest = await srs_store.get_latest_srs(session, pid)
            assert latest is not None
            assert latest.version == last - 1

            # La fila NO se borra: sigue listada (la UI la etiqueta).
            versions = await srs_store.list_srs_versions(session, pid)
            assert [v.version for v in versions] == [last, last - 1, last - 2]
            assert versions[0].status is SrsStatus.DISCARDED
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_discard_rejects_locked_version(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        v1 = await _mk_version(sm, pid)
        async with sm() as session:
            await srs_store.update_srs(session, pid, v1, status=SrsStatus.LOCKED)
            with pytest.raises(ValueError):
                await srs_store.discard_srs_version(session, pid, v1)
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_discard_missing_version_raises(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        async with sm() as session:
            with pytest.raises(KeyError):
                await srs_store.discard_srs_version(session, pid, 7)
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_discard_twice_rejects(monkeypatch):
    """Ya descartada -> ValueError (el router la mapea a 409, como PATCH)."""
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        v1 = await _mk_version(sm, pid)
        async with sm() as session:
            await srs_store.discard_srs_version(session, pid, v1)
            with pytest.raises(ValueError):
                await srs_store.discard_srs_version(session, pid, v1)
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_version_numbers_never_reused_after_discard(monkeypatch):
    """Con hard delete, la próxima versión tras descartar v3 sería OTRA v3.

    La conversación previa menciona la v9 envenenada: reutilizar el número
    haría indistinguible la historia. El max+1 corre sobre TODAS las filas,
    descartadas incluidas.
    """
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        for _ in range(3):
            await _mk_version(sm, pid)
        async with sm() as session:
            await srs_store.discard_srs_version(session, pid, 3)
        assert await _mk_version(sm, pid) == 4
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_seed_skips_discarded_latest(monkeypatch):
    """El seed siembra desde la última NO descartada (v8 limpia, no v11)."""
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        async with sm() as session:
            session.add_all([
                RequirementItem(
                    project_id=pid, code="REQ-A",
                    statement="Alcance de efectivo.",
                    type=ReqType.FUNCTIONAL, priority=Priority.MUST,
                ),
                RequirementItem(
                    project_id=pid, code="REQ-B", statement="Reportes PDF.",
                    type=ReqType.FUNCTIONAL, priority=Priority.SHOULD,
                ),
            ])
            await session.commit()
        clean = {"intro.purpose": "Prosa curada de la v8."}
        await _mk_version(sm, pid, narrative=clean)
        poisoned = await _mk_version(
            sm, pid, narrative={"intro.purpose": "nota provisional"}
        )
        async with sm() as session:
            await srs_store.discard_srs_version(session, pid, poisoned)

        holder.clear_run(pid)
        result = await _tools(pid)["seed_from_last_srs"].ainvoke({})
        assert result["reused_from_version"] == 1
        assert result["narrative_carried"] is True
        assert holder.get_run(pid).narrative == clean
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_discard_tool_reports_new_latest(monkeypatch):
    """La tool del subagente descarta y reporta la nueva base del seed."""
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        await _mk_version(sm, pid)
        poisoned = await _mk_version(sm, pid)

        result = await _tools(pid)["discard_srs_version"].ainvoke(
            {"version": poisoned}
        )
        assert result["discarded_version"] == poisoned
        assert result["new_latest_version"] == poisoned - 1

        async with sm() as session:
            srs = await srs_store.get_srs_version(session, pid, poisoned)
            assert srs.status is SrsStatus.DISCARDED
    finally:
        await engine.dispose()
        tmp.cleanup()


def test_discard_tool_is_registered():
    """Definida Y registrada: una tool sin registrar no llega al agente."""
    names = [t.name for t in srs_agent._make_stage_tools(1, "p", "d")]
    assert "discard_srs_version" in names
