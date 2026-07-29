"""Router de workspace: explorar y editar archivos del container del usuario.

Endpoints:
  GET  /api/workspaces/tree?project_id=...&path=.  árbol JSON del workspace
  GET  /api/workspaces/file?project_id=...&path=... lee un archivo
  PUT  /api/workspaces/file?project_id=...&path=... escribe un archivo

El profile sale del usuario autenticado (Depends(get_current_user)); el
project_id determina el subdirectorio del workspace (un proyecto = un dir
propio bajo {workspaces_host_root}/{profile}/{slug}). Cada operación valida
ownership del proyecto antes de tocar el filesystem.

Seguridad: TODO input de `path` se normaliza via _safe_path dentro de
file_service; un intento de traversal (`../../etc/passwd`) se rechaza con
ValueError -> HTTPException 400 "invalid_path".
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.database import AsyncSessionLocal
from backend.deps import get_current_user
from backend.models import Project, User
from backend.services import file_service

router = APIRouter()


class FileWriteBody(BaseModel):
    content: str = Field(..., max_length=2_000_000)  # 2 MB techo, suficiente para docs


class FileReadOut(BaseModel):
    path: str
    content: str


class FileWriteOut(BaseModel):
    path: str
    ok: bool = True


async def _resolve_owned_project(project_id: int, user: User) -> Project:
    """Carga el proyecto y verifica ownership. 404 si no existe o no es propio.

    Reusa AsyncSessionLocal directo (no Depends): mismo patrón que
    routers/projects.py para endpoints no-streaming.
    """
    async with AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found")
    return project


@router.get("/tree")
async def get_tree(
    project_id: int = Query(..., description="Projecto al que pertenece el workspace"),
    path: str = Query(".", max_length=512),
    max_depth: int = Query(3, ge=1, le=10, description="Profundidad máxima del árbol"),
    user: User = Depends(get_current_user),
) -> dict:
    """Devuelve un árbol JSON del workspace del proyecto."""
    project = await _resolve_owned_project(project_id, user)
    try:
        tree = await file_service.list_tree(
            user.profile, project.slug, path=path, max_depth=max_depth
        )
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_path")
    return tree


@router.get("/file", response_model=FileReadOut)
async def get_file(
    project_id: int = Query(..., description="Projecto al que pertenece el archivo"),
    path: str = Query(..., max_length=512),
    user: User = Depends(get_current_user),
) -> FileReadOut:
    """Lee un archivo del workspace como texto UTF-8."""
    project = await _resolve_owned_project(project_id, user)
    try:
        content = await file_service.read_file(user.profile, project.slug, path=path)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_path")
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file_not_found")
    return FileReadOut(path=path, content=content)


@router.put("/file", response_model=FileWriteOut)
async def put_file(
    body: FileWriteBody,
    project_id: int = Query(..., description="Projecto al que pertenece el archivo"),
    path: str = Query(..., max_length=512),
    user: User = Depends(get_current_user),
) -> FileWriteOut:
    """Escribe `content` en `path` dentro del workspace. Crea dirs padres."""
    project = await _resolve_owned_project(project_id, user)
    try:
        await file_service.write_file(
            user.profile, project.slug, path=path, content=body.content
        )
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_path")
    return FileWriteOut(path=path, ok=True)
