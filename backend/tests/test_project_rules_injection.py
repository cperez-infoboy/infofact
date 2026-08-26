"""Tests de inyección del bloque PROJECT_RULES en los prompts del pipeline.

Estrategia: capturar los mensajes que llegan al LLM con un stub que registra
y falla (el sentinel de parse-error del pipeline deja el flujo intacto) y
verificar que el bloque aparece en el USER message — y que SIN reglas el
mensaje no contiene el bloque (regla de no-regresión del patrón
DOCUMENT_CONVENTIONS: prompt intacto). Incluye la persistencia de las
convenciones descubiertas (CONVENTIONS → harness, dedup entre corridas).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.agents.pipelines.classification import (
    _PARSE_ERROR_PREFIX,
    _classify_item,
)
from backend.agents.pipelines.critique import _judge_item
from backend.agents.pipelines.extraction import (
    Chunk,
    DocumentRules,
    RawRequirement,
    extract_chunk,
)
from backend.agents.pipelines.project_pipeline import _build_project_context
from backend.agents.subagents.requirements_capture_agent import (
    _persist_convention_rules,
)
from backend.models import Base, Project
from backend.models.project_rule import RuleScope, RuleSource
from backend.services import project_rules_store as store

BLOCK = (
    "PROJECT_RULES (persistent project considerations — honor them "
    "from now on):\n- Marcar auditoría como regulatorio."
)


def _capturing_llm(monkeypatch, target: str) -> list:
    """Stub del LLM: registra mensajes y revienta (path parse-error)."""
    captured: list = []

    class _LLM:
        async def ainvoke(self, msgs, **kwargs):
            captured.append(msgs)
            raise RuntimeError("stub: parse fail")

    monkeypatch.setattr(target, lambda schema: _LLM())
    return captured


def _raw() -> RawRequirement:
    return RawRequirement(
        statement="El sistema debe auditar accesos.",
        source_span="sección 2.1",
        section="2.1",
        confidence=0.9,
    )


# --- classification ------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_item_appends_rules_block(monkeypatch):
    captured = _capturing_llm(
        monkeypatch, "backend.agents.pipelines.classification.structured_llm"
    )
    decision = await _classify_item(_raw(), rules_block=BLOCK)
    assert decision.rationale.startswith(_PARSE_ERROR_PREFIX)
    user = captured[0][1][1]
    assert "PROJECT_RULES" in user
    assert "Marcar auditoría como regulatorio." in user


@pytest.mark.asyncio
async def test_classify_item_without_block_prompt_untouched(monkeypatch):
    captured = _capturing_llm(
        monkeypatch, "backend.agents.pipelines.classification.structured_llm"
    )
    await _classify_item(_raw())
    assert "PROJECT_RULES" not in captured[0][1][1]


# --- critique ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_item_appends_rules_block(monkeypatch):
    captured = _capturing_llm(
        monkeypatch, "backend.agents.pipelines.critique.structured_llm"
    )
    verdict = await _judge_item(_raw(), rules_block=BLOCK)
    assert "parse_error" in verdict.reasons
    user = captured[0][1][1]
    assert "PROJECT_RULES" in user


# --- extraction ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_chunk_puts_block_before_chunk(monkeypatch):
    captured = _capturing_llm(
        monkeypatch, "backend.agents.pipelines.extraction._structured_llm"
    )
    chunk = Chunk(
        document_id="doc-1",
        index=0,
        section_path="2 Requerimientos",
        text="El sistema debe auditar accesos. " * 200,
    )
    with pytest.raises(RuntimeError):
        await extract_chunk(
            chunk, project_name="p", project_description="d", rules_block=BLOCK,
        )
    user = captured[0][1][1]
    # El bloque va ANTES del chunk (lost-in-the-middle con chunks largos).
    assert user.index("PROJECT_RULES") < user.index("CHUNK:")


# --- analysis (project pipeline) ------------------------------------------------


def test_build_project_context_appends_block():
    ctx = _build_project_context(
        "proyecto", "descripción", rules_block=BLOCK,
    )
    assert "PROJECT_RULES" in ctx
    ctx_empty = _build_project_context("proyecto", "descripción")
    assert "PROJECT_RULES" not in ctx_empty


# --- SRS narrativa ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrative_context_includes_srs_rules(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}"
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        monkeypatch.setattr("backend.database.AsyncSessionLocal", sm)

        async with sm() as session:
            proj = Project(user_id=1, name="srs", slug="srs", description="d")
            session.add(proj)
            await session.flush()
            await store.add_rule(
                session, proj.id, scope=RuleScope.SRS,
                content="Tono formal en la narrativa.",
                check_conflicts=False,
            )
            await session.commit()
            pid = proj.id

        async def _no_rag(project_id, section):
            return ""

        monkeypatch.setattr(
            "backend.services.srs_assembler._rag_context_for_section", _no_rag
        )
        from backend.services.srs_assembler import _build_narrative_context

        ctx = await _build_narrative_context(
            pid, "srs", "d", [], {}, {}, {},
        )
        assert "## Reglas del proyecto" in ctx
        assert "Tono formal en la narrativa." in ctx
    finally:
        await engine.dispose()
        tmp.cleanup()


# --- CONVENTIONS → harness -------------------------------------------------------


async def _rules_db(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("backend.database.AsyncSessionLocal", sm)
    return sm, tmp, engine


@pytest.mark.asyncio
async def test_persist_convention_rules_dedups_across_runs(monkeypatch):
    sm, tmp, engine = await _rules_db(monkeypatch)
    try:
        async with sm() as session:
            proj = Project(user_id=1, name="conv", slug="conv", description="d")
            session.add(proj)
            await session.flush()
            pid = proj.id
            await session.commit()

        rules = DocumentRules(
            priority_field_label="Prioridad del requerimiento",
            scope_markers=["fuera de alcance"],
            glossary={"SLA": "acuerdo de nivel de servicio"},
        )
        # Dos corridas consecutivas con las mismas señales.
        first = await _persist_convention_rules(pid, rules)
        second = await _persist_convention_rules(pid, rules)
        assert first > 0
        assert second == 0

        async with sm() as session:
            persisted = await store.list_rules(session, pid)
            contents = {r.content for r in persisted}
            assert all(
                r.source is RuleSource.CONVENTIONS for r in persisted
            )
            assert all(r.scope is RuleScope.CAPTURE for r in persisted)
            assert any("Prioridad del requerimiento" in c for c in contents)
            assert any("fuera de alcance" in c for c in contents)
            assert any("SLA" in c for c in contents)

        # rules vacías → no-op sin tocar la DB.
        assert await _persist_convention_rules(pid, DocumentRules()) == 0
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_persist_convention_rules_degrades_on_db_failure(monkeypatch):
    class _Boom:
        async def __aenter__(self):
            raise RuntimeError("db caída")

        async def __aexit__(self, *args):
            return False

    def _factory():
        return _Boom()

    import backend.database as db_module

    monkeypatch.setattr(db_module, "AsyncSessionLocal", _factory)
    rules = DocumentRules(scope_markers=["fase 2"])
    # Best-effort: devuelve 0 y NO levanta (la etapa nunca aborta).
    assert await _persist_convention_rules(1, rules) == 0
