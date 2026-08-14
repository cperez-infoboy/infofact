"""Subagente AGENTICO de analisis y diseno arquitectonico (comando ``/analisis``).

Espejo de ``srs_agent`` pero para la Fase 2 (analisis y diseno): razona la
generacion de artefactos arquitectonicos etapa por etapa (MER -> NFR ->
procesos -> ADRs -> sub-proyectos -> commit) sobre los requerimientos YA
capturados del proyecto. Cada herramienta de etapa acumula su salida en
``AnalysisRun`` (holder) y emite eventos finos al SSE; solo ``commit_analysis``
persiste el ``AnalysisDocument``.

Por que un subagente aparte del SRS: el SRS sintetiza el entregable de
requerimientos; el analisis PRODUCE artefactos de diseno (MER, diagramas de
proceso, analisis NFR, ADRs, descomposicion en sub-proyectos). Son flujos
distintos con pipelines distintos.

Orden de etapas y dependencias:
  MER -> NFR -> procesos -> ADRs -> proyectos -> sub-proyectos -> arquitectura -> commit
  - procesos depende de MER (necesita nombres de entidades).
  - ADRs depende de NFR (necesita decisiones arquitectonicas).
  - proyectos depende de MER + ADRs (agrupa BCs en subdominios DDD).
  - sub-proyectos depende de MER + ADRs + proyectos (entidades + decisiones + areas).
  - arquitectura depende de MER + NFR + sub-proyectos.
  - commit requiere todas las etapas previas completas.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.tools import tool

from backend.agents.size_guard import SizeGuardMiddleware
from backend.agents.subagents.analysis_run_holder import (
    STAGE_ADR,
    STAGE_ARCHITECTURE,
    STAGE_COMMIT,
    STAGE_MER,
    STAGE_NFR,
    STAGE_PROCESS,
    STAGE_PROJECTS,
    STAGE_SUBPROJECT,
    StageLoopExceeded,
    clear_run,
    get_or_create_run,
    get_run,
)
from backend.database import AsyncSessionLocal
from backend.models.requirement import ReqStatus, ReqType

logger = logging.getLogger(__name__)

# Estados de requerimiento que cuentan como "vivos" para el analisis (espejo
# de analysis_assembler._LIVE_STATUSES y srs_agent._LIVE_STATUSES).
_LIVE_STATUSES = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)

# Tipos de requerimiento que alimentan el MER (funcionales, de datos y de
# proceso). Espejo de analysis_assembler._FUNCTIONAL_TYPES.
_FUNCTIONAL_TYPES = frozenset(
    {ReqType.FUNCTIONAL, ReqType.DATA, ReqType.PROCESS}
)

# Tipos de requerimiento no funcional que alimentan el pipeline NFR.
# Espejo de analysis_assembler._NFR_TYPES.
_NFR_TYPES = frozenset(
    {
        ReqType.PERFORMANCE,
        ReqType.SECURITY,
        ReqType.USABILITY,
        ReqType.RELIABILITY,
        ReqType.MAINTAINABILITY,
        ReqType.COMPLIANCE,
        ReqType.CONSTRAINT,
    }
)


ANALYSIS_AGENT_PROMPT = """\
Eres el subagente AGENTICO de analisis y diseno arquitectonico de InfoFact. \
Tu trabajo es producir artefactos arquitectonicos (MER, diagramas de proceso, \
analisis NFR, ADRs y sub-proyectos) a partir de los requerimientos YA \
capturados del proyecto. NO extraes requerimientos (eso lo hace la captura); \
CONSUMES el SRS y los requerimientos capturados para PRODUCIR el diseno.

Trabajas por ETAPAS, razonando entre cada una:

1. generate_mer        — Genera el Modelo Entidad-Relacion (MER) del dominio: \
identifica entidades, atributos, relaciones y contextos delimitados (DDD). \
Renderiza el diagrama Mermaid erDiagram.
2. analyze_nfrs        — Analiza los requerimientos no funcionales y produce \
decisiones arquitectonicas concretas (stack por capa, patrones, estrategia \
de consistencia).
3. generate_processes  — Genera diagramas de proceso: maquinas de estados \
(Mermaid stateDiagram-v2) para entidades con ciclo de vida y diagramas de \
secuencia (Mermaid sequenceDiagram) para interacciones clave.
4. generate_adrs       — Redacta Architecture Decision Records (formato Nygard) \
a partir del analisis NFR: contexto, decision, alternativas, justificacion. \
Cada ADR traza a los NFRs que lo motivan.
5. discover_projects   — Descubre PROYECTOS (areas funcionales mayores) \
agrupando los bounded contexts del MER en subdominios DDD (core/supporting/ \
generic). El pipeline agrupa por cohesión de subdominio, acoplamiento \
transaccional y trazabilidad de requisitos, despues critica y refina las \
fronteras usando métricas objetivas del grafo del MER. Requiere MER + ADRs.
6. propose_subprojects — Propone una descomposicion en sub-proyectos con \
contratos explicitos (OpenAPI/AsyncAPI/Event Schema) y un diagrama de \
componentes Mermaid (graph TD). Cada sub-proyecto debe pertenecer a un \
proyecto descubierto en la etapa anterior.
7. generate_architecture — Genera el diagrama de arquitectura del sistema \
(componentes por capa) y el diagrama de infraestructura sugerida (containers, \
redes, volumenes). Requiere MER + NFR + sub-proyectos.
8. commit_analysis     — Persiste el AnalysisDocument CANDIDATE: combina MER + \
diagramas de proceso + analisis NFR + ADRs + proyectos + sub-proyectos + \
contratos + diagramas de arquitectura. Solo esta etapa escribe la DB.

