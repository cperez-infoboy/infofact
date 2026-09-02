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
from sqlalchemy import select as sa_select
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
async def test_seed_tolerates_new_requirements_and_reports_them(monkeypatch):
    """Un req vivo nuevo post-SRS NO bloquea la siembra: se reporta el diff.

    La curación (o un append de captura) agrega reqs sin hallazgos reusables;
    el guard de conteo duro forzaba el pipeline completo justo después de una
    curación exitosa (incidencia sesión 53: 45 min de link_goal a mano, luego
    stale_store -> re-run que pisaba la curación).
    """
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
        assert result["reused_from_version"] == 1
        assert result["new_requirements"] == 1
        assert result["findings_reused"] == 1
        assert holder.get_run(pid).stages_done >= {
            holder.STAGE_QUALITY, holder.STAGE_GOALS, holder.STAGE_COVERAGE,
        }
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_seed_tolerates_edits_after_generation(monkeypatch):
    """Una edición posterior al SRS (updated_at > generated_at) NO bloquea."""
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
        assert result["reused_from_version"] == 1
        assert holder.STAGE_QUALITY in holder.get_run(pid).stages_done
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_seed_drops_findings_of_requirements_no_longer_live(monkeypatch):
    """El hallazgo de un req fusionado se descarta; el del vivo sobrevive."""
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            pid, _a = await _seed_srs_v1(session)
            b_id = await session.scalar(
                sa_select(RequirementItem.id).where(
                    RequirementItem.project_id == pid,
                    RequirementItem.code == "REQ-B",
                )
            )
            session.add(RequirementFinding(
                project_id=pid, req_id=b_id, scope=FindingScope.ITEM,
                dimension=FindingDimension.AMBIGUITY, rule_id="AMB_TERMINO",
                severity="major", message="Ambiguo.", suggestion="Aclarar.",
                status=FindingStatus.OPEN,
            ))
            # Curación: REQ-B se fusiona (sale del conjunto vivo).
            await session.execute(
                update(RequirementItem)
                .where(RequirementItem.id == b_id)
                .values(status=ReqStatus.MERGED)
            )
            await session.commit()

        result = await _tools(pid)["seed_from_last_srs"].ainvoke({})
        assert result["findings_reused"] == 1
        assert result["findings_dropped"] == 1
        assert result["dropped_finding_codes"] == ["REQ-B"]
        run = holder.get_run(pid)
        assert [f["req_code"] for f in run.findings] == ["REQ-A"]
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_seed_refuses_when_store_replaced_below_overlap(monkeypatch):
    """Store reemplazado (captura nueva, otros códigos): sí es stale."""
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            pid, _a = await _seed_srs_v1(session)
            await session.execute(
                update(RequirementItem)
                .where(RequirementItem.project_id == pid)
                .values(status=ReqStatus.REJECTED)
            )
            for code in ("REQ-C", "REQ-D"):
                session.add(RequirementItem(
                    project_id=pid, code=code, statement=f"Req {code}.",
                    type=ReqType.FUNCTIONAL, priority=Priority.MUST,
                ))
            await session.commit()

        result = await _tools(pid)["seed_from_last_srs"].ainvoke({})
        assert result["error"] == "stale_store"
        assert any("solape" in r for r in result["reasons"])
        # El holder NO queda sembrado tras el rechazo.
        run = holder.get_run(pid)
        assert holder.STAGE_QUALITY not in run.stages_done
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_seed_refresh_updates_stale_metrics_for_narrative(monkeypatch):
    """Tras un seed, los conteos de §2.1 reflejan el store vivo, no el snapshot.

    Sesión 53: el seed reutiliza quality_summary/coverage del snapshot del
    documento sembrado (v7: 807 vivos / 518 funcionales); tras la curación el
    store quedó en 804/515. commit_srs re-proyecta trazabilidad y
    requirement_codes desde el store vivo, pero el bloque «Resumen del alcance
    especificado» de §2.1 sale del determinista (armado desde el run) y el
    redactor lo reapende: v8, v9 y v10 dijeron 807/518 pese a que la sección 4
    decía 515. El refresh debe ocurrir antes de redactar.
    """
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            proj = Project(user_id=1, name="srs", slug="srs", description="t")
            session.add(proj)
            await session.flush()
            a = RequirementItem(
                project_id=proj.id, code="REQ-A",
                statement="Alcance de efectivo.",
                type=ReqType.FUNCTIONAL, priority=Priority.MUST,
            )
            b = RequirementItem(
                project_id=proj.id, code="REQ-B", statement="Reportes PDF.",
                type=ReqType.PERFORMANCE, priority=Priority.SHOULD,
            )
            session.add_all([a, b])
            await session.flush()
            session.add(RequirementFinding(
                project_id=proj.id, req_id=a.id, scope=FindingScope.ITEM,
                dimension=FindingDimension.INCOSE_RULE,
                rule_id="incose.modal_missing", severity="major",
                message="Falta verbo modal.", suggestion="Agregar debe.",
                status=FindingStatus.WAIVED,
            ))
            await session.commit()
            # Snapshot v7 STALE: dice 2 vivos y 99 hallazgos (7 blockers).
            await srs_store.create_srs(session, proj.id, {
                "narrative": {"purpose": "v1"},
                "markdown": "# v1",
                "quality_summary": {
                    "total_findings": 99, "blockers": 7, "goals": {"goals": 3},
                },
                "coverage": {"totals": {"live": 2, "functional": 1, "nfr": 1}},
                "requirement_codes": ["REQ-A", "REQ-B"],
                "requirement_count": 2,
            })
            # Curación post-SRS: REQ-B fusionado (el store queda en 1 vivo).
            await session.execute(
                update(RequirementItem)
                .where(RequirementItem.id == b.id)
                .values(status=ReqStatus.MERGED)
            )
            await session.commit()

        async def _passthrough(narrative, **kwargs):
            return narrative

        monkeypatch.setattr(srs_agent, "draft_narrative_llm", _passthrough)
        tools = _tools(proj.id)
        assert (
            await tools["seed_from_last_srs"].ainvoke({})
        )["reused_from_version"] == 1
        assert (
            await tools["draft_narrative"].ainvoke({})
        )["stage"] == "narrative"

        run = holder.get_run(proj.id)
        # Totales re-proyectados desde el store vivo (no el snapshot v7).
        assert run.coverage["totals"] == {"live": 1, "functional": 1, "nfr": 0}
        # Hallazgos recontados del run ya filtrado por el seed (no 99/7).
        assert run.quality_summary["total_findings"] == 1
        assert run.quality_summary["blockers"] == 0
        perspective = run.narrative["overall.perspective"]
        assert "Requerimientos en el SRS: **1**" in perspective
        assert "(funcionales: 1, no funcionales: 0)" in perspective
        assert "99" not in perspective

        # commit_srs persiste la version con los conteos frescos.
        result = await tools["commit_srs"].ainvoke({})
        assert result["requirement_count"] == 1
        assert result["findings"] == 1
        assert result["blockers"] == 0
        async with sm() as session:
            srs = await srs_store.get_latest_srs(session, proj.id)
        assert srs.version == 2
        assert "Requerimientos en el SRS: **1**" in (
            srs.narrative["overall.perspective"]
        )
    finally:
        await engine.dispose()
        tmp.cleanup()


