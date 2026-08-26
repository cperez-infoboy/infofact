"""Tests del store de ProjectRule (harness persistente del proyecto).

Pines principales:
- ``rules_block_for`` arma el bloque PROJECT_RULES con scope pedido + ``all``
  (y solo activas); string vacío cuando no hay nada → prompt intacto.
- Ciclo soft: retire/reactivate conservan la fila; ``note`` se agrega al reason.
- Conflictos: coseno contra activas del mismo alcance; AVISA no bloquea; un
  embedder roto degrada a lista vacía (agregar nunca falla por el modelo local).
- Export/import Markdown: roundtrip estable (upsert por id, sin ids crea).
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
from backend.models import Base, Project
from backend.models.project_rule import (
    ProjectRule,
    RuleScope,
    RuleSource,
    RuleStatus,
)
from backend.services import project_rules_store as store


async def _seed_project(session, slug: str = "rules") -> int:
    proj = Project(user_id=1, name=slug, slug=slug, description="t")
    session.add(proj)
    await session.flush()
    return proj.id


async def _fresh_db():
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return sm, tmp, engine


def _fake_embed(texts: list):
    """Embedder determinista: textos idénticos → coseno 1.0; distintos → 0."""
    import numpy as np

    uniq = list(dict.fromkeys(texts))
    idx = {t: i for i, t in enumerate(uniq)}
    vecs = np.zeros((len(texts), len(uniq)), dtype="float32")
    for i, t in enumerate(texts):
        vecs[i, idx[t]] = 1.0
    return vecs


# --- bloque de inyección ------------------------------------------------------


@pytest.mark.asyncio
async def test_rules_block_includes_scope_and_all_active_only():
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed_project(session)
            await store.add_rule(
                session, pid, scope=RuleScope.CAPTURE,
                content="Prestar atención a restricciones regulatorias.",
                check_conflicts=False,
            )
            await store.add_rule(
                session, pid, scope=RuleScope.ALL,
                content="Glosario del cliente obligatorio.",
                check_conflicts=False,
            )
            await store.add_rule(
                session, pid, scope=RuleScope.SRS,
                content="Tono formal en la narrativa.",
                check_conflicts=False,
            )

            # SRS ve las de srs + all, no las de capture.
            block = await store.rules_block_for(session, pid, RuleScope.SRS)
            assert "Tono formal" in block
            assert "Glosario del cliente" in block
            assert "regulatorias" not in block
            assert block.startswith(store.RULES_BLOCK_HEADER)

            # Retirar una regla la saca del bloque.
            rules = await store.list_rules(session, pid, scope=RuleScope.ALL)
            await store.set_rule_status(
                session, pid, rules[0].id, RuleStatus.RETIRED
            )
            block = await store.rules_block_for(session, pid, RuleScope.SRS)
            assert "Glosario del cliente" not in block
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_rules_block_empty_when_no_rules():
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed_project(session)
            block = await store.rules_block_for(session, pid, RuleScope.CAPTURE)
        assert block == ""
    finally:
        await engine.dispose()
        tmp.cleanup()


# --- ciclo soft ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_retire_and_reactivate_with_note():
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed_project(session)
            result = await store.add_rule(
                session, pid, scope=RuleScope.CAPTURE,
                content="Regla original.", reason="pedido del usuario",
                check_conflicts=False,
            )
            rid = result["rule"].id

            retired = await store.set_rule_status(
                session, pid, rid, RuleStatus.RETIRED, note="el usuario la revocó"
            )
            assert retired.status is RuleStatus.RETIRED
            assert "el usuario la revocó" in retired.reason
            assert "Regla original." == retired.content  # contenido intacto

            reactivated = await store.set_rule_status(
                session, pid, rid, RuleStatus.ACTIVE
            )
            assert reactivated.status is RuleStatus.ACTIVE

            with pytest.raises(KeyError):
                await store.set_rule_status(session, pid, 999, RuleStatus.RETIRED)
    finally:
        await engine.dispose()
        tmp.cleanup()


# --- conflictos ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_rule_reports_conflict_on_same_content(monkeypatch):
    monkeypatch.setattr(
        "backend.agents.pipelines.consolidation.embed_texts", _fake_embed
    )
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed_project(session)
            first = await store.add_rule(
                session, pid, scope=RuleScope.CAPTURE,
                content="Marcar auditoría como regulatorio.",
            )
            assert first["conflicts"] == []

            # Misma frase otra vez → conflicto consigo misma (coseno 1.0),
            # pero la regla SE CREA igual (warn, no block).
            second = await store.add_rule(
                session, pid, scope=RuleScope.CAPTURE,
                content="Marcar auditoría como regulatorio.",
            )
            assert second["rule"].id != first["rule"].id
            conflict_ids = [c["rule_id"] for c in second["conflicts"]]
            assert first["rule"].id in conflict_ids

            # Contenido distinto en otro scope → sin conflicto.
            third = await store.add_rule(
                session, pid, scope=RuleScope.SRS,
                content="Tono formal en la narrativa.",
            )
            assert third["conflicts"] == []
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_broken_embedder_degrades_to_no_conflicts(monkeypatch):
    def _boom(texts):
        raise RuntimeError("modelo local caído")

    monkeypatch.setattr(
        "backend.agents.pipelines.consolidation.embed_texts", _boom
    )
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed_project(session)
            result = await store.add_rule(
                session, pid, scope=RuleScope.ALL, content="Regla sin embedder."
            )
            assert result["conflicts"] == []
            assert result["rule"].status is RuleStatus.ACTIVE
    finally:
        await engine.dispose()
        tmp.cleanup()


# --- export / import -----------------------------------------------------------


@pytest.mark.asyncio
async def test_export_import_roundtrip():
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed_project(session)
            a = await store.add_rule(
                session, pid, scope=RuleScope.CAPTURE,
                content="Regla de captura.", reason="pedido inicial",
                check_conflicts=False,
            )
            await session.commit()
            await store.add_rule(
                session, pid, scope=RuleScope.ALL,
                content="Regla global.", check_conflicts=False,
            )

            rules = await store.list_rules(session, pid, include_retired=True)
            md = store.export_rules_md(rules, project_name="rules")

            assert "## Captura" in md
            assert "## Todas" in md
            assert f"(R-{a['rule'].id}, usuario)" in md
            assert "  > Motivo: pedido inicial" in md

            # Import en OTRO proyecto: sin ids conocidos → crea todo.
            pid2 = await _seed_project(session, slug="rules2")
            await session.commit()
            stats = await store.import_rules_md(session, pid2, md)
            assert stats["created"] == 2
            imported = await store.list_rules(session, pid2)
            assert {r.content for r in imported} == {
                "Regla de captura.", "Regla global.",
            }
            assert all(r.source is RuleSource.USER for r in imported)

            # Re-import sobre el MISMO proyecto: los ids matchean → update, no dupes.
            stats2 = await store.import_rules_md(session, pid, md)
            assert stats2["updated"] == 2
            assert stats2["created"] == 0
            after = await store.list_rules(session, pid, include_retired=True)
            assert len(after) == 2

            # Líneas basura se ignoran y cuentan.
            stats3 = await store.import_rules_md(
                session, pid2, "## Captura\nlínea sin formato\n"
            )
            assert stats3["skipped"] == 1
    finally:
        await engine.dispose()
        tmp.cleanup()
