"""Convergencia de curas de calidad SRS (anti-goteo post sesión 18).

Las cuatro palancas del plan «el agente encuentra problemas nuevos en cada
iteración del SRS»:

1. OLAS DE CURACIÓN: apply_cures NO re-analiza (marca la ola); una sola tool
   close_curation_wave corre el único re-análisis delta de la ola y reporta
   el DELTA (resueltos/nuevos) contra el snapshot de apertura. commit_srs
   cierra cualquier ola olvidada (guard previo a persistir).
2. IDENTIDAD ESTABLE: rule_id del juez LLM es un vocabulario canónico cerrado
   (Literal) y el merge de hallazgos de conjunto matchea por (dimension,
   rule_id) SIN el message (que cambia en cada corrida).
3. JUEZ CON PRESUPUESTO: máx 2 hallazgos por ítem, 1 por regla (dedupe),
   prompt con umbral de materialidad; posesivos su/sus ya no disparan
   smell.pronoun salvo doble posesivo/cuyos.
4. CURAS MASIVAS: apply_cure_batch preview/aplica una regla completa,
   chunked, marcando la ola.
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
from backend.agents.subagents import srs_agent
from backend.models import Base, Priority, Project, ReqType, RequirementItem
from backend.models.srs import (
    FindingDimension,
    FindingScope,
    FindingSeverity,
    RequirementFinding,
)
from backend.services import srs_quality, srs_store


async def _fresh_db(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(srs_agent, "AsyncSessionLocal", sm)
    monkeypatch.setattr(
        "backend.agents.tools.requirements_tools.AsyncSessionLocal", sm
    )
    return sm, tmp, engine


async def _mk_project(sm) -> int:
    async with sm() as session:
        proj = Project(user_id=1, name="srs", slug="srs", description="t")
        session.add(proj)
        await session.commit()
        return proj.id


async def _mk_req(sm, pid: int, code: str, statement: str) -> int:
    async with sm() as session:
        item = RequirementItem(
            project_id=pid, code=code, statement=statement,
            type=ReqType.FUNCTIONAL, priority=Priority.MUST,
        )
        session.add(item)
        await session.commit()
        return item.id


async def _mk_finding(
    sm,
    pid: int,
    req_id: int | None,
    rule_id: str,
    *,
    severity: str = "minor",
    message: str = "m",
    suggestion: str | None = None,
    status: str = "open",
) -> int:
    async with sm() as session:
        f = RequirementFinding(
            project_id=pid,
            req_id=req_id,
            scope=FindingScope.ITEM if req_id is not None else FindingScope.SET,
            dimension=FindingDimension.REQUIREMENT_SMELL,
            rule_id=rule_id,
            severity=FindingSeverity(severity),
            message=message,
            suggestion=suggestion,
            status=status,
        )
        session.add(f)
        await session.commit()
        return f.id


def _tools(pid: int) -> dict:
    return {t.name: t for t in srs_agent._make_stage_tools(pid, "srs", "t")}


def _fake_finding(req_id: int, rule_id: str) -> dict:
    """Dict de hallazgo con la forma completa que el merge espera."""
    return {
        "scope": FindingScope.ITEM,
        "req_id": req_id,
        "dimension": FindingDimension.REQUIREMENT_SMELL,
        "rule_id": rule_id,
        "severity": FindingSeverity.MINOR,
        "message": "m",
        "suggestion": None,
        "ears_pattern": None,
        "detected_by": "programmatic",
    }


# --- 2/3: vocabulario canónico + presupuesto del juez ------------------------


def test_llm_finding_rule_id_is_canonical_vocabulary():
    """El schema del juez no acepta rule_id libre: Literal cerrado de 6."""
    import pydantic

    args = srs_quality.LlmFinding.model_fields["rule_id"].annotation.__args__
    assert set(args) == {
        "sem.ambiguous", "sem.quantification", "sem.ears_rewrite",
        "sem.incomplete", "sem.missing_requirement", "sem.set_inconsistency",
    }
    # Un id inventado por el LLM ya no pasa la validación estructurada.
    with pytest.raises(pydantic.ValidationError):
        srs_quality.LlmFinding(
            rule_id="SEM-AMB-01", dimension="ambiguity",
            severity="major", message="x",
        )


def test_verdict_to_findings_caps_two_and_dedupes_by_rule():
    v = srs_quality.ItemQualityVerdict(
        item_id="1",
        findings=[
            srs_quality.LlmFinding(
                rule_id="sem.ambiguous", dimension="ambiguity",
                severity="major", message="a1",
            ),
            srs_quality.LlmFinding(
                rule_id="sem.quantification", dimension="ambiguity",
                severity="minor", message="q1",
            ),
            srs_quality.LlmFinding(
                rule_id="sem.incomplete", dimension="incose_rule",
                severity="minor", message="i1",
            ),
            # Duplicado de regla (el LLM a veces emite 2 ambigüedades).
            srs_quality.LlmFinding(
                rule_id="sem.ambiguous", dimension="ambiguity",
                severity="major", message="a2",
            ),
        ],
    )
    out = srs_quality._verdict_to_findings(v, req_id=77)
    assert len(out) == 2  # cap mecánico
    assert out[0]["message"] == "a1"  # conserva el primero (más material)
    assert out[1]["rule_id"] == "sem.quantification"


def test_judge_prompt_carries_budget_and_materiality():
    sys_prompt = srs_quality._QUALITY_SYSTEM
    assert "AT MOST the 2 most MATERIAL" in sys_prompt
    assert "Stylistic nuances" in sys_prompt
    assert "sem.ambiguous" in sys_prompt  # vocabulario canónico explicado


# --- 2: identidad de hallazgos de conjunto sin message -----------------------


@pytest.mark.asyncio
async def test_set_finding_identity_ignores_message(monkeypatch):
    """El hallazgo de conjunto sobrevive aunque cambie el mensaje (conteo).

    Regresión del reciclaje v21 (inserted 192 / deleted 209): «7 reqs sin
    goal» y «3 reqs sin goal» son EL MISMO hallazgo gore.unlinked_requirements
    y la curación fixed/waived debe sobrevivir al cambio de conteo.
    """
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        rid = await _mk_finding(
            sm, pid, None, "gore.unlinked_requirements",
            message="7 requerimientos vivos no aportan a ningún goal",
            status="waived",
        )
        # La tanda fresca trae el mismo hallazgo con conteo distinto.
        async with sm() as session:
            stats = await srs_store.merge_findings(
                session, pid,
                [{
                    "scope": FindingScope.SET,
                    "req_id": None,
                    "dimension": FindingDimension.REQUIREMENT_SMELL,
                    "rule_id": "gore.unlinked_requirements",
                    "severity": "major",
                    "message": "3 requerimientos vivos no aportan a ningún goal",
                    "suggestion": None,
                    "detected_by": "programmatic",
                }],
            )
        assert stats["matched"] == 1
        assert stats["inserted"] == 0
        async with sm() as session:
            rows = await srs_store.list_findings(session, pid)
            assert len(rows) == 1
            assert rows[0].id == rid
            assert rows[0].status.value == "waived"  # curación sobrevive
            assert "3 requerimientos" in rows[0].message  # mensaje refrescado
    finally:
        await engine.dispose()
        tmp.cleanup()


# --- 3: smell.pronoun ya no cuenta posesivos simples --------------------------


def test_pronoun_rule_ignores_simple_possessives():
    from backend.agents.pipelines._quality_rules import (
        programmatic_findings_for_text,
    )

    assert not any(
        f.rule_id == "smell.pronoun"
        for f in programmatic_findings_for_text(
            "El sistema debe actualizar su estado."
        )
    )
    hits = programmatic_findings_for_text(
        "El sistema debe registrar su patente y su estado de ruta."
    )
    assert any(f.rule_id == "smell.pronoun" for f in hits)
    assert any(
        f.rule_id == "smell.pronoun"
        for f in programmatic_findings_for_text(
            "El conductor cuya licencia venció debe notificar."
        )
    )


# --- 1: olas de curación ------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_cures_marks_wave_without_reanalysis(monkeypatch):
    """apply_cures default: aplica ediciones, marca la ola y NO re-analiza.

    Regresión del loop de la sesión 18: cada cura disparaba su propio
    re-análisis LLM (re-juzgando el texto re-escrito) y el backlog nunca
    mostraba progreso.
    """
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        await _mk_req(sm, pid, "REQ-A", "El sistema debe actualizar su estado.")
        # Run activo (en el flujo real lo crea analyze_quality al arrancar).
        holder = srs_agent.get_or_create_run(
            pid, project_name="srs", project_description="t"
        )
        holder.findings = [
            {"req_id": 1, "rule_id": "smell.pronoun", "message": "m"},
        ]
        calls = {"n": 0}

        async def _fail_analyze(*a, **kw):
            calls["n"] += 1
            raise AssertionError("analyze_quality NO debe llamarse por cura")

        monkeypatch.setattr(srs_quality, "analyze_quality", _fail_analyze)
        result = await _tools(pid)["apply_cures"].ainvoke({
            "edits": [{
                "code": "REQ-A",
                "statement": "El operador de flota debe actualizar el estado "
                "del viaje al llegar al destino.",
                "reason": "actor faltante",
            }],
        })
        assert result["edits_applied"] == 1
        assert result["wave_open"] is True
        assert "close_curation_wave" in result["message"]
        assert calls["n"] == 0  # cero re-análisis por cura
        holder = srs_agent.get_run(pid)
        assert holder.dirty_req_ids, "la ola debe quedar marcada"
        assert holder.wave_snapshot is not None
    finally:
        srs_agent.clear_run(pid)
        await engine.dispose()
        tmp.cleanup()


def test_wave_snapshot_and_delta_report():
    """El snapshot de apertura se toma en la 1ª cura; el reporte es DELTA."""
    holder = srs_agent.get_or_create_run(
        1, project_name="srs", project_description="t"
    )
    try:
        holder.findings = [
            {"req_id": 1, "rule_id": "smell.pronoun", "message": "m"},
            {"req_id": 2, "rule_id": "sem.ambiguous", "message": "m"},
        ]
        # 1ª cura: abre la ola y congela el inventario.
        holder.mark_dirty([1])
        assert holder.wave_snapshot == {
            ("1", "smell.pronoun"), ("2", "sem.ambiguous"),
        }
        assert holder.dirty_req_ids == {1}

        # 2ª cura dentro de la misma ola: el snapshot NO cambia.
        holder.mark_dirty([2])
        assert holder.wave_snapshot == {
            ("1", "smell.pronoun"), ("2", "sem.ambiguous"),
        }

        # Inventario fresco tras el re-análisis: el 1 se curó (ya no tiene
        # hallazgos), el 2 conserva el suyo y aparece uno nuevo en el 3.
        fresh = [
            {"req_id": 2, "rule_id": "sem.ambiguous", "message": "m"},
            {"req_id": 3, "rule_id": "smell.combinator", "message": "m"},
        ]
        report = holder.wave_report(fresh)
        assert report["resolved_count"] == 1
        assert report["resolved"] == [{"req_id": "1", "rule_id": "smell.pronoun"}]
        assert report["new_count"] == 1
        assert report["new"] == [{"req_id": "3", "rule_id": "smell.combinator"}]
        assert holder.wave_snapshot is None  # ola cerrada
    finally:
        srs_agent.clear_run(1)


@pytest.mark.asyncio
async def test_close_curation_wave_reports_delta(monkeypatch):
    """close_curation_wave: UN re-análisis, reporte delta contra snapshot."""
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        await _mk_req(sm, pid, "REQ-A", "El sistema debe actualizar su estado.")
        await _mk_req(sm, pid, "REQ-B", "Reportes en PDF para el administrador.")

        holder = srs_agent.get_or_create_run(
            pid, project_name="srs", project_description="t"
        )
        # Inventario previo a la cura: smell.pronoun sobre REQ-A.
        holder.findings = [
            {"req_id": 1, "rule_id": "smell.pronoun", "message": "m"},
        ]
        result = await _tools(pid)["apply_cures"].ainvoke({
            "edits": [{
                "code": "REQ-A",
                "statement": "El operador de flota debe actualizar el estado "
                "del viaje al llegar al destino.",
            }],
        })
        assert result["wave_open"] is True

        # Re-análisis delta simulado: REQ-A curado del pronoun pero con un
        # hallazgo nuevo sobre el texto nuevo; REQ-B conserva el suyo.
        async def _fake_analyze(session, project_id, on_progress=None, *, incremental=True):
            summary = {
                "items_judged": 2, "items_reused": 0, "total_findings": 2,
                "blockers": 0, "blockers_detail": [],
                "by_severity": {"minor": 2},
            }
            findings = [
                {"req_id": 1, "rule_id": "sem.quantification", "message": "n"},
                {"req_id": 2, "rule_id": "sem.ambiguous", "message": "m"},
            ]
            return summary, findings

        monkeypatch.setattr(srs_quality, "analyze_quality", _fake_analyze)
        closed = await _tools(pid)["close_curation_wave"].ainvoke({})
        assert closed["wave_open"] is False
        delta = closed["wave_delta"]
        assert delta["resolved_count"] == 1  # smell.pronoun de REQ-A
        assert delta["resolved"] == [{"req_id": "1", "rule_id": "smell.pronoun"}]
        assert delta["new_count"] == 2
        assert closed["items_judged"] == 2

        # Sin ediciones pendientes: no-op declarado.
        noop = await _tools(pid)["close_curation_wave"].ainvoke({})
        assert "No hay ola abierta" in noop["message"]
    finally:
        srs_agent.clear_run(pid)
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_commit_closes_dirty_wave_before_persisting(monkeypatch):
    """Guard del commit: persiste con la ola cerrada (jamás hallazgos rancios)."""
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        await _mk_req(sm, pid, "REQ-A", "Requerimiento A del sistema.")
        from backend.services.srs_assembler import SRS_STRUCTURE

        async with sm() as session:
            await srs_store.create_srs(session, pid, {
                "narrative": {
                    "intro.purpose": "p", "intro.scope": "s",
                    "overall.assumptions": "a",
                },
                "structure": SRS_STRUCTURE,
                "markdown": "# srs",
                "quality_summary": {"total_findings": 0, "blockers": 0},
                "coverage": {"totals": {"live": 1}},
                "requirement_codes": ["REQ-A"],
                "requirement_count": 1,
            })

        holder = srs_agent.get_or_create_run(
            pid, project_name="srs", project_description="t"
        )
        holder.findings = [_fake_finding(1, "smell.pronoun")]
        holder.quality_summary = {"total_findings": 1, "blockers": 0}
        holder.coverage = {"totals": {"live": 1}}
        holder.goals_summary = {"goals": 0}
        holder.narrative = {
            "intro.purpose": "p", "intro.scope": "s",
            "overall.assumptions": "a",
        }
        holder.stages_done.update({"quality", "goals", "coverage", "narrative"})
        holder.mark_dirty([1])
        analyzed = {"n": 0}

        async def _fake_analyze(session, project_id, on_progress=None, *, incremental=True):
            analyzed["n"] += 1
            summary = {
                "items_judged": 1, "items_reused": 0, "total_findings": 1,
                "blockers": 0, "blockers_detail": [],
                "by_severity": {"minor": 1},
                "fresh_judged_req_ids": [1],
            }
            return summary, [_fake_finding(1, "smell.combinator")]

        monkeypatch.setattr(srs_quality, "analyze_quality", _fake_analyze)
        result = await _tools(pid)["commit_srs"].ainvoke({})
        assert result.get("error") is None
        assert analyzed["n"] == 1  # el guard cerró la ola ANTES de persistir
        assert result["wave_delta"]["resolved_count"] == 1
        # El inventario persistido es el fresco (combinator), no el rancio.
        async with sm() as session:
            rows = await srs_store.list_findings(session, pid)
            assert {f.rule_id for f in rows} == {"smell.combinator"}
    finally:
        srs_agent.clear_run(pid)
        await engine.dispose()
        tmp.cleanup()


# --- 4: curas masivas por patrón ----------------------------------------------


@pytest.mark.asyncio
async def test_apply_cure_batch_preview_and_apply(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        id_a = await _mk_req(sm, pid, "REQ-A", "El sistema debe marcar su ruta.")
        id_b = await _mk_req(sm, pid, "REQ-B", "El supervisor debe revisar su cola.")
        # Dos con reescritura completa (aplicables) + uno de solo consejo.
        await _mk_finding(
            sm, pid, id_a, "smell.pronoun",
            suggestion="El sistema debe marcar la ruta del viaje.",
        )
        await _mk_finding(
            sm, pid, id_b, "smell.pronoun",
            suggestion="El supervisor debe revisar la cola de aprobaciones.",
        )
        await _mk_finding(sm, pid, id_b, "smell.vague_term")
        tools = _tools(pid)

        preview = await tools["apply_cure_batch"].ainvoke({
            "rule_id": "smell.pronoun",
        })
        assert preview["applicable"] == 2
        assert preview["total_open"] == 2
        assert len(preview["preview"]) == 2
        assert preview["message"].startswith("2 de 2")

        applied = await tools["apply_cure_batch"].ainvoke({
            "rule_id": "smell.pronoun", "dry_run": False,
        })
        assert applied["edits_applied"] == 2
        assert applied["wave_open"] is True
        holder = srs_agent.get_run(pid)
        assert holder.dirty_req_ids == {id_a, id_b}
        from sqlalchemy import select

        async with sm() as session:
            stmts = {
                s
                for (s,) in (
                    await session.execute(
                        select(RequirementItem.statement).where(
                            RequirementItem.project_id == pid
                        )
                    )
                ).all()
            }
            assert "El sistema debe marcar la ruta del viaje." in stmts
            assert "El supervisor debe revisar la cola de aprobaciones." in stmts

        # Regla sin sugerencias: preview vacío con motivo, sin editar nada.
        none = await tools["apply_cure_batch"].ainvoke({
            "rule_id": "smell.vague_term",
        })
        assert none["applicable"] == 0
        assert "no aplica" in none["message"]
    finally:
        srs_agent.clear_run(pid)
        await engine.dispose()
        tmp.cleanup()


# --- prompt del subagente: disciplina de olas ---------------------------------


def test_srs_prompt_carries_wave_discipline():
    prompt = srs_agent.SRS_AGENT_PROMPT
    assert "CURA DE BLOCKERS POR OLAS" in prompt
    assert "close_curation_wave" in prompt
    assert "NUNCA pidas un análisis entre tandas" in prompt


# --- curas del capture-agent con deuda de veredicto LLM ------------------------


@pytest.mark.asyncio
async def test_update_requirements_reports_llm_findings_pending(monkeypatch):
    """La cura por update_requirements reporta los sem.* sin veredicto.

    Sesión 18 (cura de 12:34 por el capture-agent): findings_closed=0 sobre
    hallazgos LLM heredados y el agente quedó sin forma de saber que la cura
    no produjo veredictos — los infirió del historial de revisiones.
    """
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        await _mk_req(sm, pid, "REQ-A", "El operador debe registrar su jornada.")
        await _mk_finding(
            sm, pid, 1, "sem.ambiguous",
            severity="major", message="«jornada» ambiguo",
        )
        # Un determinista que la cura SÍ cierra (deja de disparar).
        await _mk_finding(
            sm, pid, 1, "smell.pronoun", message="posesivo",
        )
        tools = _tools(pid)
        result = await tools["update_requirements"].ainvoke({
            "updates": [{
                "code": "REQ-A",
                "statement": "El operador de terreno debe registrar la hora "
                "de inicio y fin de cada viaje.",
                "reason": "cura ola 1",
            }],
        })
        assert result["edits_applied"] if False else len(result["updated"]) == 1
        assert result["findings_closed"] == 1  # el pronoun determinista
        pending = result["llm_findings_pending"]
        assert pending == [{"code": "REQ-A", "rule_id": "sem.ambiguous"}]
        assert "re-análisis" in result["message"]
        # El sem.ambiguous sigue OPEN (su veredicto llega con /srs).
        async with sm() as session:
            rows = await srs_store.list_findings(session, pid)
            by_rule = {f.rule_id: f.status.value for f in rows}
            assert by_rule["sem.ambiguous"] == "open"
            assert by_rule["smell.pronoun"] == "fixed"
    finally:
        await engine.dispose()
        tmp.cleanup()


def test_capture_agent_has_quality_read_tools():
    """El capture-agent ve el backlog: get_quality + get_requirement_findings.

    Sesión 18: la cura de majors se hizo SIN herramientas de calidad (el
    agente lo declaró: «mi toolset no expone get_requirement_findings») y
    tuvo que inferir veredictos del historial de revisiones.
    """
    from backend.agents.subagents.requirements_capture_agent import (
        make_requirements_capture_agent_subagent,
    )

    spec = make_requirements_capture_agent_subagent(
        project_id=1, profile="t", project_slug="t",
        project_name="n", project_description="d",
    )
    names = {t.name for t in spec["tools"]}
    assert {"get_quality", "get_requirement_findings"} <= names


def test_capture_prompt_carries_quality_section():
    from backend.agents.subagents.requirements_capture_agent import (
        REQUIREMENTS_CAPTURE_AGENT_PROMPT,
    )

    assert "CALIDAD DE REQUERIMIENTOS" in REQUIREMENTS_CAPTURE_AGENT_PROMPT
    assert "llm_findings_pending" in REQUIREMENTS_CAPTURE_AGENT_PROMPT
    assert "NO re-analices por tu cuenta" in REQUIREMENTS_CAPTURE_AGENT_PROMPT


# --- consolidación de goals (edit_goal / merge_goals) --------------------------


async def _mk_goal(
    sm,
    pid: int,
    code: str,
    *,
    status: str = "proposed",
    kind: str = "functional_goal",
    statement: str = "goal",
) -> int:
    from backend.models.srs import Goal, GoalKind, GoalStatus

    async with sm() as session:
        g = Goal(
            project_id=pid, code=code, statement=statement,
            kind=GoalKind(kind), status=GoalStatus(status),
            confidence=0.9,
        )
        session.add(g)
        await session.commit()
        return g.id


async def _mk_link(sm, pid: int, goal_id: int, req_id: int, *, human: bool = False):
    from backend.models.srs import GoalLink, LinkRelation

    async with sm() as session:
        session.add(GoalLink(
            goal_id=goal_id, req_id=req_id,
            relation=LinkRelation.REALIZES,
            detected_by="human" if human else "agent",
        ))
        await session.commit()


@pytest.mark.asyncio
async def test_merge_goals_repoints_links_and_rejects_absorbed(monkeypatch):
    """El motor re-apunta aristas in-place y retira absorbidos como REJECTED.

    Deuda documentada por el agente (sesión 18): la consolidación del
    catálogo (102 STALE + solapados) exigía «CRUD de goals nivel motor» que
    no estaba expuesto. Las aristas humanas (detected_by='human') deben
    sobrevivir al re-apunte.
    """
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        await _mk_req(sm, pid, "REQ-A", "Req A del sistema.")
        await _mk_req(sm, pid, "REQ-B", "Req B del sistema.")
        id_k = await _mk_goal(sm, pid, "GOAL-KEEP", statement="Keeper original")
        id_a = await _mk_goal(sm, pid, "GOAL-ABS1")
        # ABS2 con decisión humana CONFIRMED (kind igual al keeper: fusible).
        await _mk_goal(sm, pid, "GOAL-ABS2", status="confirmed")
        # Aristas: keeper ya cubre REQ-A; ABS1 cubre REQ-A (dup) y REQ-B (humano).
        await _mk_link(sm, pid, id_k, 1)
        await _mk_link(sm, pid, id_a, 1)
        await _mk_link(sm, pid, id_a, 2, human=True)
        from backend.models.srs import GoalLink, LinkRelation

        async with sm() as session:
            stats = await srs_store.merge_goals(
                session, pid,
                keeper_code="GOAL-KEEP",
                absorbed_codes=["GOAL-ABS1", "GOAL-ABS2"],
                statement="Keeper consolidado",
            )
        assert stats == {
            "keeper": "GOAL-KEEP",
            "merged_links": 1,   # REQ-B re-apuntado in-place
            "dropped_links": 1,  # REQ-A duplicado eliminado (queda la del keeper)
            "absorbed": ["GOAL-ABS1", "GOAL-ABS2"],
            "subgoals_reparented": 0,
        }
        async with sm() as session:
            links = await srs_store.list_goal_links(session, pid)
            assert len(links) == 2
            by_req = {l.req_id: l for l in links}
            assert all(l.goal_id == id_k for l in links)
            assert by_req[2].detected_by == "human"  # curación preservada
            from sqlalchemy import select
            from backend.models.srs import Goal

            goals = (
                await session.scalars(
                    select(Goal).where(Goal.project_id == pid)
                )
            ).all()
            by_code = {g.code: g for g in goals}
            assert by_code["GOAL-KEEP"].statement == "Keeper consolidado"
            assert by_code["GOAL-KEEP"].status.value == "proposed"
            # Absorbidos: REJECTED (soft, decisión humana que la re-inferencia
            # preserva) y SIN parent_id.
            assert all(
                by_code[c].status.value == "rejected"
                for c in ("GOAL-ABS1", "GOAL-ABS2")
            )

        # Guardas: keeper inexistente, self-merge y kind distinto con decisión.
        async with sm() as session:
            from backend.models.srs import Goal, GoalKind, GoalStatus

            session.add(Goal(
                project_id=pid, code="GOAL-SOFT", statement="soft",
                kind=GoalKind.SOFTGOAL, status=GoalStatus.CONFIRMED,
            ))  # GoalKind/Status comparan por valor Enum, no por literal
            await session.commit()
        tools = {t.name: t for t in srs_agent._make_stage_tools(pid, "srs", "t")}
        nf = await tools["merge_goals"].ainvoke({
            "keeper_code": "GOAL-NOPE", "absorbed_codes": ["GOAL-KEEP"],
        })
        assert nf["error"] == "goal_not_found"
        self_m = await tools["merge_goals"].ainvoke({
            "keeper_code": "GOAL-KEEP", "absorbed_codes": ["GOAL-KEEP"],
        })
        assert self_m["error"] == "merge_rejected"
        kind_m = await tools["merge_goals"].ainvoke({
            "keeper_code": "GOAL-KEEP", "absorbed_codes": ["GOAL-SOFT"],
        })
        assert kind_m["error"] == "merge_rejected"
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_edit_goal_revives_stale_and_guards_status(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        await _mk_goal(sm, pid, "GOAL-ST1", status="stale")
        tools = {t.name: t for t in srs_agent._make_stage_tools(pid, "srs", "t")}
        result = await tools["edit_goal"].ainvoke({
            "goal_code": "GOAL-ST1",
            "status": "PROPOSED",
            "statement": "Enunciado revisado del goal",
        })
        assert result["goal"] == "GOAL-ST1"
        assert result["status"] == "proposed"
        async with sm() as session:
            g = await srs_store.get_goal(
                session, (await srs_store.list_goals(session, pid))[0].id,
                project_id=pid,
            )
            assert g.statement == "Enunciado revisado del goal"
        bad = await tools["edit_goal"].ainvoke({
            "goal_code": "GOAL-ST1", "status": "chau",
        })
        assert bad["error"] == "invalid_status"
        nf = await tools["edit_goal"].ainvoke({"goal_code": "GOAL-NOPE"})
        assert nf["error"] == "goal_not_found"
    finally:
        await engine.dispose()
        tmp.cleanup()


def test_srs_prompt_carries_goal_consolidation():
    prompt = srs_agent.SRS_AGENT_PROMPT
    assert "CONSOLIDACION DEL CATALOGO DE GOALS" in prompt
    assert "merge_goals" in prompt
    assert "edit_goal" in prompt
