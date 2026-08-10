"""LangChain tools that expose the requirement store to the capture subagent.

The pipeline writes rows (requirements_service._persist); these tools are the
editing surface the subagent (and, through chat, the human) uses afterwards:
add, update, delete, merge, split, link, resolve, list, get, approve, reject,
and attach acceptance criteria. Each maps 1:1 to a store function.

Design:
- Factory `make_requirements_tools(project_id)` closes over the project_id.
  The agent never sees project_id as an argument (it cannot spoof another
  project's rows). build_agent builds one set per request, like the agent.
- Each tool opens its own AsyncSessionLocal, calls the store, returns a plain
  dict. Errors become {"error": ...} dicts so the model sees structured
  feedback instead of a crashed tool call.
- Tools speak `code` (the human-visible "REQ-XXXX" handle), not the integer id:
  the model reads codes from list output and references them. Code->id
  resolution lives here (adapter concern), the store stays id-centric.
"""
from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AsyncSessionLocal
from backend.models.requirement import (
    Priority,
    RelationKind,
    ReqStatus,
    ReqType,
    RequirementItem,
)
from backend.services import requirement_store as store

ReqTypeValue = Literal[
    "functional", "performance", "security", "usability",
    "reliability", "maintainability", "compliance",
    "constraint", "process", "data",
]
PriorityValue = Literal["must", "should", "could", "wont"]
StatusValue = Literal[
    "unverified", "draft", "validated",
    "approved", "rejected", "merged", "superseded",
]
RelationKindValue = Literal["duplicate", "contradicts", "depends_on"]


# --- code <-> id resolution (adapter concern) -------------------------------


async def _code_to_id(
    session: AsyncSession, project_id: int, code: str
) -> int:
    """Resolve a REQ-XXXX code to its integer id within the project."""
    item_id = await session.scalar(
        select(RequirementItem.id).where(
            RequirementItem.project_id == project_id,
            RequirementItem.code == code,
        )
    )
    if item_id is None:
        raise KeyError(f"requirement {code} not found in project")
    return item_id


def _item_summary(item: RequirementItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "code": item.code,
        "statement": item.statement,
        "type": item.type.value,
        "priority": item.priority.value,
        "status": item.status.value,
        "span_verified": item.span_verified,
    }