Flujo:
1. ORIENTAR — Confirma que hay requerimientos vivos (si no, informa al usuario \
que primero debe capturar requerimientos con /captura).
2. ORQUESTAR LAS ETAPAS — Ejecuta generate_mer -> analyze_nfrs -> \
generate_processes -> generate_adrs -> discover_projects -> \
propose_subprojects -> generate_architecture -> commit_analysis EN ESE ORDEN. \
Respeta las dependencias: procesos necesita el MER, ADRs necesita el analisis \
NFR, proyectos necesita MER + ADRs, sub-proyectos necesita proyectos, \
arquitectura necesita MER + NFR + sub-proyectos.
3. REFINAR — Si una etapa genera resultados vacios o con errores, mencionalo \
en tu reporte antes de continuar. \
Si en cualquier etapa detectas que un artefacto previo tiene un problema \
(entidades mal clasificadas en el MER, bounded contexts mal definidos, \
decisiones NFR inconsistentes), puedes VOLVER ATRAS y re-ejecutar la etapa \
que necesite correccion. Por ejemplo, si descubrir_proyectos revela que dos \
BCs estan mal separados, puedes re-ejecutar generate_mer para corregir las \
fronteras de BCs. Despues de re-ejecutar una etapa, debes re-ejecutar en \
cascada todas las etapas que dependan del cambio (ver dependencias abajo). \
No abuses de esto: cada etapa tiene un maximo de 3 intentos. Si despues de \
2 intentos una etapa sigue fallando, reporta el problema y continua. \
Despues de discover_projects verifica: cada BC esta en exactamente un \
proyecto? Hay dos proyectos con mucho solapamiento de procesos? Si es asi, \
re-ejecuta discover_projects con feedback. \
Despues de propose_subprojects verifica: cada proyecto tiene al menos un \
sub-proyecto? Los contratos entre sub-proyectos de distintos proyectos son \
minimos?
4. REPORTAR — Tras commit_analysis, resume: entidades, relaciones, ADRs, \
sub-proyectos, diagramas generados y conteo de requerimientos.

Modo REFINAMIENTO (cuando el usuario da feedback sobre un analisis existente):

1. Si el usuario da feedback sobre el analisis sin usar /analisis, carga el 
   analisis previo con refine_analysis(feedback).
2. Analiza el feedback y decide QUE etapas necesitan re-generarse:
   - Si el feedback afecta entidades del MER → re-genera MER
   - Si el feedback afecta RNFs → re-genera NFR
   - Si el feedback afecta diagramas de proceso → re-genera Procesos
   - Si el feedback afecta decisiones arquitectonicas → re-genera ADRs
   - Si el feedback afecta la agrupacion de proyectos → re-genera Proyectos
   - Si el feedback afecta sub-proyectos → re-genera Sub-proyectos
   - Si el feedback afecta la arquitectura del sistema → re-genera Arquitectura
3. RESPETA las dependencias en cascada:
   - Si el MER cambio → Procesos, Proyectos, Sub-proyectos y Arquitectura \
deben re-generarse
   - Si NFR cambio → ADRs y Arquitectura deben re-generarse
   - Si ADRs cambiaron → Proyectos, Sub-proyectos y Arquitectura deben \
re-generarse
   - Si Proyectos cambiaron → Sub-proyectos y Arquitectura deben re-generarse
   - Si Sub-proyectos cambiaron → Arquitectura debe re-generarse
4. Informa al usuario que vas a re-generar y por que.
5. Usa patch_commit para persistir la nueva version.

Reglas estrictas:
- NO inventas requerimientos ni entidades. Trabajas SOLO a partir de los \
requerimientos vivos del proyecto.
- RESPETA las restricciones tecnologicas explicitas de los requerimientos. \
Si un requerimiento (especialmente tipo CONSTRAINT) indica una tecnologia \
obligatoria (ej. SQL Server, Azure, Active Directory), usa ESA tecnologia \
en el stack, los ADRs y la arquitectura. No la reemplaces por una \
alternativa que consideres mejor. Si hay una razon tecnica fuerte para \
cuestionar la restriccion, senalala en el ADR como contexto, pero la \
decision del stack debe honrar el requerimiento.
- El idioma de los enunciados y descripciones se PRESERVA (no traduzcas). Los \
mensajes van en español neutro.
- Conserva el ciclo de cada etapa (cap de 3 intentos por etapa): si una etapa \
falla reiteradamente, reportalo en vez de entrar en loop.
- Tras commit_analysis el analisis queda en estado CANDIDATE; el usuario lo \
revisa y lo cierra (LOCKED) desde la UI.
"""


# ---------------------------------------------------------------------------
# Emisores de eventos al SSE (espejo de srs_agent._make_emitters).
# Usa el canal custom de LangGraph (get_stream_writer) que chat.py relayea
# sin cambios. try/except anidado: un fallo del relay NUNCA rompe la etapa.
# ---------------------------------------------------------------------------


def _make_emitters():
    from langgraph.config import get_stream_writer

    async def _emit_progress(
        stage: str, message: str, extra: dict | None = None
    ) -> None:
        try:
            writer = get_stream_writer()
        except Exception:  # noqa: BLE001 — fuera de contexto de grafo
            return
        try:
            data: dict[str, Any] = {"stage": stage, "message": message}
            if extra:
                data.update(extra)
            writer({"event": "analysis.progress", "data": data})
        except Exception:  # noqa: BLE001 — never break the pipeline
            return

    async def _emit_event(event_type: str, data: dict) -> None:
        try:
            writer = get_stream_writer()
        except Exception:  # noqa: BLE001
            return
        try:
            writer({"event": event_type, "data": data})
        except Exception:  # noqa: BLE001 — never break the pipeline
            return

    return _emit_progress, _emit_event


def _no_run(first_tool: str) -> dict[str, Any]:
    return {
        "error": "no_active_analysis_run",
        "message": (
            f"No hay un run de analisis activo. Invoca primero `{first_tool}` "
            "(se crea al iniciar el run) o reinicia el comando /analisis."
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
    """Construye las 8 herramientas de etapa, cerrando sobre project_id."""

    @tool
    async def generate_mer() -> dict:
        """Etapa 1/8: genera el Modelo Entidad-Relacion (MER) del dominio.

        Identifica entidades, atributos, relaciones y contextos delimitados \
(DDD) a partir de los requerimientos funcionales. Renderiza el diagrama \
Mermaid erDiagram. Emite ``analysis.mer_ready``.
        """
        run = get_run(project_id)
        if run is None:
            # Arranque tolerante: si no hay run, se crea (como analyze_quality
            # en srs-agent).
            run = get_or_create_run(
                project_id,
                project_name=project_name,
                project_description=project_description,
            )
            run.reset_pipeline_outputs()
        try:
            run.bump(STAGE_MER)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(STAGE_MER, "Generando MER del dominio...")
        t0 = time.perf_counter()

        from backend.agents.pipelines.mer_pipeline import (
            generate_mer as _generate_mer,
        )
        from backend.models.srs import GoalKind, GoalStatus
        from backend.services.requirement_store import list_requirements
        from backend.services.srs_store import list_goals

        async with AsyncSessionLocal() as session:
            items = await list_requirements(
                session, project_id, include_deleted=True
            )
            all_goals = await list_goals(session, project_id)
        live = [it for it in items if it.status in _LIVE_STATUSES]
        functional = [it for it in live if it.type in _FUNCTIONAL_TYPES]
        active = [g for g in all_goals if g.status != GoalStatus.REJECTED]
        functional_goals = [
            g for g in active if g.kind == GoalKind.FUNCTIONAL_GOAL
        ]

        # Snapshot de los codigos de requerimientos para trazabilidad.
        run.requirement_codes = [it.code for it in live]
        run.requirement_count = len(live)

        result = await _generate_mer(
            functional,
            project_name=run.project_name,
            project_description=run.project_description,
            goals=functional_goals,
            feedback=run.feedback or "",
        )
        run.mer_result = result
        run.stages_done.add(STAGE_MER)

        elapsed = (time.perf_counter() - t0) * 1000
        run.timings[STAGE_MER] = elapsed
        await on_progress(
            STAGE_MER,
            "MER generado",
            {"phase": "end", "elapsed_ms": round(elapsed)},
        )
        await on_event("analysis.mer_ready", {
            "entities": len(result.entities),
            "relationships": len(result.relationships),
        })

        return {
            "stage": STAGE_MER,
            "entities": len(result.entities),
            "relationships": len(result.relationships),
            "bounded_contexts": len({
                e.bounded_context for e in result.entities
                if e.bounded_context
            }),
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def analyze_nfrs() -> dict:
        """Etapa 2/8: analiza los requerimientos no funcionales (NFR).

        Produce decisiones arquitectonicas concretas, stack recomendado por \
capa, estrategia de consistencia y patrones. Emite ``analysis.nfr_ready``.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("generate_mer")
        try:
            run.bump(STAGE_NFR)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(
            STAGE_NFR, "Analizando NFRs y decisiones arquitectonicas..."
        )
        t0 = time.perf_counter()

        from backend.agents.pipelines.nfr_pipeline import (
            analyze_nfrs as _analyze_nfrs,
        )
        from backend.models.srs import GoalKind, GoalStatus
        from backend.services.requirement_store import list_requirements
        from backend.services.srs_store import list_goals

        async with AsyncSessionLocal() as session:
            items = await list_requirements(
                session, project_id, include_deleted=True
            )
            all_goals = await list_goals(session, project_id)
        live = [it for it in items if it.status in _LIVE_STATUSES]
        nfrs = [it for it in live if it.type in _NFR_TYPES]
        active = [g for g in all_goals if g.status != GoalStatus.REJECTED]
        softgoals = [g for g in active if g.kind == GoalKind.SOFTGOAL]

        result = await _analyze_nfrs(
            nfrs,
            project_name=run.project_name,
            project_description=run.project_description,
            softgoals=softgoals,
            feedback=run.feedback or "",
        )
        run.nfr_result = result
        run.stages_done.add(STAGE_NFR)

        elapsed = (time.perf_counter() - t0) * 1000
        run.timings[STAGE_NFR] = elapsed
        await on_progress(
            STAGE_NFR,
            "Analisis NFR completado",
            {"phase": "end", "elapsed_ms": round(elapsed)},
        )
        await on_event("analysis.nfr_ready", {
            "decisions": len(result.decisions),
            "stack_layers": len(result.stack),
        })

        return {
            "stage": STAGE_NFR,
            "decisions": len(result.decisions),
            "stack_layers": len(result.stack),
            "has_consistency_strategy": bool(result.data_consistency),
            "has_patterns": bool(result.patterns),
            "stages_done": sorted(run.stages_done),
            "stats": result.stats,
        }

    @tool
    async def generate_processes() -> dict:
        """Etapa 3/8: genera diagramas de proceso.

        Maquinas de estados (Mermaid stateDiagram-v2) para entidades con \
ciclo de vida y diagramas de secuencia (Mermaid sequenceDiagram) para \
interacciones clave. Requiere que ``generate_mer`` haya corrido antes. \
Emite ``analysis.process_ready``.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("generate_mer")
        if STAGE_MER not in run.stages_done:
            return {
                "error": "missing_stages",
                "missing": [STAGE_MER],
                "message": (
                    "generate_processes requiere que generate_mer haya "
                    "corrido antes (necesita los nombres de entidades del "
                    "dominio)."
                ),
            }
        try:
            run.bump(STAGE_PROCESS)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(STAGE_PROCESS, "Generando diagramas de proceso...")
        t0 = time.perf_counter()

        from backend.agents.pipelines.process_pipeline import (
            generate_processes as _generate_processes,
        )
        from backend.models.srs import GoalKind, GoalStatus
        from backend.services.requirement_store import list_requirements
        from backend.services.srs_store import list_goals

        async with AsyncSessionLocal() as session:
            items = await list_requirements(
                session, project_id, include_deleted=True
            )
            all_goals = await list_goals(session, project_id)
        live = [it for it in items if it.status in _LIVE_STATUSES]
        functional = [it for it in live if it.type in _FUNCTIONAL_TYPES]
        active = [g for g in all_goals if g.status != GoalStatus.REJECTED]
        functional_goals = [
            g for g in active if g.kind == GoalKind.FUNCTIONAL_GOAL
        ]

        result = await _generate_processes(
            functional,
            mer_result=run.mer_result,
            project_name=run.project_name,
            project_description=run.project_description,
            goals=functional_goals,
            feedback=run.feedback or "",
        )
        run.process_result = result
        run.stages_done.add(STAGE_PROCESS)

        elapsed = (time.perf_counter() - t0) * 1000
        run.timings[STAGE_PROCESS] = elapsed
        await on_progress(
            STAGE_PROCESS,
            "Diagramas de proceso generados",
            {"phase": "end", "elapsed_ms": round(elapsed)},
        )
        await on_event("analysis.process_ready", {
            "state_machines": len(result.state_machines),
            "sequence_diagrams": len(result.sequence_diagrams),
        })

        return {
            "stage": STAGE_PROCESS,
            "state_machines": len(result.state_machines),
            "sequence_diagrams": len(result.sequence_diagrams),
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def generate_adrs() -> dict:
        """Etapa 4/8: redacta Architecture Decision Records (formato Nygard).

        A partir del analisis NFR, redacta ADRs con contexto, decision, \
