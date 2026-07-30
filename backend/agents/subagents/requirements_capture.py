"""The requirements-capture subagent.

A thin conversational orchestrator that owns three concerns:
1. check_capture_health — a fast pre-flight that probes the environment (deps,
   LLM endpoint, workspace writability) BEFORE the long pipeline runs, so a
   broken dependency fails here with a remediation hint instead of mid-capture.
2. run_requirements_capture — a single deterministic tool that runs the full
   pipeline (ingest -> extract -> consolidate -> critique -> classify -> persist)
   over the project's documents. The heavy lifting is NOT an agentic loop: it is
   one Python function, so the pipeline is deterministic and the subagent only
   decides when to call it and how to report back.
3. The editing tool set (make_requirements_tools) — approve, reject, merge,
   split, link, resolve, list, get, add, update, add_acceptance_criterion.
4. The grouping-review tools (make_grouping_tools) — review_grouping builds an
   editable Markdown merge plan from the live store; apply_grouping_plan merges
   each group after the user edits it.

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
from backend.agents.pipelines.preflight import check_capture_environment
from backend.agents.tools.requirements_tools import make_requirements_tools
from backend.agents.tools.grouping_tools import make_grouping_tools
from backend.agents.tools.vision_tools import make_vision_tools


REQUIREMENTS_CAPTURE_PROMPT = """Eres el subagente de captura y validacion de requerimientos de InfoFact.

Tu trabajo:

1. Confirmar con el usuario que documentos procesar. Por defecto se procesa todo
   el proyecto; si el usuario indica una carpeta, pasa su ruta relativa a
   run_requirements_capture.

2. ANTES de capturar, llamar a check_capture_health para verificar el entorno
   (imports de docling/opencv/tiktoken/sentence-transformers, endpoint LLM,
   escritura del workspace). Si algun probe falla, reportar el hint de
   remediacion al usuario y NO invocar run_requirements_capture hasta que se
   resuelva (asi se evita un bloqueo a mitad de captura).

2b. run_requirements_capture se protege solo: si ya existen requerimientos en
   el proyecto, NO corre sino que devuelve pending_confirmation con cuantos hay
   y el ultimo codigo (p.ej. REQ-7K3F). Cuando eso pase, AVISA al usuario los
   numeros y preguntale si prefiere resetear todo (borrar requerimientos +
   agrupamientos y empezar de cero) o agregar a los existentes. Segun su
   respuesta, vuelve a llamar a run_requirements_capture con
   on_existing='reset' (empezar de cero) u on_existing='append' (mantener lo
   existente y sumar los nuevos). NUNCA uses on_existing='reset' sin
   confirmacion explicita del usuario; ante duda, append (no destruyas). Para
   consultar el estado sin disparar la captura, usa capture_status.

3. Invocar run_requirements_capture para ejecutar el pipeline completo:
   ingesta, extraccion, consolidacion, critica, clasificacion y persistencia.
   El pipeline es deterministico (una sola funcion); no intentes reimplementar
   sus pasos con otras herramientas.

4. Reportar los hallazgos al usuario: total persistido, documentos procesados,
   duplicados propuestos, contradicciones detectadas, items marcados para
   revision y items rechazados por posible alucinacion.

5. Ayudar a revisar y editar los requerimientos con las herramientas de edicion:
   listar, ver detalle, aprobar, rechazar, fusionar duplicados, partir items no
   atomicos, enlazar dependencias, resolver conflictos y agregar criterios de
   aceptacion.