_V1_NARRATIVE = {
    "intro.purpose": "Propósito v1 con prosa curada.",
    "intro.scope": "Alcance v1.",
    "intro.definitions": "- **SRS**: especificación.",
    "intro.references": "- ISO 29148.",
    "intro.overview": "Estructura estándar.",
    "overall.perspective": (
        "Perspectiva v1 curada.\n\n\n**Resumen del alcance especificado:**\n"
        "- Requerimientos en el SRS: **2** (funcionales: 1, no funcionales: 1).\n"
        "- Goals modelados: **3** (softgoals: 2, obstáculos: 1).\n"
        "- Hallazgos de calidad: **99** (bloqueantes: 7)."
    ),
    "overall.features": "- `REQ-B` (SHOULD) — feature vieja de v1.",
    "overall.users": "Usuarios v1.",
    "overall.environment": "Entorno v1.",
    "overall.assumptions": "Supuestos v1.",
}


async def _seed_doc_with_narrative(session) -> int:
    """Proyecto con doc v1 de prosa authored + curación post-SRS. (pid).

    El snapshot del doc miente a propósito (2 vivos, 99 hallazgos): replica la
    sesión 53, donde el refresh debe conservar la prosa authored y re-proyectar
    los conteos desde el store vivo (REQ-B fusionado queda 1 vivo funcional).
    """
    proj = Project(user_id=1, name="srs", slug="srs", description="t")
    session.add(proj)
    await session.flush()
    a = RequirementItem(
        project_id=proj.id, code="REQ-A", statement="Alcance de efectivo.",
        type=ReqType.FUNCTIONAL, priority=Priority.MUST,
    )
    b = RequirementItem(
        project_id=proj.id, code="REQ-B", statement="Reportes PDF.",
        type=ReqType.PERFORMANCE, priority=Priority.SHOULD,
    )
    session.add_all([a, b])
    await session.flush()
    session.add(RequirementFinding(
        project_id=proj.id, req_id=a.id, scope=FindingScope.ITEM,
        dimension=FindingDimension.INCOSE_RULE,
        rule_id="incose.modal_missing", severity="major",
        message="Falta verbo modal.", suggestion="Agregar debe.",
        status=FindingStatus.WAIVED,
    ))
    await session.commit()
    await srs_store.create_srs(session, proj.id, {
        "narrative": dict(_V1_NARRATIVE),
        "markdown": "# v1",
        "quality_summary": {
            "total_findings": 99, "blockers": 7, "goals": {"goals": 3},
        },
        "coverage": {"totals": {"live": 2, "functional": 1, "nfr": 1}},
        "requirement_codes": ["REQ-A", "REQ-B"],
        "requirement_count": 2,
    })
    await session.execute(
        update(RequirementItem)
        .where(RequirementItem.id == b.id)
        .values(status=ReqStatus.MERGED)
    )
    await session.commit()
    return proj.id


