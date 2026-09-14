"""Work package models: entregables por sub-proyecto para equipos de desarrollo.

These tables are the DELIVERY LAYER: they consume a committed AnalysisDocument
(decomposition into sub-projects with contracts) and produce self-contained
work packages — one per sub-project — each with a minimal context (entities,
processes, ADRs, NFRs), its contracts (exposed and consumed, with specs), and
an atomic-task backlog (verbatim requirements + Gherkin acceptance criteria).
A master assembly document ties the packages together: build order by contract
dependencies, integration milestones, and a coherence report.

WorkPackageDocument is the versioned container (candidate -> in review ->
locked), mirroring AnalysisDocument. WorkPackage / WorkTask are child rows
linked via document_id. ``analysis_id`` snapshots the exact analysis version
consumed (ENT/SUB/PROJ codes are per-analysis; anchoring guarantees that the
package content stays interpretable).

No orm.relationship() — just FK columns (convention across all InfoFact models).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class PackageStatus(str, Enum):
    """Lifecycle of a work-package document (mirrors AnalysisStatus)."""

    CANDIDATE = "candidate"
    IN_REVIEW = "in_review"
    LOCKED = "locked"


class WorkPackageDocument(Base):
    """A versioned container of work packages for a project.

    ``analysis_id`` anchors the packages to one AnalysisDocument: entity /
    sub-project codes cited by packages only make sense within that analysis.
    ``coherence_report`` is the verbatim output of the coherence gate (gates
    + LLM critique findings) so the delivery decision is auditable.
    """

    __tablename__ = "work_package_documents"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "version", name="uq_workpkg_project_version"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[PackageStatus] = mapped_column(
        SAEnum(PackageStatus, native_enum=False),
        default=PackageStatus.CANDIDATE,
    )
    # AnalysisDocument.id consumed (snapshot for code resolution).
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_documents.id", ondelete="CASCADE"), index=True
    )
    # Analysis version at generation time (display only).
    analysis_version: Mapped[int] = mapped_column(default=0)
    # Rendered assembly master (build order, milestones, coherence summary).
    master_markdown: Mapped[str] = mapped_column(Text, default="")
    # Coherence gate output: {gates: [...], critique_findings: [...]}.
    coherence_report: Mapped[dict] = mapped_column(JSON, default=dict)
    requirement_count: Mapped[int] = mapped_column(default=0)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WorkPackage(Base):
    """One self-contained work package (one per sub-project).

    ``mission`` is the 1-paragraph brief. ``stack`` snapshots the sub-project
    stack. ``markdown`` is the FULL rendered package document (the .md handed
    to the developer) — snapshot at generation time so exports are stable.
    """

    __tablename__ = "work_packages"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "code", name="uq_workpkg_doc_code"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("work_package_documents.id", ondelete="CASCADE"), index=True
    )
    # Sequential handle (e.g. "WP-001"), continuing across versions.
    code: Mapped[str] = mapped_column(String(32))
    sub_project_code: Mapped[str] = mapped_column(String(32))
    sub_project_name: Mapped[str] = mapped_column(String(120))
    project_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mission: Mapped[str] = mapped_column(Text, default="")
    # {language, framework, database}.
    stack: Mapped[dict] = mapped_column(JSON, default=dict)
    # Full rendered markdown of this package.
    markdown: Mapped[str] = mapped_column(Text, default="")
    # Counters for the viewer (entities, reqs, tasks, contracts).
    counts: Mapped[dict] = mapped_column(JSON, default=dict)


class WorkTask(Base):
    """One atomic task in a package backlog (ticket-sized: 0.5-2 days).

    ``req_codes`` / ``entity_codes`` / ``contract_names`` / ``depends_on``
    keep the task linked to the traceability chain. ``acceptance`` holds the
    Gherkin criteria (verbatim from the requirements when available).
    """

    __tablename__ = "work_tasks"
    __table_args__ = (
        UniqueConstraint(
            "package_id", "code", name="uq_worktask_pkg_code"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    package_id: Mapped[int] = mapped_column(
        ForeignKey("work_packages.id", ondelete="CASCADE"), index=True
    )
    # Sequential handle within the PACKAGE (e.g. "TASK-001").
    code: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    # Self-contained instructions (a developer with ONLY the package must
    # be able to execute this without asking questions).
    description: Mapped[str] = mapped_column(Text, default="")
    # REQ-XXXX codes this task implements.
    req_codes: Mapped[list] = mapped_column(JSON, default=list)
    # ENT-XXXX codes touched by this task.
    entity_codes: Mapped[list] = mapped_column(JSON, default=list)
    # Contract names ("POST /orders") touched by this task.
    contract_names: Mapped[list] = mapped_column(JSON, default=list)
    # TASK codes of same-package tasks this one depends on.
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    # Gherkin acceptance criteria (list of scenario strings).
    acceptance: Mapped[list] = mapped_column(JSON, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
