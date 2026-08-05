"""Tools de LECTURA de documentos fuente para el orquestador (Fase D).

``make_document_read_tools(project_id)`` cierra sobre ``project_id`` (mismo
patron que ``make_requirements_read_tools``): el modelo nunca ve ``project_id``
como argumento y no puede spoofear documentos de otro proyecto. Cada tool abre
su propia ``AsyncSessionLocal``, llama a la capa de retrieval y devuelve un dict
(errores -> ``{"error": ...}``).

``document_id`` es el path absoluto en el container
(``/workspaces/{slug}/{rel_path}``), el mismo que ``RequirementItem.source``.
La resolucion ``document_id -> parse_id`` es por DB (strip del prefijo del
workspace -> ``rel_path`` -> JOIN ``project_documents`` por sha256), sin leer el
filesystem: el backend no ve ``/workspaces/...`` (eso vive en el container del
agente).

Estas tools son de LECTURA: no exponen ``read_file`` / ``execute`` del sandbox,
ni reemplazan la delegacion de la captura al subagente. Solo permiten verificar
un requerimiento contra su fuente y citar pasajes.
"""
from __future__ import annotations

from langchain_core.tools import tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.retrieval import store as retrieval
from backend.config import WORKSPACE_CONTAINER_PATH
from backend.database import AsyncSessionLocal
from backend.models.document_parse import DocumentParse
from backend.models.project import Project
from backend.models.project_document import ProjectDocument


def _candidate_rel_paths(slug: str, document_id: str) -> list[str]:
    """Paths relativos candidatos que ``document_id`` podria representar.

    El agente puede pasar el path absoluto (``/workspaces/{slug}/file.pdf``) o
    un ``rel_path`` directo. Probamos ambos: el as-is y el strip del prefijo del
    workspace.
    """
    candidates = [document_id]
    prefix = f"{WORKSPACE_CONTAINER_PATH}/{slug}/"
    if document_id.startswith(prefix):
        candidates.append(document_id[len(prefix):])
    elif document_id.startswith(f"{WORKSPACE_CONTAINER_PATH}/"):
        # Path absoluto cuyo primer segmento no es el slug esperado: igual
        # quitamos /workspaces/<segmento>/ para quedar con el rel_path.
        rest = document_id[len(WORKSPACE_CONTAINER_PATH) + 1:]
        if "/" in rest:
            candidates.append(rest.split("/", 1)[1])
    return candidates


async def _resolve_parse_id(
    session: AsyncSession, project_id: int, document_id: str
) -> int | None:
    """``document_id`` (path) -> ``parse_id``, scoping por proyecto via
    ``project_documents`` (sha256)."""
    proj = await session.get(Project, project_id)
    slug = proj.slug if proj else ""
    for rel in _candidate_rel_paths(slug, document_id):
        parse_id = (
            await session.execute(
                select(DocumentParse.id)
                .join(
                    ProjectDocument,
                    ProjectDocument.sha256 == DocumentParse.sha256,
                )
                .where(
                    ProjectDocument.project_id == project_id,
                    ProjectDocument.rel_path == rel,
                )
            )
        ).scalar_one_or_none()
        if parse_id is not None:
            return parse_id
    return None


def make_document_read_tools(project_id: int) -> list:
    """Construye las tools de lectura de documentos bound a un proyecto."""

    @tool
    async def list_documents() -> dict:
        """Lista los documentos fuente del proyecto con su estado de parseo.

        Cada item incluye ``document_id`` (path absoluto en el workspace),
        ``parse_status`` (pending|parsing|ready|failed) y ``page_count``. Usa
        este ``document_id`` en ``get_document_section`` y
        ``get_document_passage``.
        """
        try:
            async with AsyncSessionLocal() as session:
                proj = await session.get(Project, project_id)
                slug = proj.slug if proj else ""
                rows = (
                    await session.execute(
                        select(
                            ProjectDocument.rel_path,
                            ProjectDocument.filename,
                            ProjectDocument.parse_status,
                            ProjectDocument.page_count,
                            DocumentParse.id,
                        )
                        .join(
                            DocumentParse,
                            DocumentParse.sha256 == ProjectDocument.sha256,
                            isouter=True,
                        )
                        .where(ProjectDocument.project_id == project_id)
                        .order_by(ProjectDocument.rel_path)
                    )
                ).all()
                items = [
                    {
                        "document_id": f"{WORKSPACE_CONTAINER_PATH}/{slug}/{r.rel_path}",
                        "rel_path": r.rel_path,
                        "filename": r.filename,
                        "parse_status": r.parse_status,
                        "page_count": r.page_count,
                        "parsed": r.id is not None,
                    }
                    for r in rows
                ]
                return {"count": len(items), "items": items}
        except Exception as exc:  # noqa: BLE001 -- superficie al modelo
            return {"error": f"list_documents failed: {exc}"}

    @tool
    async def search_documents(query: str, top_k: int = 5) -> dict:
        """Busqueda semantica top-k sobre los documentos fuente del proyecto.

        Devuelve los chunks mas relevantes a ``query`` con su texto,
        ``document_id``, ``section_path``, ``page`` y ``score``. Usalo para
        localizar donde un tema o requerimiento se trata en los documentos, o
        para verificar si un requerimiento esta respaldado por la fuente.
        """
        try:
            hits = await retrieval.search(project_id, query, top_k=top_k)
            return {
                "count": len(hits),
                "items": [
                    {
                        "text": h.text,
                        "document_id": h.document_id,
                        "section_path": h.section_path,
                        "page": h.page,
                        "score": round(h.score, 4),
                    }
                    for h in hits
                ],
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"search_documents failed: {exc}"}

    @tool
    async def get_document_section(document_id: str, section_id: str) -> dict:
        """Metadatos de una seccion de un documento (id, titulo, nivel, pagina).

        ``document_id`` es el path absoluto que devuelve ``list_documents`` o
        ``search_documents``. ``section_id`` es el id de la seccion o parte del
        titulo. Para leer texto dentro de la seccion, usa
        ``get_document_passage`` con un quote.
        """
        try:
            async with AsyncSessionLocal() as session:
                parse_id = await _resolve_parse_id(session, project_id, document_id)
                if parse_id is None:
                    return {"error": f"document not found: {document_id}"}
                result = await retrieval.get_section(
                    parse_id, section_id, session=session
                )
                return result or {"error": f"section not found: {section_id}"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"get_document_section failed: {exc}"}

    @tool
    async def get_document_passage(
        document_id: str, quote: str, padding: int = 200
    ) -> dict:
        """Pasaje verbatim + contexto de un ``quote`` dentro de un documento.

        Localiza el quote (exacto o tolerante a typos) en el markdown cacheado y
        devuelve el slice con ``padding`` caracteres de contexto. Usalo para
        verificar que un requerimiento esta literalmente en la fuente y citar el
        pasaje exacto con su contexto.
        """
        try:
            async with AsyncSessionLocal() as session:
                parse_id = await _resolve_parse_id(session, project_id, document_id)
                if parse_id is None:
                    return {"error": f"document not found: {document_id}"}
                result = await retrieval.get_passage(
                    parse_id, quote, padding=padding, session=session
                )
                return result or {"error": f"quote not found: {quote[:60]}"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"get_document_passage failed: {exc}"}

    return [
        list_documents,
        search_documents,
        get_document_section,
        get_document_passage,
    ]
