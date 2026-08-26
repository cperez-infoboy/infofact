"""Tests de las tools del harness (add/list/retire) sobre el subagente.

Pines: superficie de nombres, validación de scope con derivación clara,
conflictos surfaced en el resultado (warn, no block) y retire de regla
inexistente con error accionable.
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
from backend.agents.tools.project_rules_tools import make_project_rules_tools
from backend.models import Base, Project
from backend.models.project_rule import RuleStatus


async def _seed_project(session) -> int:
    proj = Project(user_id=1, name="tools", slug="tools", description="t")
    session.add(proj)
    await session.flush()
    return proj.id


async def _fresh_db(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(
        "backend.agents.tools.project_rules_tools.AsyncSessionLocal", sm
    )
    return sm, tmp, engine


@pytest.mark.asyncio
async def test_tool_surface_names():
    tools = {t.name for t in make_project_rules_tools(1)}
    assert tools == {
        "add_project_rule",
        "list_project_rules",
        "retire_project_rule",
    }


@pytest.mark.asyncio
async def test_add_list_retire_flow(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            pid = await _seed_project(session)

        tools = {t.name: t for t in make_project_rules_tools(pid)}
        created = await tools["add_project_rule"].ainvoke({
            "scope": "capture",
            "content": "Prestar atención a restricciones regulatorias.",
            "reason": "pedido del cliente en sesión",
        })
        assert created["created"]["scope"] == "capture"
        assert created["created"]["status"] == "active"
        assert "conflicts" not in created

        listed = await tools["list_project_rules"].ainvoke({})
        assert listed["count"] == 1
        assert listed["rules"][0]["reason"] == "pedido del cliente en sesión"

        rid = created["created"]["id"]
        retired = await tools["retire_project_rule"].ainvoke({
            "rule_id": rid, "reason": "el usuario la revocó",
        })
        assert retired["status"] == "retired"

        # Fuera del bloque activo, visible solo con include_retired.
        active = await tools["list_project_rules"].ainvoke({})
        assert active["count"] == 0
        audit = await tools["list_project_rules"].ainvoke(
            {"include_retired": True}
        )
        assert audit["count"] == 1
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_add_invalid_scope_and_missing_rule(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            pid = await _seed_project(session)

        tools = {t.name: t for t in make_project_rules_tools(pid)}
        bad = await tools["add_project_rule"].ainvoke({
            "scope": "deploy", "content": "x",
        })
        assert "error" in bad
        assert "capture | analysis | srs | all" in bad["error"]

        missing = await tools["retire_project_rule"].ainvoke({"rule_id": 999})
        assert "error" in missing
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_add_surfaces_conflicts(monkeypatch):
    """El conflicto no bloquea: la regla se crea y el resultado guía al agente."""

    def _fake_embed(texts):
        import numpy as np

        uniq = list(dict.fromkeys(texts))
        idx = {t: i for i, t in enumerate(uniq)}
        vecs = np.zeros((len(texts), len(uniq)), dtype="float32")
        for i, t in enumerate(texts):
            vecs[i, idx[t]] = 1.0
        return vecs

    monkeypatch.setattr(
        "backend.agents.pipelines.consolidation.embed_texts", _fake_embed
    )
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            pid = await _seed_project(session)

        tools = {t.name: t for t in make_project_rules_tools(pid)}
        first = await tools["add_project_rule"].ainvoke({
            "scope": "all", "content": "Tratar auditoría como regulatorio.",
        })
        dup = await tools["add_project_rule"].ainvoke({
            "scope": "all", "content": "Tratar auditoría como regulatorio.",
        })
        assert dup["created"]["status"] == "active"
        assert dup["conflicts"][0]["rule_id"] == first["created"]["id"]
        assert "retirá" in dup["note"] or "retira" in dup["note"]
    finally:
        await engine.dispose()
        tmp.cleanup()
