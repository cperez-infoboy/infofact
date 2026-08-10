"""Router de analisis: versiones, MER (entidades + relaciones).

Endpoints (todos project-scoped, bajo /api/projects/{id}/...):
  POST   /analysis/commit                          genera un analisis CANDIDATE
                                                    (corre el assembler)
  GET    /analysis/versions                        lista versiones del proyecto
  GET    /analysis/versions/{version}              detalle de una version
  PATCH  /analysis/versions/{version}              cambio de status
  GET    /analysis/versions/{version}/entities     entidades del MER
  GET    /analysis/versions/{version}/relationships relaciones del MER

Patron de ownership (igual que srs.py): recurso inexistente o ajeno -> 404
"not_found". Sin Depends(get_db): AsyncSessionLocal por endpoint.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AsyncSessionLocal
from backend.deps import get_current_user
from backend.models import Project, User
from backend.models.analysis import AnalysisStatus
from backend.services import analysis_store
from backend.services.analysis_assembler import assemble_analysis

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_owned_project(
    db: AsyncSession, project_id: int, user: User
) -> Project:
    project = await db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return project


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class AnalysisCommitOut(BaseModel):
    project_id: int
    version: int
    status: str
    requirement_count: int
    mer_stats: dict


class AnalysisVersionOut(BaseModel):
    id: int
    project_id: int
    version: int
    status: str
    srs_version: int | None = None
    mer_diagram: str | None = None
    mer_diagram_description: str | None = None
    process_diagrams: list | None = None
    nfr_analysis: dict | None = None
    component_diagram: str | None = None
    component_diagram_description: str | None = None
    system_architecture_diagram: str | None = None
    system_architecture_description: str | None = None
    infrastructure_diagram: str | None = None
    infrastructure_description: str | None = None
    traceability: dict | None = None
    requirement_codes: list
    requirement_count: int
    generated_at: str | None = None
    locked_at: str | None = None


class AnalysisStatusUpdate(BaseModel):
    status: str  # in_review | locked


class AnalysisDiagramUpdate(BaseModel):
    """Partial update for diagram source code (only CANDIDATE status)."""

    mer_diagram: str | None = None
    mer_diagram_description: str | None = None
    process_diagrams: list[dict] | None = None
    component_diagram: str | None = None
    component_diagram_description: str | None = None
    system_architecture_diagram: str | None = None
    system_architecture_description: str | None = None
    infrastructure_diagram: str | None = None
    infrastructure_description: str | None = None


class EntityOut(BaseModel):
    id: int
    code: str
    name: str
    description: str
    attributes: list
    aggregate_root: bool
    bounded_context: str | None = None
    traced_req_codes: list


class RelationshipOut(BaseModel):
    id: int
    from_entity_code: str
    to_entity_code: str
    cardinality: str
    label: str | None = None
    description: str | None = None
    traced_req_codes: list


class AdrOut(BaseModel):
    id: int
    code: str
    title: str
    status: str
    context: str
    decision: str
    alternatives: list
    rationale: str
    nfr_codes: list


class SubProjectOut(BaseModel):
    id: int
    code: str
    name: str
    responsibility: str
    stack: dict
    bounded_contexts: list
    nfr_codes: list
    entity_codes: list
    project_code: str | None = None


class ContractOut(BaseModel):
    id: int
    from_subproject_code: str
    to_subproject_code: str
    contract_type: str
    name: str
    spec: str
    description: str | None = None


class ProjectOut(BaseModel):
    id: int
    code: str
    name: str
    description: str
    domain_type: str
    bounded_contexts: list
    entity_codes: list
    traced_req_codes: list


class SubProjectsOut(BaseModel):
    """Wrapper for projects, sub-projects and their inter-project contracts."""
    projects: list[ProjectOut] = []
    subprojects: list[SubProjectOut]
    contracts: list[ContractOut]


class TraceabilityOut(BaseModel):
    traceability: dict


def _analysis_out(a, *, with_diagrams: bool = True) -> AnalysisVersionOut:
    d = analysis_store.analysis_to_dict(a, with_diagrams=with_diagrams)
    return AnalysisVersionOut(**d)


# ---------------------------------------------------------------------------
# Commit (genera un analisis candidato)
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/analysis/commit",
    response_model=AnalysisCommitOut,
    status_code=status.HTTP_201_CREATED,
)
async def commit_analysis(
    project_id: int,
    user: User = Depends(get_current_user),
) -> AnalysisCommitOut:
    """Genera un analisis CANDIDATE: corre el assembler (MER por ahora).

    Crea una nueva version (autoincremental) en estado CANDIDATE con las
    entidades y relaciones del MER. El usuario la pasa a IN_REVIEW / LOCKED
    con PATCH.
    """
    async with AsyncSessionLocal() as db:
        project = await _load_owned_project(db, project_id, user)
        payload = await assemble_analysis(
            db,
            project_id,
            project_name=project.name,
            project_description=project.description or "",
        )
        doc = await analysis_store.create_analysis(db, project_id, payload)
    return AnalysisCommitOut(
        project_id=project_id,
        version=doc.version,
        status=doc.status.value,
        requirement_count=doc.requirement_count,
        mer_stats={
            "entities": len(payload.get("entities", [])),
            "relationships": len(payload.get("relationships", [])),
        },
    )


# ---------------------------------------------------------------------------
# Versiones
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/analysis/versions",
    response_model=list[AnalysisVersionOut],
)
async def list_analysis_versions(
    project_id: int,
    user: User = Depends(get_current_user),
) -> list[AnalysisVersionOut]:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        rows = await analysis_store.list_analysis_versions(db, project_id)
    return [_analysis_out(a, with_diagrams=False) for a in rows]


@router.get(
    "/projects/{project_id}/analysis/versions/{version}",
    response_model=AnalysisVersionOut,
)
async def get_analysis_version(
    project_id: int,
    version: int,
    user: User = Depends(get_current_user),
) -> AnalysisVersionOut:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        doc = await analysis_store.get_analysis_version(db, project_id, version)
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return _analysis_out(doc)


@router.patch(
    "/projects/{project_id}/analysis/versions/{version}",
    response_model=AnalysisVersionOut,
)
async def patch_analysis_version(
    project_id: int,
    version: int,
    body: AnalysisStatusUpdate,
    user: User = Depends(get_current_user),
) -> AnalysisVersionOut:
    """Cambia el estado de una version (candidate -> in_review -> locked)."""
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        doc = await analysis_store.get_analysis_version(db, project_id, version)
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        try:
            updated = await analysis_store.update_analysis_status(
                db, doc.id, _parse_analysis_status(body.status)
            )
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return _analysis_out(updated)


@router.patch(
    "/projects/{project_id}/analysis/versions/{version}/diagrams",
    response_model=AnalysisVersionOut,
)
async def patch_analysis_diagrams(
    project_id: int,
    version: int,
    body: AnalysisDiagramUpdate,
    user: User = Depends(get_current_user),
) -> AnalysisVersionOut:
    """Edita el codigo fuente de los diagramas (solo CANDIDATE)."""
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        doc = await analysis_store.get_analysis_version(db, project_id, version)
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        try:
            updated = await analysis_store.update_analysis_diagrams(
                db, doc.id, body.model_dump(exclude_none=True)
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return _analysis_out(updated)


# ---------------------------------------------------------------------------
# MER: entities + relationships
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/analysis/versions/{version}/entities",
    response_model=list[EntityOut],
)
async def list_entities(
    project_id: int,
    version: int,
    user: User = Depends(get_current_user),
) -> list[EntityOut]:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        doc = await analysis_store.get_analysis_version(db, project_id, version)
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        entities = await analysis_store.list_domain_entities(db, doc.id)
    return [EntityOut(**analysis_store.entity_to_dict(e)) for e in entities]


@router.get(
    "/projects/{project_id}/analysis/versions/{version}/relationships",
    response_model=list[RelationshipOut],
)
async def list_relationships(
    project_id: int,
    version: int,
    user: User = Depends(get_current_user),
) -> list[RelationshipOut]:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        doc = await analysis_store.get_analysis_version(db, project_id, version)
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        rels = await analysis_store.list_domain_relationships(db, doc.id)
    return [
        RelationshipOut(**analysis_store.relationship_to_dict(r)) for r in rels
    ]


@router.get(
    "/projects/{project_id}/analysis/versions/{version}/adrs",
    response_model=list[AdrOut],
)
async def list_adrs(
    project_id: int,
    version: int,
    user: User = Depends(get_current_user),
) -> list[AdrOut]:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        doc = await analysis_store.get_analysis_version(db, project_id, version)
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        adrs = await analysis_store.list_adrs(db, doc.id)
    return [AdrOut(**analysis_store.adr_to_dict(a)) for a in adrs]


@router.get(
    "/projects/{project_id}/analysis/versions/{version}/subprojects",
    response_model=SubProjectsOut,
)
async def list_subprojects(
    project_id: int,
    version: int,
    user: User = Depends(get_current_user),
) -> SubProjectsOut:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        doc = await analysis_store.get_analysis_version(db, project_id, version)
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        subs = await analysis_store.list_sub_projects(db, doc.id)
        contracts = await analysis_store.list_contracts(db, doc.id)
        projects = await analysis_store.list_projects(db, doc.id)
    return SubProjectsOut(
        projects=[
            ProjectOut(**analysis_store.project_to_dict(p)) for p in projects
        ],
        subprojects=[
            SubProjectOut(**analysis_store.subproject_to_dict(s)) for s in subs
        ],
        contracts=[
            ContractOut(**analysis_store.contract_to_dict(c)) for c in contracts
        ],
    )


@router.get(
    "/projects/{project_id}/analysis/versions/{version}/traceability",
    response_model=TraceabilityOut,
)
async def get_traceability(
    project_id: int,
    version: int,
    user: User = Depends(get_current_user),
) -> TraceabilityOut:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        doc = await analysis_store.get_analysis_version(db, project_id, version)
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return TraceabilityOut(
        traceability=doc.traceability or {},
    )


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------


def _parse_analysis_status(value: str) -> AnalysisStatus:
    for s in AnalysisStatus:
        if s.value == value:
            return s
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        f"invalid analysis status: {value}",
    )
