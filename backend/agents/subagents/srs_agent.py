"""Subagente AGENTICO de generación de SRS (comando ``/srs``).

Espejo de ``requirements_capture_agent`` pero para la SÍNTESIS del SRS:
razona la generación etapa por etapa (calidad -> goals -> cobertura ->
commit) sobre los requerimientos vivos del proyecto. Cada herramienta de
etapa acumula su salida en ``SrsRun`` (holder) y emite eventos finos al
SSE; solo ``commit_srs`` persiste el ``SrsDocument``.

Por qué un subagente aparte de la captura: la captura EXTRAE/cura reqs; el
SRS SINTETIZA el entregable quality-assured. Son flujos distintos con
herramientas distintas (aquí no hay ingest/extract, hay analyze/infer/commit).

Orden de etapas (corrección del plan): cobertura lee los goals persistidos,
así que va DESPUÉS de goals. La dependencia es real (``compute_coverage``
llama ``list_goals``); invertirla produciría cobertura de goals vacía.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from backend.agents.subagents.capture_run_holder import StageLoopExceeded
from backend.agents.subagents.srs_run_holder import (
    STAGE_COMMIT,
    STAGE_COVERAGE,
    STAGE_GOALS,
    STAGE_NARRATIVE,
    STAGE_QUALITY,
    clear_run,
    get_or_create_run,
    get_run,
)
from backend.agents.size_guard import SizeGuardMiddleware
from backend.agents.tools.documents_tools import make_document_read_tools
from backend.database import AsyncSessionLocal
from backend.models.requirement import ReqStatus
from backend.services import goals_engine, srs_coverage, srs_quality, srs_store
from backend.services.requirement_store import list_requirements
from backend.services.srs_assembler import (
    SRS_STRUCTURE,
    _draft_narrative,
    draft_narrative_llm,
)
from backend.services.srs_builder import build_srs

logger = logging.getLogger(__name__)

_LIVE_STATUSES = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)

# Severidades cuya detección merece un evento fino individual al SSE (las
# accionables). minor/info se reportan solo en el agregado del retorno de la
# etapa, para no inundar el stream.
_FINDING_EVENT_SEVERITIES = frozenset({"blocker", "major"})


SRS_AGENT_PROMPT = """\
Eres el subagente AGENTICO de generación de SRS de InfoFact. Tu trabajo es \
producir una Especificación de Requerimientos de Software de alta calidad, \
analizada en calidad y con modelado de goals, a partir de los requerimientos \
YA capturados del proyecto. NO extraes requerimientos (eso lo hace la captura); \
los SINTETIZAS y les aplicas análisis de calidad.

Trabajas por ETAPAS, razonando entre cada una:

1. analyze_quality  — Analiza la calidad de los requerimientos vivos: \
pre-checks programáticos (INCOSE, requirement smells, EARS) + evaluación LLM \
de ambigüedad semántica. Produces hallazgos (RequirementFinding) con severidad \
y sugerencias de reescritura.
2. infer_goals      — Infiere el MODELO DE GOALS (GORE): goals funcionales \
(el "para qué"), softgoals NFR (puente con ISO 25010) y obstacles (riesgos a \
mitigar). Vincula cada goal a los requerimientos que lo realizan \
(GoalLink: REALIZES/CONTRIBUTES/CONFLICTS).
3. check_coverage   — Audita la cobertura: ISO/IEC 25010 (qué características \
de calidad no tienen ningún requerimiento), presencia de secciones 29148 y \
cobertura de goals (goals sin reqs que los realicen = gap).
4. draft_narrative  — Redacta con LLM las 8 subsecciones authored del SRS \
(propósito, alcance, definiciones, referencias, perspectiva, usuarios, entorno \
y supuestos) usando contexto RAG de los documentos fuente de captura. Puedes \
consultar los documentos con search_documents / get_document_passage antes de \
redactar.
5. commit_srs       — Persiste el SrsDocument CANDIDATE: combina narrativa \
+ secciones proyectadas + hallazgos + goals + cobertura + matriz de \
trazabilidad. Solo esta etapa escribe la DB.

