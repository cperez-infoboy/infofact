"""Cache de parseo deterministico (Fase B): parsea una vez, reutiliza por sha256.

``parse_document_cached(path)`` reemplaza a ``ingest_document(path)`` en los dos
call sites del pipeline (``requirements_service`` y ``requirements_capture_agent``).
Lookup por sha256 del archivo:

  - HIT  -> deserializa los chunks + StructureMap desde el JSON cacheado y los
            devuelve. Como cada HIT reconstruye objetos Python nuevos desde el
            JSON, el llamador puede mutar el smap (enrich_structure_map,
            extract_conventions) sin riesgo de contaminar a otro proyecto que
            comparta el mismo sha256. No hace falta deepcopy.
  - MISS -> parsea via el router de parsers (Fase C: docling | glm-ocr |
            plaintext), serializa, persiste y devuelve los objetos recien
            parseados.

``parser_hint`` (``auto`` | ``docling`` | ``glm-ocr``) pisa la heuristica del
router (Fase C). La key del cache sigue siendo ``sha256`` + ``parser_version``:
cambiar de parser se refleja en ``parser_version`` (glm-ocr incluye el modelo),
asi un hint distinto sobre el mismo contenido es MISS y se re-parsea.

Solo se cachea la parte deterministica. Las etapas dependientes del proyecto
(enrich / conventions) corren siempre sobre el smap devuelto.

``document_id`` (= str(path)) es dependiente del PATH, no del contenido. Como el
cache es keyed por sha256 (contenido), dos proyectos con el mismo PDF tienen
paths distintos: en el HIT se rebinea el ``document_id`` de los chunks y del
smap al path del llamador, para que ``RequirementItem.source.document_id`` apunte
al path correcto de cada proyecto.

Invalidacion por ``parser_version``: si cambia la version de Docling, el modelo
de glm-ocr o el schema del codigo de parseo, el HIT se degrada a MISS
(re-parseo + update de la fila) para no servir cache obsoleto.

El cache abre su propia AsyncSession (transaccion corta, commit inmediato), asi
persiste aunque el pipeline falle despues: el parseo queda disponible para la
proxima captura.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.parsers.router import choose_parser_name, parse_document
from backend.agents.pipelines.ingestion import (
    Chunk,
    SectionNode,
    StructureMap,
    TableRef,
)
from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.models.document_parse import DocumentParse

logger = logging.getLogger(__name__)

# Bump cuando cambie el FORMATO de los campos cacheados (chunks_json, sections,
# tablas, etc.) para forzar re-parseo de caches escritos por versiones previas
# del codigo. Distinto del parser_used y de la version de Docling.
_PARSE_SCHEMA_VERSION = "2"


# ---------------------------------------------------------------------------
# Hash + claves de invalidacion
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    """SHA-256 de un archivo (streaming, 1 MiB por bloque)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _docling_version() -> str:
    """Version instalada de Docling ('unknown' si no se puede determinar)."""
    try:
        from importlib.metadata import version

        return version("docling")
    except Exception:  # pragma: no cover - depende del entorno
        return "unknown"


def _parser_version_key(parser_used: str) -> str:
    """Clave de invalidacion compuesta.

    docling incluye la version instalada (un upgrade invalida el cache).
    glm-ocr incluye el modelo OCR (cambio de modelo invalida el cache).
    hybrid combina ambas (cualquier cambio invalida el cache).
    plaintext solo usa el schema version (no depende de Docling ni de la API).
    """
    if parser_used == "docling":
        return f"docling={_docling_version()};schema={_PARSE_SCHEMA_VERSION}"
    if parser_used == "glm-ocr":
        return f"glm-ocr={settings.llm_ocr_model};schema={_PARSE_SCHEMA_VERSION}"
    if parser_used == "hybrid":
        return (
            f"hybrid=docling={_docling_version()}"
            f"+glm-ocr={settings.llm_ocr_model};schema={_PARSE_SCHEMA_VERSION}"
        )
    return f"plaintext;schema={_PARSE_SCHEMA_VERSION}"


# ---------------------------------------------------------------------------
# Serializacion Chunk / StructureMap <-> JSON plano
# ---------------------------------------------------------------------------

