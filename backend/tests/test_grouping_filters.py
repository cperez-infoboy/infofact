"""Tests de los filtros de alcance (types/documents) de build_grouping_plan.

Pins del paso "pipeline scope filters" del plan /agrupar directo:
- ``types`` filtra por valor de ``ReqType`` (lista, no solo uno); un valor
  invalido lanza ``ValueError`` listando los 10 validos.
- ``documents`` acepta filename | rel_path | path absoluto del container,
  resuelto contra las filas ``ProjectDocument`` del proyecto; un nombre sin
  match lanza ``ValueError`` listando los disponibles.
- Items manuales (``source=None``) quedan excluidos SOLO con filtro de
  documento activo; sin filtros el comportamiento es el historico (paridad).
- ``considered``/``scope`` reflejan el alcance efectivo; el early-return <2
  ocurre DESPUES del filtrado (plan vacio con considered correcto).

Determinismo: temp SQLite + ``embed_texts`` one-hot (similitud 0 entre items
distintos => sin candidatos semanticos) + ``_judge_duplicates`` stubbeado;
los grupos provienen solo de ``exact_dedup`` (verbatim, sin LLM).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.agents.pipelines import grouping
from backend.models import (
    Base,
    Priority,
    Project,
    ProjectDocument,
    ReqStatus,
    ReqType,
    RequirementItem,
)

SPEC_ABS = "/workspaces/p/docs/spec.pdf"
RFQ_ABS = "/workspaces/p/other/rfq.docx"

S_AUTH = "El sistema debe autenticar usuarios mediante Google OAuth."
S_TLS = "El sistema debe cifrar el trafico con TLS 1.3."
S_EXPORT = "El sistema debe permitir exportar reportes en PDF."
S_SEARCH = "El sistema debe permitir buscar requerimientos por codigo."

# (code, statement, type, source) — pares verbatim por sentencia.
SEEDED = [
    ("REQ-001", S_AUTH, ReqType.FUNCTIONAL, {"document_id": SPEC_ABS}),
    ("REQ-002", S_AUTH, ReqType.FUNCTIONAL, {"document_id": SPEC_ABS}),
    ("REQ-101", S_TLS, ReqType.SECURITY, {"document_id": SPEC_ABS}),
    ("REQ-102", S_TLS, ReqType.SECURITY, {"document_id": SPEC_ABS}),
    ("REQ-201", S_EXPORT, ReqType.FUNCTIONAL, None),  # manual
    ("REQ-202", S_EXPORT, ReqType.FUNCTIONAL, None),  # manual
    ("REQ-301", S_SEARCH, ReqType.FUNCTIONAL, {"document_id": RFQ_ABS}),
    ("REQ-302", S_SEARCH, ReqType.FUNCTIONAL, {"document_id": RFQ_ABS}),
]


async def _seed(session) -> int:
    proj = Project(user_id=1, name="filtros", slug="filtros", description="t")
    session.add(proj)
    await session.flush()
    pid = proj.id
    for code, stmt, rtype, source in SEEDED:
        session.add(RequirementItem(
            project_id=pid, code=code, statement=stmt,
            type=rtype, priority=Priority.MUST,
            status=ReqStatus.VALIDATED, confidence=0.9, source=source,
        ))
    session.add(ProjectDocument(
        project_id=pid, rel_path="docs/spec.pdf", filename="spec.pdf",
        extension="pdf", size_bytes=10, sha256="a" * 64,
    ))
    session.add(ProjectDocument(
        project_id=pid, rel_path="other/rfq.docx", filename="rfq.docx",
        extension="docx", size_bytes=20, sha256="b" * 64,
    ))
    await session.commit()
    return pid


@pytest.fixture
def deterministic(monkeypatch):
    """Embeddings one-hot + juez vacío: solo buckets verbatim, sin LLM."""
    monkeypatch.setattr(
        "backend.agents.pipelines.consolidation.embed_texts",
        lambda texts: np.eye(len(texts), dtype="float32"),
    )

    async def _no_judge(_reqs, _candidates, on_progress=None):
        return []

    monkeypatch.setattr(grouping, "_judge_duplicates", _no_judge)


async def _build(**kwargs) -> grouping.GroupingPlan:
    """Temp SQLite sembrado + build_grouping_plan con los filtros dados."""
    with tempfile.TemporaryDirectory() as tmp_str:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{Path(tmp_str) / 't.db'}"
        )
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sm = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            # El cache de embeddings escribe por su propia conexion: apuntarla
            # a la DB temporal para no tocar la real.
            import backend.database as _db

            orig_local = _db.AsyncSessionLocal
            _db.AsyncSessionLocal = sm
            try:
                async with sm() as session:
                    pid = await _seed(session)
                    return await grouping.build_grouping_plan(
                        session, pid, project="filtros", **kwargs
                    )
            finally:
                _db.AsyncSessionLocal = orig_local
        finally:
            await engine.dispose()


def _group_codes(plan) -> list[set[str]]:
    return [{g.keeper_code, *g.member_codes} for g in plan.groups]


def _has_group(plan, a: str, b: str) -> bool:
    return any(a in codes and b in codes for codes in _group_codes(plan))


# --- paridad sin filtros ---------------------------------------------------

@pytest.mark.asyncio
async def test_no_filters_considers_everything(deterministic):
    """Sin filtros: comportamiento historico — los 8 items y los 4 pares."""
    plan = await _build()
    assert plan.considered == 8
    assert len(plan.groups) == 4
    assert plan.scope == ""
    assert _has_group(plan, "REQ-001", "REQ-002")
    assert _has_group(plan, "REQ-101", "REQ-102")
    assert _has_group(plan, "REQ-201", "REQ-202")
    assert _has_group(plan, "REQ-301", "REQ-302")


# --- filtro types ----------------------------------------------------------

@pytest.mark.asyncio
async def test_types_functional_keeps_functional_groups_only(deterministic):
    plan = await _build(types=["functional"])
    assert plan.considered == 6  # excluye el par security (REQ-101/102)
    assert plan.scope == "tipos: functional"
    assert _has_group(plan, "REQ-001", "REQ-002")
    assert _has_group(plan, "REQ-201", "REQ-202")  # manuales: sin filtro de docs van
    assert _has_group(plan, "REQ-301", "REQ-302")
    assert not _has_group(plan, "REQ-101", "REQ-102")


@pytest.mark.asyncio
async def test_types_security_shrinks_to_security_pair(deterministic):
    plan = await _build(types=["security"])
    assert plan.considered == 2
    assert len(plan.groups) == 1
    assert _has_group(plan, "REQ-101", "REQ-102")


@pytest.mark.asyncio
async def test_types_accepts_multiple_values(deterministic):
    plan = await _build(types=["functional", "security"])
    assert plan.considered == 8
    assert plan.scope == "tipos: functional, security"


@pytest.mark.asyncio
async def test_invalid_type_raises_with_valid_values(deterministic):
    with pytest.raises(ValueError) as exc_info:
        await _build(types=["seguridad"])
    msg = str(exc_info.value)
    assert "seguridad" in msg
    # Los 10 ReqType validos estan listados para corregir sin adivinar.
    for valid in ("functional", "security", "constraint", "process", "data"):
        assert valid in msg


@pytest.mark.asyncio
async def test_types_filter_emptying_scope_returns_empty_plan(deterministic):
    """El early-return <2 ocurre DESPUES del filtro: plan vacio con considered."""
    plan = await _build(types=["performance"])
    assert plan.groups == []
    assert plan.considered == 0
    assert plan.scope == "tipos: performance"


# --- filtro documents ------------------------------------------------------

@pytest.mark.asyncio
async def test_documents_by_filename_filters_and_excludes_manual(deterministic):
    plan = await _build(documents=["spec.pdf"])
    assert plan.considered == 4  # solo los 4 items con source en spec.pdf
    assert plan.scope == "documentos: docs/spec.pdf"
    assert _has_group(plan, "REQ-001", "REQ-002")
    assert _has_group(plan, "REQ-101", "REQ-102")
    # Items manuales (source=None) quedan FUERA con filtro de documento.
    assert not _has_group(plan, "REQ-201", "REQ-202")
    assert not _has_group(plan, "REQ-301", "REQ-302")


@pytest.mark.asyncio
async def test_documents_accepts_rel_path_and_absolute_path(deterministic):
    by_rel = await _build(documents=["docs/spec.pdf"])
    assert by_rel.considered == 4
    assert _has_group(by_rel, "REQ-001", "REQ-002")

    by_abs = await _build(documents=[SPEC_ABS])
    assert by_abs.considered == 4
    assert _has_group(by_abs, "REQ-101", "REQ-102")


@pytest.mark.asyncio
async def test_documents_rfq_by_filename_selects_other_doc(deterministic):
    plan = await _build(documents=["rfq.docx"])
    assert plan.considered == 2
    assert _has_group(plan, "REQ-301", "REQ-302")
    assert not _has_group(plan, "REQ-001", "REQ-002")


@pytest.mark.asyncio
async def test_manual_items_included_without_document_filter(deterministic):
    """Los items manuales solo se excluyen con filtro de documento activo."""
    plan = await _build(types=["functional"])
    assert _has_group(plan, "REQ-201", "REQ-202")


@pytest.mark.asyncio
async def test_unknown_document_raises_listing_available(deterministic):
    with pytest.raises(ValueError) as exc_info:
        await _build(documents=["nope.pdf"])
    msg = str(exc_info.value)
    assert "nope.pdf" in msg
    assert "docs/spec.pdf" in msg
    assert "other/rfq.docx" in msg


# --- filtros combinados ----------------------------------------------------

@pytest.mark.asyncio
async def test_combined_types_and_documents(deterministic):
    plan = await _build(types=["security"], documents=["spec.pdf"])
    assert plan.considered == 2
    assert _has_group(plan, "REQ-101", "REQ-102")
    assert "tipos: security" in plan.scope
    assert "documentos: docs/spec.pdf" in plan.scope
