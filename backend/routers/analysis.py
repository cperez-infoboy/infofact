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
# Traceability graph (nodos + aristas para el explorador / GraphExplorer)
# ---------------------------------------------------------------------------


def _build_traceability_payload(
    *,
    projects: list,
    subs: list,
    contracts: list,
    entities: list,
    rels: list,
    live_reqs: dict,
    tasks_by_subproject: dict,
    task_pkg_code: dict,
    view: str = "full",
) -> tuple[list[dict], list[dict]]:
    """Construcción determinista de nodos/aristas del grafo (0 LLM, sin DB).

    ``view="overview"``: solo proyectos + sub-proyectos y sus aristas
    belongs/contract — la descomposición legible; cada sub-proyecto viaja
    con counts (entidades, reqs, tareas) en lugar de arrastrar sus nodos.
    ``view="full"``: descomposición + entidades + REQs vivos + tareas
    (el mapa completo histórico).
    """
    nodes: list[dict] = []
    edges: list[dict] = []

    def _node(id_: str, label: str, kind: str, **extra) -> dict:
        n = {"id": id_, "label": label, "kind": kind}
        n.update(extra)
        return n

    proj_by_code = {p.code: p for p in projects}
    sub_by_code = {s.code: s for s in subs}
    entity_by_code = {e.code: e for e in entities}

    for p in projects:
        nodes.append(
            _node(
                p.code,
                p.name,
                "project",
                domain_type=p.domain_type,
            )
        )
    for s in subs:
        n_tasks = len(tasks_by_subproject.get(s.code, []))
        extra: dict = {
            "project": s.project_code or "",
            "entities": len(s.entity_codes or []),
            "tasks": n_tasks,
        }
        if view == "overview":
            # REQs vivos distintos trazados por las entidades que posee:
            # el peso de trazabilidad del sub-proyecto sin sus nodos.
            traced: set[str] = set()
            for code in s.entity_codes or []:
                ent = entity_by_code.get(code)
                if ent is None:
                    continue
                traced.update(
                    c for c in (ent.traced_req_codes or []) if c in live_reqs
                )
            extra["reqs"] = len(traced)
        nodes.append(
            _node(
                s.code,
                s.name,
                "subproject",
                **extra,
            )
        )
        if s.project_code in proj_by_code:
            edges.append(
                {
                    "from": s.project_code,
                    "to": s.code,
                    "kind": "belongs",
                    "label": "",
                }
            )
    for c in contracts:
        if c.from_subproject_code in sub_by_code and c.to_subproject_code in sub_by_code:
            edges.append(
                {
                    "from": c.from_subproject_code,
                    "to": c.to_subproject_code,
                    "kind": "contract",
                    "label": c.name,
                    "contract_type": (
                        c.contract_type.value
                        if hasattr(c.contract_type, "value")
                        else str(c.contract_type)
                    ),
                }
            )

    if view != "overview":
        for e in entities:
            nodes.append(
                _node(
                    e.code,
                    e.name,
                    "entity",
                    reqs=len(e.traced_req_codes or []),
                )
            )
        owner: dict[str, str] = {}
        for s in subs:
            for code in s.entity_codes or []:
                owner[code] = s.code
        for e in entities:
            target = owner.get(e.code)
            if target:
                edges.append(
                    {"from": target, "to": e.code, "kind": "owns", "label": ""}
                )
        for r in rels:
            if r.from_entity_code in entity_by_code and r.to_entity_code in entity_by_code:
                edges.append(
                    {
                        "from": r.from_entity_code,
                        "to": r.to_entity_code,
                        "kind": "rel",
                        "label": r.label or "",
                        "cardinality": (
                            r.cardinality.value
                            if hasattr(r.cardinality, "value")
                            else str(r.cardinality)
                        ),
                    }
                )
        for code in live_reqs:
            nodes.append(_node(code, code, "req"))
        # REQ -> entity (traced_req_codes) + REQ -> subproject (nfr_codes).
        for s in subs:
            for code in s.nfr_codes or []:
                if code in live_reqs:
                    edges.append(
                        {
                            "from": code,
                            "to": s.code,
                            "kind": "nfr_of",
                            "label": "NFR",
                        }
                    )
        traced_pairs: set[tuple[str, str]] = set()
        for e in entities:
            for code in e.traced_req_codes or []:
                if code in live_reqs and (code, e.code) not in traced_pairs:
                    traced_pairs.add((code, e.code))
                    edges.append(
                        {"from": code, "to": e.code, "kind": "traces", "label": ""}
                    )
        # Tasks (from packages anchored to this analysis). Node id is
        # TASK-code suffixed with the WP code: TASK codes restart per package.
        for s in subs:
            for t in tasks_by_subproject.get(s.code, []):
                tid = f"{t.code}@{task_pkg_code.get(t.package_id, '')}"
                nodes.append(
                    _node(
                        tid,
                        f"{t.code} · {t.title}",
                        "task",
                        reqs=len(t.req_codes or []),
                    )
                )
                edges.append(
                    {"from": s.code, "to": tid, "kind": "task_of", "label": ""}
                )
                for rc in t.req_codes or []:
                    if rc in live_reqs:
                        edges.append(
                            {
                                "from": tid,
                                "to": rc,
                                "kind": "implements",
                                "label": "",
                            }
                        )

    return nodes, edges


