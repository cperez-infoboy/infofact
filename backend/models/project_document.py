"""ProjectDocument: documento fuente de un proyecto, con identidad persistente.

Hoy los documentos fuente son strings opacos dentro de ``RequirementItem.source``
(path absoluto en el container). Este modelo les da fila propia: trazabilidad
de upload, ``parse_status`` y, sobre todo, un ``sha256`` que será la clave del
cache de parseo (Fase B) y de dedupe dentro del proyecto.

``rel_path`` es relativo al root del workspace del proyecto; el path absoluto
en el container es ``/workspaces/{slug}/{rel_path}`` (constante
``WORKSPACE_CONTAINER_PATH``). Como ese target es fijo, ``rel_path`` es estable
y sirve de clave de join lógica con ``RequirementItem.source``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class ProjectDocument(Base):
    __tablename__ = "project_documents"
    # Dedupe por contenido dentro del proyecto: dos uploads del mismo archivo
    # (mismo sha256) bajo el mismo proyecto no crean duplicados. El index cubre
    # el lookup típico "documentos de un proyecto por path".
    __table_args__ = (
        UniqueConstraint(
            "project_id", "sha256", name="uq_project_documents_project_sha256"
        ),
        Index("ix_project_documents_project_relpath", "project_id", "rel_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # Relativo al root del workspace del proyecto (ej. "docs/spec.pdf").
    rel_path: Mapped[str] = mapped_column(String(512))
    filename: Mapped[str] = mapped_column(String(255))
    extension: Mapped[str] = mapped_column(String(16))
    mime: Mapped[str | None] = mapped_column(String(127), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    # Clave de dedupe + futura clave del cache de parseo (Fase B).
    sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # pending | parsing | ready | failed. Fase A solo usa "pending"; el parser
    # (Fase B/C) mueve el estado a parsing -> ready/failed.
    parse_status: Mapped[str] = mapped_column(String(16), default="pending")
    parser_used: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Preferencia de parser para este documento (auto | docling | glm-ocr).
    # "auto" deja al router decidir por extension + capa de texto; un valor
    # explicito (p.ej. glm-ocr para un PDF born-digital con paginas rotadas que
    # la heuristica no pesca) se propaga a parse_document_cached en la captura.
    parser_hint: Mapped[str] = mapped_column(String(16), default="auto")
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