def make_requirements_read_tools(project_id: int) -> list:
    """Build the read-only tool subset (list / get / build_srs).

    Registered with the orchestrator so it can answer store queries directly,
    without delegating to the capture subagent. ``project_id`` is closed over
    (same isolation contract as the editing tools); no mutation tool is exposed,
    so the orchestrator cannot change the store.

    The three tool bodies are duplicated from ``make_requirements_tools`` on
    purpose: the editing factory interleaves reads with mutations in its return
    list, and restructuring it risks the mutation tools. If you change a read
    tool here, change its twin there too.
    """

    @tool
    async def list_requirements(
        status: StatusValue | None = None,
        type: ReqTypeValue | None = None,
        priority: PriorityValue | None = None,
        include_deleted: bool = False,
    ) -> dict:
        """List requirements in the project, optionally filtered.

        Soft-deleted rows (rejected / merged / superseded) are hidden unless
        include_deleted is true. Returns one compact summary per item.
        """
        try:
            async with AsyncSessionLocal() as session:
                items = await store.list_requirements(
                    session,
                    project_id,
                    status=ReqStatus(status) if status else None,
                    type=ReqType(type) if type else None,
                    priority=Priority(priority) if priority else None,
                    include_deleted=include_deleted,
                )
                return {
                    "count": len(items),
                    "items": [_item_summary(it) for it in items],
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"list_requirements failed: {exc}"}

    @tool
    async def get_requirement(code: str) -> dict:
        """Full detail of one requirement: fields, relations, revision history.

        Use this to inspect a requirement before editing, or to review how it
        changed over time (every mutation is versioned in the revisions list).
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                return await store.get_requirement(session, req_id)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"get_requirement failed: {exc}"}

    @tool
    async def build_srs() -> dict:
        """Generate the SRS Markdown from the current requirement store.

        The SRS is built deterministically from RequirementItem rows (never
        hand-written). Returns the markdown text plus a counts summary so you
        can paste an excerpt to the user and offer the document for download.

        Call this after capture + validation, when the user asks for the SRS,
        the requirements matrix, or the deliverable document.
        """
        try:
            async with AsyncSessionLocal() as session:
                from backend.services.srs_builder import build_srs as _build
                from backend.models import Project as _Project
                proj = await session.get(_Project, project_id)
                name = getattr(proj, "name", "") if proj else ""
                desc = getattr(proj, "description", "") if proj else ""
                result = await _build(
                    session,
                    project_id,
                    project_name=name,
                    project_description=desc,
                )
                return {
                    "markdown": result["markdown"],
                    "generated_at": result["generated_at"],
                    "counts": result["counts"],
                    # Preview: primer KB para que el modelo pueda citar sin
                    # volcar el markdown completo en el contexto.
                    "preview": result["markdown"][:1024],
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"build_srs failed: {exc}"}

    return [list_requirements, get_requirement, build_srs]


def make_requirements_tools(project_id: int) -> list:
    """Build the editing tool set bound to one project.

    Returned tools close over `project_id`; each opens a fresh session, so they
    are safe to call in any order from the agent loop.
    """

    @tool
    async def add_requirement(
        statement: str,
        type: ReqTypeValue = "functional",
        priority: PriorityValue = "must",
        source_quote: str | None = None,
        source_section: str | None = None,
        source_page: int | None = None,
    ) -> dict:
        """Create a new DRAFT requirement with a fresh opaque code.

        Use this to record a requirement that the pipeline missed or that a
        human contributes during review. If source_quote is provided it becomes
        the traceability anchor (source span); otherwise the item has no source
        and stays unverified until validated.
        """
        source: dict[str, Any] | None = None
        if source_quote:
            source = {
                "quote": source_quote,
                "section": source_section,
                "page": source_page,
            }
        try:
            async with AsyncSessionLocal() as session:
                item = await store.add_requirement(
                    session,
                    project_id,
                    statement=statement,
                    type=ReqType(type),
                    priority=Priority(priority),
                    source=source,
                    explicit=source is None,
                    created_by="agent",
                )
                return _item_summary(item)
        except Exception as exc:  # noqa: BLE001 — surface to the model
            return {"error": f"add_requirement failed: {exc}"}

    @tool
    async def update_requirement(
        code: str,
        statement: str | None = None,
        type: ReqTypeValue | None = None,
        priority: PriorityValue | None = None,
        reason: str = "update",
    ) -> dict:
        """Edit a requirement's statement, type, or priority.

        Only the fields you pass are changed. Status, source, and pointers are
        not editable here — use approve / reject / merge / split for those so
        each transition is auditable on its own.
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                item = await store.update_requirement(
                    session,
                    req_id,
                    statement=statement,
                    type=ReqType(type) if type else None,
                    priority=Priority(priority) if priority else None,
                    reason=reason,
                    changed_by="agent",
                )
                return _item_summary(item)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"update_requirement failed: {exc}"}

    @tool
    async def delete_requirement(code: str, reason: str) -> dict:
        """Soft-delete a requirement (status -> REJECTED).

        The row is kept for audit; it disappears from the default list view but
        stays in history. `reason` is required and recorded.
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                item = await store.delete_requirement(
                    session, req_id, reason=reason, changed_by="agent"
                )
                return _item_summary(item)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"delete_requirement failed: {exc}"}

    @tool
    async def merge_requirements(
        codes: list[str],
        keep_statement: str | None = None,
        reason: str = "merge_duplicates",
    ) -> dict:
        """Fold duplicate requirements into one.

        The first code in the list is the keeper (unless it is rejected, in
        which case pick a live one). Sources from every duplicate are UNIONED
        onto the keeper (traceability to all originals is preserved); the rest
        become MERGED with a pointer to the keeper.
        """
        try:
            async with AsyncSessionLocal() as session:
                ids = [
                    await _code_to_id(session, project_id, c) for c in codes
                ]
                kept = await store.merge_requirements(
                    session,
                    ids,
                    keep_statement=keep_statement,
                    reason=reason,
                    changed_by="agent",
                )
                return {
                    "kept": _item_summary(kept),
                    "merged_count": len(ids) - 1,
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"merge_requirements failed: {exc}"}

    @tool
    async def split_requirement(
        code: str, parts: list[str], reason: str = "split_non_atomic"
    ) -> dict:
        """Split a non-atomic requirement into operational sub-requirements.

        The original becomes SUPERSEDED; each part is a new DRAFT with parent_id
        pointing back to the original, inheriting its source span.
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                created = await store.split_requirement(
                    session, req_id, parts, reason=reason, changed_by="agent"
                )
                return {
                    "new_codes": [_item_summary(c) for c in created],
                    "count": len(created),
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"split_requirement failed: {exc}"}

    @tool
    async def link_requirements(
        code: str,
        other_code: str,
        relation: RelationKindValue,
        note: str | None = None,
    ) -> dict:
        """Flag a relationship between two requirements.

        relation is one of: duplicate, contradicts, depends_on. The pair is
        recorded as PROPOSED for later resolution; nothing is deleted.
        """
        try:
            async with AsyncSessionLocal() as session:
                from_id = await _code_to_id(session, project_id, code)
                to_id = await _code_to_id(session, project_id, other_code)
                rel = await store.link_requirements(
                    session,
                    from_id,
                    to_id,
                    RelationKind(relation),
                    note=note,
                    detected_by="agent",
                )
                return {
                    "relation_id": rel.id,
                    "from": code,
                    "to": other_code,
                    "kind": rel.kind.value,
                    "status": rel.status.value,
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"link_requirements failed: {exc}"}

    @tool
    async def resolve_conflict(
        relation_id: int, winner_code: str, note: str | None = None
    ) -> dict:
        """Resolve a contradiction by recording the winning requirement.

        The relation moves to RESOLVED with the winner noted. The loser is NOT
        auto-rejected — rejecting it is a separate explicit action.
        """
        try:
            async with AsyncSessionLocal() as session:
                winner_id = await _code_to_id(
                    session, project_id, winner_code
                )
                rel = await store.resolve_conflict(
                    session,
                    relation_id,
                    winner_id=winner_id,
                    note=note,
                    changed_by="agent",
                )
                return {
                    "relation_id": rel.id,
                    "status": rel.status.value,
                    "note": rel.note,
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"resolve_conflict failed: {exc}"}

    @tool
    async def list_requirements(
        status: StatusValue | None = None,
        type: ReqTypeValue | None = None,
        priority: PriorityValue | None = None,
        include_deleted: bool = False,
    ) -> dict:
        """List requirements in the project, optionally filtered.

        Soft-deleted rows (rejected / merged / superseded) are hidden unless
        include_deleted is true. Returns one compact summary per item.
        """
        try:
            async with AsyncSessionLocal() as session:
                items = await store.list_requirements(
                    session,
                    project_id,
                    status=ReqStatus(status) if status else None,
                    type=ReqType(type) if type else None,
                    priority=Priority(priority) if priority else None,
                    include_deleted=include_deleted,
                )
                return {
                    "count": len(items),
                    "items": [_item_summary(it) for it in items],
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"list_requirements failed: {exc}"}

    @tool
    async def get_requirement(code: str) -> dict:
        """Full detail of one requirement: fields, relations, revision history.

        Use this to inspect a requirement before editing, or to review how it
        changed over time (every mutation is versioned in the revisions list).
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                return await store.get_requirement(session, req_id)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"get_requirement failed: {exc}"}

    @tool
    async def approve_requirement(
        code: str, mark_span_verified: bool = False,
    ) -> dict:
        """Mark a requirement APPROVED (human accepts it into the SRS).

        Set mark_span_verified=True when the human has also manually confirmed
        the source span against the original document — the approval will carry
        span_verified=True in the same auditable revision.
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                item = await store.approve_requirement(
                    session, req_id,
                    changed_by="agent",
                    mark_span_verified=mark_span_verified,
                )
                return _item_summary(item)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"approve_requirement failed: {exc}"}

    @tool
    async def verify_span(code: str) -> dict:
        """Mark a requirement's source span as human-verified.

        Use this when span_verified is False but a human has confirmed the
        requirement traces to its quoted source. Records a manual verification
        revision so the audit trail reflects WHO checked it and WHEN.
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                item = await store.verify_span(
                    session, req_id, changed_by="agent"
                )
                return _item_summary(item)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"verify_span failed: {exc}"}

    @tool
    async def reject_requirement(code: str, reason: str) -> dict:
        """Mark a requirement REJECTED (human discards it). Soft-delete.

        `reason` is required and recorded in the revision trail.
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                item = await store.reject_requirement(
                    session, req_id, reason=reason, changed_by="agent"
                )
                return _item_summary(item)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"reject_requirement failed: {exc}"}

    @tool
    async def add_acceptance_criterion(
        code: str, criterion: str
    ) -> dict:
        """Attach a Gherkin acceptance criterion to a requirement.

        Use this when the critic flagged an item as not directly testable; the
        criterion makes it verifiable without changing its statement.
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                item = await store.add_acceptance_criterion(
                    session, req_id, criterion, changed_by="agent"
                )
                return {
                    "code": item.code,
                    "acceptance_criteria": list(
                        item.acceptance_criteria or []
                    ),
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"add_acceptance_criterion failed: {exc}"}

    @tool
    async def build_srs() -> dict:
        """Generate the SRS Markdown from the current requirement store.

        The SRS is built deterministically from RequirementItem rows (never
        hand-written). Returns the markdown text plus a counts summary so you
        can paste an excerpt to the user and offer the document for download.

        Call this after capture + validation, when the user asks for the SRS,
        the requirements matrix, or the deliverable document.
        """
        try:
            async with AsyncSessionLocal() as session:
                from backend.services.srs_builder import build_srs as _build
                from backend.models import Project as _Project
                proj = await session.get(_Project, project_id)
                name = getattr(proj, "name", "") if proj else ""
                desc = getattr(proj, "description", "") if proj else ""
                result = await _build(
                    session,
                    project_id,
                    project_name=name,
                    project_description=desc,
                )
                return {
                    "markdown": result["markdown"],
                    "generated_at": result["generated_at"],
                    "counts": result["counts"],
                    # Preview: primer KB para que el modelo pueda citar sin
                    # volcar el markdown completo en el contexto.
                    "preview": result["markdown"][:1024],
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"build_srs failed: {exc}"}

    @tool
    async def capture_status() -> dict:
        """Count existing requirements + grouping plans for THIS project.

        Call this BEFORE a capture. If requirements > 0, tell the
        user how many exist and the last code, then ask whether to reset
        everything or append. Never reset without explicit user confirmation.
        """
        try:
            async with AsyncSessionLocal() as session:
                from sqlalchemy import func, select
                from backend.models.requirement import (
                    GroupingPlan,
                    RequirementItem,
                )
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
        except Exception as exc:  # noqa: BLE001
            return {"error": f"capture_status failed: {exc}"}

    @tool
    async def reset_capture() -> dict:
        """Delete ALL capture-derived data for THIS project (hard).

        Destructive and irreversible: removes every requirement, relation,
        revision, grouping plan/group, quality finding, GORE goal + link,
        and SRS document version so the next capture starts truly fresh
        with no stale findings or orphan SRS versions.
        ONLY call this after the user explicitly confirmed they want
        to reset — never on your own initiative. When unsure, do NOT reset.
        """
        try:
            async with AsyncSessionLocal() as session:
                from backend.services.requirements_service import (
                    reset_project_capture,
                )
                return await reset_project_capture(session, project_id)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"reset_capture failed: {exc}"}

    return [
        add_requirement,
        update_requirement,
        delete_requirement,
        merge_requirements,
        split_requirement,
        link_requirements,
        resolve_conflict,
        list_requirements,
        get_requirement,
        approve_requirement,
        verify_span,
        reject_requirement,
        add_acceptance_criterion,
        build_srs,
        capture_status,
        reset_capture,
    ]