Flujo:
1. ORIENTAR — Confirma que hay requerimientos vivos (si no,informa al usuario \
que primero debe capturar requerimientos con /captura_agente).
2. ORIENTARSE EN LAS FUENTES — Usa list_documents y search_documents para \
revisar los documentos que dieron origen a los requerimientos antes de \
redactar el SRS.
3. ORQUESTAR LAS ETAPAS — Ejecuta analyze_quality -> infer_goals -> \
check_coverage -> draft_narrative -> commit_srs EN ESE ORDEN (cobertura \
depende de los goals; narrativa usa el contexto acumulado).
4. REFINAR — Si analyze_quality halla bloqueantes graves, mencionalo en tu \
reporte antes de continuar (el usuario decide si corregir los reqs primero).
5. REPORTAR — Tras commit_srs, resume: versión generada, conteo de reqs, \
hallazgos por severidad, gaps de cobertura y goals inferidos.

Reglas estrictas:
- NO inventes requerimientos ni goals. Los goals se infieren SOLO de los \
requerimientos vivos del proyecto.
- El idioma de los enunciados, suggestions y rationale se PRESERVA (no \
traduzcas). Los mensajes van en español neutro.
- Conserva el ciclo de cada etapa (cap de 3 intentos por etapa): si una \
etapa falla reiteradamente, reportalo en vez de entrar en loop.
- Tras commit_srs el SRS queda en estado CANDIDATE; el usuario lo revisa \
(edita narrativa, marca pendientes) y lo cierra (LOCKED) desde la UI.
"""


# ---------------------------------------------------------------------------
# Emisores de eventos al SSE (espejo de requirements_capture._make_emitters).
# Usa el canal custom de LangGraph (get_stream_writer) que chat.py relayea
# sin cambios. try/except anidado: un fallo del relay NUNCA rompe la etapa.
# ---------------------------------------------------------------------------


def _make_emitters():
    from langgraph.config import get_stream_writer

    async def _emit_progress(stage: str, message: str) -> None:
        try:
            writer = get_stream_writer()
        except Exception:  # noqa: BLE001 — fuera de contexto de grafo
            return
        try:
            writer({"event": "srs.progress",
                    "data": {"stage": stage, "message": message}})
        except Exception:  # noqa: BLE001
            return

    async def _emit_event(event_type: str, data: dict) -> None:
        try:
            writer = get_stream_writer()
        except Exception:  # noqa: BLE001
            return
        try:
            writer({"event": event_type, "data": data})
        except Exception:  # noqa: BLE001
            return

    return _emit_progress, _emit_event


def _no_run(first_tool: str) -> dict[str, Any]:
    return {
        "error": "no_active_run",
        "message": (
            f"No hay un run SRS activo. Invoca primero `{first_tool}` (se crea "
            "al iniciar el run) o reinicia el comando /srs."
        ),
    }


def _loop_err(exc: StageLoopExceeded) -> dict[str, Any]:
    return {
        "error": "stage_loop_exceeded",
        "stage": exc.stage,
        "calls": exc.calls,
        "cap": exc.cap,
        "message": str(exc),
    }


# ---------------------------------------------------------------------------
# Stage tools.
# ---------------------------------------------------------------------------


def _make_stage_tools(
    project_id: int,
    project_name: str = "",
    project_description: str = "",
) -> list:
    """Construye las 4 herramientas de etapa, cerrando sobre project_id."""

    @tool
    async def analyze_quality() -> dict:
        """Etapa 1/4: análisis de calidad de los requerimientos vivos.

        Pre-checks programáticos (INCOSE/smells/EARS) + evaluación LLM de \
