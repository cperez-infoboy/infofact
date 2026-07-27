"""Subagent tools for the grouping-review workflow.

review_grouping     — build a plan from the live store, write it to
                      .infofact/grouping-plans/<ts>/plan.md, return it for chat.
apply_grouping_plan — parse a (user-edited) plan, validate refs against the
                      store, run the merges idempotently, rewrite frontmatter.
list_grouping_plans — list existing plan dirs so a previous review can resume.

All tools close over project_id + the host workspace path; each opens a fresh
DB session. They never load the heavy models beyond what build_grouping_plan
already needs (embeddings, same as consolidation).

Design notes
------------
- Plans live under ``<workspace>/.infofact/grouping-plans/<timestamp>/plan.md``.
  ``.infofact`` is in ``_IGNORED_DIRS`` (ingestion.py) so the capture pipeline
  never parses a plan as a client document.
- ``apply_grouping_plan`` validates EVERY referenced code against the store
  before mutating, and is idempotent: a group whose members are already
  ``MERGED`` into the keeper is reported ``already_applied`` and skipped, never
  re-merged (merge_requirements appends revisions, so a blind re-apply would
  duplicate the audit trail).
- The plan is the single source of truth for what gets merged; the
  conversation is just how the user edits it.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from langchain_core.tools import tool

from backend.agents.pipelines.grouping import (
    PlanParseError,
    build_grouping_plan,
    parse_plan_md,
    serialize_plan_md,
)
from backend.database import AsyncSessionLocal
from backend.models.requirement import RequirementItem, ReqStatus
from backend.services import requirement_store as store

logger = logging.getLogger(__name__)

_PLAN_DIRNAME = "grouping-plans"
_PLAN_FILENAME = "plan.md"


def _plans_root(host_workspace: Path) -> Path:
    return host_workspace / ".infofact" / _PLAN_DIRNAME


def _ts_dir() -> str:
    """ISO timestamp without ':' (invalid in Windows paths; sorts chronologically)."""
    return datetime.now().strftime("%Y-%m-%dT%H%M%S")


def make_grouping_tools(project_id: int, host_workspace: Path) -> list:
    """Build the grouping-review tools bound to one project + workspace."""

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(host_workspace))
        except ValueError:
            return str(p)

    @tool
    async def review_grouping() -> dict:
        """Detect duplicate requirements and produce an editable merge plan.

        Reads the live requirement store, finds duplicate groups (verbatim and
        semantic, reusing the consolidation dedup), and writes a Markdown plan
        to .infofact/grouping-plans/<timestamp>/plan.md. Returns the plan text
        so it can be shown in the chat for the user to review and edit.

        Does NOT merge anything. After the user edits the plan
        conversationally (e.g. "en el grupo 2, que el keeper sea REQ-012 y
        borrá el grupo 3"), call apply_grouping_plan.
        """
        try:
            async with AsyncSessionLocal() as session:
                plan = await build_grouping_plan(
                    session, project_id, project=str(project_id),
                )
            md = serialize_plan_md(plan)
            plan_dir = _plans_root(host_workspace) / _ts_dir()
            plan_dir.mkdir(parents=True, exist_ok=True)
            plan_path = plan_dir / _PLAN_FILENAME
            plan_path.write_text(md, encoding="utf-8")
            return {
                "plan_path": _rel(plan_path),
                "group_count": len(plan.groups),
                "groups": [
                    {
                        "keeper": g.keeper_code,
                        "members": g.member_codes,
                        "reason": g.reason,
                        "confidence": g.confidence,
                    }
                    for g in plan.groups
                ],
                "markdown": md,
            }
        except Exception as exc:  # noqa: BLE001 — surface to the model
            return {"error": f"review_grouping failed: {exc}"}

    @tool
    async def apply_grouping_plan(plan_path: str = "") -> dict:
        """Apply a (possibly user-edited) grouping plan: merge each group.

        Args:
            plan_path: path to plan.md relative to the project workspace. Empty
                picks the most recent status:proposed plan.

        Each group merges its members into the keeper (sources unioned, members
        soft-deleted as MERGED). Idempotent: a group whose members are already
        MERGED into the keeper is reported 'already_applied' and skipped.
        Groups with an unknown/already-merged keeper or missing members are
        reported 'invalid'. Rewrites the plan's frontmatter status to
        applied / partially-applied.
        """
        root = _plans_root(host_workspace)
        try:
            md_path = _resolve_plan_path(host_workspace, plan_path, root)
            md = md_path.read_text(encoding="utf-8")
            plan = parse_plan_md(md)
        except PlanParseError as exc:
            return {"error": f"plan parse failed: {exc}"}
        except FileNotFoundError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"apply_grouping_plan failed: {exc}"}

        if not plan.groups:
            return {
                "plan_path": _rel(md_path), "status": "applied",
                "applied": 0, "already_applied": 0, "invalid": 0,
                "groups": [], "note": "el plan no tiene grupos",
            }

        results: list[dict] = []
        applied = already = invalid = 0
        try:
            async with AsyncSessionLocal() as session:
                snapshot = await store.list_requirements(
                    session, project_id, include_deleted=True,
                )
                by_code = {it.code: it for it in snapshot}
                for g in plan.groups:
                    outcome = await _apply_one_group(session, by_code, g)
                    results.append(outcome)
                    if outcome["status"] == "applied":
                        applied += 1
                    elif outcome["status"] == "already_applied":
                        already += 1
                    else:
                        invalid += 1
        except Exception as exc:  # noqa: BLE001
            return {"error": f"apply_grouping_plan failed: {exc}"}

        if invalid == 0 and already == 0:
            final_status = "applied"
        elif applied > 0:
            final_status = "partially-applied"
        else:
            final_status = plan.status  # nothing applied; leave as-is
        _rewrite_status(md_path, md, final_status)

        return {
            "plan_path": _rel(md_path),
            "status": final_status,
            "applied": applied,
            "already_applied": already,
            "invalid": invalid,
            "groups": results,
        }

    @tool
    async def list_grouping_plans() -> dict:
        """List grouping-plan directories under .infofact/grouping-plans/.

        Each entry has the timestamp dir name, the frontmatter status, and the
        group count. Use this to resume a previous review or audit what was
        applied.
        """
        root = _plans_root(host_workspace)
        if not root.exists():
            return {"plans": []}
        out: list[dict] = []
        for d in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
            plan_file = d / _PLAN_FILENAME
            if not plan_file.exists():
                continue
            try:
                plan = parse_plan_md(plan_file.read_text(encoding="utf-8"))
                out.append({
                    "dir": d.name,
                    "status": plan.status,
                    "groups": len(plan.groups),
                    "path": _rel(plan_file),
                })
            except PlanParseError:
                out.append({
                    "dir": d.name, "status": "unparseable",
                    "path": _rel(plan_file),
                })
        return {"plans": out}

    return [review_grouping, apply_grouping_plan, list_grouping_plans]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _apply_one_group(session, by_code: dict, group) -> dict:
    """Merge one group idempotently. Returns an outcome dict.

    ``by_code`` is refreshed in place as merges land, so later groups in the
    same plan see updated status (a member merged in group 1 may be referenced
    again — reported already_applied, not re-merged).
    """
    keeper = by_code.get(group.keeper_code)
    if keeper is None:
        return {
            "keeper": group.keeper_code, "status": "invalid",
            "reason": f"{group.keeper_code} no existe en el proyecto",
        }
    if keeper.status == ReqStatus.MERGED:
        return {
            "keeper": group.keeper_code, "status": "invalid",
            "reason": f"el keeper {group.keeper_code} ya está MERGED",
        }

    member_items: list = []
    missing: list[str] = []
    for code in group.member_codes:
        item = by_code.get(code)
        if item is None:
            missing.append(code)
        else:
            member_items.append(item)
    if missing:
        return {
            "keeper": group.keeper_code, "status": "invalid",
            "reason": f"miembros no encontrados: {', '.join(missing)}",
        }

    live = [m for m in member_items if m.status != ReqStatus.MERGED]
    merged_into_keeper = [
        m for m in member_items
        if m.status == ReqStatus.MERGED and m.merged_into == keeper.id
    ]
    if not live:
        # No live members: either fully applied already, or merged elsewhere.
        if member_items and len(merged_into_keeper) == len(member_items):
            return {
                "keeper": group.keeper_code, "status": "already_applied",
                "members": group.member_codes,
            }
        return {
            "keeper": group.keeper_code, "status": "invalid",
            "reason": "todos los miembros ya están MERGED pero no en este keeper",
        }

    ids = [keeper.id] + [m.id for m in live]
    try:
        kept = await store.merge_requirements(
            session, ids, keep_id=keeper.id,
            reason="grouping_plan", changed_by="agent",
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "keeper": group.keeper_code, "status": "invalid",
            "reason": f"merge falló: {exc}",
        }

    # Refresh the snapshot so subsequent groups in this plan see MERGED state.
    for m in live:
        refreshed = await session.get(RequirementItem, m.id)
        if refreshed is not None:
            by_code[m.code] = refreshed
    return {
        "keeper": kept.code, "status": "applied",
        "members": [m.code for m in live],
        "merged_count": len(live),
    }


def _resolve_plan_path(
    host_workspace: Path, plan_path: str, root: Path,
) -> Path:
    """Resolve the plan to apply: explicit path, else most-recent proposed."""
    if plan_path:
        candidate = host_workspace / plan_path
        if not candidate.exists():
            raise FileNotFoundError(f"plan no encontrado: {plan_path}")
        return candidate
    if not root.exists():
        raise FileNotFoundError(
            "no hay planes de agrupamiento; llamá a review_grouping primero"
        )
    dirs = sorted(
        (d for d in root.iterdir() if d.is_dir() and (d / _PLAN_FILENAME).exists()),
        reverse=True,
    )
    for d in dirs:
        try:
            plan = parse_plan_md((d / _PLAN_FILENAME).read_text(encoding="utf-8"))
        except PlanParseError:
            continue
        if plan.status == "proposed":
            return d / _PLAN_FILENAME
    if dirs:
        return dirs[0] / _PLAN_FILENAME
    raise FileNotFoundError(
        "no hay planes de agrupamiento; llamá a review_grouping primero"
    )


def _rewrite_status(plan_path: Path, original_md: str, new_status: str) -> None:
    """Update the frontmatter status line in-place (best effort, never fatal)."""
    new_md, count = re.subn(
        r"^status:\s*\S+", f"status: {new_status}", original_md,
        count=1, flags=re.MULTILINE,
    )
    if count == 0:
        return  # no status line in frontmatter — leave the file untouched
    try:
        plan_path.write_text(new_md, encoding="utf-8")
    except OSError:
        logger.warning("no se pudo reescribir el status del plan %s", plan_path)
