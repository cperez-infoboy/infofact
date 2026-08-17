"""Subagent tools for the grouping-review workflow (DB-backed).

review_grouping     — build a plan from the live store, persist it to the
                      grouping_plans table, return the groups for chat.
                      Optional scope filters: ``types`` (ReqType values) and
                      ``documents`` (filename | rel_path | container path).
set_group_decision  — accept / reject / reset a group's curation decision.
edit_group          — change a group's keeper and/or members (REQ-codes
                      resolved to ids against the live store).
apply_grouping_plan — merge every 'accept' group idempotently, set plan status.
list_grouping_plans — list persisted plans so a previous review can resume.

All tools close over ``project_id``; each opens a fresh DB session. The plan is
a DB row (shared source of truth): the agent and the requirements UI curate the
SAME GroupingPlan / GroupingGroup rows. The Markdown export is read-only
(``grouping_store.export_plan_md``) and never the store.

Design notes
------------
- ``apply_grouping_plan`` is idempotent: a group whose members are already
  ``MERGED`` into the keeper is reported ``already_applied`` and skipped, never
  re-merged (merge_requirements appends revisions, so a blind re-apply would
  duplicate the audit trail). See grouping_store._apply_one.
- Editing is structural (DB rows), never textual: the user accepts/rejects or
  picks a different keeper via set_group_decision / edit_group, then applies.
  There is no plan file to rewrite.
- NUNCA aplicar un plan sin confirmacion explicita del usuario: las fusiones
  son destructivas (soft-delete de los perdedores como MERGED).

``run_grouping_review`` es el nucleo compartido (recibe la session ya abierta):
la tool y la ruta directa del router para ``/agrupar`` llaman a la MISMA
funcion, asi ambas producen planes identicos. En exito emite un evento custom
``grouping.ready`` (best-effort, solo dentro de un contexto de grafo) para que
el frontend refresque el panel de agrupamiento sin recarga manual.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from langchain_core.tools import tool

from backend.agents.pipelines.grouping import build_grouping_plan
from backend.database import AsyncSessionLocal
from backend.models.requirement import GroupDecision
from backend.services import grouping_store as gstore
from backend.services import requirement_store as store

logger = logging.getLogger(__name__)


def _emit_grouping_ready(result: dict) -> None:
    """Emit ``grouping.ready`` por el canal custom de LangGraph (best-effort).

    Patron de ``_make_emitters._emit_event`` (requirements_capture_agent):
    ``get_stream_writer`` solo resuelve dentro de un contexto de ejecucion del
    grafo; fuera de el (ruta directa del router, smokes, invocacion directa)
    lanza y nos quedamos en silencio. El router relayea el evento al SSE y el
    frontend refresca el panel de agrupamiento.
    """
    if "error" in result:
        return
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:  # noqa: BLE001 — no graph context (direct route / smoke)
        return
    try:
        writer({
            "event": "grouping.ready",
            "data": {
                "plan_id": result.get("plan_id"),
                "group_count": result.get("group_count", 0),
            },
        })
    except Exception:  # noqa: BLE001 — never break the review for an event
        return


async def run_grouping_review(
    session,
    project_id: int,
    *,
    types: Optional[list[str]] = None,
    documents: Optional[list[str]] = None,
    on_progress=None,
) -> dict:
    """Nucleo compartido: build -> persist -> reload de un plan de agrupamiento.

    Recibe la session YA ABIERTA (la tool abre la suya; la ruta directa del
    router reusa la suya) y no maneja sus propias transacciones. En exito
    devuelve ``plan_id`` / ``group_count`` / ``considered`` / ``scope`` y los
    grupos resueltos a REQ-codes, y emite ``grouping.ready``; en fallo,
    ``{"error": ...}``.

    ``on_progress`` (opcional, async) recibe los eventos del banner de
    progreso (etapas de build + persist + done con timings y contadores); la
    ruta directa del router los convierte a SSE ``grouping.progress``.
    """
    try:
        plan = await build_grouping_plan(
            session,
            project_id,
            project=str(project_id),
            types=types,
            documents=documents,
            on_progress=on_progress,
        )
        t0 = time.monotonic()
        if on_progress is not None:
            await on_progress({
                "stage": "persist",
                "message": f"persistiendo plan ({len(plan.groups)} grupos)",
                "phase": "start",
            })
        plan_id = await gstore.persist_plan(session, plan, project_id)
        data = await gstore.get_plan(session, plan_id)
        if on_progress is not None:
            timings = {
                **plan.timings,
                "persist": int((time.monotonic() - t0) * 1000),
            }
            await on_progress({
                "stage": "done",
                "message": "",
                "phase": "end",
                "timings": timings,
                "total_ms": sum(timings.values()),
                "group_count": len(plan.groups),
                "considered": plan.considered,
            })
    except Exception as exc:  # noqa: BLE001 -- surface to the model
        # Evidencia en docker logs: el dict de error llega al chat, pero sin
        # este registro el traceback se pierde (incidente /agrupar con
        # "Connection error." y logs mudos).
        logger.exception("review_grouping failed project_id=%s", project_id)
        return {"error": f"review_grouping failed: {exc}"}
    result = {
        "plan_id": plan_id,
        "group_count": len(plan.groups),
        "considered": plan.considered,
        "scope": plan.scope,
        "groups": data["groups"] if data else [],
    }
    _emit_grouping_ready(result)
    return result


def make_grouping_tools(project_id: int) -> list:
    """Build the grouping-review tools bound to one project (DB-backed).

    The plan lives in the DB (shared with the requirements UI); no workspace
    path is needed anymore.
    """

    async def _codes_to_ids() -> dict[str, int]:
        """REQ-code -> RequirementItem.id map for the project (live snapshot)."""
        async with AsyncSessionLocal() as session:
            items = await store.list_requirements(
                session, project_id, include_deleted=True
            )
        return {it.code: it.id for it in items}

    @tool
    async def review_grouping(
        types: Optional[list[str]] = None,
        documents: Optional[list[str]] = None,
    ) -> dict:
        """Detect duplicate requirements and persist an editable merge plan.

        Reads the live requirement store, finds duplicate groups (verbatim and
        semantic, reusing the consolidation dedup), and persists the plan to the
        grouping_plans table. Returns the plan id and the groups (each with its
        id, keeper + member REQ-codes, reason, confidence and current decision)
        so they can be shown in the chat for the user to review.

        Optional scope filters (omit both = whole live store, the historical
        behavior):
            types: only group requirements of these ReqType values —
                functional, performance, security, usability, reliability,
                maintainability, compliance, constraint, process, data.
                An invalid value is reported back listing the valid ones.
            documents: only group requirements extracted from these documents.
                Each entry may be a filename ("spec.pdf"), a workspace
                rel_path ("docs/spec.pdf") or the absolute container path.
                Manual requirements (created without a source document) are
                EXCLUDED while this filter is active.
        Use them when the user scopes the review (e.g. "agrupa solo los de
        seguridad" -> types=["security"]; "solo los de spec.pdf" ->
        documents=["spec.pdf"]).

        Does NOT merge anything. After the user curates the plan conversationally
        -- accept/reject groups with set_group_decision, change a keeper or
        members with edit_group -- call apply_grouping_plan.
        """
        try:
            async with AsyncSessionLocal() as session:
                return await run_grouping_review(
                    session, project_id, types=types, documents=documents
                )
        except Exception as exc:  # noqa: BLE001 -- surface to the model
            return {"error": f"review_grouping failed: {exc}"}

    @tool
    async def set_group_decision(group_id: int, decision: str) -> dict:
        """Set a group's curation decision: 'accept', 'reject' or 'pending'.

        Use after review_grouping to mark which duplicate groups the user wants
        merged (accept) or skipped (reject). Only 'accept' groups are merged by
        apply_grouping_plan. 'pending' resets a previous choice.
        """
        try:
            dec = GroupDecision(decision)
        except ValueError:
            return {
                "error": f"decision invalida: {decision!r} "
                "(usá accept | reject | pending)",
            }
        try:
            async with AsyncSessionLocal() as session:
                result = await gstore.set_group_decision(session, group_id, dec)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"set_group_decision failed: {exc}"}
        if result is None:
            return {"error": f"grupo {group_id} no existe"}
        return result

    @tool
    async def edit_group(
        group_id: int,
        keeper_code: Optional[str] = None,
        member_codes: Optional[list[str]] = None,
    ) -> dict:
        """Change a group's keeper and/or members (by REQ-code).

        Codes are resolved against the live store and the group row is updated.
        Pass only the fields to change. Example: the user says "en el grupo 2,
        que el keeper sea REQ-012" -> edit_group(2, keeper_code="REQ-012").
        """
        try:
            kwargs: dict = {}
            if keeper_code is not None or member_codes is not None:
                codes = await _codes_to_ids()
            if keeper_code is not None:
                if keeper_code not in codes:
                    return {"error": f"keeper no existe: {keeper_code}"}
                kwargs["keeper_id"] = codes[keeper_code]
            if member_codes is not None:
                missing = [c for c in member_codes if c not in codes]
                if missing:
                    return {"error": f"miembros no existen: {missing}"}
                kwargs["member_ids"] = [codes[c] for c in member_codes]
            async with AsyncSessionLocal() as session:
                result = await gstore.update_group(session, group_id, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"edit_group failed: {exc}"}
        if result is None:
            return {"error": f"grupo {group_id} no existe"}
        return result

    @tool
    async def apply_grouping_plan(plan_id: Optional[int] = None) -> dict:
        """Apply a grouping plan: merge every 'accept' group idempotently.

        Args:
            plan_id: id of the plan to apply. None picks the most recent
                status:'proposed' plan (else the most recent plan overall).

        Each accepted group merges its members into the keeper (sources unioned,
        members soft-deleted as MERGED). Idempotent: already-merged groups report
        'already_applied' and are skipped; invalid groups (unknown/merged keeper
        or missing members) are reported 'invalid'. Sets plan status to
        applied / partially-applied. NUNCA llamar sin confirmacion explicita del
        usuario.
        """
        try:
            async with AsyncSessionLocal() as session:
                pid = plan_id
                if pid is None:
                    plans = await gstore.list_plans(session, project_id)
                    if not plans:
                        return {
                            "error": "no hay planes de agrupamiento; "
                            "llamá a review_grouping primero",
                        }
                    pid = next(
                        (p["id"] for p in plans if p["status"] == "proposed"),
                        plans[0]["id"],
                    )
                result = await gstore.apply_plan(
                    session, pid, changed_by="agent"
                )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"apply_grouping_plan failed: {exc}"}
        return result

    @tool
    async def list_grouping_plans() -> dict:
        """List persisted grouping plans for the project (newest first).

        Each entry: id, status (proposed/applied/partially-applied),
        generated_at, applied_at, total groups and accepted count. Use to resume
        a review or audit what was applied.
        """
        async with AsyncSessionLocal() as session:
            plans = await gstore.list_plans(session, project_id)
        return {"plans": plans}

    return [
        review_grouping,
        set_group_decision,
        edit_group,
        apply_grouping_plan,
        list_grouping_plans,
    ]
