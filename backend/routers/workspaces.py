"""Router de workspace: explorar, editar y gestionar archivos del container
del usuario.

Endpoints:
  GET  /api/workspaces/tree?project_id=...&path=.  árbol JSON del workspace
  GET  /api/workspaces/file?project_id=...&path=... lee un archivo
  PUT  /api/workspaces/file?project_id=...&path=... escribe un archivo
  POST /api/workspaces/fs/mkdir|file|move|copy          gestión (explorador)
  DELETE /api/workspaces/fs/entry?project_id=...&path=...  borrar archivo/carpeta
  GET  /api/workspaces/download?project_id=...&path=...  descarga binaria

El profile sale del usuario autenticado (Depends(get_current_user)); el
project_id determina el subdirectorio del workspace (un proyecto = un dir
propio bajo {workspaces_host_root}/{profile}/{slug}). Cada operación valida
ownership del proyecto antes de tocar el filesystem.

Seguridad: TODO input de `path` se normaliza via _safe_path dentro de
file_service; un intento de traversal (`../../etc/passwd`) se rechaza con
ValueError -> HTTPException 400 "invalid_path".
"""
from __future__ import annotations

import mimetypes
import os
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
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


class FsPathBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=512)


class FsMoveBody(BaseModel):
    src: str = Field(..., min_length=1, max_length=512)
    dst: str = Field(..., min_length=1, max_length=512)


class FsCopyBody(BaseModel):
    src: str = Field(..., min_length=1, max_length=512)
    dst_dir: str = Field(..., min_length=1, max_length=512)


class FsOpOut(BaseModel):
    path: str | None = None
    ok: bool = True


# Estado HTTP por código de error de FsOpError (los desconocidos → 500).
_FS_ERROR_STATUS: dict[str, int] = {
    "is_root": status.HTTP_400_BAD_REQUEST,
    "not_found": status.HTTP_404_NOT_FOUND,
    "already_exists": status.HTTP_409_CONFLICT,
    "target_exists": status.HTTP_409_CONFLICT,
    "target_inside_source": status.HTTP_409_CONFLICT,
}


def _fs_error_to_http(exc: file_service.FsOpError) -> HTTPException:
    return HTTPException(_FS_ERROR_STATUS.get(exc.code, 500), exc.code)


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


# --- Gestión del explorador (mkdir / nuevo archivo / mover / copiar / borrar)


@router.post("/fs/mkdir", response_model=FsOpOut)
async def fs_mkdir(
    body: FsPathBody,
    project_id: int = Query(..., description="Projecto dueño del workspace"),
    user: User = Depends(get_current_user),
) -> FsOpOut:
    """Crea una carpeta. Falla con 409 already_exists si ya existe."""
    project = await _resolve_owned_project(project_id, user)
    try:
        await file_service.create_dir(user.profile, project.slug, path=body.path)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_path")
    except file_service.FsOpError as exc:
        raise _fs_error_to_http(exc)
    return FsOpOut(path=body.path)


@router.post("/fs/file", response_model=FsOpOut)
async def fs_new_file(
    body: FsPathBody,
    project_id: int = Query(..., description="Projecto dueño del workspace"),
    user: User = Depends(get_current_user),
) -> FsOpOut:
    """Crea un archivo vacío. Falla con 409 already_exists si ya existe."""
    project = await _resolve_owned_project(project_id, user)
    try:
        await file_service.create_file(user.profile, project.slug, path=body.path)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_path")
    except file_service.FsOpError as exc:
        raise _fs_error_to_http(exc)
    return FsOpOut(path=body.path)


@router.post("/fs/move", response_model=FsOpOut)
async def fs_move(
    body: FsMoveBody,
    project_id: int = Query(..., description="Projecto dueño del workspace"),
    user: User = Depends(get_current_user),
) -> FsOpOut:
    """Mueve/renombra `src` a `dst` (path destino completo). No pisa destino."""
    project = await _resolve_owned_project(project_id, user)
    try:
        await file_service.move_entry(
            user.profile, project.slug, src=body.src, dst=body.dst
        )
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_path")
    except file_service.FsOpError as exc:
        raise _fs_error_to_http(exc)
    return FsOpOut(path=body.dst)


@router.post("/fs/copy", response_model=FsOpOut)
async def fs_copy(
    body: FsCopyBody,
    project_id: int = Query(..., description="Projecto dueño del workspace"),
    user: User = Depends(get_current_user),
) -> FsOpOut:
    """Copia `src` dentro de `dst_dir`, con sufijo ` copia` ante colisión.

    Devuelve el path final de la copia (puede diferir del nombre original).
    """
    project = await _resolve_owned_project(project_id, user)
    try:
        final = await file_service.copy_entry(
            user.profile, project.slug, src=body.src, dst_dir=body.dst_dir
        )
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_path")
    except file_service.FsOpError as exc:
        raise _fs_error_to_http(exc)
    return FsOpOut(path=final)


@router.delete("/fs/entry", response_model=FsOpOut)
async def fs_delete(
    project_id: int = Query(..., description="Projecto dueño del workspace"),
    path: str = Query(..., min_length=1, max_length=512),
    user: User = Depends(get_current_user),
) -> FsOpOut:
    """Borra un archivo o carpeta (recursivo). La raíz `.` es intocable."""
    project = await _resolve_owned_project(project_id, user)
    try:
        await file_service.delete_entry(user.profile, project.slug, path=path)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_path")
    except file_service.FsOpError as exc:
        raise _fs_error_to_http(exc)
    return FsOpOut(path=path)


@router.get("/download")
async def download_file(
    project_id: int = Query(..., description="Projecto dueño del workspace"),
    path: str = Query(..., min_length=1, max_length=512),
    user: User = Depends(get_current_user),
) -> Response:
    """Descarga un archivo como adjunto (binario, con media type adivinado)."""
    project = await _resolve_owned_project(project_id, user)
    try:
        data = await file_service.read_bytes(user.profile, project.slug, path=path)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_path")
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file_not_found")

    filename = os.path.basename(path.rstrip("/")) or "descarga"
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    # filename ASCII de fallback + filename* UTF-8 para nombres no-ASCII.
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "download"
    disposition = (
        f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urlquote(filename)}"
    )
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )
