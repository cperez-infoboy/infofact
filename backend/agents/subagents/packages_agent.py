"""Subagente agéntico de paquetes de trabajo (comando ``/paquetes``).

Consumes the latest committed AnalysisDocument (sub-projects + contracts) and
produces ONE self-contained work package per sub-project plus an assembly
master. Only ``commit_packages`` writes the DB; the generation result lives in
an in-process holder keyed by project (mirror of analysis_agent, simplified:
the pipeline is one pass, not a staged multi-tool run).

Tools:
  generate_work_packages — run the pipeline against the latest analysis.
                           Emits packages.progress to SSE. Reports the
                           coherence gate outcome; does NOT persist.
  commit_packages        — persist the holder as a CANDIDATE document.
                           BLOCKED when a blocking coherence gate failed
                           (the agent must report and stop).

The refinement loop (regenerate with feedback, patch tasks) is intentionally
out of scope: regenerate is cheap (deterministic assembly + one LLM pass).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import tool

from backend.agents.llm_retry_guard import EmptyResponseRetryMiddleware
from backend.agents.sandboxes.docker_sandbox import DockerSandbox
from backend.agents.size_guard import (
    SizeGuardMiddleware,
    make_summarization_middleware,
)
from backend.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

PACKAGES_AGENT_PROMPT = """\
Eres el subagente de paquetes de trabajo de InfoFact (comando `/paquetes`). \
Tu trabajo: convertir el análisis arquitectónico YA comprometido en entregables \
para un equipo de desarrollo — un paquete autocontenido por sub-proyecto y un \
maestro de ensamblaje. Los desarrolladores implementan SIN conocer el sistema \
completo: cada uno ve SOLO su paquete (contrato-primero).

Flujo:
1. generate_work_packages — corre el pipeline sobre el último análisis \
comprometido. Revisa el resultado: cantidad de paquetes, tareas por paquete y \
el INFORME DE COHERENCIA.
2. Si la puerta de coherencia está BLOQUEADA (gates blocking en fail), NO \
puedes commitear: reporta al usuario exactamente qué gates fallaron y por qué, \
y sugiérele refinar el análisis (p. ej. la etapa propose_subprojects) antes de \
reintentar.
3. Si pasó, commit_packages persiste la versión CANDIDATE y reporta: paquetes, \
tareas totales, orden de construcción sugerido y dónde verlos/descargarlos.

Reglas estrictas:
- NO inventes paquetes ni tareas: el pipeline los produce de los datos \
persistidos; tú razonas y reportas.
- El informe de coherencia (gates informativos en warn y hallazgos de la \
crítica cruzada) va SIEMPRE en tu reporte: son deudas visibles para el \
usuario, no las ocultes.
- Si no hay un análisis comprometido, informa que primero debe correr \
/analisis.
"""


@dataclass
class PackagesRun:
    """In-process holder of one generation result (per project)."""

    project_id: int
    analysis_id: int | None = None
    analysis_version: int = 0
    result: Any = None  # PackagesResult
    calls: dict = field(default_factory=dict)


_ACTIVE_RUNS: dict[int, PackagesRun] = {}


def _get_run(project_id: int) -> PackagesRun | None:
    return _ACTIVE_RUNS.get(project_id)


def _clear_run(project_id: int) -> None:
    _ACTIVE_RUNS.pop(project_id, None)


def _make_emitters():
    from langgraph.config import get_stream_writer

    async def _emit_progress(message: str, extra: dict | None = None) -> None:
        try:
            writer = get_stream_writer()
        except Exception:  # noqa: BLE001 — fuera de contexto de grafo
            return
        try:
            data = {"message": message}
            if extra:
                data.update(extra)
            writer({"event": "packages.progress", "data": data})
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


def _make_packages_tools(project_id: int, project_name: str = "") -> list:
    @tool
    async def generate_work_packages() -> dict:
        """Genera los paquetes de trabajo a partir del último análisis comprometido.

        Ensambla un paquete autocontenido por sub-proyecto (contexto, \
contratos, backlog de tareas) más el maestro de ensamblaje, y corre la \
puerta de coherencia. NO persiste: usa commit_packages después si el \
informe lo permite. Emite ``packages.progress``.
        """
        from backend.services import analysis_store, packages_store
        from backend.agents.pipelines.workpackage_pipeline import (
            generate_work_packages as _generate,
        )

        run = _get_run(project_id)
        if run is None:
            from backend.agents.subagents.packages_agent import PackagesRun

            run = PackagesRun(project_id=project_id)
            _ACTIVE_RUNS[project_id] = run

        on_progress, _ = _make_emitters()
        await on_progress("buscando el último análisis comprometido...")

        async with AsyncSessionLocal() as session:
            latest = await analysis_store.get_latest_analysis(
                session, project_id
            )
        if latest is None:
            return {
                "error": "no_previous_analysis",
                "message": (
                    "No hay un análisis previo. Ejecuta /analisis primero: "
                    "los paquetes se generan desde un AnalysisDocument "
                    "comprometido (sub-proyectos + contratos)."
                ),
            }

        run.analysis_id = latest.id
        run.analysis_version = latest.version
        await on_progress(
            f"generando paquetes desde el análisis v{latest.version}..."
        )
        result = await _generate(
            latest.id,
            project_name=project_name,
            on_progress=lambda msg: on_progress(msg),
        )
        run.result = result

        blocking = [
            g for g in result.gates if g.blocking and g.status == "fail"
        ]
        warns = [g for g in result.gates if g.status == "warn"]
        out = {
            "stage": "generate",
            "analysis_version": latest.version,
            "packages": len(result.packages),
            "tasks": result.stats.get("tasks", 0),
            "requirements": result.stats.get("requirements", 0),
            "gates_passed": result.stats.get("gates_passed", 0),
            "blocking_gates_failed": len(blocking),
            "warn_gates": len(warns),
            "critique_findings": len(result.critique_findings),
            "can_commit": not blocking,
            "packages_detail": [
                {
                    "code_hint": c.sub_project_code,
                    "name": c.sub_project_name,
                    "requirements": len(c.requirements),
                    "tasks": len(c.tasks),
                }
                for c in result.packages
            ],
        }
        if blocking:
            out["message"] = (
                "La puerta de coherencia BLOQUEA el commit. Gates fallidos: "
                + "; ".join(
                    f"{g.gate} ({' / '.join(g.details[:2])})" for g in blocking
                )
                + ". NO uses commit_packages: reporta al usuario y sugiere "
                "refinar el análisis antes de reintentar."
            )
        elif warns or result.critique_findings:
            out["message"] = (
                "Paquetes generados con deudas visibles (gates en warn y/o "
                "crítica cruzada). El commit está PERMITIDO; incluye las "
                "deudas en tu reporte al usuario."
            )
        else:
            out["message"] = (
                "Paquetes generados y coherentes. Usa commit_packages para "
                "persistirlos."
            )
        return out

    @tool
    async def commit_packages() -> dict:
        """Persiste los paquetes generados como documento CANDIDATE versionado.

        Requiere que generate_work_packages haya corrido. Se BLOQUEA si un \
