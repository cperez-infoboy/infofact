"""Router de documentos fuente del proyecto: upload binario + scan + CRUD.

Endpoints:
  POST   /api/documents/upload          sube un binario (multipart) + registra
  POST   /api/documents/scan            registra archivos ya presentes en el workspace
  GET    /api/documents?project_id=     lista documentos con parse_status
  GET    /api/documents/{id}?project_id= detalle
  DELETE /api/documents/{id}?project_id=&purge=  borra registro (y archivo si purge)

``parser_hint`` (auto | docling | glm-ocr, Fase C) viaja en /upload y /scan: le
dice al router de parsers cómo tratar el documento. "auto" deja decidir al
router por extension + capa de texto; un valor explicito fuerza el parser
(útil para PDFs born-digital con páginas rotadas que la heurística no pesca).

Ownership: cada op valida project -> user (mismo helper que routers/workspaces.py).
404 sin filtrar existencia, igual que el resto de la API.
"""
from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel

from backend.config import settings
from backend.deps import get_current_user
from backend.models import ProjectDocument, User
from backend.routers.workspaces import _resolve_owned_project
from backend.services import document_service

router = APIRouter()

_VALID_PARSER_HINTS = {"auto", "docling", "glm-ocr"}


class DocumentOut(BaseModel):
    id: int
    project_id: int
    rel_path: str
    filename: str
    extension: str
    mime: str | None
    size_bytes: int
    sha256: str
    page_count: int | None
    parse_status: str
    parser_used: str | None
    parser_hint: str
    error: str | None
    created_at: str | None


class ScanBody(BaseModel):
    project_id: int
    rel_path: str = "."
    recursive: bool = True
    parser_hint: str = "auto"


class ScanResult(BaseModel):
    registered: list[DocumentOut]
    skipped: int


def _doc_out(d: ProjectDocument) -> DocumentOut:
    return DocumentOut(
        id=d.id,
        project_id=d.project_id,
        rel_path=d.rel_path,
        filename=d.filename,
        extension=d.extension,
        mime=d.mime,
        size_bytes=d.size_bytes,
        sha256=d.sha256,
        page_count=d.page_count,
        parse_status=d.parse_status,
        parser_used=d.parser_used,
        parser_hint=d.parser_hint or "auto",
        error=d.error,
        created_at=d.created_at.isoformat() if d.created_at else None,
    )


def _validate_hint(parser_hint: str) -> str:
    if parser_hint not in _VALID_PARSER_HINTS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"invalid_parser_hint: {parser_hint}",
        )
    return parser_hint


@router.post("/upload", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    project_id: int = Form(...),
    file: UploadFile = File(...),
    rel_path: str | None = Form(None),
    parser_hint: str = Form("auto"),
    user: User = Depends(get_current_user),
) -> DocumentOut:
    """Sube un documento binario al workspace del proyecto y lo registra.

    ``parser_hint`` (auto | docling | glm-ocr) fuerza el parser para este
    documento; se propaga a la captura vía ``ProjectDocument.parser_hint``.
    """
    project = await _resolve_owned_project(project_id, user)
    _validate_hint(parser_hint)
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file_too_large")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty_file")
    filename = file.filename or "document"
    try:
        result = await document_service.register_uploaded(
            project_id=project.id,
            profile=user.profile,
            slug=project.slug,
            filename=filename,
            data=data,
            rel_path=rel_path,
            parser_hint=parser_hint,
        )
    except document_service.UnsupportedFileError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported_file_type")
    except document_service.PathEscapeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_path")
    except document_service.WriteError as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"write_failed: {exc}"
        )
    if not result.created:
        raise HTTPException(status.HTTP_409_CONFLICT, "duplicate_document")
    return _doc_out(result.document)


@router.post("/scan", response_model=ScanResult)
async def scan_documents(
    body: ScanBody,
    user: User = Depends(get_current_user),
) -> ScanResult:
    """Registra archivos ya presentes en el workspace (sin re-subir).

    ``parser_hint`` aplica a todos los archivos registrados en este escaneo.
    """
    project = await _resolve_owned_project(body.project_id, user)
    _validate_hint(body.parser_hint)
    try:
        results = await document_service.scan_workspace(
            project_id=project.id,
            profile=user.profile,
            slug=project.slug,
            base_rel_path=body.rel_path,
            recursive=body.recursive,
            parser_hint=body.parser_hint,
        )
    except document_service.PathEscapeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_path")
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dir_not_found")
    return ScanResult(
        registered=[_doc_out(r.document) for r in results if r.created],
        skipped=sum(1 for r in results if not r.created),
    )


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    project_id: int = Query(...),
    user: User = Depends(get_current_user),
) -> list[DocumentOut]:
    project = await _resolve_owned_project(project_id, user)
    docs = await document_service.list_documents(project.id)
    return [_doc_out(d) for d in docs]


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: int,
    project_id: int = Query(...),
    user: User = Depends(get_current_user),
) -> DocumentOut:
    project = await _resolve_owned_project(project_id, user)
    doc = await document_service.get_document(document_id, project.id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return _doc_out(doc)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: int,
    project_id: int = Query(...),
    purge: bool = Query(False),
    user: User = Depends(get_current_user),
) -> None:
    project = await _resolve_owned_project(project_id, user)
    deleted = await document_service.delete_document(
        document_id=document_id,
        project_id=project.id,
        profile=user.profile,
        slug=project.slug,
        purge=purge,
    )
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
