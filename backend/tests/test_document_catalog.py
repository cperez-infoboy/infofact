"""Catálogo de documentos sincronizado con la captura y fuentes del SRS vivas.

Cubre la cadena que dejó huérfanos los documentos del segundo directorio de
Planitrack2.0 (sesión 16): ``ensure_document_registered`` (auto-registro de
la ingesta), ``reconcile_document_catalog`` (migración idempotente que repara
el catálogo contra las fuentes citadas) y la proyección de §1.4 Referencias
desde el catálogo vivo (``_capture_sources`` + ``_projected_references``).
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.models.base import Base
from backend.models.document_parse import DocumentParse
from backend.models.project import Project
from backend.models.project_document import ProjectDocument
from backend.models.requirement import Priority, ReqStatus, ReqType, RequirementItem
from backend.models.user import User


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    """DB temporal + workspace host con archivos reales (para hashear)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    profile, slug = "tester", "planitrack2-0"
    ws = tmp_path / "workspaces" / profile / slug
    docs = ws / "Docs_Entrada"
    post = docs / "Requerimientos_post_reunion"
    media = post / ".infofact-media" / "h1"
    media.mkdir(parents=True)
    (docs / "a.md").write_text("contenido A")
    (post / "b.md").write_text("contenido B")
    (media / "image1.png").write_bytes(b"\x89PNG fake media")

    monkeypatch.setattr("backend.services.document_service.AsyncSessionLocal", Session)
    monkeypatch.setattr("backend.main.engine", engine)
    from backend.config import Settings as _Settings

    # workspaces_root es una property (sin setter): se parchea a nivel clase.
    monkeypatch.setattr(
        _Settings, "workspaces_root",
        property(lambda self: tmp_path / "workspaces"),
    )

    async with Session() as session:
        user = User(id=1, email="t@t.com", password_hash="x", profile=profile)
        session.add(user)
        await session.commit()
        proj = Project(
            user_id=1, name="Planitrack2.0", slug=slug,
            description="d", phase="requirements",
        )
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        yield SimpleNamespace(
            session=session,
            Session=Session,
            pid=proj.id,
            slug=slug,
            ws=ws,
            sha_a=_sha(b"contenido A"),
            sha_b=_sha(b"contenido B"),
            sha_img=_sha(b"\x89PNG fake media"),
        )
    await engine.dispose()


async def _add_req(session, pid: int, code: str, doc_rel: str | None) -> None:
    source = (
        [{"document_id": f"/workspaces/planitrack2-0/{doc_rel}", "quote": "q"}]
        if doc_rel
        else None
    )
    session.add(
        RequirementItem(
            project_id=pid,
            code=code,
            statement=f"El sistema debe hacer {code}.",
            type=ReqType.FUNCTIONAL,
            priority=Priority.MUST,
            status=ReqStatus.DRAFT,
            source=source,
        )
    )


# ---------------------------------------------------------------------------
# ensure_document_registered / mark_document_parsed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_registers_reuses_and_corrects_rel_path(env):
    from backend.services.document_service import ensure_document_registered

    host_root = env.ws.parent  # .../tester
    path_a = env.ws / "Docs_Entrada" / "a.md"

    first = await ensure_document_registered(
        project_id=env.pid, host_root=host_root, path=path_a
    )
    assert first.created is True
    assert first.document.rel_path == "planitrack2-0/Docs_Entrada/a.md"
    assert first.document.sha256 == env.sha_a
    assert first.document.parse_status == "pending"

    # Re-ensure del mismo contenido: dedupe por (project_id, sha256).
    second = await ensure_document_registered(
        project_id=env.pid, host_root=host_root, path=path_a
    )
    assert second.created is False
    assert second.document.id == first.document.id

    # El archivo se movió de carpeta: la fila existente corrige su rel_path.
    moved_dir = env.ws / "Docs_Entrada" / "movidos"
    moved_dir.mkdir()
    moved = moved_dir / "a.md"
    (env.ws / "Docs_Entrada" / "a.md").rename(moved)
    third = await ensure_document_registered(
        project_id=env.pid, host_root=host_root, path=moved
    )
    assert third.created is False
    assert third.document.id == first.document.id
    assert third.document.rel_path == "planitrack2-0/Docs_Entrada/movidos/a.md"


