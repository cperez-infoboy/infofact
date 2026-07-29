"""Subagent tools for the grouping-review workflow (DB-backed).

review_grouping     — build a plan from the live store, persist it to the
                      grouping_plans table, return the groups for chat.
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
"""
from __future__ import annotations

import logging
from typing import Optional

from langchain_core.tools import tool

from backend.agents.pipelines.grouping import build_grouping_plan
from backend.database import AsyncSessionLocal
from backend.models.requirement import GroupDecision
from backend.services import grouping_store as gstore
from backend.services import requirement_store as store

logger = logging.getLogger(__name__)


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
    async def review_grouping() -> dict:
        """Detect duplicate requirements and persist an editable merge plan.

        Reads the live requirement store, finds duplicate groups (verbatim and
        semantic, reusing the consolidation dedup), and persists the plan to the
        grouping_plans table. Returns the plan id and the groups (each with its
        id, keeper + member REQ-codes, reason, confidence and current decision)
        so they can be shown in the chat for the user to review.

        Does NOT merge anything. After the user curates the plan conversationally
        -- accept/reject groups with set_group_decision, change a keeper or
        members with edit_group -- call apply_grouping_plan.
        """
        try:
            async with AsyncSessionLocal() as session:
                plan = await build_grouping_plan(
                    session, project_id, project=str(project_id),
                )
                plan_id = await gstore.persist_plan(session, plan, project_id)
                data = await gstore.get_plan(session, plan_id)
            return {
                "plan_id": plan_id,
                "group_count": len(plan.groups),
                "groups": data["groups"] if data else [],
            }
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
