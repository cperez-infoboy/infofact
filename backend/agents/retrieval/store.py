"""Vector store liviano sobre SQLite para retrieval semantico de documentos.

Fase D del plan de ingesta mejorada. Sin FAISS/Milvus: los embeddings viven
como bytes float32 en ``document_embeddings`` y ``search`` hace cosine en
Python/numpy sobre los chunks del proyecto (JOIN via sha256). Suficiente para
pocos documentos por proyecto (~10k chunks en menos de 15ms).

Reutiliza ``consolidation.embed_texts`` (mismo modelo
``paraphrase-multilingual-MiniLM-L12-v2``, mismo singleton): cero modelos
nuevos, y los embeddings son compatibles con los que ya usa dedup/contradiccion.

El ``document_id`` que devuelve ``search`` es el path absoluto en el container
(``/workspaces/{slug}/{rel_path}``), el mismo que ``RequirementItem.source``
usa, para que el agente pueda pasarselo de vuelta a ``get_passage`` /
``get_section`` sin conversion.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.pipelines.consolidation import embed_texts
from backend.agents.pipelines.ingestion import Chunk
from backend.config import WORKSPACE_CONTAINER_PATH
from backend.database import AsyncSessionLocal
from backend.models.document_embedding import DocumentEmbedding
from backend.models.document_parse import DocumentParse
from backend.models.project import Project
from backend.models.project_document import ProjectDocument

logger = logging.getLogger(__name__)


@dataclass
class SearchHit:
    """Resultado de search: un chunk relevante + su localizacion."""

    text: str
    parse_id: int
    document_id: str  # path absoluto en el container
    section_path: str
    page: int
    score: float


# ---------------------------------------------------------------------------
# (De)serializacion de vectores
# ---------------------------------------------------------------------------

def _vec_to_bytes(vec) -> bytes:
    import numpy as np

    return np.asarray(vec, dtype="float32").tobytes()


def _bytes_to_vec(b: bytes):
    import numpy as np

    return np.frombuffer(b, dtype="float32")


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

async def index_parse(
    parse_id: int,
    chunks: list[Chunk],
    *,
    session: AsyncSession | None = None,
) -> int:
    """Embeddea cada chunk de un parse y lo persiste. Idempotente.

    Borra los embeddings previos del parse antes de insertar, asi un re-parseo
    (version obsoleta de Docling) no deja embeddings huerfanos. Skipea chunks
    sin texto. Retorna la cantidad de chunks indexados.

    Solo campos derivados del contenido (chunk_text, section_path, page,
    chunk_index): ninguno depende del path, asi el cache global (keyed por
    parse_id / sha256) es valido para todos los proyectos que reusan el mismo
    contenido. ``embed_texts`` (sentence-transformers) es sincrono y CPU-bound,
    por eso corre off-loop via ``asyncio.to_thread``.
    """
    owns_session = session is None
    if owns_session:
        session = AsyncSessionLocal()
    assert session is not None
    try:
        # Idempotencia: limpiar embeddings previos del parse.
        await session.execute(
            delete(DocumentEmbedding).where(
                DocumentEmbedding.parse_id == parse_id
            )
        )
        indexable = [c for c in chunks if c.text and c.text.strip()]
        if not indexable:
            await session.commit()
            logger.info(
                "index_parse: 0 chunks indexables para parse_id=%d", parse_id
            )
            return 0

        texts = [c.text for c in indexable]
        vectors = await asyncio.to_thread(embed_texts, texts)
        rows = [
            DocumentEmbedding(
                parse_id=parse_id,
                chunk_index=c.index,
                chunk_text=c.text,
                embedding=_vec_to_bytes(vectors[i]),
                section_path=c.section_path or "",
                page=c.page or 0,
            )
            for i, c in enumerate(indexable)
        ]
        session.add_all(rows)
        await session.commit()
        logger.info(
            "index_parse: %d chunks indexados para parse_id=%d",
            len(rows),
            parse_id,
        )
        return len(rows)
    finally:
        if owns_session:
            await session.close()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search(
    project_id: int,
    query: str,
    *,
    top_k: int = 5,
    session: AsyncSession | None = None,
    used_in_capture_only: bool = False,
) -> list[SearchHit]:
    """Retrieval semantico top-k sobre los documentos del proyecto.

    Cuando ``used_in_capture_only=True``, filtra solo documentos marcados como
    usados en la captura de requerimientos (``ProjectDocument.used_in_capture``).
    Esto asegura que el SRS agent solo consulte documentos que generaron los
    requerimientos, no documentos presentes en el proyecto sin capturar.

    Scoping (sin cross-leak)::

        document_embeddings
          -> document_parses   (parse_id -> id, trae sha256)
          -> project_documents (sha256 -> sha256, WHERE project_id)
          -> projects          (project_id -> id, trae slug para el path)

    Como ``DocumentParse`` es keyed por sha256 (contenido, global), dos
    proyectos con el mismo PDF comparten filas de embedding, pero el
    ``WHERE project_id`` filtra sin cross-leak. El ``document_id`` se reconstruye
    con el slug del proyecto: cada proyecto ve su propio path absoluto.

    Cosine en numpy sobre los embeddings del proyecto (ya L2-normalizados al
    indexar -> dot product). Retorna los ``top_k`` chunks mas similares.
    """
    owns_session = session is None
    if owns_session:
        session = AsyncSessionLocal()
    assert session is not None
    try:
        import numpy as np

        stmt = (
            select(
                DocumentEmbedding.chunk_text,
                DocumentEmbedding.section_path,
                DocumentEmbedding.page,
                DocumentEmbedding.parse_id,
                DocumentEmbedding.embedding,
                ProjectDocument.rel_path,
                Project.slug,
            )
            .join(
                DocumentParse,
                DocumentEmbedding.parse_id == DocumentParse.id,
            )
            .join(
                ProjectDocument,
                DocumentParse.sha256 == ProjectDocument.sha256,
            )
            .join(Project, Project.id == ProjectDocument.project_id)
            .where(ProjectDocument.project_id == project_id)
        )
        if used_in_capture_only:
            stmt = stmt.where(
                ProjectDocument.used_in_capture == True  # noqa: E712
            )
        rows = (await session.execute(stmt)).all()
        if not rows:
            return []

        matrix = np.vstack([_bytes_to_vec(r.embedding) for r in rows])
        qvec = np.asarray(
            (await asyncio.to_thread(embed_texts, [query]))[0], dtype="float32"
        )
        scores = matrix @ qvec  # ya normalizado -> dot = cosine
        k = min(top_k, len(rows))
        top_idx = np.argsort(-scores)[:k]
        hits: list[SearchHit] = []
        for i in top_idx.tolist():
            r = rows[i]
            hits.append(
                SearchHit(
                    text=r.chunk_text,
                    parse_id=r.parse_id,
                    document_id=f"{WORKSPACE_CONTAINER_PATH}/{r.slug}/{r.rel_path}",
                    section_path=r.section_path or "",
                    page=r.page or 0,
                    score=float(scores[i]),
                )
            )
        return hits
    finally:
        if owns_session:
            await session.close()


# ---------------------------------------------------------------------------
# Section / passage lookup
# ---------------------------------------------------------------------------

async def get_section(
    parse_id: int,
    section_id: str,
    *,
    session: AsyncSession | None = None,
) -> dict | None:
    """Metadatos de una seccion del documento (id, titulo, nivel, pagina,
    resumen).

    Match por ``id`` exacto o por titulo (contiene, case-insensitive). No
    devuelve el texto de la seccion: sin ``char_span`` por seccion no hay forma
    segura de cortar el markdown. Para leer texto dentro de una seccion, usa
    ``get_passage`` con un quote o ``search`` por topico.
    """
    owns_session = session is None
    if owns_session:
        session = AsyncSessionLocal()
    assert session is not None
    try:
        sections_json = (
            await session.execute(
                select(DocumentParse.sections_json).where(
                    DocumentParse.id == parse_id
                )
            )
        ).scalar_one_or_none()
        if not sections_json:
            return None
        sections = json.loads(sections_json or "[]")
        target = section_id.strip().lower()
        for s in sections:
            sid = str(s.get("id", "")).lower()
            title = str(s.get("title", "")).lower()
            if target and (target == sid or target in title):
                return {"parse_id": parse_id, "section": s}
        return None
    finally:
        if owns_session:
            await session.close()


def _locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """Mejor (start, end) de ``quote`` dentro de ``text``.

    Exacto primero (case-sensitive, luego case-insensitive). Si no hay match
    exacto, fuzzy via ``rapidfuzz.fuzz.partial_ratio_alignment`` (tolera typos y
    pequenas diferencias de transcripcion). Umbral 70/100.
    """
    if not quote or not text:
        return None
    idx = text.find(quote)
    if idx >= 0:
        return idx, idx + len(quote)
    low, ql = text.lower(), quote.lower()
    idx = low.find(ql)
    if idx >= 0:
        return idx, idx + len(quote)
    try:
        from rapidfuzz import fuzz

        align = fuzz.partial_ratio_alignment(quote, text)
    except Exception:  # noqa: BLE001 -- rapidfuzz ausente o API vieja
        return None
    if align is None or align.score < 70:
        return None
    return int(align.src_start), int(align.src_end)


async def get_passage(
    parse_id: int,
    quote: str,
    *,
    padding: int = 200,
    session: AsyncSession | None = None,
) -> dict | None:
    """Pasaje verbatim + contexto alrededor de ``quote`` en el documento.

    Localiza el quote en el ``markdown`` cacheado (exacto o fuzzy) y devuelve el
    slice con ``padding`` caracteres de contexto a cada lado. Bonus: los offsets
    localizados se devuelven como ``char_span_start`` / ``end`` (resuelve para
    el pasaje lo que ``Chunk.char_span`` deferio en Fase B).
    """
    owns_session = session is None
    if owns_session:
        session = AsyncSessionLocal()
    assert session is not None
    try:
        markdown = (
            await session.execute(
                select(DocumentParse.markdown).where(
                    DocumentParse.id == parse_id
                )
            )
        ).scalar_one_or_none()
        if not markdown or not quote:
            return None
        loc = _locate_quote(markdown, quote)
        if loc is None:
            return {"parse_id": parse_id, "quote": quote, "found": False}
        start, end = loc
        lo = max(0, start - padding)
        hi = min(len(markdown), end + padding)
        return {
            "parse_id": parse_id,
            "quote": quote,
            "found": True,
            "char_span_start": start,
            "char_span_end": end,
            "passage": markdown[lo:hi],
        }
    finally:
        if owns_session:
            await session.close()
