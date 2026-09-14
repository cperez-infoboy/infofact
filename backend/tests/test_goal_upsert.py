"""Upsert idempotente de goals + links (regresión del churn GOAL-XXXX).

Sesión 9 de Planitrack: re-correr infer_goals REEMPLAZABA todos los goals
(``replace_goals`` borra filas y reasigna códigos opacos), así que GOAL-YGG7
volvía como GOAL-G7ZB: los links escritos a mano quedaban huérfanos y la
matriz de trazabilidad cambiaba de códigos entre corridas sin cambio real
alguno en el modelo.

Pines de esta suite:

- el goal con la misma frase (y kind) conserva id / code / status de curación;
- el goal nuevo entra con código fresco;
- el goal PROPOSED que la nueva inferencia ya no menciona se elimina con sus
  links; el CONFIRMED (decisión humana) se conserva con sus links;
- los links de los goals re-inferidos se reemplazan; los del preservado que
  NO volvió no se tocan;
- ``add_goal_link`` es idempotente por la tripleta (goal, req, relation) y
  ``remove_goal_link`` borra exactamente esa tripleta;
- ``goal_coverage`` cuenta los reqs vivos con y sin al menos un link.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.models import Base, Priority, Project, ReqStatus, ReqType, RequirementItem
from backend.models.srs import GoalKind, GoalLink, GoalStatus, LinkRelation
from backend.services import srs_store


async def _fresh_db() -> tuple[async_sessionmaker, object, object]:
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return sm, tmp, engine


async def _seed(sm, n: int = 3) -> int:
    async with sm() as session:
        proj = Project(user_id=1, name="g", slug="g", description="t")
        session.add(proj)
        await session.flush()
        pid = proj.id
        for i in range(n):
            session.add(
                RequirementItem(
                    project_id=pid,
                    code=f"REQ-{i:04d}",
                    statement=f"Requerimiento de prueba numero {i}.",
                    type=ReqType.FUNCTIONAL,
                    priority=Priority.MUST,
                    status=ReqStatus.DRAFT,
                )
            )
        await session.commit()
        return pid


async def _req_by_code(sm, pid: int) -> dict[str, int]:
    async with sm() as session:
        rows = (
            await session.execute(
                select(RequirementItem.id, RequirementItem.code).where(
                    RequirementItem.project_id == pid)
            )
        ).all()
        return {code: rid for rid, code in rows}


_G = GoalKind.FUNCTIONAL_GOAL


@pytest.mark.asyncio
async def test_upsert_preserves_code_statement_and_status():
    sm, tmp, engine = await _fresh_db()
    try:
        pid = await _seed(sm)
        req_by_code = await _req_by_code(sm, pid)
        async with sm() as session:
            await srs_store.replace_goals(
                session, pid,
                [{"kind": _G, "statement": "Servir pedidos", "confidence": 0.7}],
                [], req_by_code=req_by_code,
            )
            code_before = (await srs_store.list_goals(session, pid))[0].code

        async with sm() as session:
            res = await srs_store.upsert_goals(
                session, pid,
                [{"code": "G1", "kind": _G, "statement": "Servir  pedidos",
                  "confidence": 0.8}],
                [], req_by_code=req_by_code,
            )
            assert res["goals_kept"] == 1
            assert res["goals_added"] == 0
            assert res["goals_removed"] == 0
            goals = await srs_store.list_goals(session, pid)
            assert len(goals) == 1
            assert goals[0].code == code_before  # SIN churn de código
            assert goals[0].confidence == 0.8
            assert goals[0].statement == "Servir  pedidos"
            assert goals[0].status == GoalStatus.PROPOSED
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_upsert_mixed_keeps_confirmed_removes_stale_proposed():
    sm, tmp, engine = await _fresh_db()
    try:
        pid = await _seed(sm)
        req_by_code = await _req_by_code(sm, pid)
        async with sm() as session:
            await srs_store.replace_goals(
                session, pid,
                [
                    {"kind": _G, "statement": "Servir pedidos"},
                    {"kind": _G, "statement": "Reportar metricas"},
                    {"kind": _G, "statement": "Descartar basura"},
                ],
                [], req_by_code=req_by_code,
            )
            goals = {g.statement: g for g in await srs_store.list_goals(session, pid)}
            # El humano confirmó "Reportar metricas" y le agregó un link a mano.
            await srs_store.update_goal(
                session, goals["Reportar metricas"].id,
                project_id=pid, status=GoalStatus.CONFIRMED,
            )
            rid0 = req_by_code["REQ-0000"]
            await srs_store.add_goal_link(
                session, pid,
                goal_id=goals["Reportar metricas"].id,
                req_id=rid0, relation=LinkRelation.REALIZES,
                rationale="link hecho a mano",
            )
            codes = {g.statement: g.code for g in goals.values()}

        async with sm() as session:
            res = await srs_store.upsert_goals(
                session, pid,
                [
                    {"code": "G1", "kind": _G, "statement": "Servir pedidos"},
                    {"code": "G2", "kind": _G, "statement": "Auditar operaciones"},
                ],
                [{"goal_code": "G1", "req_code": "REQ-0001",
                  "relation": "realizes"}],
                req_by_code=req_by_code,
            )
            assert res["goals_kept"] == 1
            assert res["goals_added"] == 1
            # "Descartar basura" (proposed ausente): ya no se borra — se
            # marca STALE conservando fila, código y links (curación con
            # memoria; una re-inferencia posterior puede revivirlo).
            assert res["goals_stale"] == 1
            assert res["goals_removed"] == 1  # alias legado = marcados stale

            by_stmt = {g.statement: g for g in await srs_store.list_goals(session, pid)}
            assert set(by_stmt) == {
                "Servir pedidos", "Auditar operaciones",
                "Reportar metricas", "Descartar basura",
            }
            assert by_stmt["Servir pedidos"].code == codes["Servir pedidos"]
            assert by_stmt["Auditar operaciones"].code.startswith("GOAL-")
            assert by_stmt["Auditar operaciones"].code != codes["Descartar basura"]
            assert (
                by_stmt["Descartar basura"].status == GoalStatus.STALE
            )
            assert (
                by_stmt["Descartar basura"].code
                == codes["Descartar basura"]
            )

            # Links: el hecho a mano del confirmado sobrevive; el del
            # re-inferido se creó por la nueva pasada.
            links = await srs_store.list_goal_links(session, pid)
            assert len(links) == 2
            assert any(
                l.rationale == "link hecho a mano" for l in links
            )
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_add_and_remove_goal_link_idempotent():
    sm, tmp, engine = await _fresh_db()
    try:
        pid = await _seed(sm)
        req_by_code = await _req_by_code(sm, pid)
        async with sm() as session:
            await srs_store.replace_goals(
                session, pid,
                [{"kind": _G, "statement": "Servir pedidos"}],
                [], req_by_code=req_by_code,
            )
            goal = (await srs_store.list_goals(session, pid))[0]
            rid0 = req_by_code["REQ-0000"]

            first = await srs_store.add_goal_link(
                session, pid, goal_id=goal.id, req_id=rid0,
                relation=LinkRelation.REALIZES, rationale="v1",
            )
            assert first["created"] is True
            second = await srs_store.add_goal_link(
                session, pid, goal_id=goal.id, req_id=rid0,
                relation=LinkRelation.REALIZES, rationale="v2",
            )
            assert second["created"] is False  # idempotente
            assert second["link"]["rationale"] == "v2"
            assert second["link"]["id"] == first["link"]["id"]

            assert await srs_store.remove_goal_link(
                session, pid, goal_id=goal.id, req_id=rid0,
                relation=LinkRelation.REALIZES,
            ) is True
            assert await srs_store.remove_goal_link(
                session, pid, goal_id=goal.id, req_id=rid0,
                relation=LinkRelation.REALIZES,
            ) is False
            assert await srs_store.list_goal_links(session, pid) == []
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_goal_coverage_counts_live_with_and_without_link():
    sm, tmp, engine = await _fresh_db()
    try:
        pid = await _seed(sm, n=3)
        req_by_code = await _req_by_code(sm, pid)
        async with sm() as session:
            # REQ-0002 rechazado: queda fuera del conteo de vivos.
            rid2 = req_by_code["REQ-0002"]
            item = await session.get(RequirementItem, rid2)
            item.status = ReqStatus.REJECTED
            await srs_store.replace_goals(
                session, pid,
                [{"kind": _G, "statement": "Servir pedidos"}],
                [{"goal_code": None, "req_code": "REQ-0000",
                  "relation": "realizes"}][:0],  # links vacíos (alias None)
                req_by_code=req_by_code,
            )
            goal = (await srs_store.list_goals(session, pid))[0]
            await srs_store.add_goal_link(
                session, pid, goal_id=goal.id, req_id=req_by_code["REQ-0000"],
                relation=LinkRelation.REALIZES,
            )

            cov = await srs_store.goal_coverage(session, pid)
        assert cov["goals"] == 1
        assert cov["live_requirements"] == 2  # el rechazado no cuenta
        assert cov["with_goal_link"] == 1
        assert cov["without_goal_link"] == 1
        assert cov["without_goal_codes"] == ["REQ-0001"]
        assert cov["per_goal"][0]["code"] == goal.code
        assert cov["per_goal"][0]["links"] == 1
    finally:
        await engine.dispose()
        tmp.cleanup()
