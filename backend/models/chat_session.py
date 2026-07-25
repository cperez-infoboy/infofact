"""ChatSession model. `id` doubles as the DeepAgents `thread_id`."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="Untitled")
    phase: Mapped[str] = mapped_column(String(32), default="requirements")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
