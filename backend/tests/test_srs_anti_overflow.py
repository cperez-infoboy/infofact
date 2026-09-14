"""Anti-cascada y anti-overflow del SRS (incidente de la sesión 17 de Planitrack2.0).

La sesión 17 mostró dos problemas que se retroalimentan:

1. CASCADA: cada ajuste conversacional («agregá esta nota») generaba una
   versión COMPLETA del SRS (seed + draft + commit), porque el commit era el
   único escritor. Hoy existen dos palancas: ``patch_narrative_section``
   (edición in-place de una subsección authored, sin versión nueva) y el
   guard anti-no-op del commit (firma de contenido persistida: si el
   candidato es idéntico a la versión vigente, no crea versión).

2. OVERFLOW: ``get_latest_srs`` devolvía el documento entero (2.9 MB) y
   ``get_quality`` todos los hallazgos (1.9 MB); el summarizer de deepagents
   reenvió esa historia SIN recortar al LLM y Anthropic rechazó el prompt
   (1261 prompt is too long). Hoy las read tools tienen techos y paginación,
   y el summarizer se construye con techo (make_summarization_middleware).
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
from backend.agents.tools.srs_tools import make_srs_read_tools
from backend.models import Base, Project, ReqType, RequirementItem
from backend.models.srs import (  # noqa: F401 — SrsStatus: contraste de estados
    FindingDimension,
    FindingScope,
    FindingSeverity,
    RequirementFinding,
    SrsStatus,
)
from backend.services import srs_store
from backend.services.srs_assembler import SRS_STRUCTURE


async def _fresh_db(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(srs_agent, "AsyncSessionLocal", sm)
    monkeypatch.setattr("backend.agents.tools.srs_tools.AsyncSessionLocal", sm)
    return sm, tmp, engine


async def _mk_project(sm) -> int:
    async with sm() as session:
        proj = Project(user_id=1, name="srs", slug="srs", description="t")
        session.add(proj)
        await session.commit()
        return proj.id


async def _mk_version(sm, pid: int, *, narrative: dict | None = None) -> int:
    async with sm() as session:
        row = await srs_store.create_srs(session, pid, {
            "narrative": narrative
            or {
                "intro.purpose": "prosa",
                "intro.scope": "alcance",
                "overall.assumptions": "supuestos",
            },
            "structure": SRS_STRUCTURE,
            "markdown": "# srs",
            "quality_summary": {"total_findings": 0, "blockers": 0},
            "coverage": {"totals": {"live": 0}},
            "requirement_codes": [],
            "requirement_count": 0,
        })
        return row.version


def _tools(pid: int) -> dict:
    return {
        t.name: t
        for t in (
            srs_agent._make_stage_tools(pid, "srs", "t")
            + make_srs_read_tools(pid)
        )
    }


# --- patch_narrative_section: edición in-place sin versión nueva -------------


@pytest.mark.asyncio
async def test_patch_edits_section_in_place_without_new_version(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        v = await _mk_version(sm, pid)
        result = await _tools(pid)["patch_narrative_section"].ainvoke({
            "section_id": "overall.assumptions",
            "text": "Supuestos revisados: operación multi-industria.",
            "note": "nota pedida por el usuario",
        })
        assert result["version"] == v  # misma versión, in-place
        async with sm() as session:
            srs = await srs_store.get_srs_version(session, pid, v)
            assert "multi-industria" in srs.narrative["overall.assumptions"]
            # El markdown se regeneró con la prosa nueva.
            assert "multi-industria" in srs.markdown
            # Auditoría en review_flags.
            patches = srs.review_flags["narrative_patches"]
            assert patches[-1]["section_id"] == "overall.assumptions"
            assert patches[-1]["note"] == "nota pedida por el usuario"
        # Sigue habiendo exactamente 1 versión del proyecto.
        async with sm() as session:
            assert len(await srs_store.list_srs_versions(session, pid)) == 1
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_patch_rejects_unknown_section_and_noop(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        await _mk_version(sm, pid)
        unknown = await _tools(pid)["patch_narrative_section"].ainvoke({
            "section_id": "intro.references",
            "text": "x",
        })
        assert unknown["error"] == "not_found"
        noop = await _tools(pid)["patch_narrative_section"].ainvoke({
            "section_id": "intro.purpose",
            "text": "  prosa  ",
        })
        assert noop["error"] == "patch_rejected"
    finally:
        await engine.dispose()
        tmp.cleanup()


# --- guard anti-no-op del commit ---------------------------------------------

NARR = {
    "intro.purpose": "p",
    "intro.scope": "s",
    "intro.definitions": "d",
    "overall.users": "u",
    "overall.environment": "e",
    "overall.assumptions": "a",
}


async def _seed_commit_run(pid: int, *, narrative: dict) -> None:
    """Siembra el holder con todo lo que commit_srs exige antes de escribir."""
    holder = srs_agent.get_or_create_run(
        pid, project_name="srs", project_description="t"
    )
    holder.narrative = narrative
    holder.quality_summary = {"total_findings": 0, "blockers": 0}
    holder.coverage = {"totals": {"live": 1}}
    holder.goals_summary = {"goals": 0}
    for stage in ("quality", "goals", "coverage", "narrative"):
        holder.stages_done.add(stage)


@pytest.mark.asyncio
async def test_commit_skips_noop_and_second_commit_writes(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        async with sm() as session:
            first = await srs_store.create_srs(session, pid, {
                "narrative": dict(NARR),
                "markdown": "# srs",
                "quality_summary": {
                    "total_findings": 0,
                    "blockers": 0,
                    "goals": {"goals": 0},
                },
                "coverage": {"totals": {"live": 1}},
                "requirement_codes": ["REQ-A"],
                "requirement_count": 1,
            })
            from backend.models import Priority

            session.add(RequirementItem(
                project_id=pid, code="REQ-A", statement="Requerimiento A",
                type=ReqType.FUNCTIONAL, priority=Priority.MUST,
            ))
            await session.commit()
            # Firma idéntica a la que producirá el commit del run. Los
            # coverage_totals post-refresh llevan live/functional/nfr (así
            # los calcula _refresh_run_metrics antes de firmar).
            first.review_flags = {
                "commit_signature": srs_store.srs_commit_signature(
                    NARR,
                    statements=[("REQ-A", "Requerimiento A")],
                    requirement_count=1,
                    goals_summary={"goals": 0},
                    coverage_totals={"live": 1, "functional": 1, "nfr": 0},
                )
            }
            await session.commit()

        await _seed_commit_run(pid, narrative=dict(NARR))
        no_op = await _tools(pid)["commit_srs"].ainvoke({})
        assert no_op.get("no_new_version") is True
        assert no_op["latest_version"] == first.version
        async with sm() as session:
            assert len(await srs_store.list_srs_versions(session, pid)) == 1

        # Cambia la prosa -> la firma difiere -> ahora SÍ escribe la versión.
        narrative2 = dict(NARR)
        narrative2["overall.assumptions"] = "supuestos con la nota nueva"
        await _seed_commit_run(pid, narrative=narrative2)
        result = await _tools(pid)["commit_srs"].ainvoke({})
        assert result.get("no_new_version") is None
        assert result["version"] == first.version + 1
    finally:
        srs_agent.clear_run(pid)
        await engine.dispose()
        tmp.cleanup()


def test_commit_signature_detects_statement_edit_without_code_change():
    """Editar el TEXTO de un enunciado (mismo código) cambia la firma: la
    sección proyectada 2.2 debe re-proyectarse, así que no es no-op."""
    a = srs_store.srs_commit_signature(
        NARR,
        statements=[("REQ-A", "El sistema debe X")],
        requirement_count=1,
    )
    b = srs_store.srs_commit_signature(
        NARR,
        statements=[("REQ-A", "El sistema deberá X")],
        requirement_count=1,
    )
    assert a != b


# --- leak de conexión (non-checked-in) ---------------------------------------

@pytest.mark.asyncio
async def test_draft_narrative_returns_connection_to_pool(monkeypatch):
    """Regresión del leak «non-checked-in connection» (sesión 18).

    ``_functional_goal_groups`` se llamaba con la sesión DESPUÉS del cierre
    del ``async with AsyncSessionLocal()``: SQLAlchemy la revivía con una
    conexión implícita cuyo único dueño era el frame de la corutina, y esa
    conexión nunca volvía al pool (el GC la recolectaba: ERROR
    AsyncAdaptedQueuePool en producción y SAWarning en tests). El listener
    de balance checkout/checkin + un GC forzado lo detecta de forma
    determinista, sin depender del warning.
    """
    from sqlalchemy import event

    sm, tmp, engine = await _fresh_db(monkeypatch)
    checked_out: list = []
    event.listens_for(engine.sync_engine, "checkout")(
        lambda dbapi_conn, record, *a: checked_out.append(record)
    )
    event.listens_for(engine.sync_engine, "checkin")(
        lambda dbapi_conn, record: checked_out.remove(record)
    )

    async def _fake_narrator(base, **_kw):
        return {k: "prosa" for k in (
            "intro.purpose", "intro.scope", "intro.definitions",
            "overall.perspective", "overall.users", "overall.environment",
            "overall.assumptions",
        )}

    monkeypatch.setattr(srs_agent, "draft_narrative_llm", _fake_narrator)
    try:
        pid = await _mk_project(sm)
        from backend.models import Priority
        from backend.models.srs import GoalKind

        # Un goal funcional: garantiza que _functional_goal_groups consulte
        # Goal/GoalLink (el leak se ejercía SIEMPRE, con o sin goals).
        async with sm() as session:
            session.add(RequirementItem(
                project_id=pid, code="REQ-A", statement="Reportes PDF.",
                type=ReqType.FUNCTIONAL, priority=Priority.MUST,
            ))
            await session.commit()
            await srs_store.replace_goals(
                session, pid,
                [{
                    "kind": GoalKind.FUNCTIONAL_GOAL,
                    "statement": "Emitir reportes",
                    "confidence": 0.9,
                }],
                [],
                req_by_code={"REQ-A": 1},
            )
            await session.commit()

        holder = srs_agent.get_or_create_run(
            pid, project_name="srs", project_description="t"
        )
        holder.quality_summary = {"total_findings": 0, "blockers": 0}
        holder.coverage = {"totals": {"live": 1}}
        holder.goals_summary = {"goals": 1}
        holder.stages_done.update({"quality", "goals", "coverage"})

        result = await _tools(pid)["draft_narrative"].ainvoke({})
        assert result.get("error") is None
        assert result["draft_version"] is not None

        # La corutina terminó: toda conexión pedida debe estar devuelta.
        import gc

        gc.collect()
        assert checked_out == [], (
            f"{len(checked_out)} conexión(es) sin devolver al pool"
        )
    finally:
        srs_agent.clear_run(pid)
        await engine.dispose()
        tmp.cleanup()


# --- techos de las read tools -------------------------------------------------


@pytest.mark.asyncio
async def test_get_latest_srs_returns_index_not_document(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        big = {"intro.purpose": "prosa" * 100}
        await _mk_version(sm, pid, narrative=big)
        result = await _tools(pid)["get_latest_srs"].ainvoke({})
        # El payload NO trae narrativa ni enunciados: solo índice.
        assert "narrative" not in result
        assert "requirement_codes" not in result
        assert "traceability" not in result
        idx = result["sections_index"]
        purpose_chars = [s for s in idx if s["id"] == "intro"][0][
            "subsection_chars"
        ]["intro.purpose"]
        assert purpose_chars == len("prosa" * 100)
        # Pedir una subsección puntual trae su texto con cap.
        section = await _tools(pid)["get_latest_srs"].ainvoke({
            "section_id": "intro.purpose"
        })
        assert section["content"] == "prosa" * 100
        assert section["truncated"] is False
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_get_quality_pages_findings(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        await _mk_version(sm, pid)
        async with sm() as session:
            for i in range(45):
                session.add(RequirementFinding(
                    project_id=pid,
                    scope=FindingScope.SET,
                    dimension=FindingDimension.INCOSE_RULE,
                    rule_id=f"incose.r{i}",
                    severity=FindingSeverity.MINOR,
                    message=f"h {i}",
                ))
            await session.commit()
        page1 = await _tools(pid)["get_quality"].ainvoke({})
        assert page1["count"] == 45
        assert len(page1["findings"]) == 40
        assert page1["more_after"] is True
        page2 = await _tools(pid)["get_quality"].ainvoke({"offset": 40})
        assert len(page2["findings"]) == 5
        assert page2["more_after"] is False
        major = await _tools(pid)["get_quality"].ainvoke({
            "severity": "major"
        })
        assert major["count"] == 0
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_goal_coverage_caps_without_codes(monkeypatch):
    sm, tmp, engine = await _fresh_db(monkeypatch)
    try:
        pid = await _mk_project(sm)
        await _mk_version(sm, pid)
        from backend.models import Priority

        async with sm() as session:
            for i in range(70):
                session.add(RequirementItem(
                    project_id=pid,
                    code=f"REQ-{i:04d}",
                    statement=f"r {i}",
                    type=ReqType.FUNCTIONAL,
                    priority=Priority.MUST,
                ))
            await session.commit()
        cov = await _tools(pid)["goal_coverage"].ainvoke({})
        assert cov["without_goal_link"] == 70
        assert len(cov["without_goal_codes"]) == 60
        assert cov["without_goal_codes_truncated"] is True
        assert cov["without_goal_codes_total"] == 70
    finally:
        await engine.dispose()
        tmp.cleanup()


# --- cap del enunciado en los summaries de listado ----------------------------


@pytest.mark.asyncio
async def test_item_summary_caps_statement_in_list(monkeypatch):
    """El listado acota el enunciado a 500 chars y reporta el largo real.

    Sesión 18: con enunciados de varios KB, una página de 100 items pesaba
    cientos de KB en el thread (write máximo de 471 KB). El texto completo
    sigue disponible por get_requirement.
    """
    sm, tmp, engine = await _fresh_db(monkeypatch)
    monkeypatch.setattr(
        "backend.agents.tools.requirements_tools.AsyncSessionLocal", sm
    )
    try:
        pid = await _mk_project(sm)
        from backend.models import Priority

        long_stmt = "El sistema debe " + "x" * 900
        async with sm() as session:
            session.add(RequirementItem(
                project_id=pid, code="REQ-LONG", statement=long_stmt,
                type=ReqType.FUNCTIONAL, priority=Priority.MUST,
            ))
            session.add(RequirementItem(
                project_id=pid, code="REQ-SHORT", statement="Reportes PDF.",
                type=ReqType.FUNCTIONAL, priority=Priority.SHOULD,
            ))
            await session.commit()

        read_tools = {
            t.name: t
            for t in __import__(
                "backend.agents.tools.requirements_tools",
                fromlist=["make_requirements_read_tools"],
            ).make_requirements_read_tools(pid)
        }
        result = await read_tools["list_requirements"].ainvoke({})
        by_code = {it["code"]: it for it in result["items"]}
        capped = by_code["REQ-LONG"]
        assert len(capped["statement"]) == 500
        assert capped["statement_chars"] == len(long_stmt)
        normal = by_code["REQ-SHORT"]
        assert "statement_chars" not in normal
        assert normal["statement"] == "Reportes PDF."
        # El detalle completo NO se acota.
        detail = await read_tools["get_requirement"].ainvoke({
            "code": "REQ-LONG"
        })
        assert detail["statement"] == long_stmt
    finally:
        await engine.dispose()
        tmp.cleanup()


# --- summarizer con techo ------------------------------------------------------


def test_make_summarization_middleware_none_without_backend():
    from backend.agents.size_guard import make_summarization_middleware

    assert make_summarization_middleware(None) is None


def test_make_summarization_middleware_sets_trim(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "x-test")
    from backend.config import settings
    from backend.agents.size_guard import make_summarization_middleware
    from backend.agents.sandboxes.docker_sandbox import DockerSandbox

    mw = make_summarization_middleware(
        DockerSandbox(profile="__t__", project_slug="__t__")
    )
    # El ensamblador de deepagents matchea por name: si no es EXACTAMENTE
    # "SummarizationMiddleware" apilaría un segundo summarizer en vez de
    # reemplazar el default sin techo.
    assert mw.name == "SummarizationMiddleware"
    expected = max(settings.llm_summarize_trim_chars // 4, 4_000)
    assert mw._lc_helper.trim_tokens_to_summarize == expected
    # El default de la fábrica de deepagents era None (sin recorte).
    assert mw._lc_helper.trim_tokens_to_summarize is not None
