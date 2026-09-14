"""Consolidación semántica de actores: store, tool de curación y etapa.

El problema que motiva esta pieza: la detección es granular por diseño (una
pasada por documento), así que el catálogo queda con productos concretos
(«Google Maps», «OSRM») cuando el SRS necesita el ROL general («Proveedor de
cartografía»). Estos tests pine:

- ``apply_actor_consolidation`` crea el rol general con los nombres de los
  miembros como sinónimos, los retira (soft) y devuelve el mapping término ->
  rol general que alimenta la reescritura;
- es idempotente: re-aplicar sobre miembros ya retirados no hace nada;
- códigos desconocidos o retirados se saltan; un grupo con menos de 2
  miembros válidos se ignora;
- si el nombre general matchea un actor preexistente que no es miembro, ese
  actor se vuelve el general (created == 0);
- ``consolidate_actor_catalog`` (tool) consolida y reescribe enunciados en
  UNA transacción: la revisión queda auditada, los soft-deleted no se
  tocan y un fallo del LLM deja el catálogo intacto;
- la etapa ``identify_actors`` completa aunque la consolidación explote
  (best-effort, igual que la detección).
"""
from __future__ import annotations

import tempfile
import types
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.models import Base, Project
from backend.models.project_actor import ActorSource, ActorStatus
from backend.models.requirement import (
    Priority,
    ReqStatus,
    ReqType,
    RequirementItem,
    RequirementRevision,
)
from backend.services import actor_store, requirement_store

# Los stores/servicios bajo test importan AsyncSessionLocal por módulo: el
# parche va en el módulo que lo usó, no en backend.database.
from backend.agents.tools import actor_tools as actor_tools_mod
from backend.agents.pipelines import extraction as extraction_mod
from backend.agents.pipelines.extraction import (
    ActorCatalog,
    ActorConsolidation,
    ActorGroup,
)


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


async def _seed_map_providers(sm, pid: int) -> None:
    async with sm() as session:
        await actor_store.upsert_actors(
            session,
            pid,
            [
                {
                    "name": "Google Maps",
                    "synonyms": ["Google Maps Platform"],
                    "channel": "sistema_externo",
                },
                {"name": "OSRM", "channel": "sistema_externo"},
                {"name": "Coordinador de terreno", "channel": "humano"},
            ],
        )
        await session.commit()


async def _seed_item(
    sm,
    pid: int,
    code: str,
    statement: str,
    status: ReqStatus = ReqStatus.DRAFT,
) -> None:
    async with sm() as session:
        session.add(
            RequirementItem(
                project_id=pid,
                code=code,
                statement=statement,
                type=ReqType.FUNCTIONAL,
                priority=Priority.MUST,
                status=status,
            )
        )
        await session.commit()


def _group(
    codes: list[str], name: str = "Proveedor de cartografía"
) -> dict:
    return {
        "general_name": name,
        "channel": "sistema_externo",
        "rationale": "servicios de mapas",
        "member_codes": codes,
    }


# ---------------------------------------------------------------------------
# store: apply_actor_consolidation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_consolidation_creates_general_and_retires_members():
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    await _seed_map_providers(sm, pid)

    async with sm() as session:
        res = await actor_store.apply_actor_consolidation(
            session, pid, [_group(["R1", "R2"])]
        )
        await session.commit()

    assert res["created"] == 1
    assert res["retired"] == ["R1", "R2"]
    by_name = {a["name"]: a for a in res["actors"]}
    general = by_name["Proveedor de cartografía"]
    # Los nombres absorbidos viven como sinónimos: el matching léxico
    # (bloque PROJECT_ACTORS, pre-checks) sigue reconociendo «Google Maps».
    assert set(general["synonyms"]) == {
        "Google Maps",
        "Google Maps Platform",
        "OSRM",
    }
    assert general["channel"] == "sistema_externo"
    assert by_name["Google Maps"]["status"] == ActorStatus.RETIRED.value
    assert by_name["OSRM"]["status"] == ActorStatus.RETIRED.value
    assert by_name["Coordinador de terreno"]["status"] == ActorStatus.ACTIVE.value

    # Mapping: cada término retirado apunta al rol general (entrada de la
    # reescritura de enunciados).
    assert res["mapping"] == {
        "Google Maps": "Proveedor de cartografía",
        "Google Maps Platform": "Proveedor de cartografía",
        "OSRM": "Proveedor de cartografía",
    }


