"""Requirement store: typed CRUD over RequirementItem.

The pipeline writes rows via requirements_service._persist; this module is the
editing surface for everything after extraction — agent tools and human review
mutate requirements through here, never directly.

Hard rules enforced here (plan section 10.2):
- Soft-delete only. "Delete" flips status to REJECTED; merge/split set
  MERGED/SUPERSEDED with a pointer. The row stays for audit.
- Every mutation appends one RequirementRevision with a full snapshot, so the
  decision history is recoverable and the SRS is defensible.
- Merge unions source spans (traceability is multi-valued, not lost).
- Split keeps parent_id so operational sub-requirements trace back to the
  original high-level statement.

All functions are async and take an AsyncSession plus the project_id / req_id.
They commit themselves so callers (tools) do not need to manage transaction
lifecycles; a fresh AsyncSessionLocal per tool call is the intended pattern.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.requirement import (
    Priority,
    RelationKind,
    RelationStatus,
    ReqStatus,
    ReqType,
    RequirementItem,
    RequirementRelation,
    RequirementRevision,
)
from backend.services._req_codes import gen_opaque_code

_SOFT_DELETED = frozenset(
    {ReqStatus.REJECTED, ReqStatus.MERGED, ReqStatus.SUPERSEDED}
)
_CHANGE_REASON_MAX = 64  # RequirementRevision.change_reason column width


# --- helpers ----------------------------------------------------------------


async def _next_code(session: AsyncSession, project_id: int) -> str:
    """Allocate a unique opaque REQ-XXXX code for the project.

    Thin delegate over ``gen_opaque_code`` so the editing surface and the
    pipeline share one generator. Codes are opaque (Crockford base32): gaps
    after merge/delete don't read as lost requirements.
    """
    return await gen_opaque_code(session, project_id)


def _snapshot(item: RequirementItem) -> dict[str, Any]:
    """Serialize the mutable fields of an item for the revision trail."""
    return {
        "code": item.code,
        "statement": item.statement,
        "type": item.type.value if item.type else None,
        "priority": item.priority.value if item.priority else None,
        "status": item.status.value if item.status else None,
        "source": item.source,
        "explicit": item.explicit,
        "derived": item.derived,
        "parent_id": item.parent_id,
        "confidence": item.confidence,
        "span_verified": item.span_verified,
        "acceptance_criteria": list(item.acceptance_criteria or []),
        "merged_into": item.merged_into,
        "superseded_by": item.superseded_by,
    }


async def _append_revision(
    session: AsyncSession,
    item: RequirementItem,
    *,
    reason: str,
    changed_by: str,
) -> None:
    """Append an immutable snapshot row. Version is max(existing) + 1."""
    cur = await session.scalar(
        select(func.max(RequirementRevision.version)).where(
            RequirementRevision.req_id == item.id
        )
    )
    session.add(
        RequirementRevision(
            req_id=item.id,
            version=(cur or 0) + 1,
            snapshot=_snapshot(item),
            changed_by=changed_by,
            change_reason=reason[:_CHANGE_REASON_MAX],
        )
    )


async def _get_item(
    session: AsyncSession, req_id: int, *, project_id: int | None = None
) -> RequirementItem:
    """Fetch one item; raise KeyError if missing or outside the project."""
    stmt = select(RequirementItem).where(RequirementItem.id == req_id)
    if project_id is not None:
        stmt = stmt.where(RequirementItem.project_id == project_id)
    item = await session.scalar(stmt)
    if item is None:
        raise KeyError(f"requirement {req_id} not found")
    return item


def _source_list(item: RequirementItem) -> list[dict[str, Any]]:
    """Source is stored as a single dict OR a list of dicts after a merge;
    normalize to a list so unions are straightforward.
    """
    src = item.source
    if src is None:
        return []
    if isinstance(src, list):
        return list(src)
    return [src]


# --- create / update --------------------------------------------------------


async def add_requirement(
    session: AsyncSession,
    project_id: int,
    *,
    statement: str,
    type: ReqType = ReqType.FUNCTIONAL,
    priority: Priority = Priority.MUST,
    source: dict[str, Any] | None = None,
    explicit: bool = True,
    span_verified: bool = False,
    acceptance_criteria: list[str] | None = None,
    created_by: str = "agent",
    reason: str = "add",
) -> RequirementItem:
    """Create a DRAFT item with the next opaque code.

    Manually added items may have no source (human entry); in that case
    span_verified is False by default and the status stays DRAFT until the
    critic or a human validates it.
    """
    code = await _next_code(session, project_id)
    item = RequirementItem(
        project_id=project_id,
        code=code,
        statement=statement,
        type=type,
        priority=priority,
        status=ReqStatus.DRAFT,
        source=source,
        explicit=explicit,
        derived=False,
        confidence=1.0 if source is None else 0.0,
        span_verified=span_verified,
        acceptance_criteria=list(acceptance_criteria or []),
        created_by=created_by,
    )
    session.add(item)
    await session.flush()
    await _append_revision(session, item, reason=reason, changed_by=created_by)
    await session.commit()
    await session.refresh(item)
    return item


async def update_requirement(
    session: AsyncSession,
    req_id: int,
    *,
    statement: str | None = None,
    type: ReqType | None = None,
    priority: Priority | None = None,
    changed_by: str = "agent",
    reason: str = "update",
) -> RequirementItem:
    """Edit mutable fields. Only provided (non-None) fields are touched.

    status / source / pointers are NOT editable here: they move via the
    dedicated lifecycle tools (approve, reject, merge, split) so each
    transition is auditable on its own.
    """
    item = await _get_item(session, req_id)
    if statement is not None:
        item.statement = statement
    if type is not None:
        item.type = type
    if priority is not None:
        item.priority = priority
    await _append_revision(session, item, reason=reason, changed_by=changed_by)
    await session.commit()
    await session.refresh(item)
    return item


async def add_acceptance_criterion(
    session: AsyncSession,
    req_id: int,
    criterion: str,
    *,
    changed_by: str = "agent",
) -> RequirementItem:
    """Append a Gherkin acceptance criterion (verifiability).

    The critic flags items that have no testable criterion; this attaches one
    without touching the rest of the item.
    """
    item = await _get_item(session, req_id)
    criteria = list(item.acceptance_criteria or [])
    if criterion not in criteria:
        criteria.append(criterion)
        item.acceptance_criteria = criteria
        await _append_revision(
            session, item, reason="add_acceptance_criterion",
            changed_by=changed_by,
        )
        await session.commit()
        await session.refresh(item)
    return item


# --- lifecycle transitions --------------------------------------------------


async def approve_requirement(
    session: AsyncSession, req_id: int, *, changed_by: str = "human"
) -> RequirementItem:
    """Mark a requirement APPROVED by a human."""
    item = await _get_item(session, req_id)
    item.status = ReqStatus.APPROVED
    await _append_revision(
        session, item, reason="approve", changed_by=changed_by
    )
    await session.commit()
    await session.refresh(item)
    return item


async def reject_requirement(
    session: AsyncSession,
    req_id: int,
    *,
    reason: str,
    changed_by: str = "human",
) -> RequirementItem:
    """Soft-delete: status -> REJECTED. Row stays for audit."""
    item = await _get_item(session, req_id)
    item.status = ReqStatus.REJECTED
    await _append_revision(
        session, item, reason=reason[:_CHANGE_REASON_MAX], changed_by=changed_by
    )
    await session.commit()
    await session.refresh(item)
    return item


async def delete_requirement(
    session: AsyncSession,
    req_id: int,
    *,
    reason: str,
    changed_by: str = "human",
) -> RequirementItem:
    """Alias for reject_requirement (soft-delete). See module docstring."""
    return await reject_requirement(
        session, req_id, reason=reason, changed_by=changed_by
    )


async def delete_all_requirements(
    session: AsyncSession, project_id: int
) -> int:
    """Hard-delete every requirement row for a project (fresh opaque codes).

    Wipes requirement_relations, requirement_revisions and requirement_items for
    the project. Children first, then the items: the FKs are ondelete=CASCADE,
    but SQLite only honors CASCADE with PRAGMA foreign_keys=ON, so we delete in
    dependency order to be correct regardless of the pragma.

    Unlike the per-item ``delete_requirement`` (soft-delete for audit), this is
    a true wipe — only ``reset_project_capture`` should call it, when the user
    explicitly asks to start over. Does NOT commit; the caller owns the
    transaction so a multi-store reset stays atomic. Returns the item count.
    """
    count = await session.scalar(
        select(func.count()).select_from(RequirementItem).where(
            RequirementItem.project_id == project_id
        )
    )
    total = int(count or 0)
    if total == 0:
        return 0
    item_ids = select(RequirementItem.id).where(
        RequirementItem.project_id == project_id
    )
    # Relations reference items via from_id AND to_id; clear both endpoints.
    await session.execute(
        delete(RequirementRelation).where(
            RequirementRelation.from_id.in_(item_ids)
        )
    )
    await session.execute(
        delete(RequirementRelation).where(
            RequirementRelation.to_id.in_(item_ids)
        )
    )
    await session.execute(
        delete(RequirementRevision).where(
            RequirementRevision.req_id.in_(item_ids)
        )
    )
    await session.execute(
        delete(RequirementItem).where(
            RequirementItem.project_id == project_id
        )
    )
    await session.flush()
    return total


# --- structural mutations ---------------------------------------------------


async def merge_requirements(
    session: AsyncSession,
    req_ids: list[int],
    *,
    keep_id: int | None = None,
    keep_statement: str | None = None,
    reason: str = "merge_duplicates",
    changed_by: str = "agent",
) -> RequirementItem:
    """Fold N duplicate items into one.

    - The kept item (keep_id, or the first if None) absorbs the UNION of every
      source span, so traceability to all originals is preserved.
    - The rest flip to MERGED with merged_into = kept id (soft-delete).
    - A revision is appended to each participant (kept + merged).
    """
    if len(req_ids) < 2:
        raise ValueError("merge_requirements needs at least 2 ids")
    target_id = keep_id if keep_id is not None else req_ids[0]
    if target_id not in req_ids:
        raise ValueError(f"keep_id {target_id} not in req_ids")

    # Load all participants, preserving order.
    items: dict[int, RequirementItem] = {}
    for rid in req_ids:
        items[rid] = await _get_item(session, rid)
    kept = items[target_id]

    # Union sources (multi-traceability) across every participant.
    union_sources: list[dict[str, Any]] = []
    for rid in req_ids:
        for src in _source_list(items[rid]):
            if src not in union_sources:
                union_sources.append(src)
    kept.source = union_sources or None
    kept.span_verified = any(
        items[rid].span_verified for rid in req_ids
    )
    if keep_statement is not None:
        kept.statement = keep_statement
    if kept.status == ReqStatus.DRAFT:
        kept.status = ReqStatus.VALIDATED

    others = [items[rid] for rid in req_ids if rid != target_id]
    for other in others:
        other.status = ReqStatus.MERGED
        other.merged_into = kept.id

    await _append_revision(
        session, kept, reason=reason, changed_by=changed_by
    )
    for other in others:
        await _append_revision(
            session, other, reason=f"merged_into_{kept.code}",
            changed_by=changed_by,
        )
    await session.commit()
    await session.refresh(kept)
    return kept


async def split_requirement(
    session: AsyncSession,
    req_id: int,
    parts: list[str],
    *,
    reason: str = "split_non_atomic",
    changed_by: str = "agent",
) -> list[RequirementItem]:
    """Split a non-atomic item into N operational sub-requirements.

    - The original flips to SUPERSEDED (soft-delete with a pointer).
    - Each part becomes a new DRAFT row with parent_id = original, derived=True,
      inheriting the original's source (traceability to the original span).
    """
    if not parts:
        raise ValueError("split_requirement needs at least one part")
    original = await _get_item(session, req_id)
    project_id = original.project_id
    inherited_source = original.source

    created: list[RequirementItem] = []
    for part in parts:
        code = await _next_code(session, project_id)
        child = RequirementItem(
            project_id=project_id,
            code=code,
            statement=part,
            type=original.type,
            priority=original.priority,
            status=ReqStatus.DRAFT,
            source=inherited_source,
            explicit=False,
            derived=True,
            parent_id=original.id,
            confidence=original.confidence,
            span_verified=original.span_verified,
            acceptance_criteria=[],
            created_by=changed_by,
        )
        session.add(child)
        await session.flush()
        await _append_revision(
            session, child, reason="split_child", changed_by=changed_by
        )
        created.append(child)

    # Original is superseded by the first child (primary continuation).
    original.status = ReqStatus.SUPERSEDED
    original.superseded_by = created[0].id
    await _append_revision(
        session, original, reason=reason, changed_by=changed_by
    )

    await session.commit()
    for child in created:
        await session.refresh(child)
    return created


# --- relations / conflicts --------------------------------------------------


async def link_requirements(
    session: AsyncSession,
    from_id: int,
    to_id: int,
    kind: RelationKind,
    *,
    note: str | None = None,
    detected_by: str = "agent",
) -> RequirementRelation:
    """Create a directed edge (duplicate / contradicts / depends_on).

    The pair is flagged PROPOSED for a human or the agent to resolve later;
    nothing is deleted. The (from, to, kind) triple is unique.
    """
    if from_id == to_id:
        raise ValueError("cannot link a requirement to itself")
    # Validate both ends exist before creating the edge.
    await _get_item(session, from_id)
    await _get_item(session, to_id)

    existing = await session.scalar(
        select(RequirementRelation).where(
            RequirementRelation.from_id == from_id,
            RequirementRelation.to_id == to_id,
            RequirementRelation.kind == kind,
        )
    )
    if existing is not None:
        # Idempotent: update the note/status instead of violating the unique
        # constraint.
        if note is not None:
            existing.note = note
        existing.status = RelationStatus.PROPOSED
        await session.commit()
        await session.refresh(existing)
        return existing

    relation = RequirementRelation(
        from_id=from_id,
        to_id=to_id,
        kind=kind,
        status=RelationStatus.PROPOSED,
        note=note,
        detected_by=detected_by,
    )
    session.add(relation)
    await session.commit()
    await session.refresh(relation)
    return relation


async def resolve_conflict(
    session: AsyncSession,
    relation_id: int,
    *,
    winner_id: int,
    note: str | None = None,
    changed_by: str = "human",
) -> RequirementRelation:
    """Resolve a contradiction by recording the winning item.

    The relation moves to RESOLVED with the winner noted; the losing item is
    NOT auto-rejected. Rejecting the loser is an explicit human action
    (reject_requirement) so the decision and its consequence are both audited
    separately.
    """
    relation = await session.scalar(
        select(RequirementRelation).where(RequirementRelation.id == relation_id)
    )
    if relation is None:
        raise KeyError(f"relation {relation_id} not found")
    if winner_id not in (relation.from_id, relation.to_id):
        raise ValueError(
            f"winner_id {winner_id} is not part of relation {relation_id}"
        )

    relation.status = RelationStatus.RESOLVED
    loser_id = (
        relation.to_id if winner_id == relation.from_id else relation.from_id
    )
    relation.note = (
        f"winner={winner_id}; loser={loser_id}"
        + (f"; {note}" if note else "")
    )
    await session.commit()
    await session.refresh(relation)
    return relation


# --- reads ------------------------------------------------------------------


async def list_requirements(
    session: AsyncSession,
    project_id: int,
    *,
    status: ReqStatus | None = None,
    type: ReqType | None = None,
    priority: Priority | None = None,
    code: str | None = None,
    parent_id: int | None = None,
    derived: bool | None = None,
    merged_into: int | None = None,
    include_deleted: bool = False,
) -> list[RequirementItem]:
    """Query items by status / type / priority / structure.

    Extra filters:
      - code: REQ-code prefix match (e.g. "REQ-7K" matches "REQ-7K3F").
      - parent_id: only operational sub-requirements of this parent.
      - derived: True = only derived sub-items; False = only atomic originals.
      - merged_into: items soft-deleted into this keeper id (set
        include_deleted=True too, or the MERGED rows are filtered out first).

    Soft-deleted rows (REJECTED / MERGED / SUPERSEDED) are excluded unless
    include_deleted is set, so the default view matches the live SRS.
    """
    stmt = select(RequirementItem).where(
        RequirementItem.project_id == project_id
    )
    if status is not None:
        stmt = stmt.where(RequirementItem.status == status)
    elif not include_deleted:
        stmt = stmt.where(RequirementItem.status.not_in(_SOFT_DELETED))
    if type is not None:
        stmt = stmt.where(RequirementItem.type == type)
    if priority is not None:
        stmt = stmt.where(RequirementItem.priority == priority)
    if code is not None:
        stmt = stmt.where(RequirementItem.code.like(f"{code}%"))
    if parent_id is not None:
        stmt = stmt.where(RequirementItem.parent_id == parent_id)
    if derived is not None:
        stmt = stmt.where(RequirementItem.derived == derived)
    if merged_into is not None:
        stmt = stmt.where(RequirementItem.merged_into == merged_into)
    stmt = stmt.order_by(RequirementItem.id)
    rows = await session.scalars(stmt)
    return list(rows)


async def get_requirement(
    session: AsyncSession, req_id: int
) -> dict[str, Any]:
    """Full detail: item + relations + revision history.

    Relations are fetched both as-source (from_id) and as-target (to_id) so the
    caller sees every edge touching this item regardless of direction.
    """
    item = await _get_item(session, req_id)

    parent_code: str | None = None
    if item.parent_id is not None:
        parent_code = await session.scalar(
            select(RequirementItem.code).where(
                RequirementItem.id == item.parent_id
            )
        )

    rel_from = await session.scalars(
        select(RequirementRelation).where(
            RequirementRelation.from_id == req_id
        )
    )
    rel_to = await session.scalars(
        select(RequirementRelation).where(
            RequirementRelation.to_id == req_id
        )
    )
    revisions = await session.scalars(
        select(RequirementRevision)
        .where(RequirementRevision.req_id == req_id)
        .order_by(RequirementRevision.version.desc())
    )

    def _rel(r: RequirementRelation) -> dict[str, Any]:
        other = r.to_id if r.from_id == req_id else r.from_id
        return {
            "id": r.id,
            "other_id": other,
            "kind": r.kind.value,
            "status": r.status.value,
            "note": r.note,
            "detected_by": r.detected_by,
        }

    return {
        "id": item.id,
        "project_id": item.project_id,
        "code": item.code,
        "statement": item.statement,
        "type": item.type.value,
        "priority": item.priority.value,
        "status": item.status.value,
        "source": item.source,
        "explicit": item.explicit,
        "derived": item.derived,
        "parent_id": item.parent_id,
        "parent_code": parent_code,
        "confidence": item.confidence,
        "span_verified": item.span_verified,
        "acceptance_criteria": list(item.acceptance_criteria or []),
        "merged_into": item.merged_into,
        "superseded_by": item.superseded_by,
        "created_by": item.created_by,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "relations": [_rel(r) for r in [*rel_from, *rel_to]],
        "revisions": [
            {
                "version": rev.version,
                "snapshot": rev.snapshot,
                "changed_by": rev.changed_by,
                "change_reason": rev.change_reason,
                "changed_at": rev.changed_at.isoformat()
                if rev.changed_at
                else None,
            }
            for rev in revisions
        ],
    }