@pytest.mark.asyncio
async def test_seed_carries_narrative_and_draft_reuses_verbatim(monkeypatch):
    """El seed conserva la prosa authored; draft sin instrucciones la reusa.

    Sesión 53 v11: el seed solo cargaba findings/goals/coverage, así que el
    redactor NUNCA recibía el texto authored de la versión previa y lo
    re-draftó de cero marcando 7 de 9 secciones con «nota provisional». El
    usuario pidió la v8 verbatim y no existía canal para dársela.
    """
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            pid = await _seed_doc_with_narrative(session)

        async def _forbidden_llm(*_a, **_k):
            raise AssertionError("el reuso verbatim no debe llamar al redactor")

        monkeypatch.setattr(srs_agent, "draft_narrative_llm", _forbidden_llm)
        tools = _tools(pid)
        seed_result = await tools["seed_from_last_srs"].ainvoke({})
        assert seed_result["narrative_carried"] is True
        result = await tools["draft_narrative"].ainvoke({})
        assert result["mode"] == "reused_previous"

        run = holder.get_run(pid)
        # Prosa authored preservada verbatim de la versión sembrada.
        assert run.narrative["intro.purpose"] == "Propósito v1 con prosa curada."
        assert run.narrative["overall.users"] == "Usuarios v1."
        # Bloque de conteos re-proyectado (fresco); el stale fue removido.
        perspective = run.narrative["overall.perspective"]
        assert perspective.startswith("Perspectiva v1 curada.")
        assert "Requerimientos en el SRS: **1**" in perspective
        assert "(funcionales: 1, no funcionales: 0)" in perspective
        assert "**99**" not in perspective
        assert "**2**" not in perspective
        # Deterministas frescos: la feature del req fusionado ya no aparece.
        assert "feature vieja de v1" not in run.narrative["overall.features"]

        commit = await tools["commit_srs"].ainvoke({})
        assert commit["requirement_count"] == 1
        async with sm() as session:
            srs = await srs_store.get_latest_srs(session, pid)
        assert srs.narrative["intro.purpose"] == "Propósito v1 con prosa curada."
        assert "Requerimientos en el SRS: **1**" in (
            srs.narrative["overall.perspective"]
        )
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_seed_draft_with_instructions_revises_carried_text(monkeypatch):
    """Con instrucciones, el redactor recibe el texto previo como base."""
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        async with sm() as session:
            pid = await _seed_doc_with_narrative(session)

        seen: dict = {}

        async def _spy(base, **kwargs):
            seen.update(kwargs)
            seen["base"] = base
            return base

        monkeypatch.setattr(srs_agent, "draft_narrative_llm", _spy)
        tools = _tools(pid)
        await tools["seed_from_last_srs"].ainvoke({})
        result = await tools["draft_narrative"].ainvoke({
            "instructions": "mencionar el catálogo de roles en el propósito",
        })
        assert result["mode"] == "revised_previous"
        assert (
            seen["instructions"]
            == "mencionar el catálogo de roles en el propósito"
        )
        # La base previa viaja al redactor (prosa authored de la v sembrada).
        assert seen["base"]["intro.purpose"] == "Propósito v1 con prosa curada."
        assert seen["previous_narrative"]["overall.users"] == "Usuarios v1."
        run = holder.get_run(pid)
        assert run.narrative["intro.purpose"] == "Propósito v1 con prosa curada."
    finally:
        await engine.dispose()
        tmp.cleanup()


def test_prompt_teaches_the_seed_shortcut():
    """Sin este pin el subagente re-arranca el pipeline completo por default."""
    p = srs_agent.SRS_AGENT_PROMPT
    assert "seed_from_last_srs" in p
    assert "solo-narrativa" in p or "solo la NARRATIVA" in p


def test_prompt_pins_curation_refresh_path():
    """La curación se refleja en el doc con seed, nunca con el pipeline.

    Sesión 53: tras curar links el guard de seed devolvía stale_store cuyo
    mensaje ordenaba el pipeline completo; infer_goals reemplaza los links de
    los goals re-inferidos y habría PISADO los 220 link_goal a mano.
    """
    p = srs_agent.SRS_AGENT_PROMPT
    assert "seed_from_last_srs + draft_narrative + commit_srs" in p
    assert "PISA" in p  # advertencia explícita del costo del re-run
    assert "link_goals" in p  # tool de lote para pares ya conocidos


def test_seed_tool_is_registered():
    tools = srs_agent._make_stage_tools(1, "p", "d")
    names = [t.name for t in tools]
    assert "seed_from_last_srs" in names
    # Gotcha del archivo: definida Y registrada (una tool sin registrar no
    # llega al agente y no falla ningun import).
    assert names[0] == "seed_from_last_srs"
