"""ChatMessage model. role: user | assistant | tool."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Foto de sesión: la tabla es la línea de tiempo de despliegue que el
    # relay persiste mientras streamea (ver docs/planeaciones/2026-08-19).
    # kind 'text' | 'tool' discrimina la fila; NULL = fila legacy
    # (pre-migración, se sirve por la ruta de reconstrucción).
    kind: Mapped[str | None] = mapped_column(String(8), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_args: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Segmento assistant cerrado por un tool_start (razonamiento) vs cerrado
    # por completed (respuesta final). NULL en filas no-assistant o legacy.
    is_intermediate: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