@router.get(
    "/projects/{project_id}/analysis/versions/{version}/traceability/graph",
)
async def get_traceability_graph(
    project_id: int,
    version: int,
    focus: str = "",
    levels: int = 1,
    view: str = "full",
    user: User = Depends(get_current_user),
) -> dict:
    """Grafo de trazabilidad consultable (determinista, 0 LLM).

    Sin ``focus``: devuelve el grafo según ``view`` — ``full`` (default)
    incluye entidades + REQs vivos + tareas además de la descomposición;
    ``overview`` devuelve SOLO proyectos + sub-proyectos con sus contratos
    (counts en los nodos: entidades/reqs/tareas), la vista legible del
    Mapa. Con ``focus`` (código de cualquier nodo, ej. ``REQ-7K3F``,
    ``ENT-7K3F``, ``SUB-001``, ``TASK-012``): devuelve SOLO el vecindario
    del nodo hasta ``levels`` saltos (explorador de trazabilidad,
    navegación encadenada).
    """
    from backend.models.analysis import (
        AnalysisProject,
        DomainEntity,
        DomainRelationship,
        SubProject,
        SubProjectContract,
    )
    from backend.models.packages import (
        WorkPackage,
        WorkPackageDocument,
        WorkTask,
    )
    from backend.models.requirement import ReqStatus, RequirementItem
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        doc = await analysis_store.get_analysis_version(
            db, project_id, version
        )
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")

        projects = (
            (
                await db.execute(
                    select(AnalysisProject).where(
                        AnalysisProject.analysis_id == doc.id
                    )
                )
            )
            .scalars()
            .all()
        )
        subs = (
            (
                await db.execute(
                    select(SubProject).where(SubProject.analysis_id == doc.id)
                )
            )
            .scalars()
            .all()
        )
        contracts = (
            (
                await db.execute(
                    select(SubProjectContract).where(
                        SubProjectContract.analysis_id == doc.id
                    )
                )
            )
            .scalars()
            .all()
        )
        entities = (
            (
                await db.execute(
                    select(DomainEntity).where(
                        DomainEntity.analysis_id == doc.id
                    )
                )
            )
            .scalars()
            .all()
        )
        rels = (
            (
                await db.execute(
                    select(DomainRelationship).where(
                        DomainRelationship.analysis_id == doc.id
                    )
                )
            )
            .scalars()
            .all()
        )
        items = (
            (
                await db.execute(
                    select(RequirementItem).where(
                        RequirementItem.project_id == project_id
                    )
                )
            )
            .scalars()
            .all()
        )
        live_reqs = {
            it.code: it
            for it in items
            if it.status
            in (ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT)
        }
        # Paquetes anclados a ESTA versión de análisis (tareas incluidas).
        pkg_docs = (
            (
                await db.execute(
                    select(WorkPackageDocument).where(
                        WorkPackageDocument.project_id == project_id,
                        WorkPackageDocument.analysis_id == doc.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        tasks_by_subproject: dict[str, list] = {}
        task_pkg_code: dict[int, str] = {}
        if pkg_docs:
            doc_ids = [d.id for d in pkg_docs]
            wps = (
                (
                    await db.execute(
                        select(WorkPackage).where(
                            WorkPackage.document_id.in_(doc_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for wp in wps:
                task_pkg_code[wp.id] = wp.code
                tasks = (
                    (
                        await db.execute(
                            select(WorkTask).where(
                                WorkTask.package_id == wp.id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                tasks_by_subproject.setdefault(
                    wp.sub_project_code, []
                ).extend(tasks)

    # --- Grafo determinista desde filas persistidas ---
    nodes, edges = _build_traceability_payload(
        projects=projects,
        subs=subs,
        contracts=contracts,
        entities=entities,
        rels=rels,
        live_reqs=live_reqs,
        tasks_by_subproject=tasks_by_subproject,
        task_pkg_code=task_pkg_code,
        view="overview" if view == "overview" else "full",
    )

    all_nodes = {n["id"]: n for n in nodes}
    adj: dict[str, list[tuple[str, dict]]] = {}
    for e in edges:
        adj.setdefault(e["from"], []).append((e["to"], e))
        adj.setdefault(e["to"], []).append((e["from"], e))

    if focus:
        if focus not in all_nodes:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "focus_not_found")
        keep = {focus}
        frontier = {focus}
        for _ in range(max(1, min(levels, 4))):
            nxt: set[str] = set()
            for node in frontier:
                for nb, _e in adj.get(node, []):
                    if nb not in keep:
                        nxt.add(nb)
            keep |= nxt
            frontier = nxt
        sel_nodes = [all_nodes[i] for i in sorted(keep) if i in all_nodes]
        sel_edges = [
            e
            for e in edges
            if e["from"] in keep and e["to"] in keep
        ]
        return {
            "focus": focus,
            "levels": levels,
            "nodes": sel_nodes,
            "edges": sel_edges,
        }

    return {
        "focus": None,
        "levels": None,
        "nodes": nodes,
        "edges": edges,
    }


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
