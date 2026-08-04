"""Persistence + reset helpers for the agent-driven requirements capture.

Host-side only: the Z.ai LLM calls and SQLite run in the FastAPI process.
Documents are read from the workspace path (a host-side bind mount), never via
the DockerSandbox (Decision 1 of CLAUDE.md). The capture itself is driven
stage-by-stage by the ``requirements-capture-agent`` subagent; this module owns
only the DB-touching pieces it reuses:

  - ``_persist``              write RequirementItem rows (opaque REQ-XXXX codes,
                              derived sub-items, requirement.added events)
  - ``reset_project_capture`` wipe every requirement + grouping row (start over)

The end-to-end orchestration that used to live here
(``run_requirements_pipeline``) was removed when the deterministic capture was
consolidated into the single agent-driven path.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.pipelines.classification import (
    ClassificationDecision,
    ClassificationResult,
)
from backend.agents.pipelines.extraction import RawRequirement
from backend.models.requirement import (
    Priority,
    ReqStatus,
    ReqType,
    RequirementItem,
)
from backend.services._req_codes import gen_opaque_code

logger = logging.getLogger(__name__)

EventCb = Callable[[str, dict], Awaitable[None]]


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _source_from_raw(it: RawRequirement) -> dict:
    """Build the JSON `source` payload from a raw item.

    `quote` is the verbatim source_span — the anti-hallucination anchor and the
    jump-to-source handle for the frontend (DocViewer, Paso 9).
    """
    return {
        "document_id": it.document_id,
        "section": it.section,
        "page": it.page,
        "quote": it.source_span,
    }


def _status_from_raw(it: RawRequirement) -> ReqStatus:
    """Map pipeline status to the store ReqStatus."""
    if not it.span_verified:
        return ReqStatus.UNVERIFIED
    return ReqStatus.DRAFT


async def _persist(
    session: AsyncSession,
    project_id: int,
    items: list[RawRequirement],
    classification: ClassificationResult,
    on_event: EventCb | None = None,
) -> list[int]:
    """Write RequirementItem rows. Returns the persisted parent ids.

    Codes are opaque (Crockford base32), allocated uniquely per project: parent
    first, then its decomposition sub-items, then the next parent. Codes are
    PRE-ASSIGNED in one pass before any row is created — a shared ``reserved``
    set prevents collisions before the rows are flushed.

    Sub-items from decomposition are written as derived rows with parent_id set
    and the parent's source inherited (preserves traceability to the original
    span even though the sub-statement is LLM-derived).

    ``on_event`` (optional) receives a ``requirement.added`` event per row so the
    frontend can render items incrementally (live DocViewer, Paso 9).
    """
    async def _added(payload: dict) -> None:
        """Emit requirement.added; never break persistence on a callback failure."""
        if not on_event:
            return
        try:
            await on_event("requirement.added", payload)
        except Exception:
            logger.exception("on_event failed (requirement.added)")

    # Pass 0 — pre-assign unique opaque codes (parent + its subs) per item.
    # A shared `reserved` set guarantees no collision within this batch before
    # the rows are flushed.
    reserved: set[str] = set()
    plan: list[tuple[str, RawRequirement, ClassificationDecision | None,
                     list, list[str]]] = []
    for it in items:
        decision = classification.decisions.get(it.id)
        parent_code = await gen_opaque_code(
            session, project_id, reserved=reserved
        )
        sub_parts: list = (
            classification.decompositions.get(it.id, [])
            if decision and decision.decomposition_needed else []
        )
        sub_codes: list[str] = []
        for _ in sub_parts:
            sub_codes.append(
                await gen_opaque_code(session, project_id, reserved=reserved)
            )
        plan.append((parent_code, it, decision, sub_parts, sub_codes))

    created_ids: list[int] = []
    code_to_id: dict[str, int] = {}

    # Pass 1 — parents (flush so we know their ids for child parent_id).
    for parent_code, it, decision, _sub_parts, _sub_codes in plan:
        rtype = decision.type if decision else ReqType.FUNCTIONAL
        prio = decision.priority if decision else Priority.MUST
        row = RequirementItem(
            project_id=project_id,
            code=parent_code,
            statement=it.statement,
            type=rtype,
            priority=prio,
            status=_status_from_raw(it),
            source=_source_from_raw(it),
            explicit=it.explicit,
            derived=False,
            explicit_priority=bool((it.priority_hint or "").strip()),
            confidence=it.confidence,
            span_verified=it.span_verified,
            acceptance_criteria=[],
            created_by="agent",
        )
        session.add(row)
        await session.flush()
        created_ids.append(row.id)
        code_to_id[parent_code] = row.id
        await _added({
            "code": parent_code,
            "statement": it.statement,
            "type": rtype.value,
            "priority": prio.value,
            "explicit": it.explicit,
            "derived": False,
            "confidence": it.confidence,
            "span_verified": it.span_verified,
            "source": _source_from_raw(it),
        })

    # Pass 2 — derived sub-items (parent_id now known), inheriting parent source.
    for parent_code, it, decision, sub_parts, sub_codes in plan:
        if not sub_parts:
            continue
        parent_id = code_to_id[parent_code]
        rtype = decision.type if decision else ReqType.FUNCTIONAL
        prio = decision.priority if decision else Priority.MUST
        inherited = _source_from_raw(it)
        for part, sub_code in zip(sub_parts, sub_codes):
            session.add(RequirementItem(
                project_id=project_id,
                code=sub_code,
                statement=part.statement,
                type=rtype,
                priority=prio,
                status=ReqStatus.DRAFT,
                source=inherited,
                explicit=False,
                derived=True,
                explicit_priority=bool((it.priority_hint or "").strip()),
                parent_id=parent_id,
                confidence=it.confidence,
                span_verified=it.span_verified,
                acceptance_criteria=[],
                created_by="agent",
            ))
            await _added({
                "code": sub_code,
                "statement": part.statement,
                "type": rtype.value,
                "priority": prio.value,
                "explicit": False,
                "derived": True,
                "parent_code": parent_code,
                "confidence": it.confidence,
                "span_verified": it.span_verified,
                "source": inherited,
            })
    await session.commit()
    return created_ids


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def reset_project_capture(
    session: AsyncSession, project_id: int
) -> dict:
    """Wipe ALL capture-derived data for a project (start over).

    Deletes in dependency order so it is correct whether or not SQLite enforces
    the CASCADE pragma:

    1. Goal-links (FK to goals + requirement_items)
    2. Goals
    3. SRS findings (FK to requirement_items)
    4. SRS document versions
    5. Grouping plans (FK keeper_id -> requirement_items)
    6. Requirements (relations, revisions, items)

    After this the project is truly clean: the next capture allocates fresh
    opaque codes, the quality tab shows no stale findings, and no orphan SRS
    versions reference recycled requirement IDs.

    Destructive and irreversible — callers (the reset_capture agent tool) must
    gate this behind explicit user confirmation.
    """
    from backend.services.requirement_store import delete_all_requirements
    from backend.services.grouping_store import delete_all_plans
    from backend.services.srs_store import (
        delete_all_findings,
        delete_all_goals,
        delete_all_srs,
    )

    goals = await delete_all_goals(session, project_id)
    findings = await delete_all_findings(session, project_id)
    srs = await delete_all_srs(session, project_id)
    plans = await delete_all_plans(session, project_id)
    reqs = await delete_all_requirements(session, project_id)
    await session.commit()
    logger.info(
        "reset_project_capture(project_id=%s): deleted %d requirement(s), "
        "%d plan(s), %d finding(s), %d goal(s), %d SRS version(s)",
        project_id, reqs, plans, findings, goals, srs,
    )
    return {
        "deleted_requirements": reqs,
        "deleted_plans": plans,
        "deleted_findings": findings,
        "deleted_goals": goals,
        "deleted_srs_versions": srs,
    }