alternativas y justificacion. Cada ADR traza a los NFRs que lo motivan. \
Requiere que ``analyze_nfrs`` haya corrido antes. Emite ``analysis.adr_ready``.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("generate_mer")
        if STAGE_NFR not in run.stages_done:
            return {
                "error": "missing_stages",
                "missing": [STAGE_NFR],
                "message": (
                    "generate_adrs requiere que analyze_nfrs haya corrido "
                    "antes (necesita las decisiones arquitectonicas derivadas "
                    "de los NFRs)."
                ),
            }
        try:
            run.bump(STAGE_ADR)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(
            STAGE_ADR, "Redactando Architecture Decision Records..."
        )
        t0 = time.perf_counter()

        from backend.agents.pipelines.adr_pipeline import (
            generate_adrs as _generate_adrs,
        )
        from backend.models.srs import GoalKind, GoalStatus
        from backend.services.srs_store import list_goals

        async with AsyncSessionLocal() as session:
            all_goals = await list_goals(session, project_id)
        active = [g for g in all_goals if g.status != GoalStatus.REJECTED]
        softgoals = [g for g in active if g.kind == GoalKind.SOFTGOAL]

        result = await _generate_adrs(
            run.nfr_result,
            run.mer_result,
            project_name=run.project_name,
            project_description=run.project_description,
            softgoals=softgoals,
            feedback=run.feedback or "",
        )
        run.adr_result = result
        run.stages_done.add(STAGE_ADR)

        elapsed = (time.perf_counter() - t0) * 1000
        run.timings[STAGE_ADR] = elapsed
        await on_progress(
            STAGE_ADR,
            "ADRs generados",
            {"phase": "end", "elapsed_ms": round(elapsed)},
        )
        await on_event("analysis.adr_ready", {
            "adrs": len(result.adrs),
        })

        return {
            "stage": STAGE_ADR,
            "adrs": len(result.adrs),
            "stages_done": sorted(run.stages_done),
            "stats": result.stats,
        }

    @tool
    async def discover_projects() -> dict:
        """Etapa 5/8: descubre proyectos (areas funcionales mayores).

        Agrupa los bounded contexts del MER en subdominios DDD \
(core/supporting/generic). El pipeline agrupa por cohesión de subdominio, \
acoplamiento transaccional y trazabilidad de requisitos, despues critica \
las fronteras usando métricas objetivas del grafo del MER. Requiere que \
``generate_mer`` y ``generate_adrs`` hayan corrido antes. Emite \
``analysis.projects_ready``.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("generate_mer")
        missing = []
        if STAGE_MER not in run.stages_done:
            missing.append(STAGE_MER)
        if STAGE_ADR not in run.stages_done:
            missing.append(STAGE_ADR)
        if missing:
            return {
                "error": "missing_stages",
                "missing": missing,
                "message": (
                    "discover_projects requiere que generate_mer y "
                    "generate_adrs hayan corrido antes."
                ),
            }
        try:
            run.bump(STAGE_PROJECTS)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(
            STAGE_PROJECTS,
            "Descubriendo proyectos (subdominios DDD)...",
        )
        t0 = time.perf_counter()

        from backend.agents.pipelines.project_pipeline import (
            discover_projects as _discover_projects,
        )
        from backend.models.srs import GoalStatus
        from backend.services.srs_store import list_goals

        async with AsyncSessionLocal() as session:
            all_goals = await list_goals(session, project_id)
        active = [g for g in all_goals if g.status != GoalStatus.REJECTED]

        result = await _discover_projects(
            mer_result=run.mer_result,
            process_result=run.process_result,
            adr_result=run.adr_result,
            project_name=run.project_name,
            project_description=run.project_description,
            goals=active,
            feedback=run.feedback or "",
        )
        run.project_result = result
        run.stages_done.add(STAGE_PROJECTS)

        elapsed = (time.perf_counter() - t0) * 1000
        run.timings[STAGE_PROJECTS] = elapsed
        await on_progress(
            STAGE_PROJECTS,
            "Proyectos descubiertos",
            {"phase": "end", "elapsed_ms": round(elapsed)},
        )
        await on_event("analysis.projects_ready", {
            "projects": len(result.projects),
        })

        return {
            "stage": STAGE_PROJECTS,
            "projects": len(result.projects),
            "critique_issues": len(result.critique_issues),
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def propose_subprojects() -> dict:
        """Etapa 6/8: propone descomposicion en sub-proyectos con contratos.

        Define sub-proyectos con responsabilidad clara, contratos \
