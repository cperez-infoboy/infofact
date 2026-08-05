#!/usr/bin/env python3
"""Smoke del cache de parseo (Fase B): serializacion + HIT/MISS por sha256.

Tres grupos de asserts, todos sin tocar la DB de produccion:

  1. PURE  - serializacion Chunk/StructureMap <-> JSON (round-trip identico).
  2. PURE  - claves de invalidacion (_parser_version_key) y _sha256_file.
  3. DB temporal (SQLite en archivo tmp) - fixture .txt (sin Docling):
       - MISS: parsea + persiste (1 fila).
       - HIT: devuelve chunks/smap identicos SIN crear otra fila.
       - Independencia: mutar el smap del HIT no afecta a otro HIT (no hay
         estado compartido; la frontera JSON lo garantiza).
       - Rebind de document_id: dos paths distintos con el mismo contenido
         (mismo sha256) devuelven cada uno su propio document_id.

Convencion smoke PURE (ver scripts/smoke_consolidation.py): asserts directos,
sin pytest/conftest. La parte DB usa un engine async temporal con create_all.

Uso:
  .venv/bin/python scripts/smoke_parse_cache.py
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import pathlib
import sys
import tempfile

# Asegurar import del paquete backend desde la raiz del repo.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.agents.pipelines.ingestion import (
    Chunk,
    SectionNode,
    StructureMap,
    TableRef,
)
from backend.agents.pipelines.parse_cache import (
    _chunk_from_dict,
    _chunk_to_dict,
    _parser_version_key,
    _smap_payload,
    _sha256_file,
    deserialize_chunks,
    parse_document_cached,
    serialize_chunks,
)
from backend.models.base import Base
import backend.models  # noqa: F401 - registra todas las tablas en Base.metadata
from backend.models.document_parse import DocumentParse


def test_serialize_roundtrip() -> None:
    """Chunks + StructureMap serializan y deserializan sin perdida."""
    chunks = [
        Chunk(
            text="El sistema debe registrar usuarios.",
            document_id="/workspaces/perfil/proj/spec.pdf",
            section_path="Cap. 3 > 3.1 Registro",
            element_kinds=("paragraph",),
            page=4,
            index=0,
        ),
        Chunk(
            text="Diagrama de arquitectura con 3 capas.",
            document_id="/workspaces/perfil/proj/spec.pdf",
            section_path="Anexo A",
            element_kinds=("image_description",),
            index=1,
        ),
    ]
    smap = StructureMap(
        document_id="/workspaces/perfil/proj/spec.pdf",
        sections=[
            SectionNode(id="3.1", title="Registro", level=2, page=4),
            SectionNode(id="A", title="Anexo A", level=1, page=None),
        ],
        tables=[
            TableRef(
                document_id="/workspaces/perfil/proj/spec.pdf",
                index=0,
                caption="Tabla de prioridades",
                row_count=3,
                col_count=2,
                page=5,
            ),
        ],
        full_text="# Spec\n\nEl sistema debe registrar usuarios.",
        page_count=10,
    )

    # Round-trip de chunks
    raw = serialize_chunks(chunks)
    back = deserialize_chunks(raw)
    assert len(back) == 2
    assert back[0].text == chunks[0].text
    assert back[0].element_kinds == chunks[0].element_kinds
    assert back[0].page == chunks[0].page
    assert back[1].element_kinds == ("image_description",)

    # Round-trip de smap (via _smap_payload; _hydrate lo reconstruye igual)
    payload = _smap_payload(smap)
    assert payload["full_text"] == smap.full_text
    assert payload["page_count"] == 10
    assert len(payload["sections"]) == 2
    assert payload["sections"][0]["title"] == "Registro"
    assert len(payload["tables"]) == 1
    assert payload["tables"][0]["row_count"] == 3
    assert payload["glossary"] == {}

    # Chunk dict <-> dataclass estable
    again = _chunk_from_dict(_chunk_to_dict(chunks[0]))
    assert again.text == chunks[0].text
    assert again.element_kinds == chunks[0].element_kinds

    print("  ok serialize roundtrip")


def test_version_key_and_hash(tmp_path: pathlib.Path) -> None:
    """_parser_version_key es estable; _sha256_file calza con hashlib."""
    assert _parser_version_key("plaintext") == _parser_version_key("plaintext")
    assert _parser_version_key("plaintext").startswith("plaintext;schema=")
    assert _parser_version_key("docling").startswith("docling=")

    f = tmp_path / "blob.bin"
    data = b"infofact-cache-fixture"
    f.write_bytes(data)
    assert _sha256_file(f) == hashlib.sha256(data).hexdigest()
    print("  ok version key + sha256")


async def _make_engine(tmp_path: pathlib.Path, db_name: str):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / db_name}", future=True
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    return engine, maker


async def test_cache_hit_miss(tmp_path: pathlib.Path) -> None:
    """Flujo completo MISS -> HIT sobre SQLite temporal, fixture .txt (sin Docling)."""
    engine, maker = await _make_engine(tmp_path, "cache.db")
    try:
        fixture = tmp_path / "spec.txt"
        fixture.write_text(
            "El sistema debe permitir exportar reportes en PDF.",
            encoding="utf-8",
        )

        # MISS: primera llamada parsea + persiste.
        async with maker() as s1:
            chunks1, smap1 = await parse_document_cached(fixture, session=s1)
        assert len(chunks1) >= 1
        assert smap1.document_id == str(fixture)
        assert "exportar" in smap1.full_text

        # HIT: segunda llamada no crea otra fila ni re-parsea.
        async with maker() as s2:
            chunks2, smap2 = await parse_document_cached(fixture, session=s2)
        assert [c.text for c in chunks2] == [c.text for c in chunks1]
        assert smap2.full_text == smap1.full_text
        assert smap2.document_id == str(fixture)

        async with maker() as s_count:
            n = await s_count.scalar(select(func.count()).select_from(DocumentParse))
        assert n == 1, f"esperaba 1 fila de parseo, hay {n}"

        # Independencia: mutar smap2 no contamina un tercer HIT.
        smap2.glossary["TERM"] = "def"
        smap2.sections[0].summary = "resumen inyectado"
        async with maker() as s3:
            chunks3, smap3 = await parse_document_cached(fixture, session=s3)
        assert "TERM" not in smap3.glossary
        assert smap3.sections[0].summary == ""

        print("  ok cache HIT/MISS + independencia")
    finally:
        await engine.dispose()


async def test_cache_rebind_document_id(tmp_path: pathlib.Path) -> None:
    """Mismo contenido en dos paths (mismo sha256) -> cada HIT devuelve su path."""
    engine, maker = await _make_engine(tmp_path, "cache2.db")
    try:
        content = "Requerimiento compartido entre proyectos."
        path_a = tmp_path / "projA" / "spec.txt"
        path_b = tmp_path / "projB" / "spec.txt"
        path_a.parent.mkdir(parents=True)
        path_b.parent.mkdir(parents=True)
        path_a.write_text(content, encoding="utf-8")
        path_b.write_text(content, encoding="utf-8")

        async with maker() as sa:
            chunks_a, smap_a = await parse_document_cached(path_a, session=sa)
        async with maker() as sb:
            chunks_b, smap_b = await parse_document_cached(path_b, session=sb)

        # Mismo sha256 -> una sola fila compartida.
        async with maker() as s_count:
            n = await s_count.scalar(select(func.count()).select_from(DocumentParse))
        assert n == 1, f"mismo contenido deberia compartir 1 fila, hay {n}"

        # Pero cada uno apunta a SU path (rebind en el HIT).
        assert smap_a.document_id == str(path_a)
        assert smap_b.document_id == str(path_b)
        assert chunks_a[0].document_id == str(path_a)
        assert chunks_b[0].document_id == str(path_b)
        assert str(path_a) != str(path_b)
        print("  ok rebind document_id por path")
    finally:
        await engine.dispose()


async def amain() -> int:
    tmp_path = pathlib.Path(tempfile.mkdtemp(prefix="infofact-smoke-"))
    try:
        print("smoke_parse_cache:")
        test_serialize_roundtrip()
        test_version_key_and_hash(tmp_path)
        await test_cache_hit_miss(tmp_path)
        await test_cache_rebind_document_id(tmp_path)
        print("\nTodos los asserts OK.")
        return 0
    except AssertionError:
        print("\nFALLO: assert roto", file=sys.stderr)
        raise


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
