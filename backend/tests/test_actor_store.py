"""Catálogo de actores (ProjectActor): store, bloque y tools de curación.

El problema que motiva esta pieza: los enunciados dicen «El usuario debe...»
o «El sistema debe...» porque nadie determinó QUIÉN usa el sistema antes de
extraer. La etapa ``identify_actors`` descubre el catálogo por documento y lo
persiste; estos tests pines:

- el upsert deduplica por nombre o sinónimo (case-insensitive) y consolida
  sinónimos sin duplicar filas; el nombre nunca queda también como sinónimo;
- los códigos R1..Rn son estables y secuenciales por proyecto;
- re-descubrir un actor RETIRADO no lo reactiva (decisión humana);
- ``set_actor_status`` falla si el actor no es del proyecto;
- el bloque PROJECT_ACTORS es vacío sin catálogo activo y lista los activos
  con código, canal y sinónimos;
- ``actor_role_terms`` alimenta ``detect_actor``: un rol del catálogo que el
  léxico base no trae (p. ej. «Jefe de campo») cuenta como actor nombrado;
- las tools de curación (list/add/retire) cierran sobre project_id y
  persisten en una sesión fresca.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.models import Base, Project
from backend.models.project_actor import ActorSource, ActorStatus
from backend.services import actor_store

# Los stores/servicios bajo test importan AsyncSessionLocal por módulo: el
# parche va en el módulo que lo usó, no en backend.database.
from backend.agents.tools import actor_tools as actor_tools_mod
from backend.agents.tools import requirements_tools as req_tools_mod
from backend.agents.pipelines import extraction as extraction_mod
from backend.agents.pipelines.ingestion import StructureMap


async def _fresh_db() -> tuple[async_sessionmaker, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return sm, tmp


async def _seed(sm) -> int:
    async with sm() as session:
        proj = Project(user_id=1, name="g", slug="g", description="t")
        session.add(proj)
        await session.flush()
        return proj.id


# ---------------------------------------------------------------------------
# store: upsert / códigos / soft lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_creates_codes_and_dedupes():
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)

    first = [
        {"name": "Coordinador de terreno", "synonyms": [], "channel": "humano"},
        {"name": "Jefe de campo", "synonyms": ["Field Manager"]},
        {"name": "Pasarela de pagos", "channel": "sistema_externo"},
    ]
    async with sm() as session:
        res = await actor_store.upsert_actors(
            session, pid, first, source=ActorSource.USER
        )
        await session.commit()
    assert res["created"] == 3 and res["updated"] == 0
    assert [a["code"] for a in res["actors"]] == ["R1", "R2", "R3"]

    # Segunda pasada: mismo rol en otra caja (nombre distinto-case, un
    # sinónimo ya conocido y uno genuinamente nuevo).
    again = [
        {
            "name": "coordinador de terreno",
            "synonyms": ["Coordinación de Terreno"],
            "channel": "humano",
            "rationale": "aparece en el manual",
        },
        {"name": "Field Manager", "synonyms": []},
    ]
    async with sm() as session:
        res2 = await actor_store.upsert_actors(session, pid, again)
        await session.commit()
    assert res2["created"] == 0 and res2["updated"] == 2
    by_name = {a["name"]: a for a in res2["actors"]}
    coord = by_name["Coordinador de terreno"]
    assert coord["synonyms"] == ["Coordinación de Terreno"]
    assert coord["channel"] == "humano"  # solo-si-vacío: no se pisa
    assert coord["rationale"] == "aparece en el manual"
    jefe = by_name["Jefe de campo"]
    # El sinónimo matcheó y se consolidó; el nombre NO queda como sinónimo.
    assert jefe["synonyms"] == ["Field Manager"]

    # Sin entradas útiles: nada creado.
    async with sm() as session:
        res3 = await actor_store.upsert_actors(session, pid, [{"name": "   "}])
        await session.commit()
    assert res3["created"] == 0 and res3["updated"] == 0


@pytest.mark.asyncio
async def test_codes_are_stable_and_incremental():
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    async with sm() as session:
        await actor_store.upsert_actors(
            session, pid, [{"name": "Rol A"}, {"name": "Rol B"}]
        )
        await session.commit()
        async with sm() as session:
            await actor_store.upsert_actors(session, pid, [{"name": "Rol C"}])
            await session.commit()
            actors = await actor_store.list_actors(
                session, pid, include_retired=True
            )
    assert [a.code for a in actors] == ["R1", "R2", "R3"]


@pytest.mark.asyncio
async def test_retired_actor_is_not_reactivated():
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    async with sm() as session:
        res = await actor_store.upsert_actors(
            session, pid, [{"name": "Auditor externo"}]
        )
        await session.commit()
    actor_id = res["actors"][0]["id"]

    async with sm() as session:
        await actor_store.set_actor_status(
            session, pid, actor_id, ActorStatus.RETIRED
        )
        await session.commit()

    # Re-descubrir el mismo rol: matchea (no duplica) pero NO reactiva.
    async with sm() as session:
        res2 = await actor_store.upsert_actors(
            session, pid, [{"name": "Auditor externo"}]
        )
        await session.commit()
    assert res2["created"] == 0 and res2["updated"] == 1
    assert all(a["status"] == ActorStatus.RETIRED.value for a in res2["actors"])
    async with sm() as session:
        assert await actor_store.list_actors(session, pid) == []
        assert len(await actor_store.list_actors(session, pid, include_retired=True)) == 1


@pytest.mark.asyncio
async def test_set_actor_status_rejects_foreign_project():
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    other = pid + 1
    async with sm() as session:
        res = await actor_store.upsert_actors(session, pid, [{"name": "Rol X"}])
        await session.commit()
    actor_id = res["actors"][0]["id"]
    async with sm() as session:
        with pytest.raises(KeyError):
            await actor_store.set_actor_status(
                session, other, actor_id, ActorStatus.RETIRED
            )


# ---------------------------------------------------------------------------
# bloque PROJECT_ACTORS y role_terms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_actors_block_empty_without_active_catalog():
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    async with sm() as session:
        assert await actor_store.actors_block(session, pid) == ""

    async with sm() as session:
        res = await actor_store.upsert_actors(
            session, pid, [{"name": "Solo retirado"}]
        )
        await session.commit()
        await actor_store.set_actor_status(
            session, pid, res["actors"][0]["id"], ActorStatus.RETIRED
        )
        await session.commit()
        assert await actor_store.actors_block(session, pid) == ""


@pytest.mark.asyncio
async def test_actors_block_lists_active_actors():
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    async with sm() as session:
        await actor_store.upsert_actors(
            session,
            pid,
            [
                {
                    "name": "Coordinador de terreno",
                    "synonyms": ["Jefe de campo"],
                    "channel": "humano",
                },
                {"name": "Pasarela de pagos", "channel": "sistema_externo"},
            ],
        )
        await session.commit()
        block = await actor_store.actors_block(session, pid)
    assert block.startswith(actor_store.ACTORS_BLOCK_HEADER)
    assert "- R1 Coordinador de terreno (humano) [aka: Jefe de campo]" in block
    assert "- R2 Pasarela de pagos (sistema_externo)" in block


@pytest.mark.asyncio
async def test_actor_role_terms_feed_detect_actor():
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    from backend.agents.pipelines._quality_rules import detect_actor

    # Sin catálogo: «Jefe de campo» no está en el léxico base.
    async with sm() as session:
        assert await actor_store.actor_role_terms(session, pid) == []
    assert detect_actor("El Jefe de campo aprueba el plan.") == "none"

    async with sm() as session:
        await actor_store.upsert_actors(
            session,
            pid,
            [{"name": "Coordinador de terreno", "synonyms": ["Jefe de campo"]}],
        )
        await session.commit()
        terms = await actor_store.actor_role_terms(session, pid)
    assert terms == ["coordinador de terreno", "jefe de campo"]
    assert (
        detect_actor(
            "El Jefe de campo aprueba el plan.", role_terms=terms
        )
        == "named"
    )
    assert (
        detect_actor("El usuario consulta el mapa.", role_terms=terms)
        == "generic"
    )


# ---------------------------------------------------------------------------
# tools de curación
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_actor_tools_add_list_retire(monkeypatch):
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    monkeypatch.setattr(actor_tools_mod, "AsyncSessionLocal", sm)
    list_actors, add_actors, retire_actor = actor_tools_mod.make_actor_tools(pid)

    res = await add_actors.ainvoke(
        {"actors": [{"name": "Coordinador de terreno", "channel": "humano"}]}
    )
    assert res["created"] == 1 and res["actors"][0]["code"] == "R1"

    listed = await list_actors.ainvoke({})
    assert listed["count"] == 1

    retired = await retire_actor.ainvoke({"code": "r1"})
    assert retired["already_retired"] is False
    assert retired["actor"]["status"] == ActorStatus.RETIRED.value

    # Idempotente + listado incluye retirados marcados.
    again = await retire_actor.ainvoke({"code": "R1"})
    assert again["already_retired"] is True
    listed = await list_actors.ainvoke({})
    assert listed["count"] == 1
    assert listed["actors"][0]["status"] == ActorStatus.RETIRED.value

    missing = await retire_actor.ainvoke({"code": "R99"})
    assert missing["error"] == "not_found"

    empty = await add_actors.ainvoke({"actors": []})
    assert empty["error"] == "empty"


@pytest.mark.asyncio
async def test_list_requirements_uses_catalog_terms(monkeypatch):
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    from backend.models import Priority, ReqStatus, ReqType, RequirementItem

    async with sm() as session:
        await actor_store.upsert_actors(
            session,
            pid,
            [{"name": "Coordinador de terreno", "synonyms": ["Jefe de campo"]}],
        )
        await session.commit()
        for stmt in (
            "El Jefe de campo aprueba el plan semanal.",  # sinónimo del catálogo
            "El usuario consulta el mapa.",  # genérico
            "El sistema registra la operación.",  # sin actor
        ):
            session.add(
                RequirementItem(
                    project_id=pid,
                    code=f"REQ-{abs(hash(stmt)) % 10000:04d}",
                    statement=stmt,
                    type=ReqType.FUNCTIONAL,
                    priority=Priority.MUST,
                    status=ReqStatus.DRAFT,
                )
            )
        await session.commit()

    monkeypatch.setattr(req_tools_mod, "AsyncSessionLocal", sm)
    read_tools = req_tools_mod.make_requirements_read_tools(pid)
    list_tool = next(t for t in read_tools if t.name == "list_requirements")

    named = await list_tool.ainvoke({"has_actor": True})
    assert named["count"] == 1
    assert named["items"][0]["actor"] == "named"

    queue = await list_tool.ainvoke({"has_actor": False})
    assert queue["count"] == 2
    assert {it["actor"] for it in queue["items"]} == {"generic", "none"}


@pytest.mark.asyncio
async def test_extract_actors_degrades_gracefully(monkeypatch):
    # Texto ausente: catálogo vacío sin tocar el LLM.
    empty = StructureMap(document_id="d0", sections=[], tables=[], glossary={})
    catalog = await extraction_mod.extract_actors(
        empty, project_name="p", project_description="d"
    )
    assert catalog.actors == []

    # Fallo del LLM: catálogo vacío, sin excepción.
    smap = StructureMap(
        document_id="d1",
        sections=[],
        tables=[],
        glossary={},
        full_text="El Coordinador de terreno visita las obras.",
    )

    def _boom(_schema):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(extraction_mod, "_structured_llm", _boom)
    catalog = await extraction_mod.extract_actors(
        smap, project_name="p", project_description="d"
    )
    assert catalog.actors == []
