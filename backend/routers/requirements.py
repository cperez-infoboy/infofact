"""Router de requerimientos y planes de agrupamiento (fuente de verdad: DB).

Endpoints (todos requieren auth + ownership del proyecto):
  GET    /api/projects/{pid}/requirements                          lista con filtros
  GET    /api/requirements/{id}                                     detalle enriquecido
  PATCH  /api/requirements/{id}                                     update (statement/type/priority)
  POST   /api/requirements/merge                                    fusiona N en uno
  GET    /api/projects/{pid}/grouping-plans                         lista planes
  GET    /api/projects/{pid}/grouping-plans/{plan_id}               detalle + grupos
  PATCH  /api/projects/{pid}/grouping-plans/{plan_id}/groups/{gid}  decision del grupo
  POST   /api/projects/{pid}/grouping-plans/{plan_id}/apply         aplica los grupos accept
  GET    /api/projects/{pid}/grouping-plans/{plan_id}/export.md     export markdown read-only

Patrón de ownership (CLAUDE.md decisión 7): si el recurso no existe O no
pertenece al usuario, devolvemos 404 "not_found" idéntico en ambos casos.
Usamos Depends(get_current_user) (ningún endpoint hace streaming SSE).

La UI y el agente son dos clientes del mismo store: ambos leen y aplican sobre
la misma DB. La guarda de idempotencia compartida es el `decision` por grupo +
el `status` por plan, así nunca se doble-aplica venga de donde venga.
"""
from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    status,
)
from pydantic import BaseModel, Field

from backend.database import AsyncSessionLocal
from backend.deps import get_current_user
from backend.models import Project, User
from backend.models.requirement import (
    GroupDecision,
    Priority,
    ReqStatus,
    ReqType,
    RequirementItem,
)
from backend.services import grouping_store as gstore
from backend.services import requirement_store as store

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic request schemas
# ---------------------------------------------------------------------------


class RequirementUpdate(BaseModel):
    statement: str | None = Field(None, min_length=1)
    type: str | None = None
    priority: str | None = None


class MergeRequest(BaseModel):
    keep_id: int
    member_ids: list[int] = Field(..., min_length=1)
    reason: str = "manual_merge"


class GroupDecisionUpdate(BaseModel):
    decision: str  # accept | reject | pending


# ---------------------------------------------------------------------------
# Ownership helpers (404 on missing OR not-owned, identical response)
# ---------------------------------------------------------------------------


async def _load_owned_project(db, project_id: int, user: User) -> Project:
    project = await db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return project


