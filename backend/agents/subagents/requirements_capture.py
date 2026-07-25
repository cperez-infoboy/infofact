"""The requirements-capture subagent.

A thin conversational orchestrator that owns two concerns:
1. run_requirements_capture — a single deterministic tool that runs the full
   pipeline (ingest -> extract -> consolidate -> critique -> classify -> persist)
   over the project's documents. The heavy lifting is NOT an agentic loop: it is
   one Python function, so the pipeline is deterministic and the subagent only
   decides when to call it and how to report back.
2. The editing tool set (make_requirements_tools) — approve, reject, merge,
   split, link, resolve, list, get, add, update, add_acceptance_criterion.

The subagent never invents requirements: every item comes from the pipeline
(with a source span) or from an explicit human add_requirement. Conflicts and
duplicates are proposed, never auto-resolved — the human decides.

Registered in agent_service.build_agent when phase == "requirements". The
DeepAgents subagent contract (verified against docs.langchain.com Python API)
is a dict: {"name", "description", "system_prompt", "tools"}.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from backend.services.requirements_service import run_requirements_pipeline
from backend.agents.tools.requirements_tools import make_requirements_tools


REQUIREMENTS_CAPTURE_PROMPT = """Eres el subagente de captura y validacion de requerimientos de InfoFact.

Tu trabajo:

1. Confirmar con el usuario que documentos procesar. Por defecto se procesa todo
   el proyecto; si el usuario indica una carpeta, pasa su ruta relativa a
   run_requirements_capture.

2. Invocar run_requirements_capture para ejecutar el pipeline completo:
   ingesta, extraccion, consolidacion, critica, clasificacion y persistencia.
   El pipeline es deterministico (una sola funcion); no intentes reimplementar
   sus pasos con otras herramientas.

3. Reportar los hallazgos al usuario: total persistido, documentos procesados,
   duplicados propuestos, contradicciones detectadas, items marcados para
   revision y items rechazados por posible alucinacion.

4. Ayudar a revisar y editar los requerimientos con las herramientas de edicion:
   listar, ver detalle, aprobar, rechazar, fusionar duplicados, partir items no
   atomicos, enlazar dependencias, resolver conflictos y agregar criterios de
   aceptacion.

Reglas estrictas:

- NUNCA inventes requerimientos. Todo item debe venir del pipeline (con su
  source_span) o ser agregado explicitamente por el usuario con add_requirement.
- Los conflictos y duplicados NO se resuelven automaticamente: los propones y el
  humano decide. Usa link_requirements para marcar y resolve_conflict solo cuando
  el humano indique el ganador.
