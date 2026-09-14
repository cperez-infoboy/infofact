"""Paquetes de trabajo: store (versionado, códigos) + router (export.md) +
grafo de trazabilidad. DB real en SQLite temporal (patrón test_goal_upsert).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.deps import get_current_user as _real_dep
from backend.models import Base, Priority, Project, ReqStatus, ReqType, RequirementItem
from backend.models.analysis import (
    AnalysisDocument,
    AnalysisProject,
    DomainEntity,
    SubProject,
    SubProjectContract,
)
from backend.routers import analysis as analysis_router
from backend.routers import packages as packages_router
from backend.services import packages_store
from backend.services.packages_store import create_packages_document
from backend.agents.pipelines.workpackage_pipeline import (
    CoherenceGate,
    PackageContext,
    PackageTask,
    PackagesResult,
    render_package_markdown,
)

# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db):
    """Proyecto con 1 análisis (proyecto + subproyecto + contrato + entidad)
    y 2 requerimientos vivos. Devuelve (pid, analysis_id)."""
    async with db() as session:
        proj = Project(user_id=1, name="p", slug="p", description="t")
        session.add(proj)
        await session.flush()
        pid = proj.id
        for i in range(2):
            session.add(
                RequirementItem(
                    project_id=pid,
                    code=f"REQ-{i:04d}",
                    statement=f"Requerimiento {i} del sistema de prueba.",
                    type=ReqType.FUNCTIONAL,
                    priority=Priority.MUST,
                    status=ReqStatus.VALIDATED,
                    acceptance_criteria=[f"Given r{i} Then ok"],
                )
            )
        doc = AnalysisDocument(project_id=pid, version=1)
        session.add(doc)
        await session.flush()
        session.add(
            AnalysisProject(
                project_id=pid, analysis_id=doc.id, code="PROJ-001",
                name="Ventas", domain_type="core",
            )
        )
        session.add(
            DomainEntity(
                project_id=pid, analysis_id=doc.id, code="ENT-AAAA",
                name="Cliente", attributes=[],
                traced_req_codes=["REQ-0000"],
            )
        )
        session.add(
            SubProject(
                project_id=pid, analysis_id=doc.id, code="SUB-001",
                name="ventas-api", responsibility="Gestión de clientes",
                entity_codes=["ENT-AAAA"], nfr_codes=["REQ-0001"],
                project_code="PROJ-001",
            )
        )
        session.add(
            SubProjectContract(
                project_id=pid, analysis_id=doc.id,
                from_subproject_code="SUB-001", to_subproject_code="SUB-001",
                contract_type="openapi", name="POST /interno", spec="openapi: 3.0",
            )
        )
        await session.commit()
        return db, pid, doc.id


def _fake_result(analysis_id: int) -> PackagesResult:
    ctx = PackageContext(
        sub_project_code="SUB-001",
        sub_project_name="ventas-api",
        project_code="PROJ-001",
        project_name="Ventas",
        mission="Gestión de clientes",
        entities=[{"code": "ENT-AAAA", "name": "Cliente", "attributes": []}],
        requirements=[{
            "code": "REQ-0000", "statement": "s", "type": "functional",
            "priority": "must", "acceptance": ["Given r0 Then ok"],
        }],
        contracts_exposed=[{
            "from": "SUB-001", "to": "SUB-002", "contract_type": "openapi",
            "name": "GET /clientes", "spec": "",
        }],
        tasks=[PackageTask(
            code="TASK-001", title="Modelo Cliente",
            req_codes=["REQ-0000"], acceptance=["Given r0 Then ok"],
        )],
    )
    ctx.markdown = render_package_markdown(ctx)
    ctx.counts = {"tasks": 1}
    return PackagesResult(
        packages=[ctx],
        master_markdown="# Maestro\n",
        gates=[CoherenceGate(gate="cierre_cobertura", status="pass", blocking=True)],
        stats={"tasks": 1, "requirements": 1},
    )


# --------------------------------------------------------------------------- #
# Store: versionado y códigos                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_packages_document_versions_and_codes(seeded):
    sm, pid, analysis_id = seeded
    async with sm() as session:
        doc = await create_packages_document(
            session, pid,
            analysis_id=analysis_id, analysis_version=1,
            result=_fake_result(analysis_id), requirement_count=1,
        )
        await session.commit()
        assert doc.version == 1
        wps = await packages_store.list_packages(session, doc.id)
        assert len(wps) == 1
        assert wps[0].code == "WP-001"
        tasks = await packages_store.list_tasks(session, wps[0].id)
        assert [t.code for t in tasks] == ["TASK-001"]

        # Segunda versión: versión+1, WP continúa (WP-002), TASK reinicia.
        doc2 = await create_packages_document(
            session, pid,
            analysis_id=analysis_id, analysis_version=1,
            result=_fake_result(analysis_id), requirement_count=1,
        )
        await session.commit()
        assert doc2.version == 2
        wps2 = await packages_store.list_packages(session, doc2.id)
        assert wps2[0].code == "WP-002"


# --------------------------------------------------------------------------- #
# Router: versions + export.md + ownership                                     #
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def client(seeded, monkeypatch):
    sm, pid, analysis_id = seeded

    # Los routers usan `from backend.database import AsyncSessionLocal`
    # (binding local): parchear el atributo del módulo router.
    monkeypatch.setattr(packages_router, "AsyncSessionLocal", sm)
    monkeypatch.setattr(analysis_router, "AsyncSessionLocal", sm)

    async with sm() as session:
        await create_packages_document(
            session, pid,
            analysis_id=analysis_id, analysis_version=1,
            result=_fake_result(analysis_id), requirement_count=1,
        )
        await session.commit()

    app = FastAPI()
    app.include_router(packages_router.router, prefix="/api")
    app.include_router(analysis_router.router, prefix="/api")

    class _U:
        id = 1

    app.dependency_overrides[_real_dep] = lambda: _U()
    return TestClient(app)


async def _commit_doc(sm, pid, analysis_id):
    async with sm() as session:
        await create_packages_document(
            session, pid,
            analysis_id=analysis_id, analysis_version=1,
            result=_fake_result(analysis_id), requirement_count=1,
        )
        await session.commit()


@pytest.mark.asyncio
async def test_router_versions_and_export(client):
    r = client.get("/api/projects/1/packages/versions")
    assert r.status_code == 200
    versions = r.json()
    assert len(versions) == 1 and versions[0]["version"] == 1

    detail = client.get("/api/projects/1/packages/versions/1").json()
    assert detail["master_markdown"] == "# Maestro\n"
    assert len(detail["packages"]) == 1
    wp = detail["packages"][0]
    assert wp["code"] == "WP-001"
    assert wp["tasks"][0]["title"] == "Modelo Cliente"

    r = client.get(
        "/api/projects/1/packages/versions/1/WP-001/export.md"
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers["content-disposition"]
    assert "# Paquete SUB-001" in r.text

    r = client.get("/api/projects/1/packages/versions/1/master/export.md")
    assert r.status_code == 200 and "# Maestro" in r.text

    # Paquete inexistente -> 404.
    r = client.get("/api/projects/1/packages/versions/1/WP-999/export.md")
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Grafo de trazabilidad                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_traceability_graph_full_and_focus(seeded, monkeypatch):
    sm, pid, analysis_id = seeded
    monkeypatch.setattr(analysis_router, "AsyncSessionLocal", sm)
    client = _graph_client(sm, pid)
    r = client.get("/api/projects/{pid}/analysis/versions/1/traceability/graph".replace("{pid}", str(pid)))
    assert r.status_code == 200
    g = r.json()
    kinds = {n["kind"] for n in g["nodes"]}
    assert {"project", "subproject", "entity", "req"} <= kinds
    edge_kinds = {e["kind"] for e in g["edges"]}
    assert {"belongs", "owns", "traces", "nfr_of"} <= edge_kinds

    # Foco en la entidad: solo su vecindario (SUB dueño + REQ trazado).
    r = client.get(
        f"/api/projects/{pid}/analysis/versions/1/traceability/graph"
        f"?focus=ENT-AAAA&levels=1"
    )
    assert r.status_code == 200
    focused = r.json()
    ids = {n["id"] for n in focused["nodes"]}
    assert {"ENT-AAAA", "SUB-001", "REQ-0000"} <= ids
    assert "PROJ-001" not in ids  # a 1 salto no llega al proyecto

    # Foco inexistente -> 404.
    r = client.get(
        f"/api/projects/{pid}/analysis/versions/1/traceability/graph"
        f"?focus=NOPE-1"
    )
    assert r.status_code == 404


def _graph_client(sm, pid: int) -> TestClient:
    app = FastAPI()
    app.include_router(analysis_router.router, prefix="/api")

    class _U:
        id = 1

    app.dependency_overrides[_real_dep] = lambda: _U()
    return TestClient(app)
