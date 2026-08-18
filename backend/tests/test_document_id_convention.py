"""Convencion unica de document_id: rebase host->contenedor en todo el camino.

Cubre: persistencia (_source_from_raw), lectura (_source_list / get via
_source_out), filtros por documento (tools + scope de grouping, filas legacy
y sources en lista post-merge) y la migracion de arranque de main.py.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import backend.main as main_module
from backend.agents.pipelines.grouping import _item_in_documents
from backend.agents.tools.requirements_tools import _item_in_document, _source_documents
from backend.config import settings
from backend.models.requirement import RequirementItem
from backend.services.requirement_store import _source_list, _source_out
from backend.services.requirements_service import _source_from_raw

HOST = str(settings.workspaces_root)
LEGACY = f"{HOST}/perfil_claudio/planitrack2-0/docs/spec.pdf"
REBASED = "/workspaces/planitrack2-0/docs/spec.pdf"


def _raw(document_id=LEGACY):
    return SimpleNamespace(
        document_id=document_id, section="s", page=1, source_span="q"
    )


def _item(source):
    return RequirementItem(
        project_id=1, code="REQ-TEST", statement="s", source=source
    )


# --- escritura / lectura ------------------------------------------------------


def test_source_from_raw_persists_container_convention():
    src = _source_from_raw(_raw())
    assert src["document_id"] == REBASED


def test_source_list_rebases_legacy_without_mutating_stored():
    item = _item({"document_id": LEGACY, "quote": "q"})
    out = _source_list(item)
    assert out[0]["document_id"] == REBASED
    # El JSON almacenado queda intacto (solo la migracion de arranque lo reescribe).
    assert item.source["document_id"] == LEGACY


def test_source_out_preserves_shape():
    assert _source_out(_item(None)) is None
    assert _source_out(_item({"document_id": LEGACY}))["document_id"] == REBASED
    out = _source_out(_item([{"document_id": LEGACY}, {"document_id": REBASED}]))
    assert isinstance(out, list) and len(out) == 2


def test_source_documents_dedup_after_rebase():
    item = _item([
        {"document_id": LEGACY},
        {"document_id": REBASED},  # mismo documento, ya en convencion nueva
    ])
    assert _source_documents(item) == [REBASED]


# --- filtros ------------------------------------------------------------------


def test_item_in_document_matches_legacy_rows_by_filename():
    assert _item_in_document(_item({"document_id": LEGACY}), "spec.pdf")
    assert _item_in_document(_item({"document_id": LEGACY}), "docs/spec.pdf")
    assert _item_in_document(_item({"document_id": LEGACY}), REBASED)


def test_grouping_scope_matches_legacy_and_list_sources():
    assert _item_in_documents(_item({"document_id": LEGACY}), {"docs/spec.pdf"})
    # Source en LISTA (post-merge): antes quedaba fuera del scope sin avisar.
    assert _item_in_documents(
        _item([{"document_id": LEGACY}, {"document_id": REBASED}]),
        {"docs/spec.pdf"},
    )
    assert not _item_in_documents(_item(None), {"docs/spec.pdf"})


# --- migracion de arranque ------------------------------------------------------


@pytest.mark.asyncio
async def test_migrate_requirement_document_ids_idempotent(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(
            text("CREATE TABLE requirement_items (id INTEGER PRIMARY KEY, source TEXT)")
        )
        await conn.execute(
            text("INSERT INTO requirement_items (id, source) VALUES (1, :s)"),
            {"s": json.dumps({"document_id": LEGACY, "quote": "q"})},
        )
        await conn.execute(
            text("INSERT INTO requirement_items (id, source) VALUES (2, :s)"),
            {"s": json.dumps({"document_id": REBASED})},
        )
        await conn.execute(
            text("INSERT INTO requirement_items (id, source) VALUES (3, :s)"),
            {"s": json.dumps([{"document_id": LEGACY}, {"document_id": REBASED}])},
        )

    # La migracion usa el engine global de main: apuntarlo al de test.
    monkeypatch.setattr(main_module, "engine", engine)

    await main_module.migrate_requirement_document_ids()

    async with engine.connect() as conn:
        rows = dict(
            (await conn.execute(text("SELECT id, source FROM requirement_items"))).all()
        )
    assert json.loads(rows[1])["document_id"] == REBASED
    assert json.loads(rows[2])["document_id"] == REBASED  # ya estaba: intacta
    merged = json.loads(rows[3])
    assert [e["document_id"] for e in merged] == [REBASED, REBASED]

    # Segunda corrida = no-op (idempotente).
    await main_module.migrate_requirement_document_ids()
    async with engine.connect() as conn:
        rows2 = dict(
            (await conn.execute(text("SELECT id, source FROM requirement_items"))).all()
        )
    assert rows2 == rows

    await engine.dispose()