def _chunk_to_dict(c: Chunk) -> dict:
    return {
        "text": c.text,
        "document_id": c.document_id,
        "section_path": c.section_path,
        "element_kinds": list(c.element_kinds),
        "page": c.page,
        "index": c.index,
    }


def _chunk_from_dict(d: dict) -> Chunk:
    return Chunk(
        text=d["text"],
        document_id=d["document_id"],
        section_path=d.get("section_path", ""),
        element_kinds=tuple(d.get("element_kinds") or ()),
        page=d.get("page"),
        index=d.get("index", 0),
    )


def _section_to_dict(s: SectionNode) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "level": s.level,
        "page": s.page,
        "summary": s.summary,
        "req_likelihood": s.req_likelihood,
        "is_boilerplate": s.is_boilerplate,
    }


def _section_from_dict(d: dict) -> SectionNode:
    return SectionNode(
        id=d["id"],
        title=d["title"],
        level=d["level"],
        page=d["page"],
        summary=d.get("summary", ""),
        req_likelihood=d.get("req_likelihood", "medium"),
        is_boilerplate=d.get("is_boilerplate", False),
    )


def _table_to_dict(t: TableRef) -> dict:
    return {
        "document_id": t.document_id,
        "index": t.index,
        "caption": t.caption,
        "row_count": t.row_count,
        "col_count": t.col_count,
        "page": t.page,
    }


def _table_from_dict(d: dict) -> TableRef:
    return TableRef(
        document_id=d["document_id"],
        index=d["index"],
        caption=d.get("caption"),
        row_count=d["row_count"],
        col_count=d["col_count"],
        page=d.get("page"),
    )


def serialize_chunks(chunks: list[Chunk]) -> str:
    """Lista de chunks -> JSON (incluye chunks image_description)."""
    return json.dumps([_chunk_to_dict(c) for c in chunks], ensure_ascii=False)


def deserialize_chunks(raw: str) -> list[Chunk]:
    return [_chunk_from_dict(d) for d in json.loads(raw)]


def _smap_payload(smap: StructureMap) -> dict:
    """Serializa el StructureMap a campos planos (sin document_id: es del path)."""
    return {
        "sections": [_section_to_dict(s) for s in smap.sections],
        "tables": [_table_to_dict(t) for t in smap.tables],
        "glossary": dict(smap.glossary),
        "full_text": smap.full_text,
        "page_count": smap.page_count,
    }


def _hydrate(row: DocumentParse) -> tuple[list[Chunk], StructureMap]:
    """Reconstruye (chunks, smap) desde una fila cacheada.

    El ``document_id`` queda en un placeholder (el sha256); el llamador lo
    rebinea al path real tras el HIT.
    """
    chunks = deserialize_chunks(row.chunks_json)
    sections = [_section_from_dict(d) for d in json.loads(row.sections_json or "[]")]
    tables = [_table_from_dict(d) for d in json.loads(row.tables_json or "[]")]
    glossary_raw = json.loads(row.glossary_json or "{}")
    smap = StructureMap(
        document_id=row.sha256,
        sections=sections,
        tables=tables,
        glossary=glossary_raw if isinstance(glossary_raw, dict) else {},
        full_text=row.markdown or "",
        page_count=row.page_count or 0,
    )
    return chunks, smap


def _rebind_document_id(
    chunks: list[Chunk], smap: StructureMap, document_id: str
) -> None:
    """Restaura el document_id del llamador (el cache es por contenido, no por path)."""
    smap.document_id = document_id
    for c in chunks:
        c.document_id = document_id


def _fill_row(
    row: DocumentParse,
    *,
    parser_used: str,
    version_key: str,
    chunks: list[Chunk],
    payload: dict,
) -> None:
    row.parser_used = parser_used
    row.parser_version = version_key
    row.markdown = payload["full_text"]
    row.chunks_json = serialize_chunks(chunks)
    row.sections_json = json.dumps(payload["sections"], ensure_ascii=False)
    row.tables_json = json.dumps(payload["tables"], ensure_ascii=False)
    row.glossary_json = json.dumps(payload["glossary"], ensure_ascii=False)
    row.page_count = payload["page_count"]


