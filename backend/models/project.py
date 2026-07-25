"""Project model: a unit of work owned by a user, pinned to one agent phase."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class Project(Base):
    __tablename__ = "projects"
    # Slug uniqueness is scoped per user: two different users may share a slug,
    # but a single user cannot have two projects with the same slug. Enforced
    # via the unique index below; the host-side workspace dir is keyed by
    # (profile, slug), so this constraint protects the filesystem namespace too.
    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_projects_user_slug"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    # Auto-derived slug from `name` (regex ^[a-z0-9_-]{2,48}$). Collision within
    # the same user is resolved with a `-2`, `-3`, ... suffix at creation time.
    # The slug is the key for the project's workspace subdirectory on disk:
    #   {workspaces_host_root}/{profile}/{slug}/
    slug: Mapped[str] = mapped_column(String(48), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Current agent phase: requirements | analysis | implementation | testing | deploy
    phase: Mapped[str] = mapped_column(String(32), default="requirements")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
