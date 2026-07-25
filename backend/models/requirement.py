"""Requirement models: the structured store for extracted requirements.

A requirement is NOT free markdown. It is a typed, traceable, versioned record
linked to a Project, with a source span that pins it to the client document.

The store is a graph: items reference each other via RequirementRelation
(duplicate / contradicts / depends_on), and every mutation appends a
RequirementRevision for auditability. Deletion is soft (a status change), never
hard, so the SRS stays auditable and the history of decisions is preserved.

Note on identity: InfoFact uses integer primary keys across all models. The
plan's human-readable "REQ-<project>-0042" lives in the `code` column (unique
per project); cross-references between requirements use the integer id.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


# --- Domain enums -----------------------------------------------------------


class ReqType(str, Enum):
    """Requirement taxonomy (ISO/IEC 25010 NFRs + IEEE 29148).

    Closed set so classification is reproducible and comparable across
    documents, instead of free-form labels that fragment the store.
    """

    FUNCTIONAL = "functional"
    PERFORMANCE = "performance"
    SECURITY = "security"
    USABILITY = "usability"
    RELIABILITY = "reliability"
    MAINTAINABILITY = "maintainability"
    COMPLIANCE = "compliance"
    CONSTRAINT = "constraint"
    PROCESS = "process"
    DATA = "data"


class Priority(str, Enum):
    """MoSCoW priority. Any source convention is normalized to this scale."""

    MUST = "must"
    SHOULD = "should"
    COULD = "could"
    WONT = "wont"


class ReqStatus(str, Enum):
    """Lifecycle of a requirement item.

    UNVERIFIED  source_span failed programmatic verification -> human review.
    DRAFT       extracted, not yet critiqued.
    VALIDATED   passed the critic loop.
    APPROVED    human accepted.
    REJECTED    human discarded.
    MERGED      folded into another item (soft-delete, merged_into points on).
    SUPERSEDED  replaced by a split/rewrite (superseded_by points on).

    MERGED / SUPERSEDED / REJECTED are soft-deletes: the row is kept for audit.
    """

    UNVERIFIED = "unverified"
    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"
    REJECTED = "rejected"
    MERGED = "merged"
    SUPERSEDED = "superseded"


class RelationKind(str, Enum):
    DUPLICATE = "duplicate"
    CONTRADICTS = "contradicts"
    DEPENDS_ON = "depends_on"


class RelationStatus(str, Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    RESOLVED = "resolved"


class ChangedBy(str, Enum):
    """Who performed a mutation. App-layer validation; columns store the value."""

    AGENT = "agent"
    HUMAN = "human"


# --- Tables -----------------------------------------------------------------


class RequirementItem(Base):
    """A single, atomic, traceable requirement.

    `source` is the SourceRef payload (document_id, section, page, char_span,
    quote) stored as JSON. Manually added requirements may have no source, so
    it is nullable. `code` is assigned by the service (unique within a project).
    """

    __tablename__ = "requirement_items"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_requirement_project_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # Stable human-readable handle, unique within a project (e.g. "REQ-001").
    code: Mapped[str] = mapped_column(String(32))
    statement: Mapped[str] = mapped_column(Text)
    type: Mapped[ReqType] = mapped_column(SAEnum(ReqType, native_enum=False))
    priority: Mapped[Priority] = mapped_column(SAEnum(Priority, native_enum=False))
    status: Mapped[ReqStatus] = mapped_column(
        SAEnum(ReqStatus, native_enum=False), default=ReqStatus.DRAFT
    )
    source: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    explicit: Mapped[bool] = mapped_column(Boolean, default=True)
    derived: Mapped[bool] = mapped_column(Boolean, default=False)
    # Decomposition parent: operational sub-requirements keep parent_id.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_items.id", ondelete="SET NULL"), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    span_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # Gherkin acceptance criteria (verifiability, produced by the critic pass).
    acceptance_criteria: Mapped[list] = mapped_column(JSON, default=list)
    # Soft-delete pointers, set when status is MERGED / SUPERSEDED.
    merged_into: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_items.id", ondelete="SET NULL"), nullable=True
    )
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_items.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(16), default="agent")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class RequirementRelation(Base):
    """A directed edge between two requirements (duplicate / contradicts /
    depends_on). Creating a relation never deletes anything; it flags a pair
    for a human or the agent to resolve."""

    __tablename__ = "requirement_relations"
    __table_args__ = (
        UniqueConstraint(
            "from_id", "to_id", "kind", name="uq_requirement_relation_edge"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    from_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_items.id", ondelete="CASCADE"), index=True
    )
    to_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_items.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[RelationKind] = mapped_column(
        SAEnum(RelationKind, native_enum=False)
    )
    status: Mapped[RelationStatus] = mapped_column(
        SAEnum(RelationStatus, native_enum=False), default=RelationStatus.PROPOSED
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_by: Mapped[str] = mapped_column(String(16), default="agent")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RequirementRevision(Base):
    """Immutable audit trail. Every mutating tool (add / update / merge /
    split / approve / reject) appends one row with a snapshot of the item, so
    the SRS is defensible and the decision history is recoverable."""

    __tablename__ = "requirement_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    req_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_items.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(default=1)
    # Full item snapshot at this revision (relevant fields serialized as JSON).
    snapshot: Mapped[dict] = mapped_column(JSON)
    changed_by: Mapped[str] = mapped_column(String(16), default="agent")
    change_reason: Mapped[str] = mapped_column(String(64))
    changed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