async def _lookup(session: AsyncSession, sha: str) -> DocumentParse | None:
    result = await session.execute(
        select(DocumentParse).where(DocumentParse.sha256 == sha)
    )
    return result.scalar_one_or_none()


async def _index_safe(
    parse_id: int, chunks: list[Chunk], session: AsyncSession
) -> None:
    """Indexa los chunks para retrieval (Fase D). Best-effort: si falla, el
    parseo ya esta cacheado y la captura sigue; search simplemente no encuentra
    este doc hasta el proximo re-parseo. Import diferido para evitar cualquier
    ciclo de imports con retrieval.store."""
    try:
        from backend.agents.retrieval.store import index_parse

        await index_parse(parse_id, chunks, session=session)
    except Exception:  # noqa: BLE001 -- retrieval no puede romper el cacheo
        logger.exception(
            "index_parse failed for parse_id=%s (non-fatal)", parse_id
        )


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

async def parse_document_cached(
    path: Path, *, session: AsyncSession | None = None,
    parser_hint: str = "auto",
) -> tuple[list[Chunk], StructureMap]:
    """Devuelve (chunks, StructureMap) para ``path``, cacheado por sha256.

    Reemplaza a ``ingest_document(path)`` en los call sites del pipeline. Abre
    su propia AsyncSession (transaccion corta, commit inmediato) salvo que se
    pase ``session`` (para tests con un engine temporal).

    ``parser_hint`` (``auto`` | ``docling`` | ``glm-ocr``) pisa la heuristica del
    router de parsers (Fase C): ``auto`` enruta por extension + capa de texto del
    PDF. El cache persiste independiente del pipeline: si la captura falla
    despues, el parseo queda igual para la proxima corrida.
    """
    sha = await asyncio.to_thread(_sha256_file, path)
    parser_used = await asyncio.to_thread(choose_parser_name, path, parser_hint)
    version_key = _parser_version_key(parser_used)

    owns_session = session is None
    if owns_session:
        session = AsyncSessionLocal()
    assert session is not None
    try:
        row = await _lookup(session, sha)
        if row is not None and row.parser_version == version_key:
            chunks, smap = _hydrate(row)
            _rebind_document_id(chunks, smap, str(path))
            logger.debug("parse cache HIT sha256=%s", sha[:12])
            return chunks, smap

        # MISS (o version obsoleta): parsear via el router + persistir.
        # ``_name`` evita re-decidir el parser dentro de ``parse_document``
        # (ya lo decidio ``choose_parser_name`` arriba; mismo path + mismo hint).
        parsed = await asyncio.to_thread(
            parse_document, path, parser_hint, _name=parser_used
        )
        chunks, smap = parsed.chunks, parsed.smap
        payload = _smap_payload(smap)
        if row is not None:
            # Version obsoleta: actualizar la fila existente in-place.
            _fill_row(
                row, parser_used=parser_used, version_key=version_key,
                chunks=chunks, payload=payload,
            )
            await session.commit()
            await _index_safe(row.id, chunks, session)
            logger.info(
                "parse cache REFRESH sha256=%s (parser_version actualizado)",
                sha[:12],
            )
            return chunks, smap

        new_row = DocumentParse(
            sha256=sha, parser_used=parser_used, parser_version=version_key
        )
        _fill_row(
            new_row, parser_used=parser_used, version_key=version_key,
            chunks=chunks, payload=payload,
        )
        session.add(new_row)
        try:
            await session.commit()
            await _index_safe(new_row.id, chunks, session)
        except IntegrityError:
            # Carrera: otro proceso inserto el mismo sha256 mientras parseabamos.
            # Re-leer y devolver esa fila (no dejar al llamador sin parseo).
            await session.rollback()
            existing = await _lookup(session, sha)
            if existing is not None:
                chunks, smap = _hydrate(existing)
                _rebind_document_id(chunks, smap, str(path))
                logger.info("parse cache HIT sha256=%s (post-race)", sha[:12])
                return chunks, smap
        logger.info("parse cache MISS sha256=%s parseado y persistido", sha[:12])
        return chunks, smap
    finally:
        if owns_session:
            await session.close()