- Las eliminaciones son logicas (soft-delete): el historial siempre se conserva.
- Habla en espanol neutro. Se conciso y tecnico.
"""


def _resolve_target(host_workspace: Path, subpath: str) -> Path:
    """Resolve a subpath under the project workspace, refusing traversal.

    The pipeline runs host-side and reads from the bind-mounted workspace on
    disk; this guard keeps the target inside the project's own directory so a
    crafted relative path cannot reach another project or the host filesystem.
    """
    root = host_workspace.resolve()
    target = (root / subpath).resolve() if subpath else root
    if target != root and not str(target).startswith(str(root) + os.sep):
        raise ValueError(
            f"target '{subpath}' escapes the project workspace"
        )
    if not target.exists():
        raise ValueError(f"target '{target}' does not exist")
    return target


def _make_emitters():
    """Build (on_progress, on_event) callbacks that stream to the SSE relay.

    Uses LangGraph's custom stream channel (``get_stream_writer``) so events
    reach the SSE relay without changing the tool signature. ``get_stream_writer``
    only resolves inside a LangGraph execution context; outside one (smoke tests,
    direct invocation) it raises — we catch and stay silent so the tool still runs.

    ``on_progress`` carries coarse stage events (``extraction.progress``);
    ``on_event`` carries fine-grained events (``conflict.found``,
    ``validation.report``, ``requirement.added``) for live UI updates.
    """
    from langgraph.config import get_stream_writer

    async def _emit_progress(stage: str, message: str) -> None:
        try:
            writer = get_stream_writer()
        except Exception:  # noqa: BLE001 — no graph context (smoke / direct call)
            return
        try:
            writer({
                "event": "extraction.progress",
                "data": {"stage": stage, "message": message},
            })
        except Exception:  # noqa: BLE001 — never break the pipeline for progress
            return

    async def _emit_event(event_type: str, data: dict) -> None:
        try:
            writer = get_stream_writer()
        except Exception:  # noqa: BLE001 — no graph context (smoke / direct call)
            return
        try:
            writer({"event": event_type, "data": data})
        except Exception:  # noqa: BLE001 — never break the pipeline for an event
            return

    return _emit_progress, _emit_event


def _make_run_capture_tool(
    project_id: int,
    host_workspace: Path,
    project_name: str,
    project_description: str,
):
    """Build the run_requirements_capture tool bound to one project.

    Closes over project_id + the resolved host workspace path. Returns a compact
    report (counts + a small sample) so the model gets actionable feedback
    without a huge payload.
    """

    @tool
    async def run_requirements_capture(
        target_subpath: str = "",
    ) -> dict:
        """Run the full capture pipeline over the project's documents.

        Args:
            target_subpath: optional folder relative to the project workspace.
                Omit to process the whole project.

        Returns a report: documents processed, requirements persisted, proposed
        duplicates, contradictions, flagged items (needs review), and rejected
        items (likely hallucination). The pipeline takes several minutes on real
        documents; call it once per capture pass, not per question.
        """
        try:
            target = _resolve_target(host_workspace, target_subpath)
        except ValueError as exc:
            return {"error": str(exc)}

        try:
            on_progress, on_event = _make_emitters()
            report = await run_requirements_pipeline(
                project_id,
                target,
                project_name=project_name,
                project_description=project_description,
                on_progress=on_progress,
                on_event=on_event,
            )
        except Exception as exc:  # noqa: BLE001 — surface to the model
            return {"error": f"pipeline failed: {exc}"}

        if report.stats.get("error"):
            return {
                "error": report.stats["error"],
                "documents": report.documents,
            }

        return {
            "documents": report.documents,
            "persisted": len(report.item_ids),
            "stats": {
                "raw_extracted": report.stats.get("raw_extracted"),
                "after_consolidate": report.stats.get("after_consolidate"),
                "after_critique": report.stats.get("after_critique"),
                "sub_items": report.stats.get("sub_items", 0),
            },
            "duplicates_proposed": len(report.duplicates),
            "contradictions": len(report.contradictions),
            "flagged_for_review": len(report.flagged),
            "rejected_hallucination": len(report.rejected),
        }

    return run_requirements_capture


def make_requirements_capture_subagent(
    *,
    project_id: int,
    profile: str,
    project_slug: str,
    project_name: str = "",
    project_description: str = "",
) -> dict[str, Any]:
    """Build the requirements-capture subagent dict for DeepAgents.

    Tools: the capture pipeline (one deterministic call) + the 12 editing tools.
    project_id is closed over so the model cannot address another project's rows.
    """
    # Imported here to avoid a config import at module load time (tests that
    # rebind settings work without surprises).
    from backend.config import settings

    host_workspace = (
        Path(settings.workspaces_host_root) / profile / project_slug
    )
    capture_tool = _make_run_capture_tool(
        project_id,
        host_workspace,
        project_name,
        project_description,
    )
    editing_tools = make_requirements_tools(project_id)

    return {
        "name": "requirements-capture",
        "description": (
            "Captura y valida requerimientos de software desde documentos de "
            "cliente (RFPs, minutas, especificaciones, contratos) y edita el "
            "store de requerimientos resultante. Usalo cuando el usuario suba "
            "documentos de cliente, pida extraer o validar requerimientos, use "
            "el comando /captura, o quiera aprobar, rechazar, fusionar o partir "
            "un requerimiento existente."
        ),
        "system_prompt": REQUIREMENTS_CAPTURE_PROMPT,
        "tools": [capture_tool, *editing_tools],
    }
