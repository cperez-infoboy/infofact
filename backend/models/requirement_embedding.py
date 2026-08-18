"""RequirementEmbedding: cache de embeddings de enunciados (agrupamiento).

La etapa de embeddings de ``build_grouping_plan`` re-encodea los mismos
enunciados en cada corrida. La clave es content-addressed —
``(model_name, sha256(statement))`` — asi el cache no necesita invalidacion
manual: un enunciado editado cambia de hash y se re-codifica, y enunciados
repetidos (en distintos proyectos) comparten fila.

Mismo tradeoff que ``DocumentEmbedding``: numpy float32 -> ``.tobytes()``.
"""
from __future__ import annotations

from sqlalchemy import Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class RequirementEmbedding(Base):
    __tablename__ = "requirement_embeddings"

    model_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    stmt_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    # numpy float32 L2-normalizado -> .tobytes(). Cosine = dot product.
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
