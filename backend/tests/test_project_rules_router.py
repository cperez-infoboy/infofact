"""Tests del router de reglas del proyecto (invocación directa de endpoints).

El router usa ``AsyncSessionLocal`` interno (patrón requirements.py), así que
se invocan las funciones de endpoint directo con un user fake. Pines:
ownership 404 (proyecto ajeno/inexistente), CRUD + retire, export.md con
media-type markdown e import upsert.
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
from backend.models import Base, Project, User
from backend.routers import project_rules as router

_U1 = User(id=1, email="a@x.com", password_hash="x", profile="p1")
_U2 = User(id=2, email="b@x.com", password_hash="x", profile="p2")


async def _fresh_db(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("backend.routers.project_rules.AsyncSessionLocal", sm)
    async with sm() as session:
        session.add(Project(user_id=1, name="r", slug="r", description="d"))
        await session.commit()
    return sm, tmp, engine


@pytest.mark.asyncio
async def test_create_list_patch_flow(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        created = await router.create_project_rule(
            1,
            router.RuleCreate(
                scope="capture",
                content="Prestar atención a restricciones regulatorias.",
                reason="pedido del cliente",
            ),
            user=_U1,
        )
        assert created["scope"] == "capture"
        assert created["status"] == "active"

        listed = await router.list_project_rules(1, scope=None, user=_U1)
        assert listed["count"] == 1

        retired = await router.patch_project_rule(
            1,
            created["id"],
            router.RuleUpdate(status="retired"),
            user=_U1,
        )
        assert retired["status"] == "retired"

        audit = await router.list_project_rules(
            1, scope=None, include_retired=True, user=_U1
        )
        assert audit["count"] == 1
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_ownership_404_and_invalid_scope(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        from fastapi import HTTPException

        # Proyecto existente pero ajeno → 404 idéntico al inexistente.
        for user in (_U1, _U2):
            with pytest.raises(HTTPException) as exc:
                await router.list_project_rules(999, scope=None, user=user)
            assert exc.value.status_code == 404
        with pytest.raises(HTTPException) as exc:
            await router.list_project_rules(1, scope=None, user=_U2)
        assert exc.value.status_code == 404

        with pytest.raises(HTTPException) as exc:
            await router.create_project_rule(
                1,
                router.RuleCreate(scope="deploy", content="x"),
                user=_U1,
            )
        assert exc.value.status_code == 400
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_export_and_import_roundtrip(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        created = await router.create_project_rule(
            1,
            router.RuleCreate(scope="all", content="Regla global."),
            user=_U1,
        )
        retired = await router.patch_project_rule(
            1,
            created["id"],
            router.RuleUpdate(status="retired"),
            user=_U1,
        )
        assert retired["status"] == "retired"

        response = await router.export_project_rules_md(1, user=_U1)
        assert response.media_type == "text/markdown"
        md = response.body.decode("utf-8")
        assert "## Todas" in md
        assert "[retirada]" in md

        # Import con la misma vista → upsert por id (sin duplicar).
        stats = await router.import_project_rules_md(
            1, router.RuleImport(markdown=md), user=_U1
        )
        assert stats["updated"] == 1
        assert stats["created"] == 0
        listed = await router.list_project_rules(
            1, scope=None, include_retired=True, user=_U1
        )
        assert listed["count"] == 1
    finally:
        await engine.dispose()
        tmp.cleanup()
