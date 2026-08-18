"""Tests de seed_from_last_srs: actualización de solo-narrativa del SRS.

Pin del caso real de la sesión de Planitrack2.0: el usuario pidió ajustar la
narrativa del SRS v1 (detalle multi-industria en el alcance) y el pipeline
re-arrancó desde analyze_quality — ~165 llamadas LLM re-juzgando los mismos
825 enunciados, porque cada etapa exige la anterior y commit_srs borra el
holder. La tool siembra el holder desde el último SRS PERSISTIDO y marca las
etapas 1-3 como hechas, con guarda de staleness (conteo de vivos + filas
tocadas tras generated_at).
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.agents.subagents import srs_agent, srs_run_holder as holder
from backend.database import AsyncSessionLocal as _RealLocal
from backend.models import (
    Base,
    Priority,
    Project,
    ReqStatus,
    ReqType,
    RequirementItem,
)
from backend.models.srs import (
    FindingDimension,
    FindingScope,
    FindingStatus,
    RequirementFinding,
)
from backend.services import srs_store


async def _seed_srs_v1(session) -> tuple[int, int]:
    """Proyecto + 2 items vivos + SRS v1 persistido con findings. (pid, item1)."""
    proj = Project(user_id=1, name="srs", slug="srs", description="t")
    session.add(proj)
    await session.flush()
    a = RequirementItem(
        project_id=proj.id, code="REQ-A", statement="Alcance de efectivo.",
        type=ReqType.FUNCTIONAL, priority=Priority.MUST,
    )
    b = RequirementItem(
        project_id=proj.id, code="REQ-B", statement="Campos del modulo.",
        type=ReqType.FUNCTIONAL, priority=Priority.SHOULD,
    )
    session.add_all([a, b])
    await session.flush()
    finding = RequirementFinding(
        project_id=proj.id, req_id=a.id, scope=FindingScope.ITEM,
        dimension=FindingDimension.INCOSE_RULE,
        rule_id="incose.modal_missing", severity="major",
        message="Falta verbo modal.", suggestion="Agregar debe.",
        status=FindingStatus.WAIVED,  # curado por el usuario en la UI
    )
    session.add(finding)
    await session.commit()

    await srs_store.create_srs(session, proj.id, {
        "narrative": {"purpose": "v1"},
        "markdown": "# v1",
        "quality_summary": {"total_findings": 1, "blockers": 0, "goals": {"goals": 3}},
        "coverage": {"totals": {"live": 2}},
        "requirement_codes": ["REQ-A", "REQ-B"],
        "requirement_count": 2,
    })
    return proj.id, a.id


async def _fresh_db(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(srs_agent, "AsyncSessionLocal", sm)
    # El registro de runs vive en el PROCESO (keyed por project_id) y cada DB
    # temporal reinicia los ids en 1: sin esto, el cap de loop de STAGE_SEED
    # acumula entre tests y el 4to seed truena con stage_loop_exceeded.
    holder.clear_run(1)
    return sm, tmp, engine


def _tools(pid: int) -> dict:
    return {t.name: t for t in srs_agent._make_stage_tools(pid, "srs", "t")}


@pytest.mark.asyncio
async def test_seed_reuses_stages_and_preserves_finding_curation(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            pid, _a = await _seed_srs_v1(session)

        result = await _tools(pid)["seed_from_last_srs"].ainvoke({})
        assert result["reused_from_version"] == 1
        assert result["findings_reused"] == 1

        run = holder.get_run(pid)
        assert run.stages_done == {
            holder.STAGE_QUALITY, holder.STAGE_GOALS, holder.STAGE_COVERAGE,
        }
        # El atajo NO marca narrativa: draft_narrative + commit_srs faltan.
        assert run.missing_stages_before_commit() == [holder.STAGE_NARRATIVE]
        # goals_summary sale del anidado del payload persistido.
        assert run.goals_summary == {"goals": 3}
        assert run.quality_summary == {"total_findings": 1, "blockers": 0}
        assert run.coverage == {"totals": {"live": 2}}
        # La curacion de estado de la UI sobrevive la siembra.
        assert run.findings[0]["status"] == "waived"
        assert run.findings[0]["req_code"] == "REQ-A"
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_seed_without_previous_srs_errors(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            proj = Project(user_id=1, name="x", slug="x", description="t")
            session.add(proj)
            await session.commit()

        result = await _tools(proj.id)["seed_from_last_srs"].ainvoke({})
        assert result["error"] == "no_previous_srs"
        assert holder.get_run(proj.id).missing_stages_before_commit() == [
            holder.STAGE_QUALITY, holder.STAGE_GOALS,
            holder.STAGE_COVERAGE, holder.STAGE_NARRATIVE,
        ]
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_seed_refuses_when_store_changed_by_count(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            pid, _a = await _seed_srs_v1(session)
            # Item vivo NUEVO tras el SRS: el conteo deja de coincidir.
            session.add(RequirementItem(
                project_id=pid, code="REQ-C", statement="Nuevo post-SRS.",
                type=ReqType.FUNCTIONAL, priority=Priority.COULD,
            ))
            await session.commit()

        result = await _tools(pid)["seed_from_last_srs"].ainvoke({})
        assert result["error"] == "stale_store"
        assert any("vivos" in r for r in result["reasons"])
        # El holder NO queda sembrado tras el rechazo.
        run = holder.get_run(pid)
        assert holder.STAGE_QUALITY not in run.stages_done
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_seed_refuses_when_items_touched_after_generation(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            pid, item_id = await _seed_srs_v1(session)
            srs = await srs_store.get_latest_srs(session, pid)
            # Edicion posterior al SRS: updated_at > generated_at (mismo conteo).
            touched = srs.generated_at + timedelta(hours=1)
            await session.execute(
                update(RequirementItem)
                .where(RequirementItem.id == item_id)
                .values(updated_at=touched, statement="Editado luego.")
            )
            await session.commit()

        result = await _tools(pid)["seed_from_last_srs"].ainvoke({})
        assert result["error"] == "stale_store"
        assert any("modificado" in r for r in result["reasons"])
    finally:
        await engine.dispose()
        tmp.cleanup()


def test_prompt_teaches_the_seed_shortcut():
    """Sin este pin el subagente re-arranca el pipeline completo por default."""
    p = srs_agent.SRS_AGENT_PROMPT
    assert "seed_from_last_srs" in p
    assert "solo-narrativa" in p or "solo la NARRATIVA" in p


def test_seed_tool_is_registered():
    tools = srs_agent._make_stage_tools(1, "p", "d")
    names = [t.name for t in tools]
    assert "seed_from_last_srs" in names
    # Gotcha del archivo: definida Y registrada (una tool sin registrar no
    # llega al agente y no falla ningun import).
    assert names[0] == "seed_from_last_srs"