ambigüedad semántica. Acumula los hallazgos en el holder; persisten en \
commit_srs. Emite ``quality.found`` por cada hallazgo accionable \
(blocker/major).
        """
        run = get_run(project_id)
        if run is None:
            # Arranque tolerante: si no hay run, se crea (como ingest en captura).
            run = get_or_create_run(
                project_id,
                project_name=project_name,
                project_description=project_description,
            )
            run.reset_pipeline_outputs()
        try:
            run.bump(STAGE_QUALITY)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(STAGE_QUALITY, "análisis de calidad (INCOSE + smells + EARS + LLM)")
        async with AsyncSessionLocal() as session:
            summary, findings = await srs_quality.analyze_quality(session, project_id)

        run.quality_summary = summary
        run.findings = list(findings)
        run.stages_done.add(STAGE_QUALITY)

        emitted = 0
        for f in findings:
            sev = f.get("severity").value if hasattr(f.get("severity"), "value") else str(f.get("severity"))
            if sev in _FINDING_EVENT_SEVERITIES:
                await on_event("quality.found", {
                    "rule_id": f.get("rule_id"),
                    "severity": sev,
                    "dimension": f.get("dimension").value if hasattr(f.get("dimension"), "value") else str(f.get("dimension")),
                    "message": f.get("message"),
                })
                emitted += 1

        return {
            "stage": STAGE_QUALITY,
            "items_analyzed": summary.get("items_analyzed", 0),
            "total_findings": summary.get("total_findings", 0),
            "blockers": summary.get("blockers", 0),
            "by_severity": summary.get("by_severity", {}),
            "events_emitted": emitted,
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def infer_goals() -> dict:
        """Etapa 2/4: inferencia del modelo de goals (GORE: KAOS + i* + NFR).

        Infiere goals funcionales, softgoals NFR y obstacles, y los vincula a \
