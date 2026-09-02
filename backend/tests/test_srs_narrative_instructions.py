"""Tests del canal de instrucciones narrativas del SRS + verificación lectora.

Pin del incidente v1/v2/v3 de Planitrack2.0: el usuario pidió incorporar el
carácter multi-industria en tres secciones y tres versiones salieron sin el
contenido — porque `draft_narrative_llm` arma su prompt SOLO desde store +
RAG interno; el contexto conversacional del subagente (citas verbatim
cargadas con get_document_passage) nunca llegaba al redactor.

- `instructions` en draft_narrative (tool) → draft_narrative_llm → bloque
  "INDICACIONES DEL USUARIO SOBRE LA NARRATIVA" en el prompt del redactor.
- El srs-agent ahora también recibe las read tools de SRS
  (read_srs_section & co.) para verificar lo persistido antes de reportar.
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
from backend.agents.subagents import srs_agent, srs_run_holder as holder
from backend.models import Base, Priority, Project, ReqType, RequirementItem
from backend.services.srs_assembler import SrsNarrativeDraft, draft_narrative_llm

INSTR = (
    "Incorporar el carácter multi-industria en propósito y alcance: no es un "
    "sistema logístico; tipos de gestión definidos por el modelo de operación "
    "del tenant."
)


def _draft() -> SrsNarrativeDraft:
    return SrsNarrativeDraft(
        purpose="p", scope="s", definitions="d", references="r",
        perspective="per", users="u", environment="e", assumptions="a",
    )


def _full_narrative() -> dict[str, str]:
    """Todas las claves: el merge final reconstruye intro/overall desde ellas."""
    keys = [
        "intro.purpose", "intro.scope", "intro.definitions",
        "intro.references", "intro.overview", "overall.perspective",
        "overall.features", "overall.users", "overall.environment",
        "overall.assumptions",
    ]
    return {k: "det" for k in keys}


class _CaptureRunner:
    """Runner falso que captura los mensajes y devuelve un draft válido."""

    def __init__(self, schema):
        self.schema = schema
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return _draft()


# --- assembler: el bloque llega al prompt del redactor -----------------------


@pytest.mark.asyncio
async def test_instructions_reach_the_narrator_prompt(monkeypatch):
    captured = {}

    def _fake_structured_llm(schema):
        runner = _CaptureRunner(schema)
        captured["runner"] = runner
        return runner

    monkeypatch.setattr(
        "backend.services.srs_assembler.structured_llm", _fake_structured_llm
    )

    await draft_narrative_llm(
        _full_narrative(),
        project_id=1, project_name="p", project_description="d",
        live_items=[], quality_summary={}, coverage={}, goals_summary={},
        instructions=INSTR,
    )
    user_msg = captured["runner"].messages[1][1]
    assert "INDICACIONES DEL USUARIO SOBRE LA NARRATIVA" in user_msg
    assert INSTR in user_msg


@pytest.mark.asyncio
async def test_no_instructions_no_block(monkeypatch):
    captured = {}

    def _fake_structured_llm(schema):
        runner = _CaptureRunner(schema)
        captured["runner"] = runner
        return runner

    monkeypatch.setattr(
        "backend.services.srs_assembler.structured_llm", _fake_structured_llm
    )

    await draft_narrative_llm(
        _full_narrative(),
        project_id=1, project_name="p", project_description="d",
        live_items=[], quality_summary={}, coverage={}, goals_summary={},
    )
    user_msg = captured["runner"].messages[1][1]
    assert "INDICACIONES DEL USUARIO" not in user_msg


# --- assembler: el catálogo de actores llega al prompt del redactor ----------


async def _make_project_db(monkeypatch):
    """DB temporal con un proyecto; devuelve el sessionmaker."""
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("backend.database.AsyncSessionLocal", sm)
    async with sm() as session:
        proj = Project(user_id=1, name="p", slug="p", description="t")
        session.add(proj)
        await session.commit()
        proj_id = proj.id
    return tmp, engine, sm, proj_id


@pytest.mark.asyncio
async def test_actor_catalog_reaches_the_narrator_prompt(monkeypatch):
    """Con catálogo, el bloque PROJECT_ACTORS entra al contexto del redactor
    (§2.4 usuarios se ancla a los roles canonizados en la captura)."""
    from backend.services import actor_store

    tmp, engine, sm, proj_id = await _make_project_db(monkeypatch)
    try:
        async with sm() as session:
            await actor_store.upsert_actors(
                session,
                proj_id,
                [{
                    "name": "Coordinador de terreno",
                    "channel": "humano",
                    "synonyms": ["Coordinador"],
                }],
            )
            await session.commit()

        captured = {}

        def _fake_structured_llm(schema):
            runner = _CaptureRunner(schema)
            captured["runner"] = runner
            return runner

        monkeypatch.setattr(
            "backend.services.srs_assembler.structured_llm", _fake_structured_llm
        )

        await draft_narrative_llm(
            _full_narrative(),
            project_id=proj_id, project_name="p", project_description="d",
            live_items=[], quality_summary={}, coverage={}, goals_summary={},
        )
        user_msg = captured["runner"].messages[1][1]
        assert "Actores del proyecto (catálogo definido en la captura)" in user_msg
        assert "PROJECT_ACTORS" in user_msg
        assert "R1 Coordinador de terreno (humano)" in user_msg
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_no_actor_catalog_no_block(monkeypatch):
    """Sin catálogo, el contexto no menciona actores y el redactor sigue
    infiriendo de fragmentos (comportamiento previo)."""
    tmp, engine, sm, proj_id = await _make_project_db(monkeypatch)
    try:
        captured = {}

        def _fake_structured_llm(schema):
            runner = _CaptureRunner(schema)
            captured["runner"] = runner
            return runner

        monkeypatch.setattr(
            "backend.services.srs_assembler.structured_llm", _fake_structured_llm
        )

        await draft_narrative_llm(
            _full_narrative(),
            project_id=proj_id, project_name="p", project_description="d",
            live_items=[], quality_summary={}, coverage={}, goals_summary={},
        )
        user_msg = captured["runner"].messages[1][1]
        assert "Actores del proyecto" not in user_msg
        assert "PROJECT_ACTORS" not in user_msg
    finally:
        await engine.dispose()
        tmp.cleanup()


# --- tool: la instrucción se reenvía al redactor ------------------------------


@pytest.mark.asyncio
async def test_draft_narrative_tool_forwards_instructions(monkeypatch):
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
        monkeypatch.setattr(srs_agent, "AsyncSessionLocal", sm)
        async with sm() as session:
            proj = Project(user_id=1, name="p", slug="p", description="t")
            session.add(proj)
            await session.flush()
            session.add(RequirementItem(
                project_id=proj.id, code="REQ-A", statement="stmt.",
                type=ReqType.FUNCTIONAL, priority=Priority.MUST,
            ))
            await session.commit()

        holder.clear_run(proj.id)
        run = holder.get_or_create_run(proj.id)
        run.stages_done.add(holder.STAGE_COVERAGE)

        seen: dict = {}

        def _fake_det(*_a, **_k):
            return {"intro.purpose": "det"}

        async def _fake_llm(_narrative, **kwargs):
            seen.update(kwargs)
            return {"intro.purpose": "llm"}

        monkeypatch.setattr(srs_agent, "_draft_narrative", _fake_det)
        monkeypatch.setattr(srs_agent, "draft_narrative_llm", _fake_llm)

        tools = {t.name: t for t in srs_agent._make_stage_tools(proj.id)}
        result = await tools["draft_narrative"].ainvoke({"instructions": INSTR})

        assert seen["instructions"] == INSTR
        assert result["instructions_received"] is True

        # Sin instrucciones: None viaja y el retorno lo refleja.
        result_none = await tools["draft_narrative"].ainvoke({})
        assert result_none["instructions_received"] is False
    finally:
        await engine.dispose()
        tmp.cleanup()


# --- superficie del subagente --------------------------------------------------


def test_srs_agent_has_read_tools():
    """El subagente verifica lo persistido (incidente: reportó citas que no
    pudo leer de vuelta)."""
    spec = srs_agent.make_srs_agent_subagent(
        project_id=1, profile="p", project_slug="s"
    )
    names = {t.name for t in spec["tools"]}
    assert "read_srs_section" in names
    assert "list_srs_sections" in names
    assert "get_latest_srs" in names


def test_prompt_pins_instructions_channel_and_verification():
    p = srs_agent.SRS_AGENT_PROMPT
    assert "parametro `instructions`" in p
    assert "UNICO canal" in p
    assert "read_srs_section" in p
    assert "nunca afirmes contenido que no" in p