@pytest.mark.asyncio
async def test_apply_consolidation_is_idempotent():
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    await _seed_map_providers(sm, pid)
    async with sm() as session:
        await actor_store.apply_actor_consolidation(
            session, pid, [_group(["R1", "R2"])]
        )
        await session.commit()

    # Re-aplicar: los miembros están retirados, no hay nada que consolidar.
    async with sm() as session:
        res2 = await actor_store.apply_actor_consolidation(
            session, pid, [_group(["R1", "R2"])]
        )
        await session.commit()
    assert res2["created"] == 0
    assert res2["retired"] == []
    assert res2["mapping"] == {}
    async with sm() as session:
        actors = await actor_store.list_actors(session, pid)
    assert [a.name for a in actors] == [
        "Coordinador de terreno",
        "Proveedor de cartografía",
    ]


@pytest.mark.asyncio
async def test_apply_consolidation_skips_unknown_and_degenerate_groups():
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    await _seed_map_providers(sm, pid)

    groups = [
        _group(["R1", "R99"]),  # código inexistente -> 1 válido -> fuera
        {"general_name": "", "member_codes": ["R1", "R2"]},  # sin nombre
        {"general_name": "Solo Uno", "member_codes": ["R2"]},  # degenerado
        {"general_name": "Fantasma", "member_codes": []},  # vacío
    ]
    async with sm() as session:
        res = await actor_store.apply_actor_consolidation(session, pid, groups)
        await session.commit()
    assert res["created"] == 0 and res["retired"] == [] and res["mapping"] == {}


@pytest.mark.asyncio
async def test_apply_consolidation_reuses_preexisting_general():
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    await _seed_map_providers(sm, pid)
    async with sm() as session:
        await actor_store.upsert_actors(
            session, pid, [{"name": "Proveedor de cartografía"}]
        )
        await session.commit()

    async with sm() as session:
        res = await actor_store.apply_actor_consolidation(
            session, pid, [_group(["R1", "R2"])]
        )
        await session.commit()
    # El general ya existía (R4): se reusa, no se crea otro.
    assert res["created"] == 0
    assert res["retired"] == ["R1", "R2"]
    by_name = {a["name"]: a for a in res["actors"]}
    general = by_name["Proveedor de cartografía"]
    assert general["status"] == ActorStatus.ACTIVE.value
    assert set(general["synonyms"]) == {
        "Google Maps",
        "Google Maps Platform",
        "OSRM",
    }


# ---------------------------------------------------------------------------
# requirement_store: reescritura de enunciados
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_statements_for_actor_mapping():
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    await _seed_item(
        sm, pid, "REQ-0001", "El sistema calculará rutas con Google Maps."
    )
    await _seed_item(
        sm, pid, "REQ-0002", "El sistema sincronizará con OSRM.", ReqStatus.APPROVED
    )
    # Word-boundary: «OSRMX» contiene «OSRM» pero no es una mención.
    await _seed_item(sm, pid, "REQ-0003", "Exportar el reporte en formato OSRMX.")
    # Soft-deleted: historial, NO se reescribe.
    await _seed_item(
        sm, pid, "REQ-0004", "Ruta rápida via Google Maps.", ReqStatus.REJECTED
    )

    async def fake_rewrite(items, mapping):
        codes = {it["code"] for it in items}
        # El finder nunca propone el REJECTED: es historial.
        assert "REQ-0004" not in codes
        assert "REQ-0003" not in codes
        out = []
        for it in items:
            stmt = it["statement"]
            if "Google Maps" in stmt:
                stmt = stmt.replace("Google Maps", "el proveedor de cartografía")
            else:
                stmt = stmt.replace("OSRM", "el proveedor de cartografía")
            out.append({"code": it["code"], "new_statement": stmt})
        return out

    async with sm() as session:
        res = await requirement_store.rewrite_statements_for_actor_mapping(
            session,
            pid,
            {
                "Google Maps": "Proveedor de cartografía",
                "OSRM": "Proveedor de cartografía",
            },
            fake_rewrite,
        )
        await session.commit()
    assert {r["code"] for r in res} == {"REQ-0001", "REQ-0002"}

    async with sm() as session:
        rows = list(
            await session.scalars(
                select(RequirementItem).where(RequirementItem.project_id == pid)
            )
        )
    by_code = {r.code: r for r in rows}
    assert (
        by_code["REQ-0001"].statement
        == "El sistema calculará rutas con el proveedor de cartografía."
    )
    assert by_code["REQ-0004"].statement == "Ruta rápida via Google Maps."
    async with sm() as session:
        revs = list(
            await session.scalars(
                select(RequirementRevision).where(
                    RequirementRevision.req_id == by_code["REQ-0001"].id
                )
            )
        )
    assert [r.change_reason for r in revs] == ["actor_consolidation"]
    assert (
        revs[0].snapshot["statement"]
        == "El sistema calculará rutas con Google Maps."
    )


