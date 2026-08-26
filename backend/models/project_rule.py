"""ProjectRule: persistent, per-project agent harness rules.

A rule is a short, actionable consideration the agent must honor from the
moment it is registered — in requirements capture, analysis or SRS drafting.
Rules are the project's "continual harness": user-driven (chat: "from now on,
pay attention to X"), agent-curated, or derived from the CONVENTIONS stage
(``source=conventions``). They are soft state: retiring keeps the row for
audit; nothing is ever hard-deleted.

``scope`` is the injection filter — a rule only enters the user messages of
the pipelines it applies to (``all`` enters every phase). This is progressive
disclosure: each LLM call sees only its slice, never the whole harness.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class RuleScope(str, Enum):
    """Which pipelines a rule is injected into.

    ``all`` is always included, regardless of the requested scope, so a
    project-wide consideration (e.g. regulatory constraints) reaches every
    phase without being duplicated per scope.
    """

    CAPTURE = "capture"
    ANALYSIS = "analysis"
    SRS = "srs"
    ALL = "all"


class RuleSource(str, Enum):
    """Provenance: who added the rule and why it can be trusted."""

    USER = "user"
    AGENT = "agent"
    CONVENTIONS = "conventions"


class RuleStatus(str, Enum):
    """Soft lifecycle: retired rules stay queryable for audit and rollback."""

    ACTIVE = "active"
    RETIRED = "retired"


class ProjectRule(Base):
    """One persistent consideration, scoped to a project and a pipeline set."""

    __tablename__ = "project_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[RuleScope] = mapped_column(SAEnum(RuleScope, native_enum=False))
    # The rule itself: short, imperative, actionable ("mark audit-related
    # items as regulatory"). This is the ONLY field injected into prompts.
    content: Mapped[str] = mapped_column(Text)
    # Why the rule exists (evidence): user request, observed convention, etc.
    # Audit-only — never injected, keeps the prompt block lean.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[RuleSource] = mapped_column(
        SAEnum(RuleSource, native_enum=False), default=RuleSource.USER
    )
    status: Mapped[RuleStatus] = mapped_column(
        SAEnum(RuleStatus, native_enum=False), default=RuleStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
