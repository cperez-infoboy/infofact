"""Router del harness de reglas del proyecto (ProjectRule).

Endpoints (todos requieren auth + ownership del proyecto):
  GET    /api/projects/{pid}/rules                  lista (filtros scope/include_retired)
  POST   /api/projects/{pid}/rules                  crea (source=user)
  PATCH  /api/projects/{pid}/rules/{rid}            edita content/reason o status
  GET    /api/projects/{pid}/rules/export.md        vista Markdown editable
  POST   /api/projects/{pid}/rules/import           re-ingresa desde Markdown (upsert)

La UI y el agente son dos clientes del mismo store (mismo contrato que
requirements.py): las tools del subagente y estos endpoints escriben las
mismas filas. El Markdown es una vista: la DB es la fuente de verdad.
"""
from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Response,
    status,
)
from pydantic import BaseModel, Field

from backend.database import AsyncSessionLocal
from backend.deps import get_current_user
from backend.models import Project, User
from backend.models.project_rule import RuleScope, RuleStatus
from backend.services import project_rules_store as store

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RuleCreate(BaseModel):
    scope: str = Field(..., description="capture | analysis | srs | all")
    content: str = Field(..., min_length=1)
    reason: str | None = None


class RuleUpdate(BaseModel):
    content: str | None = Field(None, min_length=1)
    reason: str | None = None
    status: str | None = Field(None, description="active | retired")


class RuleImport(BaseModel):
    markdown: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_scope(raw: str | None) -> RuleScope | None:
    if raw is None:
        return None
    try:
        return RuleScope(raw)
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"scope inválido: {raw!r}"
        )


async def _load_owned_project(db, project_id: int, user: User) -> Project:
    project = await db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return project


def _rule_out(rule) -> dict:
    return {
        "id": rule.id,
        "project_id": rule.project_id,
        "scope": rule.scope.value,
        "content": rule.content,
        "reason": rule.reason,
        "source": rule.source.value,
        "status": rule.status.value,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
        "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/rules")
async def list_project_rules(
    project_id: int,
    scope: str | None = None,
    include_retired: bool = False,
    user: User = Depends(get_current_user),
) -> dict:
    """Lista las reglas del harness del proyecto."""
    rule_scope = _parse_scope(scope)
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        rules = await store.list_rules(
            db, project_id, scope=rule_scope, include_retired=include_retired
        )
    return {
        "project_id": project_id,
        "count": len(rules),
        "rules": [_rule_out(r) for r in rules],
    }


@router.post("/projects/{project_id}/rules", status_code=status.HTTP_201_CREATED)
async def create_project_rule(
    project_id: int,
    body: RuleCreate,
    user: User = Depends(get_current_user),
) -> dict:
    """Crea una regla persistente (source=user) y devuelve sus conflictos."""
    rule_scope = _parse_scope(body.scope)
    if rule_scope is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "scope requerido")
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        result = await store.add_rule(
            db,
            project_id,
            scope=rule_scope,
            content=body.content,
            reason=body.reason,
        )
        await db.commit()
    out = _rule_out(result["rule"])
    if result["conflicts"]:
        out["conflicts"] = result["conflicts"]
    return out


@router.patch("/projects/{project_id}/rules/{rule_id}")
async def patch_project_rule(
    project_id: int,
    rule_id: int,
    body: RuleUpdate,
    user: User = Depends(get_current_user),
) -> dict:
    """Edita una regla (content/reason) o cambia su status (retire/reactivate)."""
    if (
        body.status is None
        and body.content is None
        and body.reason is None
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "nada que actualizar: content | reason | status",
        )
    new_status: RuleStatus | None = None
    if body.status is not None:
        try:
            new_status = RuleStatus(body.status)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"status inválido: {body.status!r}",
            )
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        try:
            rule = None
            if new_status is not None:
                rule = await store.set_rule_status(
                    db, project_id, rule_id, new_status
                )
            if body.content is not None or body.reason is not None:
                rule = await store.update_rule(
                    db,
                    project_id,
                    rule_id,
                    content=body.content,
                    reason=body.reason,
                )
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        await db.commit()
    return _rule_out(rule)


@router.get(
    "/projects/{project_id}/rules/export.md",
    response_class=Response,
)
async def export_project_rules_md(
    project_id: int,
    user: User = Depends(get_current_user),
) -> Response:
    """Vista Markdown editable del harness completo (incluye retiradas)."""
    async with AsyncSessionLocal() as db:
        project = await _load_owned_project(db, project_id, user)
        rules = await store.list_rules(
            db, project_id, include_retired=True
        )
    md = store.export_rules_md(rules, project_name=project.name)
    return Response(content=md, media_type="text/markdown")


@router.post("/projects/{project_id}/rules/import")
async def import_project_rules_md(
    project_id: int,
    body: RuleImport,
    user: User = Depends(get_current_user),
) -> dict:
    """Re-ingresa reglas desde el Markdown del export (upsert por id)."""
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        result = await store.import_rules_md(db, project_id, body.markdown)
        await db.commit()
    return result