# ---------------------------------------------------------------------------
# tool de curación: consolidate_actor_catalog
# ---------------------------------------------------------------------------


_MAP_GROUP = ActorGroup(
    general_name="Proveedor de cartografía",
    channel="sistema_externo",
    rationale="servicios de mapas",
    member_codes=["R1", "R2"],
)


def _patch_llm(monkeypatch, consolidation, rewrite=None, boom=False):
    if boom:
        async def fake_consolidate(*a, **kw):
            raise RuntimeError("LLM down")
    else:
        async def fake_consolidate(*a, **kw):
            return consolidation

    monkeypatch.setattr(actor_tools_mod, "consolidate_actors", fake_consolidate)

    async def fake_rewrite(items, mapping):
        if rewrite is None:
            return []
        return await rewrite(items, mapping)

    monkeypatch.setattr(actor_tools_mod, "rewrite_actor_mentions", fake_rewrite)


@pytest.mark.asyncio
async def test_consolidate_catalog_tool_happy_path(monkeypatch):
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    await _seed_map_providers(sm, pid)
    await _seed_item(
        sm, pid, "REQ-0001", "El sistema calculará rutas con Google Maps."
    )
    await _seed_item(
        sm,
        pid,
        "REQ-0002",
        "Reporte de incidentes via Google Maps.",
        ReqStatus.REJECTED,
    )

    async def fake_llm_rewrite(items, mapping):
        return [
            {
                "code": it["code"],
                "new_statement": it["statement"].replace(
                    "Google Maps", "el proveedor de cartografía"
                ),
            }
            for it in items
        ]

    _patch_llm(
        monkeypatch,
        ActorConsolidation(groups=[_MAP_GROUP]),
        rewrite=fake_llm_rewrite,
    )
    monkeypatch.setattr(actor_tools_mod, "AsyncSessionLocal", sm)

    res = await actor_tools_mod.consolidate_actor_catalog(
        pid, project_name="g", project_description="t"
    )
    assert res["consolidated"] is True
    assert res["groups"] == 1 and res["created"] == 1
    assert res["retired"] == ["R1", "R2"]
    assert [r["code"] for r in res["rewritten"]] == ["REQ-0001"]

    # Catálogo consolidado y enunciado reescrito con revisión auditada.
    async with sm() as session:
        actors = await actor_store.list_actors(session, pid)
        terms = await actor_store.actor_role_terms(session, pid)
    assert {a.name for a in actors} == {
        "Coordinador de terreno",
        "Proveedor de cartografía",
    }
    assert "google maps" in terms  # el sinónimo absorbido mantiene el matching
    async with sm() as session:
        row = await session.scalar(
            select(RequirementItem).where(
                RequirementItem.project_id == pid,
                RequirementItem.code == "REQ-0001",
            )
        )
    assert row.statement == (
        "El sistema calculará rutas con el proveedor de cartografía."
    )
    async with sm() as session:
        revs = list(
            await session.scalars(
                select(RequirementRevision).where(
                    RequirementRevision.req_id == row.id
                )
            )
        )
    assert [r.change_reason for r in revs] == ["actor_consolidation"]


