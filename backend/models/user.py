"""User model.

email/password now. Swap to Google OAuth later by adding `google_sub` and
making it the primary identity (Google allows email changes).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Stable alphanumeric slug: container name + workspace path key.
    # Constraint mirrors registration validation: ^[a-z0-9_-]{2,48}$.
    profile: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
