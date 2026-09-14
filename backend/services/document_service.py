"""Registro y almacenamiento de documentos fuente del proyecto.

Dos vías de entrada:

  - ``register_uploaded``: el binario viene en el request (POST /upload).
    Calcula ``sha256`` de los bytes, escribe al workspace vía
    ``file_service.write_bytes`` y persiste ``ProjectDocument``.
  - ``scan_workspace``: los archivos ya viven out-of-band en el workspace host
    (copiados al mount sin pasar por la API). Camina el dir host, calcula
    ``sha256`` de cada archivo y registra los no presentes.

``sha256`` es la clave de dedupe (unique por ``project_id``) y la clave del
cache de parseo (Fase B). La preparación pura (validar extensión, calcular
hash/mime/size, construir el modelo) vive en ``prepare_document``, separada de
DB/filesystem para que el smoke la ejerza sin levantar el stack.

``parser_hint`` (auto | docling | glm-ocr, Fase C) es la preferencia de parser
del documento: viaja desde /upload + /scan hasta ``ProjectDocument`` y la captura
la lee vía ``parser_hint_map`` para pasarla a ``parse_document_cached``.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.models import DocumentParse, ProjectDocument
from backend.services import file_service

# Extensiones admitidas como documentos fuente. Otras se rechazan en upload
# para evitar que el agente trate de parsear binarios arbitrarios.
_ALLOWED_EXT: set[str] = {
    ".pdf", ".docx", ".doc", ".txt", ".md", ".markdown",
    ".html", ".htm", ".xlsx", ".xls", ".pptx", ".ppt",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
}

# Valores válidos para parser_hint (preferencia de parser por documento, Fase C).
# "auto" deja al router decidir por extension + capa de texto; "docling"/
# "glm-ocr" fuerzan un parser (útil para PDFs born-digital con páginas rotadas
# cuya capa de texto no dispara la heurística automática).
_VALID_PARSER_HINTS: set[str] = {"auto", "docling", "glm-ocr"}


class UnsupportedFileError(ValueError):
    """Extensión no admitida como documento fuente."""


class WriteError(RuntimeError):
    """El archivo no pudo escribirse al workspace del container."""


class PathEscapeError(ValueError):
    """El path intenta escapar del root del workspace del proyecto."""


@dataclass
class RegisterResult:
    """Resultado de registrar un documento.

    ``created`` es False cuando el ``(project_id, sha256)`` ya existía (dedupe):
    se devuelve el documento preexistente sin reescribir ni reinsertar.
    """

    document: ProjectDocument
    created: bool


def _ext(filename: str) -> str:
    return os.path.splitext(filename)[1].lower()


def _guess_mime(filename: str) -> str | None:
    guess, _ = mimetypes.guess_type(filename)
    return guess


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_host_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):  # chunks de 1 MiB
            h.update(chunk)
    return h.hexdigest()


def _normalize_hint(parser_hint: str) -> str:
    """Cualquier valor fuera de _VALID_PARSER_HINTS degrada a "auto" (lenient)."""
    return parser_hint if parser_hint in _VALID_PARSER_HINTS else "auto"


def _default_rel_path(filename: str) -> str:
    """Path por defecto para uploads sueltos: el filename directo en la raíz del workspace.

    No creamos una carpeta ``documents/`` artificial: el usuario organiza los
    archivos en carpetas después (move entre carpetas, futuro). El ``rel_path``
    sigue siendo mutable, así que esto no ata al documento a la raíz.
    """
    return filename


def prepare_document(
    project_id: int,
    filename: str,
    data: bytes,
    rel_path: str | None = None,
    parser_hint: str = "auto",
) -> ProjectDocument:
    """Construye un ``ProjectDocument`` sin tocar DB ni filesystem.

    Valida la extensión, calcula ``sha256``/``mime``/``size``, fija
    ``parse_status='pending'`` y ``parser_hint`` (normalizado). Lanza
    ``UnsupportedFileError`` si la extensión no está admitida. Pura → testeable
    sin levantar el stack.
    """
    ext = _ext(filename)
    if ext not in _ALLOWED_EXT:
        raise UnsupportedFileError(filename)
    return ProjectDocument(
        project_id=project_id,
        rel_path=rel_path or _default_rel_path(filename),
        filename=filename,
        extension=ext,
        mime=_guess_mime(filename),
        size_bytes=len(data),
        sha256=_sha256_bytes(data),
        page_count=None,
        parse_status="pending",
        parser_hint=_normalize_hint(parser_hint),
    )


async def _find_by_sha(db, project_id: int, sha256: str) -> ProjectDocument | None:
    return await db.scalar(
        select(ProjectDocument).where(
            ProjectDocument.project_id == project_id,
            ProjectDocument.sha256 == sha256,
        )
    )


async def register_uploaded(
    *,
    project_id: int,
    profile: str,
    slug: str,
    filename: str,
    data: bytes,
    rel_path: str | None = None,
    parser_hint: str = "auto",
) -> RegisterResult:
    """Registra un binario subido vía API.

    Flujo: dedupe-check → write al workspace → re-check + insert. Escribir
    antes de insertar evita dejar una fila huérfana si el container cae; el
    re-check tras la escritura cubre la carrera entre dos uploads simultáneos
    del mismo archivo.
    """
    doc = prepare_document(project_id, filename, data, rel_path, parser_hint)

    # 1) Dedupe barato antes de tocar el filesystem.
    async with AsyncSessionLocal() as db:
        existing = await _find_by_sha(db, project_id, doc.sha256)
    if existing is not None:
        return RegisterResult(document=existing, created=False)

    # 2) Write al workspace (aún sin fila); el container hace mkdir -p.
    try:
        await file_service.write_bytes(profile, slug, doc.rel_path, data)
    except ValueError as exc:
        raise PathEscapeError(str(exc)) from exc
    except RuntimeError as exc:
        raise WriteError(str(exc)) from exc

    # 3) Insert con re-check de carrera.
    async with AsyncSessionLocal() as db:
        existing = await _find_by_sha(db, project_id, doc.sha256)
        if existing is not None:
            return RegisterResult(document=existing, created=False)
        db.add(doc)
        await db.commit()
        await db.refresh(doc)
    return RegisterResult(document=doc, created=True)


async def scan_workspace(
    *,
    project_id: int,
    profile: str,
    slug: str,
    base_rel_path: str = ".",
    recursive: bool = True,
    parser_hint: str = "auto",
) -> list[RegisterResult]:
    """Registra archivos ya presentes en el workspace host (sin re-subir).

    Camina ``{workspaces_root}/{profile}/{slug}/{base_rel_path}``, calcula
    ``sha256`` de cada archivo con extensión admitida y registra los no
    presentes. Omite silenciosamente los que ya están registrados (dedupe).
    ``parser_hint`` aplica a todos los archivos registrados en este escaneo.
    """
    hint = _normalize_hint(parser_hint)
    host_root = (settings.workspaces_root / profile / slug).resolve()
    base = (host_root / base_rel_path).resolve()
    try:
        base.relative_to(host_root)
    except ValueError as exc:
        raise PathEscapeError(base_rel_path) from exc
    if not base.is_dir():
        raise FileNotFoundError(str(base))

    if recursive:
        found = [
            Path(dirpath) / name
            for dirpath, _, names in os.walk(base)
            for name in names
        ]
    else:
        found = [p for p in base.iterdir() if p.is_file()]
    found.sort()

    results: list[RegisterResult] = []
    for path in found:
        if _ext(path.name) not in _ALLOWED_EXT:
            continue
        sha = _sha256_host_file(str(path))
        async with AsyncSessionLocal() as db:
            existing = await _find_by_sha(db, project_id, sha)
            if existing is not None:
                results.append(RegisterResult(document=existing, created=False))
                continue
            doc = ProjectDocument(
                project_id=project_id,
                rel_path=path.relative_to(host_root).as_posix(),
                filename=path.name,
                extension=_ext(path.name),
                mime=_guess_mime(path.name),
                size_bytes=path.stat().st_size,
                sha256=sha,
                parse_status="pending",
                parser_hint=hint,
            )
            db.add(doc)
            await db.commit()
            await db.refresh(doc)
        results.append(RegisterResult(document=doc, created=True))
    return results


async def ensure_document_registered(
    *,
    project_id: int,
    host_root: Path,
    path: Path,
    parser_hint: str = "auto",
) -> RegisterResult:
    """Registra un archivo del workspace host si aún no está en el catálogo.

    Upsert idempotente por ``(project_id, sha256)``: si el contenido ya está
    registrado devuelve la fila existente y corrige ``rel_path`` cuando el
    archivo cambió de carpeta. Es la vía por la que la ingesta del agente
    mantiene el catálogo sincronizado con lo que realmente procesa: sin ella,
    documentos que llegaron al workspace fuera de /upload y /scan sostienen
    requerimientos pero quedan invisibles para el conteo de fuentes y el RAG
    del SRS (el join de retrieval pasa por project_documents). No valida
    extensión: la llamada proviene de discover_documents, que ya filtró.
    """
    root = host_root.resolve()
    rel = path.resolve().relative_to(root).as_posix()
    sha = _sha256_host_file(str(path))
    async with AsyncSessionLocal() as db:
        existing = await _find_by_sha(db, project_id, sha)
        if existing is not None:
            if existing.rel_path != rel:
                existing.rel_path = rel
                await db.commit()
            return RegisterResult(document=existing, created=False)
        doc = ProjectDocument(
            project_id=project_id,
            rel_path=rel,
            filename=path.name,
            extension=_ext(path.name),
            mime=_guess_mime(path.name),
            size_bytes=path.stat().st_size,
            sha256=sha,
            parse_status="pending",
            parser_hint=_normalize_hint(parser_hint),
        )
        db.add(doc)
        await db.commit()
        await db.refresh(doc)
    return RegisterResult(document=doc, created=True)


async def mark_document_parsed(*, document_id: int, sha256: str) -> bool:
    """Completa la fila del catálogo tras un parseo exitoso de la captura.

    Copia ``parser_used``/``page_count`` desde el cache global
    (``DocumentParse``, keyed por sha256) y marca ``parse_status='ready'``.
    Devuelve False si la fila ya no existe (p. ej. borrada a mitad de captura).
    """
    async with AsyncSessionLocal() as db:
        doc = await db.get(ProjectDocument, document_id)
        if doc is None:
            return False
        parse = await db.scalar(
            select(DocumentParse).where(DocumentParse.sha256 == sha256)
        )
        doc.parse_status = "ready"
        doc.parsed_at = datetime.utcnow()
        doc.error = None
        if parse is not None:
            doc.parser_used = parse.parser_used
            if parse.page_count:
                doc.page_count = parse.page_count
        await db.commit()
    return True


async def list_documents(project_id: int) -> list[ProjectDocument]:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(ProjectDocument)
                .where(ProjectDocument.project_id == project_id)
                .order_by(ProjectDocument.rel_path.asc())
            )
        ).scalars().all()
    return list(rows)


async def get_document(document_id: int, project_id: int) -> ProjectDocument | None:
    async with AsyncSessionLocal() as db:
        doc = await db.get(ProjectDocument, document_id)
    if doc is None or doc.project_id != project_id:
        return None
    return doc


async def parser_hint_map(
    project_id: int, workspace_root: Path
) -> dict[str, str]:
    """``{abs_path: parser_hint}`` para los docs del proyecto con hint explícito.

    Usado por la captura para pasar el hint por-documento a
    ``parse_document_cached`` (Fase C). Solo incluye docs con hint en
    (``docling``, ``glm-ocr``); los ``auto`` usan el default del router. Las
    claves son paths absolutos resueltos para matchear contra los paths que
    devuelve ``discover_documents`` en el pipeline.
    """
    out: dict[str, str] = {}
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(ProjectDocument.rel_path, ProjectDocument.parser_hint).where(
                    ProjectDocument.project_id == project_id,
                    ProjectDocument.parser_hint.in_(("docling", "glm-ocr")),
                )
            )
        ).all()
    root = workspace_root.resolve()
    for rel, hint in rows:
        out[str((root / rel).resolve())] = hint
    return out


async def delete_document(
    *,
    document_id: int,
    project_id: int,
    profile: str,
    slug: str,
    purge: bool = False,
) -> bool:
    """Borra el registro. Si ``purge``, borra también el archivo del workspace.

    Devuelve False si no existe o no pertenece al proyecto (→ 404 en el router).
    El purge es best-effort: si el archivo ya no existe, no se propaga error.
    """
    async with AsyncSessionLocal() as db:
        doc = await db.get(ProjectDocument, document_id)
        if doc is None or doc.project_id != project_id:
            return False
        rel = doc.rel_path
        await db.delete(doc)
        await db.commit()
    if purge:
        try:
            await file_service.delete_file(profile, slug, rel)
        except RuntimeError:
            # best-effort: el registro ya se borró; un fallo de purge no revierte.
            pass
    return True
