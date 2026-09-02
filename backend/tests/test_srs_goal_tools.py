"""Tools quirúrgicas de goals (link_goal / unlink_goal / infer_goal_links).

Cubren el cableado expuesto al agente: validación de relation, errores
not_found, idempotencia y la read tool goal_coverage. La lógica de fondo
(upsert idempotente, matching difuso, inferencia incremental) está pineada en
``test_goal_upsert`` y ``test_goals_engine``; acá el LLM es stubado y la DB es
sqlite temporal con ``AsyncSessionLocal`` parcheado en los módulos de tools.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.agents.subagents import srs_agent as sa_mod
from backend.agents.tools import srs_tools as st_mod
from backend.models import Base, Priority, Project, ReqType, RequirementItem
from backend.models.srs import GoalKind
from backend.services import goals_engine as ge
from backend.services import srs_store


async def _fresh_db():
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return sm, tmp, engine


async def _seed(sm) -> tuple[int, str]:
    """Proyecto con 2 reqs y 1 goal. Devuelve (project_id, GOAL-code)."""
    async with sm() as session:
        proj = Project(user_id=1, name="g", slug="g", description="t")
        session.add(proj)
        await session.flush()
        pid = proj.id
        req_by_code: dict[str, int] = {}
        for i in range(2):
            item = RequirementItem(
                project_id=pid,
                code=f"REQ-{i:04d}",
                statement=f"Requerimiento de prueba numero {i}.",
                type=ReqType.FUNCTIONAL,
                priority=Priority.MUST,
            )
            session.add(item)
            await session.flush()
            req_by_code[item.code] = item.id
        await srs_store.replace_goals(
            session, pid,
            [{"kind": GoalKind.FUNCTIONAL_GOAL, "statement": "Servir pedidos"}],
            [], req_by_code=req_by_code,
        )
        goal = (await srs_store.list_goals(session, pid))[0]
        return pid, goal.code


def _stub_goal_links(monkeypatch, *, goal_code: str, req_code: str) -> None:
    """Stub de ge.structured_llm: una GoalLinks con un único link."""

    def factory(schema, *, temperature=0.0, extra_body=None):
        async def ainvoke(msgs, config=None, **kwargs):
            return ge.GoalLinks(
                links=[
                    ge.InferredLink(
                        goal_code=goal_code, req_code=req_code,
                        relation="realizes", rationale="stub",
                    )
                ]
            )

        return SimpleNamespace(ainvoke=ainvoke)

    monkeypatch.setattr(ge, "structured_llm", factory)
    monkeypatch.setattr(
        ge, "disable_thinking_body", lambda: {"thinking": {"type": "disabled"}}
    )


@pytest.mark.asyncio
async def test_link_goal_roundtrip_idempotent_and_coverage(monkeypatch):
    sm, tmp, engine = await _fresh_db()
    monkeypatch.setattr(sa_mod, "AsyncSessionLocal", sm)
    monkeypatch.setattr(st_mod, "AsyncSessionLocal", sm)
    try:
        pid, goal_code = await _seed(sm)
        tools = {t.name: t for t in sa_mod._make_stage_tools(pid)}
        read = {t.name: t for t in st_mod.make_srs_read_tools(pid)}
        assert "goal_coverage" in read

        first = await tools["link_goal"].ainvoke(
            {
                "goal_code": goal_code,
                "req_code": "REQ-0000",
                "relation": "realizes",
                "rationale": "a mano",
            }
        )
        assert first["created"] is True
        assert first["goal"] == goal_code

        again = await tools["link_goal"].ainvoke(
            {
                "goal_code": goal_code,
                "req_code": "REQ-0000",
                "relation": "realizes",
                "rationale": "v2",
            }
        )
        assert again["created"] is False  # idempotente

        cov = await read["goal_coverage"].ainvoke({})
        assert cov["with_goal_link"] == 1
        assert cov["without_goal_codes"] == ["REQ-0001"]
        assert cov["per_goal"][0]["code"] == goal_code
        assert cov["per_goal"][0]["links"] == 1

        removed = await tools["unlink_goal"].ainvoke(
            {
                "goal_code": goal_code,
                "req_code": "REQ-0000",
                "relation": "realizes",
            }
        )
        assert removed["removed"] is True
        removed_again = await tools["unlink_goal"].ainvoke(
            {
                "goal_code": goal_code,
                "req_code": "REQ-0000",
                "relation": "realizes",
            }
        )
        assert removed_again["removed"] is False
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_link_goal_reports_actionable_errors(monkeypatch):
    sm, tmp, engine = await _fresh_db()
    monkeypatch.setattr(sa_mod, "AsyncSessionLocal", sm)
    monkeypatch.setattr(st_mod, "AsyncSessionLocal", sm)
    try:
        pid, goal_code = await _seed(sm)
        tools = {t.name: t for t in sa_mod._make_stage_tools(pid)}
        link = tools["link_goal"]

        bad_rel = await link.ainvoke(
            {
                "goal_code": goal_code,
                "req_code": "REQ-0000",
                "relation": "viola",
            }
        )
        assert bad_rel["error"] == "invalid_relation"

        bad_goal = await link.ainvoke(
            {
                "goal_code": "GOAL-XXXX",
                "req_code": "REQ-0000",
                "relation": "realizes",
            }
        )
        assert bad_goal["error"] == "goal_not_found"

        bad_req = await link.ainvoke(
            {
                "goal_code": goal_code,
                "req_code": "REQ-9999",
                "relation": "realizes",
            }
        )
        assert bad_req["error"] == "requirement_not_found"
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_link_goals_bulk_creates_updates_and_reports_errors(monkeypatch):
    """Lote de pares ya decididos: crea/actualiza y reporta errores por fila
    sin abortar el resto del lote (220 link_goal individuales costaron ~45
    min en la sesión 53; el lote es UN llamado)."""
    sm, tmp, engine = await _fresh_db()
    monkeypatch.setattr(sa_mod, "AsyncSessionLocal", sm)
    monkeypatch.setattr(st_mod, "AsyncSessionLocal", sm)
    try:
        pid, goal_code = await _seed(sm)
        tools = {t.name: t for t in sa_mod._make_stage_tools(pid)}

        res = await tools["link_goals"].ainvoke({"entries": [
            {"goal_code": goal_code, "req_code": "REQ-0000",
             "relation": "realizes", "rationale": "a mano"},
            # Misma arista: refresh de rationale, cuenta como updated.
            {"goal_code": goal_code, "req_code": "REQ-0000",
             "relation": "realizes", "rationale": "a mano v2"},
            {"goal_code": goal_code, "req_code": "REQ-0001",
             "relation": "contributes"},
            # Errores por fila, sin abortar el lote.
            {"goal_code": goal_code, "req_code": "REQ-0001",
             "relation": "viola"},
            {"goal_code": "GOAL-XXXX", "req_code": "REQ-0000",
             "relation": "realizes"},
            {"goal_code": goal_code, "req_code": "REQ-9999",
             "relation": "realizes"},
        ]})

        assert res["created"] == 2
        assert res["updated"] == 1
        assert len(res["errors"]) == 3
        assert {e["error"] for e in res["errors"]} == {
            "invalid_relation", "goal_not_found", "requirement_not_found",
        }

        # Persistencia real del lote.
        read = {t.name: t for t in st_mod.make_srs_read_tools(pid)}
        cov = await read["goal_coverage"].ainvoke({})
        assert cov["with_goal_link"] == 2
        assert cov["without_goal_codes"] == []
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_link_goals_rejects_oversized_batch(monkeypatch):
    sm, tmp, engine = await _fresh_db()
    monkeypatch.setattr(sa_mod, "AsyncSessionLocal", sm)
    monkeypatch.setattr(st_mod, "AsyncSessionLocal", sm)
    try:
        pid, goal_code = await _seed(sm)
        tools = {t.name: t for t in sa_mod._make_stage_tools(pid)}
        big = [
            {"goal_code": goal_code, "req_code": "REQ-0000",
             "relation": "realizes"}
            for _ in range(41)
        ]
        res = await tools["link_goals"].ainvoke({"entries": big})
        assert res["error"] == "too_many_entries"
        assert res["max_entries"] == 40
    finally:
        await engine.dispose()
        tmp.cleanup()


def test_link_goals_tool_is_registered():
    names = [t.name for t in sa_mod._make_stage_tools(1)]
    assert "link_goals" in names


@pytest.mark.asyncio
async def test_infer_goal_links_tool_persists_stubbed_links(monkeypatch):
    sm, tmp, engine = await _fresh_db()
    monkeypatch.setattr(sa_mod, "AsyncSessionLocal", sm)
    monkeypatch.setattr(st_mod, "AsyncSessionLocal", sm)
    try:
        pid, goal_code = await _seed(sm)
        _stub_goal_links(monkeypatch, goal_code=goal_code, req_code="REQ-0001")

        tools = {t.name: t for t in sa_mod._make_stage_tools(pid)}
        summary = await tools["infer_goal_links"].ainvoke(
            {"req_codes": ["REQ-0001"]}
        )
        assert summary["links_added"] == 1
        assert summary["reqs"] == 1

        # Segunda pasada sobre el mismo req: idempotente (existing, no add).
        summary2 = await tools["infer_goal_links"].ainvoke(
            {"req_codes": ["REQ-0001"]}
        )
        assert summary2["links_added"] == 0
        assert summary2["links_existing"] == 1

        # Sin goals (proyecto vacío): error explícito, no crash.
        empty_tools = {
            t.name: t for t in sa_mod._make_stage_tools(pid + 12345)
        }
        err = await empty_tools["infer_goal_links"].ainvoke(
            {"req_codes": ["REQ-0001"]}
        )
        assert "error" in err
    finally:
        await engine.dispose()
        tmp.cleanup()
