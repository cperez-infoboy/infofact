"""Tests de la capa de tools de requerimientos (requirements_tools).

Antes no existia ningun test de esta capa. Pines de esta suite:

- ``list_requirements`` devuelve ``source_documents`` / ``parent_code`` /
  ``derived`` / ``confidence`` por item: la ausencia de esos campos obligaba
  al agente a un ``get_requirement`` por item (patron N+1 visto en runs
  reales del orquestador).
- El filtro ``document`` replica la semantica del scope de un /agrupar
  (substring case-insensitive sobre los document_id de la fuente).
- ``get_requirement`` omite la historia de revisiones salvo pedido explicito
  (es la parte mas pesada del detalle).
- ``capture_status.by_document`` es la vista GROUP BY documento<->conteos
  sobre items vivos, y el factory de lectura la expone al orquestador.

Setup: SQLite in-memory + monkeypatch de ``requirements_tools
.AsyncSessionLocal`` (las tools abren sus propias sesiones contra esa
factory). Estilo de test_requirement_ordering / test_grouping_progress.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.agents.tools import requirements_tools
from backend.models import (
    Base,
    Priority,
    Project,
    ReqStatus,
    ReqType,
    RequirementItem,
)
from backend.services import requirement_store as store

DOC = "Docs_Entrada/05-diccionario-datos.md"
OTHER = "Docs_Entrada/01-vision-producto.md"


async def _setup(monkeypatch) -> int:
    """DB in-memory + proyecto semilla; las tools apuntan a esa factory."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(requirements_tools, "AsyncSessionLocal", sm)
    async with sm() as session:
        proj = Project(user_id=1, name="p", slug="p", description="t")
        session.add(proj)
        await session.flush()
        pid = proj.id
        await session.commit()
    return pid


async def _seed(pid: int, rows: list[RequirementItem]) -> None:
    async with requirements_tools.AsyncSessionLocal() as session:
        for row in rows:
            session.add(row)
        await session.commit()


def _req(
    pid: int,
    code: str,
    statement: str = "s",
    *,
    source=None,
    parent_id=None,
    derived=False,
    type=ReqType.FUNCTIONAL,
    status=ReqStatus.DRAFT,
):
    return RequirementItem(
        project_id=pid,
        code=code,
        statement=statement,
        type=type,
        priority=Priority.MUST,
        status=status,
        confidence=0.9,
        source=source,
        explicit=source is None,
        derived=derived,
        parent_id=parent_id,
        created_by="test",
    )


def _tool(tools: list, name: str):
    return next(t for t in tools if t.name == name)


# --- listado enriquecido -------------------------------------------------------


@pytest.mark.asyncio
async def test_list_carries_source_documents_dedup(monkeypatch):
    """source_documents deduplica (dict y lista post-merge) y va ordenado;
    el resumen tambien expone derived y confidence."""
    pid = await _setup(monkeypatch)
    await _seed(pid, [
        _req(pid, "REQ-A", source={"document_id": DOC, "quote": "q"}),
        _req(pid, "REQ-B", source=[  # union post-merge: DOC dos veces + OTHER
            {"document_id": DOC, "quote": "q1"},
            {"document_id": DOC, "quote": "q2"},
            {"document_id": OTHER, "quote": "q3"},
        ]),
    ])

    out = await _tool(
        requirements_tools.make_requirements_read_tools(pid),
        "list_requirements",
    ).ainvoke({})

    by_code = {i["code"]: i for i in out["items"]}
    assert by_code["REQ-A"]["source_documents"] == [DOC]
    assert by_code["REQ-B"]["source_documents"] == [OTHER, DOC]  # dedup + orden
    assert by_code["REQ-A"]["parent_code"] is None
    assert by_code["REQ-A"]["derived"] is False
    assert by_code["REQ-A"]["confidence"] == 0.9


@pytest.mark.asyncio
async def test_list_resolves_parent_code_outside_filter(monkeypatch):
    """parent_code se resuelve en batch aunque el padre no este en el
    resultado filtrado (el mapa id->code sale de UNA query)."""
    pid = await _setup(monkeypatch)
    await _seed(pid, [_req(pid, "REQ-P", statement="padre")])
    async with requirements_tools.AsyncSessionLocal() as session:
        parent_id = await session.scalar(
            select(RequirementItem.id).where(RequirementItem.code == "REQ-P")
        )
        session.add(_req(
            pid, "REQ-C", statement="hijo",
            source={"document_id": DOC, "quote": "q"},
            parent_id=parent_id, derived=True, type=ReqType.DATA,
        ))
        await session.commit()

    out = await _tool(
        requirements_tools.make_requirements_read_tools(pid),
        "list_requirements",
    ).ainvoke({"type": "data"})

    assert out["count"] == 1
    item = out["items"][0]
    assert item["code"] == "REQ-C"
    assert item["parent_code"] == "REQ-P"
    assert item["derived"] is True