gate de coherencia bloqueante falló (reporta en vez de persistir). Emite \
``packages.ready``.
        """
        from backend.services import packages_store

        run = _get_run(project_id)
        if run is None or run.result is None:
            return {
                "error": "no_generation",
                "message": (
                    "No hay una generación en memoria. Invoca "
                    "generate_work_packages primero."
                ),
            }
        blocking = [
            g
            for g in run.result.gates
            if g.blocking and g.status == "fail"
        ]
        if blocking:
            return {
                "error": "coherence_blocked",
                "stage": "commit",
                "gates_failed": [g.gate for g in blocking],
                "message": (
                    "El commit está BLOQUEADO por la puerta de coherencia: "
                    + ", ".join(g.gate for g in blocking)
                    + ". Los paquetes NO se persistieron."
                ),
            }

        on_progress, on_event = _make_emitters()
        await on_progress("persistiendo paquetes de trabajo...")
        async with AsyncSessionLocal() as session:
            doc = await packages_store.create_packages_document(
                session,
                project_id,
                analysis_id=run.analysis_id,
                analysis_version=run.analysis_version,
                result=run.result,
                requirement_count=run.result.stats.get("requirements", 0),
                # commit (single write) owns the transaction.
            )
            await session.commit()

        _clear_run(project_id)
        await on_event("packages.ready", {
            "version": doc.version,
            "status": doc.status.value,
            "packages": len(run.result.packages),
            "tasks": run.result.stats.get("tasks", 0),
        })
        return {
            "stage": "commit",
            "version": doc.version,
            "status": doc.status.value,
            "packages": len(run.result.packages),
            "tasks": run.result.stats.get("tasks", 0),
            "message": (
                f"Paquetes de trabajo v{doc.version} persistidos "
                f"(CANDIDATE): {len(run.result.packages)} paquetes, "
                f"{run.result.stats.get('tasks', 0)} tareas. Visibles en el "
                "visor de Paquetes; cada paquete y el maestro de ensamblaje "
                "son descargables en .md."
            ),
        }

    return [generate_work_packages, commit_packages]


def make_packages_agent_subagent(
    *,
    project_id: int | None,
    profile: str = "",
    project_slug: str = "",
    project_name: str = "",
    project_description: str = "",
) -> dict:
    """Subagent spec for the packages delivery agent (molde analysis-agent)."""
    from backend.agents.no_fs_tools import NoFilesystemToolsMiddleware

    tools = (
        _make_packages_tools(project_id, project_name=project_name or "")
        if project_id is not None
        else []
    )
    return {
        "name": "packages-agent",
        "description": (
            "Genera paquetes de trabajo entregables por sub-proyecto "
            "(contratos + backlog atómico + maestro de ensamblaje) desde el "
            "análisis comprometido. Úsalo con el comando /paquetes."
        ),
        "system_prompt": PACKAGES_AGENT_PROMPT,
        "tools": tools,
        "middleware": [
            SizeGuardMiddleware(),
            EmptyResponseRetryMiddleware(),
            NoFilesystemToolsMiddleware(),
            # deepagents NO propaga el middleware del orquestador a los
            # subagentes: este spec necesita su summarizer con techo
            # (reemplaza al default de deepagents por nombre; sesion 17).
            # El backend es el mismo sandbox del proyecto.
            make_summarization_middleware(
                DockerSandbox(profile=profile, project_slug=project_slug)
            ),
        ],
    }