@pytest.mark.asyncio
async def test_mark_document_parsed_copies_parse_metadata(env):
    from backend.services.document_service import (
        ensure_document_registered,
        mark_document_parsed,
    )

    host_root = env.ws.parent
    session = env.session
    session.add(
        DocumentParse(
            sha256=env.sha_a, parser_used="docling",
            parser_version="v1", markdown="", page_count=3,
        )
    )
    await session.commit()

    reg = await ensure_document_registered(
        project_id=env.pid, host_root=host_root,
        path=env.ws / "Docs_Entrada" / "a.md",
    )
    ok = await mark_document_parsed(
        document_id=reg.document.id, sha256=env.sha_a
    )
    assert ok is True

    # El objeto vino de la sesión interna del service: releer por id.
    row = await session.get(ProjectDocument, reg.document.id)
    assert row is not None
    assert row.parse_status == "ready"
    assert row.parsed_at is not None
    assert row.parser_used == "docling"
    assert row.page_count == 3


# ---------------------------------------------------------------------------
# reconcile_document_catalog (migración idempotente)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_repairs_missing_rows_and_flags(env):
    from backend.main import reconcile_document_catalog

    session, pid = env.session, env.pid
    # Fila existente registrada como "filename plano" (como los 4 originales
    # de Planitrack2.0), sin flag y con parse_status pendiente.
    legacy = ProjectDocument(
        project_id=pid, rel_path="a.md", filename="a.md",
        extension=".md", mime="text/markdown", size_bytes=11,
        sha256=env.sha_a, parse_status="pending",
    )
    session.add(legacy)
    # Parses globales (el cache los tiene aunque el catálogo no se enteró).
    session.add_all(
        [
            DocumentParse(
                sha256=env.sha_b, parser_used="plaintext",
                parser_version="v1", markdown="", page_count=2,
            ),
            DocumentParse(
                sha256=env.sha_a, parser_used="docling",
                parser_version="v1", markdown="", page_count=5,
            ),
        ]
    )
    await session.commit()

    # Requerimientos que citan: a.md (2), b.md (1), image1.png (1) y un
    # archivo que ya no existe en el workspace (perdido.md).
    await _add_req(session, pid, "REQ-0001", "Docs_Entrada/a.md")
    await _add_req(session, pid, "REQ-0002", "Docs_Entrada/a.md")
    await _add_req(
        session, pid, "REQ-0003",
        "Docs_Entrada/Requerimientos_post_reunion/b.md",
    )
    await _add_req(
        session, pid, "REQ-0004",
        "Docs_Entrada/Requerimientos_post_reunion/.infofact-media/h1/image1.png",
    )
    await _add_req(session, pid, "REQ-0005", "Docs_Entrada/perdido.md")
    await session.commit()

    await reconcile_document_catalog()

    # La migración escribió por otra conexión: expirar el identity map de la
    # sesión del fixture para releer estado fresco.
    session.expire_all()
    rows = {
        r.rel_path: r
        for r in (
            await session.execute(
                select(ProjectDocument).where(ProjectDocument.project_id == pid)
            )
        ).scalars()
    }

    # La fila legada se corrigió: rel_path real + flag + parse listo.
    assert "a.md" not in rows
    fixed = rows["Docs_Entrada/a.md"]
    assert fixed.id == legacy.id
    assert fixed.used_in_capture is True
    assert fixed.parse_status == "ready"

    # b.md obtuvo fila con sha + metadata del parse global.
    new_b = rows["Docs_Entrada/Requerimientos_post_reunion/b.md"]
    assert new_b.sha256 == env.sha_b
    assert new_b.used_in_capture is True
    assert new_b.parse_status == "ready"
    assert new_b.parser_used == "plaintext"
    assert new_b.page_count == 2

    # La media embebida también queda registrada (el RAG la necesita).
    img = rows[
        "Docs_Entrada/Requerimientos_post_reunion/.infofact-media/h1/image1.png"
    ]
    assert img.used_in_capture is True

    # El archivo desaparecido igual cuenta: sha sintético keyed por ruta
    # (la columna es NOT NULL) + flag.
    lost = rows["Docs_Entrada/perdido.md"]
    assert lost.sha256 == hashlib.sha256(
        b"missing:Docs_Entrada/perdido.md"
    ).hexdigest()
    assert lost.used_in_capture is True
    assert lost.parse_status == "pending"

    # Idempotente: la segunda corrida no crea ni duplica filas.
    await reconcile_document_catalog()
    count = len(
        (
            await session.execute(
                select(ProjectDocument).where(ProjectDocument.project_id == pid)
            )
        ).scalars().all()
    )
    assert count == 4