6. Revision de agrupamiento (duplicados en el store):
   - Cuando el usuario pida revisar duplicados o agrupamiento, llamar a
     review_grouping. Detecta grupos de duplicados (verbatim y semanticos) sobre
     el store actual y persiste un plan editable en la base de datos
     (grouping_plans), devolviendo cada grupo con su id, keeper, miembros,
     razon y confianza para mostrarlo en el chat.
   - El usuario revisa el plan y puede pedir cambios (por ejemplo, "en el grupo
     2, que el keeper sea REQ-7K3F" o "rechazar el grupo 3"). Aplicar los cambios
     con set_group_decision (aceptar o rechazar un grupo) y edit_group (cambiar
     el keeper o los miembros por codigo REQ), y volver a mostrar el plan para
     confirmar antes de aplicar.
   - Cuando el usuario aprueba, llamar a apply_grouping_plan (por defecto aplica
     el plan propuesto mas reciente). Solo fusiona los grupos marcados como
     accept (union de sources, soft-delete de los perdedores) y es idempotente
     (re-aplicar un plan ya aplicado reporta already_applied). Reportar el
     resultado por grupo (applied / already_applied / invalid).
   - Para listar planes previos (reanudar una revision o auditar lo aplicado),
     llamar a list_grouping_plans.
   - NUNCA aplicar un plan sin confirmacion explicita del usuario: las fusiones
     son destructivas (soft-delete de los perdedores).

Reglas estrictas:

- NUNCA inventes requerimientos. Todo item debe venir del pipeline (con su
  source_span) o ser agregado explicitamente por el usuario con add_requirement.
- Los conflictos y duplicados NO se resuelven automaticamente: los propones y el
  humano decide. Usa link_requirements para marcar y resolve_conflict solo cuando
  el humano indique el ganador. Las fusiones del plan de agrupamiento tambien
  requieren aprobacion explicita del usuario.
- Las eliminaciones son logicas (soft-delete): el historial siempre se conserva.
  Excepcion: reset_capture es un hard-delete destructivo e irreversible que
  borra TODOS los requerimientos, relaciones, revisiones y planes del proyecto.
  Solo lo llamas tras un "si" explicito del usuario; nunca por iniciativa propia.
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


def _existing_gate(existing_count: int, on_existing: str) -> str:
    """Decide what run_requirements_capture does given existing rows.

    Pure policy (no I/O) so it is unit-tested in isolation. Returns one of:
      - "block":   requirements exist and the caller has not decided yet -> the
                   tool must return pending_confirmation and NOT run.
      - "reset":   wipe existing rows first, then run.
      - "proceed": run without wiping (nothing exists, or the user chose append).

    on_existing must be "ask" (default), "reset" or "append"; anything else
    raises ValueError so the tool surfaces a clear error instead of silently
    defaulting.
    """
    if on_existing not in {"ask", "reset", "append"}:
        raise ValueError(
            "on_existing must be 'ask', 'reset' or 'append', got "
            f"{on_existing!r}"
        )
    if on_existing == "append":
        return "proceed"
    if on_existing == "reset":
        return "reset" if existing_count > 0 else "proceed"
    # on_existing == "ask"
    return "block" if existing_count > 0 else "proceed"


async def _count_existing(project_id: int) -> dict:
    """Count existing requirement + grouping rows for one project (preflight).

    Used by run_requirements_capture guard to decide whether a capture would
    append over existing data. Counts ALL rows (including soft-deleted) so the
    guard fires whenever the project has been captured before, even if every
    row was later rejected -- matches capture_status accounting.
    """
    from sqlalchemy import func, select

    from backend.database import AsyncSessionLocal
    from backend.models.requirement import GroupingPlan, RequirementItem

    async with AsyncSessionLocal() as session:
        req_count = await session.scalar(
            select(func.count())
            .select_from(RequirementItem)
            .where(RequirementItem.project_id == project_id)
        )
        plan_count = await session.scalar(
            select(func.count())
            .select_from(GroupingPlan)
            .where(GroupingPlan.project_id == project_id)
        )
        last_code = await session.scalar(
            select(RequirementItem.code)
            .where(RequirementItem.project_id == project_id)
            .order_by(RequirementItem.id.desc())
            .limit(1)
        )
        return {
            "requirements": int(req_count or 0),
            "grouping_plans": int(plan_count or 0),
            "last_code": last_code,
        }


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

    async def _emit_progress(
        stage: str, message: str, extra: dict | None = None
    ) -> None:
        try:
            writer = get_stream_writer()
        except Exception:  # noqa: BLE001 — no graph context (smoke / direct call)
            return
        try:
            data: dict = {"stage": stage, "message": message}
            if extra:
                data.update(extra)
            writer({"event": "extraction.progress", "data": data})
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


def _make_check_health_tool():
    """Build the check_capture_health tool.

    Wraps ``check_capture_environment`` (pure probes) and streams a coarse
    progress event so the UI shows 'verificando entorno' before the long capture
    run. No project closure is needed — the probes read global settings.
    """

    @tool
    async def check_capture_health() -> dict:
        """Verify the capture environment BEFORE running the pipeline.

        Probes dependency imports (docling, opencv, tiktoken,
        sentence-transformers), LLM endpoint reachability, and workspace
        writability. Fast (<2s). Call this FIRST, before
        run_requirements_capture, so a broken dependency fails here with a
        remediation hint instead of crashing mid-capture.
        """
        on_progress, _on_event = _make_emitters()
        await on_progress("preflight", "Verificando entorno de captura...")
        report = check_capture_environment()
        if not report.ok:
            failing = ", ".join(p.name for p in report.failures())
            await on_progress(
                "preflight_failed",
                f"Entorno con problemas: {failing}",
            )
        return report.as_dict()

    return check_capture_health


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
        on_existing: str = "ask",
    ) -> dict:
        """Run the full capture pipeline over the project's documents.

        Args:
            target_subpath: optional folder relative to the project workspace.
                Omit to process the whole project.
            on_existing: what to do when requirements already exist in this
                project. The default "ask" never silently appends: if any
                requirement exists, the tool returns pending_confirmation (with
                the counts and last code) instead of running -- surface those to
                the user, then call again with the user's choice.
                - "reset": delete ALL existing requirements + grouping plans
                  first (hard, irreversible; the next capture starts
                  fresh), then run. Use ONLY after the user explicitly chose
                  to start over.
                - "append": keep existing requirements and add the new ones.

        Returns a report: documents processed, requirements persisted, proposed
        duplicates, contradictions, flagged items (needs review), and rejected
        items (likely hallucination). The pipeline takes several minutes on real
        documents; call it once per capture pass, not per question.
        """
        try:
            target = _resolve_target(host_workspace, target_subpath)
        except ValueError as exc:
            return {"error": str(exc)}

        # Guardrail: refuse to silently append over an existing capture. The
        # system prompt also asks the agent to probe capture_status first, but
        # this enforces the check at the tool level so a forgotten pre-check
        # cannot accumulate duplicates across runs.
        try:
            existing = await _count_existing(project_id)
            decision = _existing_gate(existing["requirements"], on_existing)
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — surface, never crash silently
            return {"error": f"capture pre-check failed: {exc}"}

        if decision == "block":
            return {
                "pending_confirmation": True,
                "requirements": existing["requirements"],
                "grouping_plans": existing["grouping_plans"],
                "last_code": existing["last_code"],
                "message": (
                    f"Ya existen {existing['requirements']} requerimiento(s) "
                    f"en este proyecto"
                    + (
                        f" (ultimo codigo {existing['last_code']})"
                        if existing["last_code"]
                        else ""
                    )
                    + ". Pregunta al usuario si prefiere resetear todo "
                    "(borrar requerimientos y agrupamientos, empezar "
                    "de cero) o agregar a los existentes, y vuelve a llamar "
                    "con on_existing='reset' u on_existing='append'."
                ),
            }

        on_progress, on_event = _make_emitters()

        if decision == "reset":
            from backend.database import AsyncSessionLocal
            from backend.services.requirements_service import (
                reset_project_capture,
            )
            await on_progress("reset", "Borrando captura previa...")
            try:
                async with AsyncSessionLocal() as session:
                    await reset_project_capture(session, project_id)
            except Exception as exc:  # noqa: BLE001
                return {"error": f"reset before capture failed: {exc}"}

        try:
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

    Tools: the pre-flight health check + the capture pipeline (one deterministic
    call) + the editing tools + the grouping-review tools, plus the
    image-inspection tool when vision is configured. project_id is closed over
    so the model cannot address another project's rows.
    """
    # Imported here to avoid a config import at module load time (tests that
    # rebind settings work without surprises).
    from backend.config import settings

    host_workspace = (
        settings.workspaces_root / profile / project_slug
    )
    health_tool = _make_check_health_tool()
    capture_tool = _make_run_capture_tool(
        project_id,
        host_workspace,
        project_name,
        project_description,
    )
    editing_tools = make_requirements_tools(project_id)
    grouping_tools = make_grouping_tools(project_id)
    vision_tools = make_vision_tools(profile, project_slug)

    return {
        "name": "requirements-capture",
        "description": (
            "Captura y valida requerimientos de software desde documentos de "
            "cliente (RFPs, minutas, especificaciones, contratos) y edita el "
            "store de requerimientos resultante. Usalo cuando el usuario suba "
            "documentos de cliente, pida extraer o validar requerimientos, use "
            "el comando /captura, quiera aprobar, rechazar, fusionar o partir "
            "un requerimiento existente, o pida revisar el agrupamiento de "
            "duplicados del store."
        ),
        "system_prompt": REQUIREMENTS_CAPTURE_PROMPT,
        "tools": [
            health_tool,
            capture_tool,
            *editing_tools,
            *grouping_tools,
            *vision_tools,
        ],
    }