los requerimientos (GoalLink). PERSISTE goals + links. Emite \
``goal.inferred`` por cada goal.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("analyze_quality")
        try:
            run.bump(STAGE_GOALS)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(STAGE_GOALS, "inferencia de goals (GORE)")
        async with AsyncSessionLocal() as session:
            summary = await goals_engine.infer_goals(session, project_id)
            goals = await srs_store.list_goals(session, project_id)

        run.goals_summary = summary
        run.stages_done.add(STAGE_GOALS)

        for g in goals:
            await on_event("goal.inferred", {
                "code": g.code,
                "kind": g.kind.value,
                "statement": g.statement,
                "status": g.status.value,
            })

        return {
            "stage": STAGE_GOALS,
            "goals": summary.get("goals", 0),
            "links": summary.get("links", 0),
            "softgoals": summary.get("softgoals", 0),
            "obstacles": summary.get("obstacles", 0),
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def check_coverage() -> dict:
        """Etapa 3/4: auditoría de cobertura (ISO 25010 + 29148 + goals).

        Mapea los requerimientos a las 8 características de ISO 25010, marca \
los gaps, verifica presencia de secciones 29148 y la cobertura de goals. \
Acumula hallazgos de cobertura en el holder. Emite ``coverage.report``.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("analyze_quality")
        try:
            run.bump(STAGE_COVERAGE)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(STAGE_COVERAGE, "auditoría de cobertura (ISO 25010 + 29148 + goals)")
        async with AsyncSessionLocal() as session:
            coverage, cov_findings = await srs_coverage.compute_coverage(session, project_id)

        run.coverage = coverage
        run.findings.extend(cov_findings)
        run.stages_done.add(STAGE_COVERAGE)

        gaps = coverage.get("gaps_25010", [])
        totals = coverage.get("totals", {})
        await on_event("coverage.report", {
            "total": totals.get("live", 0),
            "functional": totals.get("functional", 0),
            "nfr": totals.get("nfr", 0),
            "gaps_25010": gaps,
            "unrealized_goals": coverage.get("goals", {}).get("unrealized", []),
            "unmitigated_obstacles": coverage.get("goals", {}).get("unmitigated_obstacles", []),
        })

        return {
            "stage": STAGE_COVERAGE,
            "live": totals.get("live", 0),
            "gaps_25010": gaps,
            "unrealized_goals": coverage.get("goals", {}).get("unrealized", []),
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def draft_narrative() -> dict:
        """Etapa 4/5: redacta con LLM las 8 subsecciones authored del SRS.

        Genera prosa para propósito, alcance, definiciones, referencias, \
perspectiva, usuarios, entorno operativo y supuestos, usando contexto RAG \
de los documentos fuente de captura. Preserva las secciones deterministas \
(overview, features) y reapende el bloque de conteos a la perspectiva. \
Emite ``narrative.drafted`` al terminar.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("analyze_quality")
        if STAGE_COVERAGE not in run.stages_done:
            return {
                "error": "missing_stages",
                "missing": [STAGE_COVERAGE],
                "message": (
                    "draft_narrative requiere que check_coverage haya corrido "
                    "antes (para usar las métricas de cobertura en el contexto)."
                ),
            }
        try:
            run.bump(STAGE_NARRATIVE)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(
            STAGE_NARRATIVE,
            "redacción narrativa asistida por LLM (8 secciones authored)",
        )
        try:
            async with AsyncSessionLocal() as session:
                items = await list_requirements(
                    session, project_id, include_deleted=True
                )
            live = [it for it in items if it.status in _LIVE_STATUSES]

            # Narrativa determinista como base/fallback.
            det_narrative = _draft_narrative(
                run.project_name,
                run.project_description,
                run.quality_summary or {},
                run.coverage or {},
                run.goals_summary or {},
                len(live),
                live_items=live,
            )
            # Enriquecer con LLM (fallback determinista on failure).
            narrative = await draft_narrative_llm(
                det_narrative,
                project_id=project_id,
                project_name=run.project_name,
                project_description=run.project_description,
                live_items=live,
                quality_summary=run.quality_summary or {},
                coverage=run.coverage or {},
                goals_summary=run.goals_summary or {},
            )
        except Exception as exc:  # noqa: BLE001 — surface al modelo
            logger.exception("draft_narrative failed")
            return {"error": f"draft_narrative failed: {exc}"}

        run.narrative = narrative
        run.stages_done.add(STAGE_NARRATIVE)

        # Contar cuántas subsecciones authored dejaron de tener placeholder.
        placeholders = sum(
            1 for v in narrative.values()
            if isinstance(v, str) and "Editor:" in v
        )
        await on_event("narrative.drafted", {
            "subsections": 8,
            "remaining_placeholders": placeholders,
        })

        return {
            "stage": STAGE_NARRATIVE,
            "subsections": 8,
            "remaining_placeholders": placeholders,
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def commit_srs() -> dict:
        """Etapa 5/5: persiste el SrsDocument CANDIDATE.

        Combina narrativa (borrador) + secciones proyectadas (markdown) + \
hallazgos + goals + cobertura + matriz de trazabilidad. Es la ÚNICA etapa \
que escribe la DB (findings + SrsDocument). Emite ``srs.ready`` al terminar \
y limpia el holder.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("analyze_quality")
        try:
            run.bump(STAGE_COMMIT)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        missing = run.missing_stages_before_commit()
        if missing:
            return {
                "error": "missing_stages",
                "missing": missing,
                "message": (
                    "Faltan etapas obligatorias antes de commit. Ejecuta: "
                    + ", ".join(missing)
                ),
            }

        on_progress, on_event = _make_emitters()
        await on_progress(STAGE_COMMIT, "persistiendo SRS candidato")
        try:
            async with AsyncSessionLocal() as session:
                # Hallazgos (calidad + cobertura) -> persistencia unica.
                await srs_store.replace_findings(session, project_id, run.findings)
                # Trazabilidad + items vivos.
                traceability = await srs_store.build_traceability(session, project_id)
                items = await list_requirements(session, project_id, include_deleted=True)
                live = [it for it in items if it.status in _LIVE_STATUSES]
                # Narrativa editable: usa la del stage narrative si existe,
                # si no, genera el borrador determinista como fallback.
                if run.narrative is not None:
                    narrative = run.narrative
                else:
                    narrative = _draft_narrative(
                        run.project_name,
                        run.project_description,
                        run.quality_summary or {},
                        run.coverage or {},
                        run.goals_summary or {},
                        len(live),
                        live_items=live,
                    )
                # Proyeccion Markdown (con narrative + structure 29148).
                built = await build_srs(
                    session,
                    project_id,
                    project_name=run.project_name,
                    project_description=run.project_description,
                    narrative=narrative,
                    structure=SRS_STRUCTURE,
                )
                payload = {
                    "structure": SRS_STRUCTURE,
                    "narrative": narrative,
                    "markdown": built["markdown"],
                    "quality_summary": {
                        **(run.quality_summary or {}),
                        "goals": run.goals_summary or {},
                    },
                    "coverage": run.coverage or {},
                    "traceability": traceability,
                    "requirement_codes": [it.code for it in live],
                    "requirement_count": len(live),
                    "generated_by": "agent",
                }
                srs = await srs_store.create_srs(session, project_id, payload)
        except Exception as exc:  # noqa: BLE001 — surface al modelo
            logger.exception("commit_srs failed")
            return {"error": f"commit_srs failed: {exc}"}

        await on_event("srs.ready", {
            "version": srs.version,
            "status": srs.status.value,
            "requirement_count": srs.requirement_count,
            "findings": (run.quality_summary or {}).get("total_findings", 0),
            "blockers": (run.quality_summary or {}).get("blockers", 0),
            "goals": (run.goals_summary or {}).get("goals", 0),
        })

        clear_run(project_id)
        return {
            "stage": STAGE_COMMIT,
            "version": srs.version,
            "status": srs.status.value,
            "requirement_count": srs.requirement_count,
            "findings": (run.quality_summary or {}).get("total_findings", 0),
            "blockers": (run.quality_summary or {}).get("blockers", 0),
            "goals": (run.goals_summary or {}).get("goals", 0),
            "message": (
                f"SRS versión {srs.version} generado (CANDIDATE) con "
                f"{srs.requirement_count} requerimientos."
            ),
        }

    return [analyze_quality, infer_goals, check_coverage, draft_narrative, commit_srs]


def make_srs_agent_subagent(
    *,
    project_id: int,
    profile: str,
    project_slug: str,
    project_name: str = "",
    project_description: str = "",
) -> dict[str, Any]:
    """Construye el subagente srs-agent como dict para ``create_deep_agent``.

    No llama create_deep_agent: devuelve el descriptor que el orquestador
    recibe en ``subagents=[...]`` y DeepAgents envuelve como tool ``task``.
    """
    stage_tools = _make_stage_tools(project_id, project_name, project_description)
    doc_tools = make_document_read_tools(project_id)
    tools = stage_tools + doc_tools
    return {
        "name": "srs-agent",
        "description": (
            "Subagente AGENTICO de generación de SRS: analiza la calidad de los "
            "requerimientos (INCOSE/smells/EARS + LLM), infiere el modelo de "
            "goals (GORE), audita la cobertura (ISO 25010), redacta la "
            "narrativa del SRS con LLM y persiste un SrsDocument CANDIDATE "
            "estructurado, editable y trazable. Razona etapa por etapa "
            "(calidad -> goals -> cobertura -> narrativa -> commit)."
        ),
        "system_prompt": SRS_AGENT_PROMPT,
        "tools": tools,
        # deepagents NO propaga el middleware del orquestador a los
        # subagentes: cada spec necesita su propia guarda de tamaño.
        "middleware": [SizeGuardMiddleware()],
    }