# ---------------------------------------------------------------------------
# _capture_sources + _projected_references (§1.4 proyectada)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capture_sources_projection_with_media_annex(env):
    from backend.services.srs_assembler import (
        _capture_sources,
        _projected_references,
    )

    session, pid = env.session, env.pid
    session.add_all(
        [
            ProjectDocument(
                project_id=pid, rel_path="Docs_Entrada/a.md", filename="a.md",
                extension=".md", mime="text/markdown", size_bytes=11,
                sha256=env.sha_a, parse_status="ready", used_in_capture=True,
            ),
            ProjectDocument(
                project_id=pid,
                rel_path="Docs_Entrada/Requerimientos_post_reunion/b.md",
                filename="b.md", extension=".md", mime="text/markdown",
                size_bytes=11, sha256=env.sha_b, parse_status="ready",
                used_in_capture=True,
            ),
            ProjectDocument(
                project_id=pid,
                rel_path=(
                    "Docs_Entrada/Requerimientos_post_reunion/"
                    ".infofact-media/h1/image1.png"
                ),
                filename="image1.png", extension=".png", mime="image/png",
                size_bytes=16, sha256=env.sha_img, parse_status="ready",
                used_in_capture=True,
            ),
            # Registrado por upload pero nunca capturado: excluido.
            ProjectDocument(
                project_id=pid, rel_path="Docs_Entrada/sin_captura.md",
                filename="sin_captura.md", extension=".md",
                mime="text/markdown", size_bytes=5, sha256="otro",
                parse_status="pending", used_in_capture=False,
            ),
        ]
    )
    await _add_req(session, pid, "REQ-0001", "Docs_Entrada/a.md")
    await _add_req(session, pid, "REQ-0002", "Docs_Entrada/a.md")
    await _add_req(
        session, pid, "REQ-0003",
        "Docs_Entrada/Requerimientos_post_reunion/b.md",
    )
    await _add_req(
        session, pid, "REQ-0004",
        "Docs_Entrada/Requerimientos_post_reunion/.infofact-media/h1/image1.png",
    )
    # Cita sin fila en el catálogo: entra marcada para no perder el respaldo.
    await _add_req(session, pid, "REQ-0005", "Docs_Entrada/sin_registro.md")
    await session.commit()

    sources = await _capture_sources(session, pid)
    assert sources["total_files"] == 4  # a, b, sin_registro + media
    assert sources["media_files"] == 1
    assert sources["media_reqs"] == 1
    names = [e["filename"] for e in sources["documents"]]
    assert names == ["a.md", "b.md", "sin_registro.md"]  # orden por reqs desc
    unreg = [e for e in sources["documents"] if e["unregistered"]]
    assert [e["filename"] for e in unreg] == ["sin_registro.md"]

    table = _projected_references(sources)
    assert "| # | Fuente | Tipo | Reqs respaldados |" in table
    assert "| 1 | a.md | MD | 2 |" in table
    assert "| 2 | b.md | MD | 1 |" in table
    assert "Anexo de imágenes (OCR, 1 archivo(s))" in table
    assert "(sin registro)" in table
    assert "ISO/IEC/IEEE 29148:2018" in table
    # El documento no capturado no aparece.
    assert "sin_captura" not in table


def test_projected_references_empty_and_carryover_refresh():
    from backend.services.srs_assembler import (
        _carryover_narrative,
        _projected_references,
    )

    empty = _projected_references(None)
    assert "Sin documentos fuente" in empty

    det = {
        "intro.purpose": "nuevo",
        "intro.references": _projected_references(
            {"documents": [{"filename": "x.md", "rel_path": "x.md",
                            "extension": "md", "reqs": 3, "unregistered": False}],
             "media_files": 0, "media_reqs": 0, "total_files": 1}
        ),
        "overall.perspective": "prosa\n\n**Resumen del alcance especificado:**\n- x",
    }
    previous = {
        "intro.purpose": "VIEJA prosa curada",
        "intro.references": "VIEJO conteo congelado: 4 fuentes",
        "overall.perspective": "VIEJA perspectiva\n\n**Resumen del alcance especificado:**\n- viejo",
    }
    carried = _carryover_narrative(previous, det)
    # La prosa authored se conserva; la tabla de referencias SIEMPRE fresca.
    assert carried["intro.purpose"] == "VIEJA prosa curada"
    assert carried["intro.references"] == det["intro.references"]
    assert "VIEJO conteo congelado" not in carried["intro.references"]
    assert "x.md" in carried["intro.references"]