@pytest.mark.asyncio
async def test_document_filter_matches_scoping_semantics(monkeypatch):
    """`document` matchea por substring case-insensitive sobre dict y lista,
    con la misma semantica que el filtro de un /agrupar scopeado."""
    pid = await _setup(monkeypatch)
    await _seed(pid, [
        _req(pid, "REQ-A", source={"document_id": DOC, "quote": "q"}),
        _req(pid, "REQ-B", source=[  # cita DOC via segunda fuente post-merge
            {"document_id": OTHER, "quote": "q1"},
            {"document_id": DOC, "quote": "q2"},
        ]),
        _req(pid, "REQ-C", source={"document_id": OTHER, "quote": "q3"}),
        _req(pid, "REQ-D", source=None),
    ])

    out = await _tool(
        requirements_tools.make_requirements_read_tools(pid),
        "list_requirements",
    ).ainvoke({"document": "DICCIONARIO"})

    assert out["count"] == 2
    assert sorted(i["code"] for i in out["items"]) == ["REQ-A", "REQ-B"]


# --- get_requirement liviano ---------------------------------------------------


@pytest.mark.asyncio
async def test_get_requirement_omits_revisions_unless_asked(monkeypatch):
    """Sin flag NO viaja la historia de revisiones (la parte mas pesada);
    con include_revisions=True si."""
    pid = await _setup(monkeypatch)
    await _seed(pid, [_req(pid, "REQ-A", statement="original")])
    async with requirements_tools.AsyncSessionLocal() as session:
        item_id = await session.scalar(
            select(RequirementItem.id).where(RequirementItem.code == "REQ-A")
        )
        await store.update_requirement(
            session, item_id, statement="editado",
            reason="test", changed_by="test",
        )

    tools = requirements_tools.make_requirements_read_tools(pid)
    get_tool = _tool(tools, "get_requirement")

    light = await get_tool.ainvoke({"code": "REQ-A"})
    full = await get_tool.ainvoke({"code": "REQ-A", "include_revisions": True})

    assert "revisions" not in light
    assert light["statement"] == "editado"
    assert len(full["revisions"]) >= 1


@pytest.mark.asyncio
async def test_add_requirement_summary_survives_extended_helper(monkeypatch):
    """Las tools de edicion llaman _item_summary sin mapa de padres: el
    helper extendido no las rompe (parent_code None, sin fuentes)."""
    pid = await _setup(monkeypatch)

    out = await _tool(
        requirements_tools.make_requirements_tools(pid), "add_requirement"
    ).ainvoke({"statement": "nuevo req", "source_quote": "cita"})

    assert out["code"].startswith("REQ-")
    assert out["source_documents"] == []
    assert out["parent_code"] is None
    assert out["derived"] is False
    assert "confidence" in out


# --- capture_status: la vista GROUP BY -----------------------------------------


@pytest.mark.asyncio
async def test_capture_status_by_document_census(monkeypatch):
    """by_document cuenta citas por documento sobre items VIVOS (los
    soft-deleted quedan fuera del censo pero suman al total de filas)."""
    pid = await _setup(monkeypatch)
    await _seed(pid, [
        _req(pid, "REQ-A", source={"document_id": DOC}),
        _req(pid, "REQ-B", source={"document_id": DOC}),
        _req(pid, "REQ-C", source={"document_id": OTHER}),
        _req(pid, "REQ-D", source=None),
        _req(pid, "REQ-E", source={"document_id": DOC},
             status=ReqStatus.REJECTED),
    ])

    out = await _tool(
        requirements_tools.make_requirements_read_tools(pid),
        "capture_status",
    ).ainvoke({})

    assert out["requirements"] == 5  # todas las filas (guard de captura)
    assert out["by_document"] == [
        {"document": DOC, "count": 2},
        {"document": "(sin fuente)", "count": 1},
        {"document": OTHER, "count": 1},
    ]


def test_twins_and_read_factory_surface():
    """Paridad de twins (mismos args en ambos factories) y el factory de
    lectura expone capture_status: el orquestador tiene la vista GROUP BY."""
    read = {t.name for t in requirements_tools.make_requirements_read_tools(1)}
    edit = {t.name for t in requirements_tools.make_requirements_tools(1)}
    assert "capture_status" in read
    assert read <= edit  # todo lo de lectura existe en el factory de edicion

    for make in (
        requirements_tools.make_requirements_read_tools,
        requirements_tools.make_requirements_tools,
    ):
        tools = {t.name: t for t in make(1)}
        assert "document" in tools["list_requirements"].args
        assert "include_revisions" in tools["get_requirement"].args
