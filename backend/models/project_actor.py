"""ProjectActor: catálogo persistente de actores/usuarios por proyecto.

El modelo de actores que faltaba: la captura escribía «el usuario»/«el
sistema» porque nadie había determinado QUIÉN usa el sistema. El catálogo se
determina una vez (etapa ``identify_actors`` de la captura, o edición manual
del agente) y lo consumen la extracción, la crítica y la clasificación (bloque
``PROJECT_ACTORS`` inyectado en sus prompts) y los pre-checks programáticos de
actor (``detect_actor`` con ``role_terms`` del catálogo).

Espejo de ``ProjectRule``: ciclo de vida soft (retirado se queda para
auditoría), procedencia declarada, nunca hard-delete. El código corto R1..Rn
es estable dentro del proyecto y permite referenciar al actor en UI y chat.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class ActorSource(str, Enum):
    """Procedencia: etapa de captura, curación del agente o usuario."""

    USER = "user"
    AGENT = "agent"
    ACTORS_STAGE = "actors_stage"


class ActorStatus(str, Enum):
    """Soft lifecycle: retirado se conserva para auditoría y reactivación."""

    ACTIVE = "active"
    RETIRED = "retired"


class ProjectActor(Base):
    """Un actor del sistema, con su rol canónico y sus sinónimos."""

    __tablename__ = "project_actors"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_project_actor_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # Código corto y estable dentro del proyecto: R1..Rn.
    code: Mapped[str] = mapped_column(String(8))
    # Rol canónico en singular (p. ej. "Coordinador de terreno"). El store
    # deduplica por nombre y sinónimos, case-insensitive.
    name: Mapped[str] = mapped_column(String(160))
    # Otras formas en que los documentos nombran al mismo rol.
    synonyms: Mapped[list] = mapped_column(JSON, default=list)
    # Canal del actor: 'humano' | 'sistema_externo' | ... (libre, orientativo;
    # 'sistema' a secas NO es un actor: es el sistema en sí).
    channel: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Evidencia: de qué documento/sección salió el rol (auditoría).
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[ActorSource] = mapped_column(
        SAEnum(ActorSource, native_enum=False), default=ActorSource.USER
    )
    status: Mapped[ActorStatus] = mapped_column(
        SAEnum(ActorStatus, native_enum=False), default=ActorStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


def actor_code_sort_key(code: str) -> int:
    """Clave numérica del código R<nnn> para ordenar (R12 > R2)."""
    try:
        return int(code.removeprefix("R"))
    except (ValueError, TypeError):
        return 0