@pytest.mark.asyncio
async def test_consolidate_catalog_tool_noop_and_failure(monkeypatch):
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    await _seed_map_providers(sm, pid)
    monkeypatch.setattr(actor_tools_mod, "AsyncSessionLocal", sm)

    # Sin grupos propuestos: no-op explícito.
    _patch_llm(monkeypatch, ActorConsolidation(groups=[]))
    res = await actor_tools_mod.consolidate_actor_catalog(pid)
    assert res == {"consolidated": False, "groups": 0}

    # LLM caído: la tool lo degrada; el catálogo queda intacto.
    _patch_llm(monkeypatch, None, boom=True)
    list_actors, _add, _retire, consolidate = (
        actor_tools_mod.make_actor_tools(pid)
    )
    out = await consolidate.ainvoke({})
    assert out["error"] == "consolidation_failed"
    listed = await list_actors.ainvoke({})
    assert listed["count"] == 3  # nada cambió


# ---------------------------------------------------------------------------
# etapa identify_actors: la consolidación es best-effort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identify_actors_survives_consolidation_failure(monkeypatch):
    import backend.agents.subagents.requirements_capture_agent as mod
    import backend.database as database
    from backend.agents.subagents import capture_run_holder as holder

    pid = 99101
    sm, _tmp = await _fresh_db()
    holder.clear_run(pid)

    smap = types.SimpleNamespace(
        document_id="doc1", full_text="texto", name="doc1.pdf"
    )

    async def fake_resolve(ws, subpath):
        return Path("/tmp/ws/docs")

    def fake_discover(target):
        return [Path("doc1.pdf")]

    async def fake_ingest(doc, *, session=None, parser_hint="auto"):
        return [["chunk-1"], smap]

    async def fake_enrich(s, **kw):
        return None

    async def fake_extract_actors(smap, **kw):
        return ActorCatalog(
            actors=[
                extraction_mod.ActorCandidate(
                    name="Google Maps", channel="sistema_externo"
                ),
                extraction_mod.ActorCandidate(
                    name="OSRM", channel="sistema_externo"
                ),
            ]
        )

    async def fake_count(project_id):
        return {"requirements": 0, "grouping_plans": 0, "last_code": None}

    async def fake_hint_map(project_id, workspace_root):
        return {}

    async def boom_consolidate(*a, **kw):
        raise RuntimeError("consolidation down")

    monkeypatch.setattr(mod, "_resolve_target", fake_resolve)
    monkeypatch.setattr(mod, "discover_documents", fake_discover)
    monkeypatch.setattr(mod, "parse_document_cached", fake_ingest)
    monkeypatch.setattr(mod, "enrich_structure_map", fake_enrich)
    monkeypatch.setattr(mod, "extract_actors", fake_extract_actors)
    monkeypatch.setattr(mod, "_count_existing", fake_count)
    monkeypatch.setattr(mod, "parser_hint_map", fake_hint_map)
    monkeypatch.setattr(extraction_mod, "consolidate_actors", boom_consolidate)
    monkeypatch.setattr(database, "AsyncSessionLocal", sm)

    tools = mod._make_stage_tools(pid, Path("/tmp/ws"), "Proj", "desc")
    await tools[0].ainvoke({"target_subpath": ""})  # ingest
    out = await tools[2].ainvoke({})  # identify_actors

    # La etapa completó pese al fallo de consolidación: catálogo granular.
    assert "actors" in out["stages_done"]
    assert out["actors"] == 2
    assert out["consolidated_groups"] == 0
    assert out["retired_actors"] == 0
    run = holder.get_run(pid)
    assert len(run.actor_catalog) == 2
    async with sm() as session:
        actors = await actor_store.list_actors(session, pid)
    assert {a.name for a in actors} == {"Google Maps", "OSRM"}
    holder.clear_run(pid)
