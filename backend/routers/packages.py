"""HTTP API of work packages (molde routers/analysis.py + export pattern).

All routes are project-scoped and ownership-checked (user_id match, 404
otherwise — same convention as project_rules).
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
from pydantic import BaseModel
from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.deps import get_current_user
from backend.models import Project, User
from backend.models.analysis import AnalysisDocument
from backend.models.packages import (
    PackageStatus,
    WorkPackageDocument,
)
from backend.services import packages_store as store

logger = logging.getLogger(__name__)

router = APIRouter()


async def _load_owned_project(
    db, project_id: int, user: User
) -> Project:
    project = await db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return project


async def _load_document(
    db, project_id: int, version: int, user: User
) -> WorkPackageDocument:
    await _load_owned_project(db, project_id, user)
    doc = await store.get_document_by_version(db, project_id, version)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return doc


class StatusUpdate(BaseModel):
    status: str  # candidate | in_review | locked


@router.get("/projects/{project_id}/packages/versions")
async def list_versions(
    project_id: int,
    user: User = Depends(get_current_user),
) -> list[dict]:
    """Versiones de paquetes de trabajo del proyecto (más reciente primero)."""
    async with AsyncSessionLocal() as db:
        await _load_owned_project(db, project_id, user)
        docs = await store.list_documents(db, project_id)
    return [store.document_to_dict(d) for d in docs]


@router.get("/projects/{project_id}/packages/versions/{version}")
async def get_version(
    project_id: int,
    version: int,
    user: User = Depends(get_current_user),
) -> dict:
    """Detalle de una versión: doc + paquetes (sin markdown) + tareas."""
    async with AsyncSessionLocal() as db:
        doc = await _load_document(db, project_id, version, user)
        wps = await store.list_packages(db, doc.id)
        packages = []
        for wp in wps:
            tasks = await store.list_tasks(db, wp.id)
            entry = store.package_to_dict(wp)
            entry["tasks"] = [store.task_to_dict(t) for t in tasks]
            packages.append(entry)
        master = doc.master_markdown
    out = store.document_to_dict(doc)
    out["packages"] = packages
    out["master_markdown"] = master
    return out


@router.get("/projects/{project_id}/packages/versions/{version}/{wp_code}")
async def get_package(
    project_id: int,
    version: int,
    wp_code: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Un paquete (con markdown renderizado) y sus tareas."""
    async with AsyncSessionLocal() as db:
        doc = await _load_document(db, project_id, version, user)
        wps = await store.list_packages(db, doc.id)
        wp = next((w for w in wps if w.code == wp_code), None)
        if wp is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        tasks = await store.list_tasks(db, wp.id)
    out = store.package_to_dict(wp, include_markdown=True)
    out["tasks"] = [store.task_to_dict(t) for t in tasks]
    return out


@router.patch("/projects/{project_id}/packages/versions/{version}")
async def patch_version_status(
    project_id: int,
    version: int,
    body: StatusUpdate,
    user: User = Depends(get_current_user),
) -> dict:
    """Transición de estado (candidate -> in_review -> locked)."""
    try:
        new_status = PackageStatus(body.status)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_status"
        )
    async with AsyncSessionLocal() as db:
        doc = await _load_document(db, project_id, version, user)
        if doc.status == PackageStatus.LOCKED and new_status != PackageStatus.LOCKED:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "locked_is_immutable"
            )
        updated = await store.update_status(db, doc.id, new_status)
        await db.commit()
    return store.document_to_dict(updated)


@router.get(
    "/projects/{project_id}/packages/versions/{version}/master/export.md",
    response_class=Response,
)
async def export_master_md(
    project_id: int,
    version: int,
    user: User = Depends(get_current_user),
) -> Response:
    """El maestro de ensamblaje (orden, hitos, informe de coherencia).

    Registrada ANTES que la ruta dinámica ``/{wp_code}/export.md``: si no,
    FastAPI matchea ``master`` como wp_code y responde 404.
    """
    async with AsyncSessionLocal() as db:
        doc = await _load_document(db, project_id, version, user)
        md = doc.master_markdown
    return Response(
        content=md,
        media_type="text/markdown",
        headers={
            "Content-Disposition": (
                f'attachment; filename="PackagesMaster_v{version}.md"'
            )
        },
    )


@router.get(
    "/projects/{project_id}/packages/versions/{version}/{wp_code}/export.md",
    response_class=Response,
)
async def export_package_md(
    project_id: int,
    version: int,
    wp_code: str,
    user: User = Depends(get_current_user),
) -> Response:
    """El .md del paquete (entregable directo al desarrollador)."""
    async with AsyncSessionLocal() as db:
        doc = await _load_document(db, project_id, version, user)
        wps = await store.list_packages(db, doc.id)
        wp = next((w for w in wps if w.code == wp_code), None)
        if wp is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        md = wp.markdown
    return Response(
        content=md,
        media_type="text/markdown",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{wp_code}_v{version}.md"'
            )
        },
    )