async def _load_owned_requirement(
    db, req_id: int, user: User
) -> RequirementItem:
    item = await db.get(RequirementItem, req_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    project = await db.get(Project, item.project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return item


def _item_to_dict(it: RequirementItem) -> dict:
    """Vista ligera de un RequirementItem (sin relaciones ni revisiones)."""
    return {
        "id": it.id,
        "project_id": it.project_id,
        "code": it.code,
        "statement": it.statement,
        "type": it.type.value if it.type else None,
        "priority": it.priority.value if it.priority else None,
        "status": it.status.value if it.status else None,
        "confidence": it.confidence,
        "span_verified": it.span_verified,
        "parent_id": it.parent_id,
        "derived": it.derived,
        "merged_into": it.merged_into,
        "source": it.source,
        "created_at": it.created_at.isoformat() if it.created_at else None,
        "updated_at": it.updated_at.isoformat() if it.updated_at else None,
    }


async def _load_plan_or_404(db, project_id: int, plan_id: int) -> dict:
    """Carga un plan y valida que pertenezca al proyecto; 404 si no."""
    data = await gstore.get_plan(db, plan_id)
    if data is None or data.get("project_id") != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return data


# ---------------------------------------------------------------------------
# Requirements: list / detail / update / merge
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/requirements")
async def list_project_requirements(
    project_id: int,
    type: str | None = None,
    priority: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    include_deleted: bool = False,
    user: User = Depends(get_current_user),
) -> dict:
    """Lista los requerimientos de un proyecto con filtros opcionales."""
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        kwargs: dict = {"include_deleted": include_deleted}
        if type:
            kwargs["type"] = ReqType(type)
        if priority:
            kwargs["priority"] = Priority(priority)
        if status_filter:
            kwargs["status"] = ReqStatus(status_filter)
        items = await store.list_requirements(db, project_id, **kwargs)
    return {
        "project_id": project_id,
        "count": len(items),
        "items": [_item_to_dict(it) for it in items],
    }


@router.get("/requirements/{req_id}")
async def get_requirement_detail(
    req_id: int,
    user: User = Depends(get_current_user),
) -> dict:
    """Detalle enriquecido de un requerimiento (source, relaciones, revisiones)."""
    async with AsyncSessionLocal() as db:
        await _load_owned_requirement(db, req_id, user)
        detail = await store.get_requirement(db, req_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return detail


@router.patch("/requirements/{req_id}")
async def patch_requirement(
    req_id: int,
    body: RequirementUpdate,
    user: User = Depends(get_current_user),
) -> dict:
    """Actualiza statement / type / priority de un requerimiento."""
    async with AsyncSessionLocal() as db:
        item = await _load_owned_requirement(db, req_id, user)
        kwargs: dict = {"changed_by": f"user:{user.id}", "reason": "ui_update"}
        if body.statement is not None:
            kwargs["statement"] = body.statement
        if body.type is not None:
            kwargs["type"] = ReqType(body.type)
        if body.priority is not None:
            kwargs["priority"] = Priority(body.priority)
        updated = await store.update_requirement(db, item.id, **kwargs)
    return _item_to_dict(updated)


@router.post("/requirements/merge", status_code=status.HTTP_200_OK)
async def merge_requirements_handler(
    body: MergeRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """Fusiona N requerimientos en uno (keeper absorbe miembros).

    Todos los ids deben pertenecer al mismo proyecto del usuario. La fusion es
    idempotente: miembros ya MERGED en el keeper se ignoran (merge_requirements).
    """
    async with AsyncSessionLocal() as db:
        ids = [body.keep_id, *body.member_ids]
        seen_projects: set[int] = set()
        for rid in ids:
            item = await db.get(RequirementItem, rid)
            if item is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
            project = await db.get(Project, item.project_id)
            if project is None or project.user_id != user.id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
            seen_projects.add(item.project_id)
        if len(seen_projects) > 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "no se pueden fusionar requerimientos de distintos proyectos",
            )
        kept = await store.merge_requirements(
            db, ids, keep_id=body.keep_id,
            reason=body.reason, changed_by=f"user:{user.id}",
        )
    return _item_to_dict(kept)


# ---------------------------------------------------------------------------
# Grouping plans (shared source of truth: agent + UI)
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/grouping-plans")
async def list_grouping_plans(
    project_id: int,
    user: User = Depends(get_current_user),
) -> dict:
    """Lista los planes de agrupamiento del proyecto (más recientes primero)."""
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        plans = await gstore.list_plans(db, project_id)
    return {"project_id": project_id, "plans": plans}


@router.get("/projects/{project_id}/grouping-plans/{plan_id}")
async def get_grouping_plan(
    project_id: int,
    plan_id: int,
    user: User = Depends(get_current_user),
) -> dict:
    """Detalle de un plan: estado + grupos (keeper/members resueltos a codes)."""
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        data = await _load_plan_or_404(db, project_id, plan_id)
    return data


@router.patch(
    "/projects/{project_id}/grouping-plans/{plan_id}/groups/{group_id}"
)
async def patch_group_decision(
    project_id: int,
    plan_id: int,
    group_id: int,
    body: GroupDecisionUpdate,
    user: User = Depends(get_current_user),
) -> dict:
    """Cambia la decision de curacion de un grupo (accept/reject/pending)."""
    try:
        decision = GroupDecision(body.decision)
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"decision invalida: {body.decision!r}",
        )
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        plan = await _load_plan_or_404(db, project_id, plan_id)
        if not any(g["id"] == group_id for g in plan["groups"]):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        result = await gstore.set_group_decision(db, group_id, decision)
    return result or {}


@router.post("/projects/{project_id}/grouping-plans/{plan_id}/apply")
async def apply_grouping_plan(
    project_id: int,
    plan_id: int,
    user: User = Depends(get_current_user),
) -> dict:
    """Aplica el plan: fusiona los grupos con decision=accept (idempotente)."""
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        await _load_plan_or_404(db, project_id, plan_id)
        result = await gstore.apply_plan(
            db, plan_id, changed_by=f"user:{user.id}"
        )
    return result


@router.post("/projects/{project_id}/grouping-plans/{plan_id}/archive")
async def archive_grouping_plan(
    project_id: int,
    plan_id: int,
    user: User = Depends(get_current_user),
) -> dict:
    """Archiva un plan obsoleto: cierra sin aplicar (no toca requerimientos)."""
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        await _load_plan_or_404(db, project_id, plan_id)
        result = await gstore.archive_plan(db, plan_id)
    return result


@router.get(
    "/projects/{project_id}/grouping-plans/{plan_id}/export.md",
    response_class=Response,
)
async def export_grouping_plan_md(
    project_id: int,
    plan_id: int,
    user: User = Depends(get_current_user),
) -> Response:
    """Export read-only del plan como Markdown (auditoria; no es el store)."""
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        await _load_plan_or_404(db, project_id, plan_id)
        md = await gstore.export_plan_md(db, plan_id)
    if md is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return Response(content=md, media_type="text/markdown")
