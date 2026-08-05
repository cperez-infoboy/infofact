"""DocumentEmbedding: embedding de un chunk para retrieval semantico (Fase D).

Un vector por chunk de cada ``DocumentParse``. El scoping por proyecto se hace
en ``search`` con un JOIN::

    document_embeddings -> document_parses (sha256) -> project_documents (project_id)

Como ``DocumentParse`` es keyed global por ``sha256`` (contenido), dos proyectos
que comparten el mismo PDF comparten las mismas filas de embedding; el filtro
``project_id`` del JOIN evita el cross-leak sin tablas de enlace extra.

Solo se guardan campos derivados del CONTENIDO (chunk_text, section_path, page,
chunk_index). Ninguno depende del path, por lo que el cache global (keyed por
parse_id / sha256) es valido para todos los proyectos que reusan el mismo
contenido. El ``document_id`` absoluto se reconstruye por proyecto en la query
de ``search`` (rel_path + slug del proyecto).

``chunk_text`` se desnormaliza aqui (tambien vive en ``chunks_json``) para que
``search`` devuelva resultados autocontenidos en una sola query, sin re-parsear
el JSON del parse. Es el tradeoff pragmatico de la primera version: mas
almacenamiento a cambio de queries simples.

``char_span_start`` / ``end`` quedan ``nullable`` (Fase B los deferio): el offset
exacto de cada chunk en el markdown es dificil de calcular para tablas. Se
incluyen para que una futura iteracion los pueble sin migracion (no hay alembic;
``create_all`` no agrega columnas a tablas existentes).
"""
from __future__ import annotations

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class DocumentEmbedding(Base):
    __tablename__ = "document_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "parse_id", "chunk_index", name="uq_document_embeddings_parse_chunk"
        ),
        Index("ix_document_embeddings_parse", "parse_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    parse_id: Mapped[int] = mapped_column(
        ForeignKey("document_parses.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    # Desnormalizado: search devuelve el texto sin releer chunks_json.
    chunk_text: Mapped[str] = mapped_column(Text)
    # numpy float32 L2-normalizado -> .tobytes(). Cosine = dot product.
    embedding: Mapped[bytes] = mapped_column(LargeBinary)
    section_path: Mapped[str] = mapped_column(Text, default="")
    page: Mapped[int] = mapped_column(Integer, default=0)
    # Forward-compat (Fase B los deferio): offset del chunk en el markdown.
    char_span_start: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    char_span_end: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
