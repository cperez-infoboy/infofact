#!/usr/bin/env python3
"""Smoke: retrieval de documentos (Fase D) — index/search/section/passage + scoping.

PURE: asserts directos, no pytest. Usa un engine SQLite temporal y la capa de
retrieval directamente (``index_parse`` / ``search`` / ``get_section`` /
``get_passage``). Verifica:

  1. ``index_parse`` + ``search`` recall (el chunk relevante queda primero).
  2. Scoping por ``project_id`` (dos proyectos, sin cross-leak).
  3. ``get_section`` por id y por titulo.
  4. ``get_passage`` exacto y fuzzy (rapidfuzz).

Carga el modelo de embeddings (sentence-transformers) en el primer ``index_parse``:
~5s de calentamiento, despues el singleton esta caliente.

Uso:
  .venv/bin/python scripts/smoke_documents_tools.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

# Resolver el paquete backend desde la raiz del repo.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.agents.pipelines.ingestion import Chunk
from backend.agents.retrieval import store as retrieval
from backend.models.base import Base
from backend.models.document_parse import DocumentParse
from backend.models.project import Project
from backend.models.project_document import ProjectDocument


def _chunk(i: int, text: str, section: str = "", page: int = 1) -> Chunk:
    return Chunk(
        text=text,
        document_id="/workspaces/test/doc.pdf",
        section_path=section,
        element_kinds=("text",),
        page=page,
        index=i,
    )


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


async def main() -> int:
    tmp = Path(tempfile.mkdtemp()) / "smoke_fased.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        # --- Fixtures: dos proyectos, dos documentos (distinto sha256) -------
        proj_a = Project(user_id=1, name="A", slug="proj-a", description="")
        proj_b = Project(user_id=1, name="B", slug="proj-b", description="")
        session.add_all([proj_a, proj_b])
        await session.flush()

        sha_a = "a" * 64
        sha_b = "b" * 64

        md_a = (
            "# Autenticacion\n"
            "El sistema debe autenticar usuarios con usuario y contrasena.\n\n"
            "## Reportes\n"
            "El sistema debe generar reportes mensuales de ventas.\n\n"
            "El sistema debe enviar notificaciones por email al cliente.\n"
        )
        md_b = (
            "# Despliegue\n"
            "El sistema debe desplegarse en Kubernetes con autoescalado.\n"
        )

        parse_a = DocumentParse(
            sha256=sha_a,
            parser_used="docling",
            parser_version="docling=test;schema=1",
            markdown=md_a,
            sections_json=json.dumps([
                {"id": "s-auth", "title": "Autenticacion", "level": 1, "page": 1},
                {"id": "s-rep", "title": "Reportes", "level": 2, "page": 1},
            ]),
        )
        parse_b = DocumentParse(
            sha256=sha_b,
            parser_used="docling",
            parser_version="docling=test;schema=1",
            markdown=md_b,
            sections_json="[]",
        )
        session.add_all([parse_a, parse_b])
        await session.flush()

        session.add_all([
            ProjectDocument(
                project_id=proj_a.id,
                rel_path="doc_a.pdf",
                filename="doc_a.pdf",
                extension=".pdf",
                size_bytes=100,
                sha256=sha_a,
                parse_status="ready",
            ),
            ProjectDocument(
                project_id=proj_b.id,
                rel_path="doc_b.pdf",
                filename="doc_b.pdf",
                extension=".pdf",
                size_bytes=100,
                sha256=sha_b,
                parse_status="ready",
            ),
        ])
        await session.commit()

        chunks_a = [
            _chunk(0, "El sistema debe autenticar usuarios con usuario y contrasena.", "Autenticacion"),
            _chunk(1, "El sistema debe generar reportes mensuales de ventas.", "Reportes"),
            _chunk(2, "El sistema debe enviar notificaciones por email al cliente.", "Reportes"),
        ]
        chunks_b = [
            _chunk(0, "El sistema debe desplegarse en Kubernetes con autoescalado.", "Despliegue"),
        ]

        # --- 1. index_parse --------------------------------------------------
        print("1) index_parse")
        n_a = await retrieval.index_parse(parse_a.id, chunks_a, session=session)
        n_b = await retrieval.index_parse(parse_b.id, chunks_b, session=session)
        assert n_a == 3, f"esperaba 3 chunks en A, got {n_a}"
        assert n_b == 1, f"esperaba 1 chunk en B, got {n_b}"
        _ok(f"indexados {n_a} (A) + {n_b} (B) chunks")

        # Idempotencia: re-indexar no duplica.
        n_a2 = await retrieval.index_parse(parse_a.id, chunks_a, session=session)
        assert n_a2 == 3, f"re-index deberia seguir 3, got {n_a2}"
        _ok("re-index idempotente (3, sin duplicados)")

        # --- 2. search recall + scoping -------------------------------------
        print("2) search (recall + scoping)")
        hits_a = await retrieval.search(proj_a.id, "autenticacion de usuarios", session=session)
        assert hits_a, "search(A) no devolvio hits"
        assert "autenticar" in hits_a[0].text.lower(), (
            f"el top hit deberia ser el chunk de auth, got: {hits_a[0].text!r}"
        )
        assert all(h.parse_id == parse_a.id for h in hits_a), "cross-leak: hits de B en A"
        _ok(f"recall: top hit es auth (score={hits_a[0].score:.3f})")
        _ok("scoping: ningun hit de proyecto B en A")

        # Proyecto B: solo su chunk de despliegue.
        hits_b = await retrieval.search(proj_b.id, "despliegue kubernetes", session=session)
        assert hits_b and all(h.parse_id == parse_b.id for h in hits_b), (
            "cross-leak: hits de A en B"
        )
        _ok("scoping inverso: proyecto B solo ve sus chunks")

        # document_id reconstruido con el slug correcto.
        assert hits_a[0].document_id == "/workspaces/proj-a/doc_a.pdf", (
            f"document_id mal reconstruido: {hits_a[0].document_id!r}"
        )
        _ok(f"document_id absoluto correcto: {hits_a[0].document_id}")

        # --- 3. get_section --------------------------------------------------
        print("3) get_section")
        sec_by_id = await retrieval.get_section(parse_a.id, "s-auth", session=session)
        assert sec_by_id is not None and sec_by_id["section"]["title"] == "Autenticacion", (
            f"get_section por id fallo: {sec_by_id}"
        )
        _ok("match por id (s-auth -> Autenticacion)")
        sec_by_title = await retrieval.get_section(parse_a.id, "reportes", session=session)
        assert sec_by_title is not None and sec_by_title["section"]["id"] == "s-rep", (
            f"get_section por titulo fallo: {sec_by_title}"
        )
        _ok("match por titulo (reportes -> s-rep)")

        # --- 4. get_passage (exacto + fuzzy) --------------------------------
        print("4) get_passage")
        exact = await retrieval.get_passage(
            parse_a.id, "autenticar usuarios", padding=30, session=session
        )
        assert exact and exact["found"], f"passage exacto no encontrado: {exact}"
        assert "autenticar usuarios" in exact["passage"]
        assert exact["char_span_end"] > exact["char_span_start"]
        _ok(f"exacto: encontrado en [{exact['char_span_start']},{exact['char_span_end']}]")

        fuzzy = await retrieval.get_passage(
            parse_a.id, "autenticar usarios", padding=30, session=session  # typo
        )
        assert fuzzy and fuzzy["found"], f"passage fuzzy no encontrado: {fuzzy}"
        _ok("fuzzy: match tolerante a typo (autenticar usarios)")

        missing = await retrieval.get_passage(
            parse_a.id, "xyzzy noexiste", padding=10, session=session
        )
        assert missing is not None and missing["found"] is False, (
            f"quote inexistente deberia ser found=False: {missing}"
        )
        _ok("quote inexistente -> found=False (no alucina)")

    await engine.dispose()
    print("\nFase D smoke: OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