explicitos (OpenAPI/AsyncAPI/Event Schema) y un diagrama de componentes \
Mermaid. Cada sub-proyecto pertenece a un proyecto descubierto en la etapa \
anterior. Requiere que ``generate_mer``, ``generate_adrs`` y \
``discover_projects`` hayan corrido antes. Emite ``analysis.subproject_ready``.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("generate_mer")
        missing = []
        if STAGE_MER not in run.stages_done:
            missing.append(STAGE_MER)
        if STAGE_ADR not in run.stages_done:
            missing.append(STAGE_ADR)
        if STAGE_PROJECTS not in run.stages_done:
            missing.append(STAGE_PROJECTS)
        if missing:
            return {
                "error": "missing_stages",
                "missing": missing,
                "message": (
                    "propose_subprojects requiere que generate_mer, "
                    "generate_adrs y discover_projects hayan corrido antes."
                ),
            }
        try:
            run.bump(STAGE_SUBPROJECT)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(
            STAGE_SUBPROJECT,
            "Proponiendo descomposicion en sub-proyectos...",
        )
        t0 = time.perf_counter()

        from backend.agents.pipelines.subproject_pipeline import (
            propose_subprojects as _propose_subprojects,
        )
        from backend.models.srs import GoalStatus
        from backend.services.srs_store import list_goals

        async with AsyncSessionLocal() as session:
            all_goals = await list_goals(session, project_id)
        active = [g for g in all_goals if g.status != GoalStatus.REJECTED]

        result = await _propose_subprojects(
            mer_result=run.mer_result,
            adr_result=run.adr_result,
            nfr_result=run.nfr_result,
            project_result=run.project_result,
            project_name=run.project_name,
            project_description=run.project_description,
            goals=active,
            feedback=run.feedback or "",
        )
        run.subproject_result = result
        run.stages_done.add(STAGE_SUBPROJECT)

        elapsed = (time.perf_counter() - t0) * 1000
        run.timings[STAGE_SUBPROJECT] = elapsed
        await on_progress(
            STAGE_SUBPROJECT,
            "Sub-proyectos propuestos",
            {"phase": "end", "elapsed_ms": round(elapsed)},
        )
        await on_event("analysis.subproject_ready", {
            "sub_projects": len(result.sub_projects),
            "contracts": len(result.contracts),
        })

        return {
            "stage": STAGE_SUBPROJECT,
            "sub_projects": len(result.sub_projects),
            "contracts": len(result.contracts),
            "has_component_diagram": bool(result.component_diagram_mermaid),
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def generate_architecture() -> dict:
        """Etapa 7/8: genera diagramas de arquitectura del sistema e \
infraestructura.

        Genera el diagrama de arquitectura del sistema (componentes por capa) \
y el diagrama de infraestructura sugerida (containers, redes, volumenes). \
Requiere que ``generate_mer``, ``analyze_nfrs`` y ``propose_subprojects`` \
hayan corrido antes. Emite ``analysis.architecture_ready``.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("generate_mer")
        missing = []
        if STAGE_MER not in run.stages_done:
            missing.append(STAGE_MER)
        if STAGE_NFR not in run.stages_done:
            missing.append(STAGE_NFR)
        if STAGE_SUBPROJECT not in run.stages_done:
            missing.append(STAGE_SUBPROJECT)
        if missing:
            return {
                "error": "missing_stages",
                "missing": missing,
                "message": (
                    "generate_architecture requiere que generate_mer, "
                    "analyze_nfrs y propose_subprojects hayan corrido antes "
                    "(necesita entidades, stack tecnologico y "
                    "sub-proyectos)."
                ),
            }
        try:
            run.bump(STAGE_ARCHITECTURE)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(
            STAGE_ARCHITECTURE,
            "Generando diagramas de arquitectura...",
        )
        t0 = time.perf_counter()

        from backend.agents.pipelines.architecture_pipeline import (
            generate_architecture as _generate_architecture,
        )

        try:
            result = await _generate_architecture(
                mer_result=run.mer_result,
                nfr_result=run.nfr_result,
                adr_result=run.adr_result,
                subproject_result=run.subproject_result,
                project_name=project_name,
                project_description=project_description,
                goals=None,
            )
            run.architecture_result = result
            run.stages_done.add(STAGE_ARCHITECTURE)

            elapsed = (time.perf_counter() - t0) * 1000
            run.timings[STAGE_ARCHITECTURE] = elapsed
            await on_progress(
                STAGE_ARCHITECTURE,
                "Diagramas de arquitectura generados",
                {"phase": "end", "elapsed_ms": round(elapsed)},
            )
            await on_event("analysis.architecture_ready", {
                "components": result.stats.get("components", 0),
                "containers": result.stats.get("containers", 0),
            })

            return {
                "stage": STAGE_ARCHITECTURE,
                "components": result.stats.get("components", 0),
                "containers": result.stats.get("containers", 0),
                "elapsed_ms": round(elapsed),
                "message": (
                    f"Arquitectura generada: "
                    f"{result.stats.get('components', 0)} componentes, "
                    f"{result.stats.get('containers', 0)} containers."
                ),
            }
        except Exception as exc:  # noqa: BLE001 — surface to the model
            logger.exception("generate_architecture failed")
            return {"error": f"generate_architecture failed: {exc}"}

    @tool
    async def commit_analysis() -> dict:
        """Etapa 8/8: persiste el AnalysisDocument CANDIDATE.

        Combina MER + diagramas de proceso + analisis NFR + ADRs + \
proyectos + sub-proyectos + contratos + diagramas de arquitectura en un \
AnalysisDocument versionado. Es la UNICA etapa que escribe la DB. Emite \
``analysis.ready`` al terminar y limpia el holder.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("generate_mer")
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
        await on_progress(STAGE_COMMIT, "Persistiendo analisis candidato...")
        t0 = time.perf_counter()

        from backend.models.srs import GoalKind, GoalStatus
        from backend.services import analysis_store
        from backend.services.requirement_store import list_requirements
        from backend.services.srs_store import (
            list_goal_links,
            list_goals,
        )

        try:
            async with AsyncSessionLocal() as session:
                # Codigos de todos los requerimientos vivos para trazabilidad.
                items = await list_requirements(
                    session, project_id, include_deleted=True
                )
                all_goals = await list_goals(session, project_id)
                goal_links = await list_goal_links(session, project_id)
            live = [it for it in items if it.status in _LIVE_STATUSES]
            codes = [it.code for it in live]

            # Goal traceability (espejo de analysis_assembler).
            active_goals = [
                g for g in all_goals if g.status != GoalStatus.REJECTED
            ]
            goal_traceability: dict[str, Any] = {
                "goals": [
                    {
                        "code": g.code,
                        "statement": g.statement,
                        "kind": g.kind.value,
                    }
                    for g in active_goals
                ],
            }
            if goal_links:
                goal_map = {g.id: g.code for g in all_goals}
                req_map = {it.id: it.code for it in live}
                goal_traceability["goal_links"] = [
                    {
                        "goal_code": goal_map.get(lk.goal_id, ""),
                        "req_code": req_map.get(lk.req_id, ""),
                        "relation": (
                            lk.relation.value
                            if hasattr(lk.relation, "value")
                            else str(lk.relation)
                        ),
                    }
                    for lk in goal_links
                    if lk.goal_id in goal_map and lk.req_id in req_map
                ]

            # --- Build payload from run results (espejo de assemble_analysis).
            entities: list[dict[str, Any]] = []
            if run.mer_result:
                for ent in run.mer_result.entities:
                    entities.append({
                        "name": ent.name,
                        "description": ent.description,
                        "attributes": [
                            a.model_dump() for a in ent.attributes
                        ],
                        "aggregate_root": ent.aggregate_root,
                        "bounded_context": ent.bounded_context,
                        "traced_req_codes": ent.traced_req_codes,
                    })

            relationships: list[dict[str, Any]] = []
            if run.mer_result:
                for rel in run.mer_result.relationships:
                    relationships.append({
                        "from_entity": rel.from_entity,
                        "to_entity": rel.to_entity,
                        "cardinality": rel.cardinality,
                        "label": rel.label,
                        "description": rel.description,
                        "traced_req_codes": rel.traced_req_codes,
                    })

            nfr_analysis: dict[str, Any] = {
                "decisions": (
                    [d.model_dump() for d in run.nfr_result.decisions]
                    if run.nfr_result
                    else []
                ),
                "stack": (
                    [s.model_dump() for s in run.nfr_result.stack]
                    if run.nfr_result
                    else []
                ),
                "data_consistency": (
                    run.nfr_result.data_consistency
                    if run.nfr_result
                    else ""
                ),
                "patterns": (
                    run.nfr_result.patterns if run.nfr_result else ""
                ),
            }

            adrs = (
                [a.model_dump() for a in run.adr_result.adrs]
                if run.adr_result
                else []
            )

            process_diagrams: list[dict[str, Any]] = []
            if run.process_result:
                for sm in run.process_result.state_machines:
                    process_diagrams.append({
                        "name": sm.entity_name,
                        "type": "state_machine",
                        "mermaid": sm.mermaid,
                        "entity_name": sm.entity_name,
                        "traced_req_codes": sm.traced_req_codes,
                    })
                for sq in run.process_result.sequence_diagrams:
                    process_diagrams.append({
                        "name": sq.name,
                        "type": "sequence",
                        "mermaid": sq.mermaid,
                        "traced_req_codes": sq.traced_req_codes,
                    })

            sub_projects = (
                [
                    sp.model_dump()
                    for sp in run.subproject_result.sub_projects
                ]
                if run.subproject_result
                else []
            )
            contracts = (
                [c.model_dump() for c in run.subproject_result.contracts]
                if run.subproject_result
                else []
            )
            component_diagram = (
                run.subproject_result.component_diagram_mermaid
                if run.subproject_result
                else ""
            )
            projects = (
                [p.model_dump() for p in run.project_result.projects]
                if run.project_result
                else []
            )

            payload = {
                "srs_version": run.srs_version,
                "mer_diagram": (
                    run.mer_result.mermaid if run.mer_result else ""
                ),
                "mer_diagram_description": (
                    run.mer_result.description if run.mer_result else ""
                ),
                "process_diagrams": process_diagrams,
                "nfr_analysis": nfr_analysis,
                "component_diagram": component_diagram,
                "component_diagram_description": (
                    run.subproject_result.component_diagram_description
                    if run.subproject_result
                    else ""
                ),
                "system_architecture_diagram": (
                    run.architecture_result.system_architecture_diagram
                    if run.architecture_result
                    else ""
                ),
                "system_architecture_description": (
                    run.architecture_result.system_architecture_description
                    if run.architecture_result
                    else ""
                ),
                "infrastructure_diagram": (
                    run.architecture_result.infrastructure_diagram
                    if run.architecture_result
                    else ""
                ),
                "infrastructure_description": (
                    run.architecture_result.infrastructure_description
                    if run.architecture_result
                    else ""
                ),
                "traceability": goal_traceability,
                "requirement_codes": codes,
                "requirement_count": len(live),
                "entities": entities,
                "relationships": relationships,
                "adrs": adrs,
                "projects": projects,
                "sub_projects": sub_projects,
                "contracts": contracts,
            }

            async with AsyncSessionLocal() as session:
                analysis = await analysis_store.create_analysis(
                    session, project_id, payload
                )
        except Exception as exc:  # noqa: BLE001 — surface to the model
            logger.exception("commit_analysis failed")
            return {"error": f"commit_analysis failed: {exc}"}

        elapsed = (time.perf_counter() - t0) * 1000
        run.timings[STAGE_COMMIT] = elapsed
        await on_progress(
            STAGE_COMMIT,
            "Analisis persistido",
            {"phase": "end", "elapsed_ms": round(elapsed)},
        )
        await on_event("analysis.ready", {
            "version": analysis.version,
            "status": analysis.status.value,
            "entities": len(entities),
            "relationships": len(relationships),
            "adrs": len(adrs),
            "sub_projects": len(sub_projects),
            "requirement_count": analysis.requirement_count,
        })

        clear_run(project_id)
        return {
            "stage": STAGE_COMMIT,
            "version": analysis.version,
            "status": analysis.status.value,
            "entities": len(entities),
            "relationships": len(relationships),
            "adrs": len(adrs),
            "sub_projects": len(sub_projects),
            "requirement_count": analysis.requirement_count,
            "timings": dict(run.timings),
            "message": (
                f"Analisis version {analysis.version} generado (CANDIDATE) "
                f"con {len(entities)} entidades, {len(adrs)} ADRs y "
                f"{len(sub_projects)} sub-proyectos."
            ),
        }

    # ------------------------------------------------------------------ #
    # Refinement tools (conversational refinement mode)
    # ------------------------------------------------------------------ #

    @tool
    async def refine_analysis(feedback: str) -> dict:
        """Carga la ultima version del analisis y prepara el run para refinamiento.

        Lee el AnalysisDocument mas reciente del proyecto, marca todas las 
        etapas como done y registra el feedback del usuario. El agente puede 
        despues re-ejecutar solo las etapas afectadas y usar patch_commit 
        para persistir.
        """
        try:
            run = get_or_create_run(
                project_id,
                project_name=project_name,
                project_description=project_description,
            )
            run.feedback = feedback

            async with AsyncSessionLocal() as session:
                from backend.services.analysis_store import (
                    get_latest_analysis,
                )
                latest = await get_latest_analysis(session, project_id)
                if latest is None:
                    return {
                        "error": "no_previous_analysis",
                        "message": (
                            "No hay un analisis previo. Ejecuta /analisis "
                            "primero para generar el analisis inicial."
                        ),
                    }

                run.previous_analysis_id = latest.id

            run.stages_done = {
                STAGE_MER,
                STAGE_NFR,
                STAGE_PROCESS,
                STAGE_ADR,
                STAGE_PROJECTS,
                STAGE_SUBPROJECT,
                STAGE_ARCHITECTURE,
            }
            run.calls = {}

            return {
                "status": "loaded",
                "version": latest.version,
                "feedback": feedback,
                "message": (
                    f"Analisis version {latest.version} cargado. Feedback "
                    f"registrado. Decide que etapas re-generar basandote en "
                    f"el feedback y las dependencias entre etapas."
                ),
            }
        except Exception as exc:  # noqa: BLE001 — surface to the model
            logger.exception("refine_analysis failed")
            return {"error": f"refine_analysis failed: {exc}"}

    @tool
    async def patch_commit() -> dict:
        """Commitea una nueva version del analisis (refinamiento).

        A diferencia de commit_analysis, NO requiere que todas las etapas se 
        hayan re-ejecutado. Para cada etapa:
        - Si fue re-ejecutada (calls[stage] > 0), usa el resultado del run holder.
        - Si NO fue re-ejecutada, copia los datos de la version anterior.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("refine_analysis")
        if run.previous_analysis_id is None:
            return {
                "error": "not_in_refinement_mode",
                "message": (
                    "patch_commit requiere refinamiento. Llama "
                    "refine_analysis primero para cargar un analisis previo."
                ),
            }
        rerun = {s for s, c in run.calls.items() if c > 0}
        if not rerun:
            return {
                "error": "no_stages_rerun",
                "message": (
                    "No se re-ejecuto ninguna etapa. Re-genera al menos una "
                    "etapa (generate_mer, analyze_nfrs, etc.) antes de usar "
                    "patch_commit."
                ),
            }
        try:
            run.bump(STAGE_COMMIT)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(STAGE_COMMIT, "Persistiendo analisis refinado...")
        t0 = time.perf_counter()

        try:
            from backend.models.analysis import AnalysisDocument
            from backend.models.srs import GoalStatus
            from backend.services import analysis_store
            from backend.services.requirement_store import list_requirements
            from backend.services.srs_store import (
                list_goal_links,
                list_goals,
            )

            async with AsyncSessionLocal() as session:
                prev_doc = await session.get(
                    AnalysisDocument, run.previous_analysis_id
                )
                if prev_doc is None:
                    return {
                        "error": "previous_analysis_not_found",
                        "message": (
                            f"No se encontro el analisis previo "
                            f"(id={run.previous_analysis_id})."
                        ),
                    }
                prev_entities = await analysis_store.list_domain_entities(
                    session, prev_doc.id
                )
                prev_relationships = (
                    await analysis_store.list_domain_relationships(
                        session, prev_doc.id
                    )
                )
                prev_adrs = await analysis_store.list_adrs(
                    session, prev_doc.id
                )
                prev_sub_projects = (
                    await analysis_store.list_sub_projects(
                        session, prev_doc.id
                    )
                )
                prev_contracts = await analysis_store.list_contracts(
                    session, prev_doc.id
                )
                items = await list_requirements(
                    session, project_id, include_deleted=True
                )
                all_goals = await list_goals(session, project_id)
                goal_links = await list_goal_links(session, project_id)

            live = [it for it in items if it.status in _LIVE_STATUSES]
            codes = [it.code for it in live]
            active_goals = [
                g for g in all_goals if g.status != GoalStatus.REJECTED
            ]
            goal_traceability: dict[str, Any] = {
                "goals": [
                    {
                        "code": g.code,
                        "statement": g.statement,
                        "kind": g.kind.value,
                    }
                    for g in active_goals
                ],
            }
            if goal_links:
                goal_map = {g.id: g.code for g in all_goals}
                req_map = {it.id: it.code for it in live}
                goal_traceability["goal_links"] = [
                    {
                        "goal_code": goal_map.get(lk.goal_id, ""),
                        "req_code": req_map.get(lk.req_id, ""),
                        "relation": (
                            lk.relation.value
                            if hasattr(lk.relation, "value")
                            else str(lk.relation)
                        ),
                    }
                    for lk in goal_links
                    if lk.goal_id in goal_map and lk.req_id in req_map
                ]

            # --- Entities / relationships / MER diagram ---
            if STAGE_MER in rerun and run.mer_result:
                entities = [
                    {
                        "name": ent.name,
                        "description": ent.description,
                        "attributes": [
                            a.model_dump() for a in ent.attributes
                        ],
                        "aggregate_root": ent.aggregate_root,
                        "bounded_context": ent.bounded_context,
                        "traced_req_codes": ent.traced_req_codes,
                    }
                    for ent in run.mer_result.entities
                ]
                relationships = [
                    {
                        "from_entity": rel.from_entity,
                        "to_entity": rel.to_entity,
                        "cardinality": rel.cardinality,
                        "label": rel.label,
                        "description": rel.description,
                        "traced_req_codes": rel.traced_req_codes,
                    }
                    for rel in run.mer_result.relationships
                ]
                mer_diagram = run.mer_result.mermaid
            else:
                entities = [
                    analysis_store.entity_to_dict(e) for e in prev_entities
                ]
                relationships = [
                    analysis_store.relationship_to_dict(r)
                    for r in prev_relationships
                ]
                mer_diagram = prev_doc.mer_diagram

            # --- NFR analysis ---
            if STAGE_NFR in rerun and run.nfr_result:
                nfr_analysis = {
                    "decisions": [
                        d.model_dump() for d in run.nfr_result.decisions
                    ],
                    "stack": [
                        s.model_dump() for s in run.nfr_result.stack
                    ],
                    "data_consistency": run.nfr_result.data_consistency,
                    "patterns": run.nfr_result.patterns,
                }
            else:
                nfr_analysis = prev_doc.nfr_analysis or {}

            # --- Process diagrams ---
            if STAGE_PROCESS in rerun and run.process_result:
                process_diagrams = []
                for sm in run.process_result.state_machines:
                    process_diagrams.append({
                        "name": sm.entity_name,
                        "type": "state_machine",
                        "mermaid": sm.mermaid,
                        "entity_name": sm.entity_name,
                        "traced_req_codes": sm.traced_req_codes,
                    })
                for sq in run.process_result.sequence_diagrams:
                    process_diagrams.append({
                        "name": sq.name,
                        "type": "sequence",
                        "mermaid": sq.mermaid,
                        "traced_req_codes": sq.traced_req_codes,
                    })
            else:
                process_diagrams = prev_doc.process_diagrams or []

            # --- ADRs ---
            if STAGE_ADR in rerun and run.adr_result:
                adrs = [a.model_dump() for a in run.adr_result.adrs]
            else:
                adrs = [
                    analysis_store.adr_to_dict(a) for a in prev_adrs
                ]

            # --- Projects (project areas) ---
            if STAGE_PROJECTS in rerun and run.project_result:
                projects = [
                    p.model_dump()
                    for p in run.project_result.projects
                ]
            else:
                prev_projects = await analysis_store.list_projects(
                    session, prev_doc.id
                )
                projects = [
                    analysis_store.project_to_dict(p)
                    for p in prev_projects
                ]

            # --- Sub-projects / contracts / component diagram ---
            if STAGE_SUBPROJECT in rerun and run.subproject_result:
                sub_projects = [
                    sp.model_dump()
                    for sp in run.subproject_result.sub_projects
                ]
                contracts = [
                    c.model_dump()
                    for c in run.subproject_result.contracts
                ]
                component_diagram = (
                    run.subproject_result.component_diagram_mermaid
                )
            else:
                sub_projects = [
                    analysis_store.subproject_to_dict(s)
                    for s in prev_sub_projects
                ]
                # Resolve old SUB-NNN codes to names so create_analysis can
                # re-resolve them to new codes.
                sp_code_to_name = {
                    s.code: s.name for s in prev_sub_projects
                }
                contracts = []
                for c in prev_contracts:
                    contracts.append({
                        "from_subproject": sp_code_to_name.get(
                            c.from_subproject_code, c.from_subproject_code
                        ),
                        "to_subproject": sp_code_to_name.get(
                            c.to_subproject_code, c.to_subproject_code
                        ),
                        "contract_type": (
                            c.contract_type.value
                            if hasattr(c.contract_type, "value")
                            else c.contract_type
                        ),
                        "name": c.name,
                        "spec": c.spec,
                        "description": c.description,
                    })
                component_diagram = prev_doc.component_diagram

            # --- Architecture diagrams ---
            if STAGE_ARCHITECTURE in rerun and run.architecture_result:
                system_architecture_diagram = run.architecture_result.system_architecture_diagram
                system_architecture_description = run.architecture_result.system_architecture_description
                infrastructure_diagram = run.architecture_result.infrastructure_diagram
                infrastructure_description = run.architecture_result.infrastructure_description
            else:
                system_architecture_diagram = prev_doc.system_architecture_diagram
                system_architecture_description = prev_doc.system_architecture_description
                infrastructure_diagram = prev_doc.infrastructure_diagram
                infrastructure_description = prev_doc.infrastructure_description

            payload = {
                "srs_version": (
                    run.srs_version or prev_doc.srs_version
                ),
                "mer_diagram": mer_diagram,
                "process_diagrams": process_diagrams,
                "nfr_analysis": nfr_analysis,
                "component_diagram": component_diagram,
                "system_architecture_diagram": system_architecture_diagram,
                "system_architecture_description": system_architecture_description,
                "infrastructure_diagram": infrastructure_diagram,
                "infrastructure_description": infrastructure_description,
                "traceability": goal_traceability,
                "requirement_codes": codes,
                "requirement_count": len(live),
                "entities": entities,
                "relationships": relationships,
                "adrs": adrs,
                "projects": projects,
                "sub_projects": sub_projects,
                "contracts": contracts,
            }

            async with AsyncSessionLocal() as session:
                analysis = await analysis_store.create_analysis(
                    session, project_id, payload
                )
        except Exception as exc:  # noqa: BLE001 — surface to the model
            logger.exception("patch_commit failed")
            return {"error": f"patch_commit failed: {exc}"}

        elapsed = (time.perf_counter() - t0) * 1000
        run.timings[STAGE_COMMIT] = elapsed
        rerun_list = sorted(rerun)
        await on_progress(
            STAGE_COMMIT,
            "Analisis refinado persistido",
            {"phase": "end", "elapsed_ms": round(elapsed)},
        )
        await on_event("analysis.ready", {
            "version": analysis.version,
            "status": analysis.status.value,
            "entities": len(entities),
            "relationships": len(relationships),
            "adrs": len(adrs),
            "sub_projects": len(sub_projects),
            "requirement_count": analysis.requirement_count,
            "refined_stages": rerun_list,
        })

        prev_version = prev_doc.version
        clear_run(project_id)
        return {
            "stage": STAGE_COMMIT,
            "version": analysis.version,
            "status": analysis.status.value,
            "entities": len(entities),
            "relationships": len(relationships),
            "adrs": len(adrs),
            "sub_projects": len(sub_projects),
            "requirement_count": analysis.requirement_count,
            "refined_stages": rerun_list,
            "timings": dict(run.timings),
            "message": (
                f"Analisis version {analysis.version} generado (refinamiento "
                f"de version {prev_version}). Etapas re-generadas: "
                f"{', '.join(rerun_list)}."
            ),
        }

    return [
        generate_mer,
        analyze_nfrs,
        generate_processes,
        generate_adrs,
        discover_projects,
        propose_subprojects,
        generate_architecture,
        commit_analysis,
        refine_analysis,
        patch_commit,
    ]


def _make_read_tools(project_id: int) -> list:
    """Read-only tools so the agent can query requirements, SRS, and documents.

    ``make_document_read_tools`` already includes ``search_documents`` (RAG
    semantic search via ``retrieval/store.py``), so no separate RAG tool is
    needed.
    """
    from backend.agents.tools.requirements_tools import (
        make_requirements_read_tools,
    )
    from backend.agents.tools.srs_tools import make_srs_read_tools
    from backend.agents.tools.documents_tools import make_document_read_tools
    return (
        make_requirements_read_tools(project_id)
        + make_srs_read_tools(project_id)
        + make_document_read_tools(project_id)
    )


def make_analysis_agent_subagent(
    *,
    project_id: int,
    profile: str,
    project_slug: str,
    project_name: str = "",
    project_description: str = "",
) -> dict[str, Any]:
    """Construye el subagente analysis-agent como dict para ``create_deep_agent``.

    No llama create_deep_agent: devuelve el descriptor que el orquestador
    recibe en ``subagents=[...]`` y DeepAgents envuelve como tool ``task``.
    """
    stage_tools = _make_stage_tools(project_id, project_name, project_description)
    return {
        "name": "analysis-agent",
        "description": (
            "Subagente AGENTICO de analisis y diseno arquitectonico: genera el "
            "MER del dominio, diagramas de proceso (maquinas de estados + "
            "secuencias), analisis NFR con stack recomendado, Architecture "
            "Decision Records (ADRs), descomposicion en proyectos (subdominios "
            "DDD) y sub-proyectos con contratos y diagramas de arquitectura "
            "del sistema e infraestructura. Persiste un AnalysisDocument "
            "CANDIDATE estructurado, editable y trazable. Razona etapa por "
            "etapa (MER -> NFR -> procesos -> ADRs -> proyectos -> "
            "sub-proyectos -> arquitectura -> commit)."
        ),
        "system_prompt": ANALYSIS_AGENT_PROMPT,
        "tools": stage_tools + _make_read_tools(project_id),
        # deepagents NO propaga el middleware del orquestador a los
        # subagentes: cada spec necesita su propia guarda de tamaño.
        "middleware": [SizeGuardMiddleware()],
    }
