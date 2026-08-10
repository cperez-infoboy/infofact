"""Router del SRS: versiones, calidad, cobertura, goals y trazabilidad.

Endpoints (todos project-scoped, bajo /api/projects/{id}/...):
  POST   /srs/commit                          genera un SRS CANDIDATE (orquesta
                                               quality + goals + coverage)
  GET    /srs/versions                        lista versiones del proyecto
  GET    /srs/versions/{version}              detalle de una versión
  PATCH  /srs/versions/{version}              estado / narrativa / pendientes
  POST   /srs/versions/{version}/reproject    refresca solo las secciones proyectadas
  GET    /quality                             resumen de calidad + hallazgos
  GET    /coverage                            matriz de cobertura
  GET    /requirements/{req_id}/findings      hallazgos de un requerimiento
  GET    /goals                               lista de goals
  GET    /goals/{goal_id}                     detalle de un goal
  PATCH  /goals/{goal_id}                     edita un goal
  GET    /traceability                        matriz goal <-> req <-> fuente

Patrón de ownership (igual que projects.py): recurso inexistente o ajeno -> 404
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
from backend.models.srs import FindingStatus, GoalStatus, LinkStatus, SrsStatus
from backend.services import srs_store
from backend.services.srs_assembler import assemble_srs
from backend.services.srs_builder import build_srs

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


class SrsVersionOut(BaseModel):
    id: int
    project_id: int
    version: int
    status: str
    structure: list
    narrative: dict
    markdown: str | None = None
    quality_summary: dict
    coverage: dict
    traceability: dict
    review_flags: dict
    requirement_codes: list
    requirement_count: int
    generated_at: str | None = None
    generated_by: str | None = None
    reviewed_at: str | None = None
    locked_at: str | None = None


class SrsCommitOut(BaseModel):
    project_id: int
    version: int
    status: str
    requirement_count: int
    quality_summary: dict
    coverage: dict
    goals: dict


class SrsStatusUpdate(BaseModel):
    status: str  # in_review | locked


class SrsNarrativeUpdate(BaseModel):
    narrative: dict


class SrsReviewFlagsUpdate(BaseModel):
    review_flags: dict


class FindingOut(BaseModel):
    id: int
    req_id: int | None = None
    # Codigo opaque REQ-XXXX del requerimiento; None en hallazgos de conjunto.
    req_code: str | None = None
    scope: str
    dimension: str
    rule_id: str
    severity: str
    message: str
    suggestion: str | None = None
    status: str
    ears_pattern: str | None = None
    detected_by: str | None = None


class QualityOut(BaseModel):
    summary: dict
    findings: list[FindingOut]


class CoverageOut(BaseModel):
    coverage: dict


class GoalOut(BaseModel):
    id: int
    code: str
    statement: str
    kind: str
    parent_id: int | None = None
    rationale: str | None = None
    source: dict | None = None
    confidence: float
    status: str


class GoalUpdate(BaseModel):
    statement: str | None = None
    rationale: str | None = None
    status: str | None = None  # proposed | confirmed | rejected


class TraceabilityOut(BaseModel):
    goals: list[GoalOut]
    matrix: list[dict]


def _srs_out(s, *, with_markdown: bool = True) -> SrsVersionOut:
    d = srs_store.srs_to_dict(s, with_markdown=with_markdown)
    return SrsVersionOut(**d)


# ---------------------------------------------------------------------------
# Commit (genera un SRS candidato)
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/srs/commit",
    response_model=SrsCommitOut,
    status_code=status.HTTP_201_CREATED,
)
async def commit_srs(
    project_id: int,
    user: User = Depends(get_current_user),
) -> SrsCommitOut:
    """Genera un SRS CANDIDATE: orquesta calidad + goals + cobertura.

    Corre los tres motores sobre los requerimientos vivos del proyecto, crea
    una nueva versión (autoincremental) en estado CANDIDATE y persiste
    hallazgos, goals, cobertura y trazabilidad. El usuario la pasa a IN_REVIEW
    / LOCKED con PATCH.
    """
    async with AsyncSessionLocal() as db:
        project = await _load_owned_project(db, project_id, user)
        payload = await assemble_srs(
            db,
            project_id,
            project_name=project.name,
            project_description=project.description or "",
        )
        srs = await srs_store.create_srs(db, project_id, payload)
    return SrsCommitOut(
        project_id=project_id,
        version=srs.version,
        status=srs.status.value,
        requirement_count=srs.requirement_count,
        quality_summary=srs.quality_summary,
        coverage=srs.coverage,
        goals=srs.quality_summary.get("goals", {}),
    )


# ---------------------------------------------------------------------------
# Versiones
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/srs/versions", response_model=list[SrsVersionOut]
)
async def list_srs_versions(
    project_id: int,
    user: User = Depends(get_current_user),
) -> list[SrsVersionOut]:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        rows = await srs_store.list_srs_versions(db, project_id)
    return [_srs_out(s, with_markdown=False) for s in rows]


@router.get(
    "/projects/{project_id}/srs/versions/{version}", response_model=SrsVersionOut
)
async def get_srs_version(
    project_id: int,
    version: int,
    user: User = Depends(get_current_user),
) -> SrsVersionOut:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        srs = await srs_store.get_srs_version(db, project_id, version)
        if srs is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return _srs_out(srs)


@router.patch(
    "/projects/{project_id}/srs/versions/{version}", response_model=SrsVersionOut
)
async def patch_srs_version(
    project_id: int,
    version: int,
    body: SrsStatusUpdate | SrsNarrativeUpdate | SrsReviewFlagsUpdate,
    user: User = Depends(get_current_user),
) -> SrsVersionOut:
    """Mutación controlada de una versión.

    Un solo PATCH admite tres formas ( FastAPI las intenta en orden por la
    unión): cambio de estado (IN_REVIEW/LOCKED), edición de narrativa, o
    edición de la cola de pendientes. El store rechaza mutar un LOCKED.
    """
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        try:
            if isinstance(body, SrsStatusUpdate):
                srs = await srs_store.update_srs(
                    db, project_id, version, status=_parse_srs_status(body.status)
                )
            elif isinstance(body, SrsNarrativeUpdate):
                srs = await srs_store.update_srs(
                    db, project_id, version, narrative=body.narrative
                )
            else:
                srs = await srs_store.update_srs(
                    db, project_id, version, review_flags=body.review_flags
                )
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return _srs_out(srs)


@router.post(
    "/projects/{project_id}/srs/versions/{version}/reproject",
    response_model=SrsVersionOut,
)
async def reproject_srs(
    project_id: int,
    version: int,
    user: User = Depends(get_current_user),
) -> SrsVersionOut:
    """Refresca SOLO las secciones proyectadas de una versión (markdown + codes)."""
    async with AsyncSessionLocal() as db:
        project = await _load_owned_project(db, project_id, user)
        # Cargar narrativa y estructura de la versión existente para
        # preservar el texto editado por el usuario al re-proyectar.
        existing = await srs_store.get_srs_version(db, project_id, version)
        if existing is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        built = await build_srs(
            db,
            project_id,
            project_name=project.name,
            project_description=project.description or "",
            narrative=existing.narrative,
            structure=existing.structure,
        )
        try:
            srs = await srs_store.reproject_srs(
                db,
                project_id,
                version,
                markdown=built["markdown"],
                requirement_codes=[],
                requirement_count=built["counts"].get("live", 0),
            )
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return _srs_out(srs)


# ---------------------------------------------------------------------------
# Secciones del SRS (acceso granular para agente / UI)
# ---------------------------------------------------------------------------


class SectionSubsectionOut(BaseModel):
    id: str
    title: str
    has_content: bool


class SectionOut(BaseModel):
    id: str
    title: str
    kind: str
    subsections: list[SectionSubsectionOut] | None = None
    item_count: int | None = None


class SectionContentOut(BaseModel):
    id: str
    title: str
    kind: str
    content: str | None = None
    items: list[dict] | None = None


@router.get(
    "/projects/{project_id}/srs/versions/{version}/sections",
    response_model=list[SectionOut],
)
async def list_srs_sections(
    project_id: int,
    version: int,
    user: User = Depends(get_current_user),
) -> list[SectionOut]:
    """Devuelve el árbol de secciones del SRS con metadatos.

    Para secciones ``authored`` indica si cada subsection tiene contenido.
    Para secciones ``projected`` indica cuántos requerimientos pertenecen.
    """
    from backend.services.srs_builder import SECTION_REQTYPE_MAP
    from backend.services.requirement_store import list_requirements

    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        srs = await srs_store.get_srs_version(db, project_id, version)
        if srs is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")

        all_items = await list_requirements(db, project_id, include_deleted=False)
        live_items = [
            it for it in all_items
            if it.status.value in ("validated", "approved", "draft")
        ]

    result: list[SectionOut] = []
    for section in srs.structure:
        sec = SectionOut(
            id=section["id"],
            title=section["title"],
            kind=section.get("kind", "projected"),
        )
        if section.get("kind") == "authored" and section.get("subsections"):
            sec.subsections = []
            for sub in section["subsections"]:
                sec.subsections.append(SectionSubsectionOut(
                    id=sub["id"],
                    title=sub["title"],
                    has_content=bool(srs.narrative.get(sub["id"])),
                ))
        elif section["id"] in SECTION_REQTYPE_MAP:
            reqtypes = SECTION_REQTYPE_MAP[section["id"]]
            sec.item_count = sum(
                1 for it in live_items if it.type in reqtypes
            )
        result.append(sec)
    return result


@router.get(
    "/projects/{project_id}/srs/versions/{version}/sections/{section_id}",
    response_model=SectionContentOut,
)
async def get_srs_section(
    project_id: int,
    version: int,
    section_id: str,
    user: User = Depends(get_current_user),
) -> SectionContentOut:
    """Devuelve el contenido de una sección o subsection específica.

    Para ``authored``: el texto de ``narrative[section_id]``.
    Para ``projected``: los RequirementItem de esa sección.
    """
    from backend.services.srs_builder import SECTION_REQTYPE_MAP
    from backend.services.requirement_store import list_requirements

    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        srs = await srs_store.get_srs_version(db, project_id, version)
        if srs is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")

    # Buscar la sección o subsection en la estructura.
    target_title: str = section_id
    target_kind: str = "projected"

    for section in srs.structure:
        if section["id"] == section_id:
            target_title = section["title"]
            target_kind = section.get("kind", "projected")
            break
        for sub in section.get("subsections", []):
            if sub["id"] == section_id:
                target_title = sub["title"]
                target_kind = section.get("kind", "authored")
                break

    if target_kind == "authored":
        content = srs.narrative.get(section_id, "")
        return SectionContentOut(
            id=section_id,
            title=target_title,
            kind="authored",
            content=content or None,
        )

    # Projected: devolver items de la sección.
    if section_id not in SECTION_REQTYPE_MAP:
        return SectionContentOut(
            id=section_id,
            title=target_title,
            kind="projected",
            items=[],
        )

    reqtypes = SECTION_REQTYPE_MAP[section_id]
    async with AsyncSessionLocal() as db:
        all_items = await list_requirements(db, project_id, include_deleted=False)

    items_data = [
        {
            "code": it.code,
            "statement": it.statement,
            "priority": it.priority.value,
            "type": it.type.value,
            "acceptance_criteria": list(it.acceptance_criteria or []),
            "derived": it.derived,
        }
        for it in all_items
        if it.type in reqtypes and it.status.value in ("validated", "approved", "draft")
    ]
    return SectionContentOut(
        id=section_id,
        title=target_title,
        kind="projected",
        items=items_data,
    )


# ---------------------------------------------------------------------------
# Calidad y cobertura
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/quality", response_model=QualityOut
)
async def get_quality(
    project_id: int,
    user: User = Depends(get_current_user),
) -> QualityOut:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        findings = await srs_store.list_findings(db, project_id)
        # Reconstruye el resumen desde los hallazgos persistidos.
        summary = _summarize_findings(findings)
        # Resuelve req_id -> codigo opaque (REQ-XXXX) para que cada hallazgo
        # identifique al requerimiento en la UI y no solo su id interno.
        code_map = await srs_store._req_code_map(db, project_id)
    return QualityOut(
        summary=summary,
        findings=[
            FindingOut(**d)
            for d in srs_store.findings_to_dicts(findings, code_map)
        ],
    )


@router.get("/projects/{project_id}/coverage", response_model=CoverageOut)
async def get_coverage(
    project_id: int,
    user: User = Depends(get_current_user),
) -> CoverageOut:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        latest = await srs_store.get_latest_srs(db, project_id)
    if latest is None or not latest.coverage:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "no_coverage_run",
        )
    return CoverageOut(coverage=latest.coverage)


@router.get(
    "/projects/{project_id}/requirements/{req_id}/findings",
    response_model=list[FindingOut],
)
async def get_requirement_findings(
    project_id: int,
    req_id: int,
    user: User = Depends(get_current_user),
) -> list[FindingOut]:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        findings = await srs_store.list_findings(db, project_id, req_id=req_id)
        code_map = await srs_store._req_code_map(db, project_id)
    return [
        FindingOut(**d)
        for d in srs_store.findings_to_dicts(findings, code_map)
    ]


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/goals", response_model=list[GoalOut])
async def list_goals(
    project_id: int,
    user: User = Depends(get_current_user),
) -> list[GoalOut]:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        goals = await srs_store.list_goals(db, project_id)
    return [GoalOut(**srs_store.goal_to_dict(g)) for g in goals]


@router.get("/projects/{project_id}/goals/{goal_id}", response_model=GoalOut)
async def get_goal(
    project_id: int,
    goal_id: int,
    user: User = Depends(get_current_user),
) -> GoalOut:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        try:
            g = await srs_store.get_goal(db, goal_id, project_id=project_id)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return GoalOut(**srs_store.goal_to_dict(g))


@router.patch("/projects/{project_id}/goals/{goal_id}", response_model=GoalOut)
async def patch_goal(
    project_id: int,
    goal_id: int,
    body: GoalUpdate,
    user: User = Depends(get_current_user),
) -> GoalOut:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        try:
            g = await srs_store.update_goal(
                db,
                goal_id,
                project_id=project_id,
                statement=body.statement,
                rationale=body.rationale,
                status=_parse_goal_status(body.status) if body.status else None,
            )
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return GoalOut(**srs_store.goal_to_dict(g))


@router.get(
    "/projects/{project_id}/traceability", response_model=TraceabilityOut
)
async def get_traceability(
    project_id: int,
    user: User = Depends(get_current_user),
) -> TraceabilityOut:
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        trace = await srs_store.build_traceability(db, project_id)
    return TraceabilityOut(
        goals=[GoalOut(**g) for g in trace["goals"]],
        matrix=trace["matrix"],
    )


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------


def _parse_srs_status(value: str) -> SrsStatus:
    for s in SrsStatus:
        if s.value == value:
            return s
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        f"invalid srs status: {value}",
    )


def _parse_goal_status(value: str) -> GoalStatus:
    for s in GoalStatus:
        if s.value == value:
            return s
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        f"invalid goal status: {value}",
    )


def _summarize_findings(findings) -> dict:
    by_severity: dict[str, int] = {}
    by_dimension: dict[str, int] = {}
    blockers = 0
    items_with_findings: set[int] = set()
    for f in findings:
        sev = f.severity.value
        dim = f.dimension.value
        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_dimension[dim] = by_dimension.get(dim, 0) + 1
        if f.req_id:
            items_with_findings.add(f.req_id)
        if sev == "blocker":
            blockers += 1
    return {
        "total_findings": len(findings),
        "by_severity": by_severity,
        "by_dimension": by_dimension,
        "items_with_findings": len(items_with_findings),
        "blockers": blockers,
    }
