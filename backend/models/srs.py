"""SRS models: calidad de requerimientos, goals (GORE) y el SRS versionado.

Estas tablas son las CAPAS DE EXTRACCIÓN del pipeline de SRS, en paralelo al
almacén de RequirementItem: la calidad de los requerimientos se analiza y se
persiste (no se recalcula en cada lectura), los goals (GORE/KAOS) se infieren y
se enlazan a requerimientos, y el SRS mismo se captura como un documento
versionado y editable (candidato -> en revisión -> cerrado) en vez de una
proyección Markdown transitoria.

RequirementItem sigue siendo la fuente de verdad de los enunciados.
RequirementFinding / Goal / GoalLink / SrsDocument son capas ortogonales que
describen, explican y empaquetan esos requerimientos.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


# --- Enums de dominio -------------------------------------------------------


class FindingScope(str, Enum):
    """¿El hallazgo aplica a un requerimiento individual o al conjunto?"""

    ITEM = "item"
    SET = "set"


class FindingDimension(str, Enum):
    """Dimensión del análisis de calidad.

    Mapea a las familias del motor: reglas INCOSE, requirement smells,
    violaciones de plantilla EARS, ambigüedad semántica, gaps de cobertura y
    requerimientos faltantes detectados.
    """

    INCOSE_RULE = "incose_rule"
    REQUIREMENT_SMELL = "requirement_smell"
    EARS_VIOLATION = "ears_violation"
    AMBIGUITY = "ambiguity"
    COVERAGE_GAP = "coverage_gap"
    MISSING_REQ = "missing_req"


class FindingSeverity(str, Enum):
    """Severidad del hallazgo. BLOCKER impide el cierre del SRS."""

    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"


class FindingStatus(str, Enum):
    """Estado de curación del hallazgo."""

    OPEN = "open"
    FIXED = "fixed"
    WAIVED = "waived"


class GoalKind(str, Enum):
    """Tipo de goal (GORE / KAOS).

    FUNCTIONAL_GOAL  objetivo funcional (el sistema debe lograr X).
    SOFTGOAL         objetivo de calidad / NFR (rendimiento, seguridad, ...).
    OBSTACLE         anti-goal / riesgo que requerimientos deben mitigar.
    """

    FUNCTIONAL_GOAL = "functional_goal"
    SOFTGOAL = "softgoal"
    OBSTACLE = "obstacle"


class GoalStatus(str, Enum):
    """Estado de curación del goal."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class LinkRelation(str, Enum):
    """Cómo un requerimiento se relaciona con un goal.

    REALIZES     el requerimiento satisface plenamente el goal.
    CONTRIBUTES  el requerimiento contribuye parcialmente al goal.
    CONFLICTS    el requerimiento entra en conflicto con el goal.
    """

    REALIZES = "realizes"
    CONTRIBUTES = "contributes"
    CONFLICTS = "conflicts"


class LinkStatus(str, Enum):
    """Estado de curación del vínculo goal <-> requerimiento."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"


class SrsStatus(str, Enum):
    """Ciclo de vida del documento SRS.

    CANDIDATE  generado por el agente, pendiente de revisión humana.
    IN_REVIEW  el usuario edita la prosa y marca pendientes.
    LOCKED     snapshot inmutable consumido por la fase de diseño (fase 2).
    """

    CANDIDATE = "candidate"
    IN_REVIEW = "in_review"
    LOCKED = "locked"


# --- Hallazgos de calidad ---------------------------------------------------


class RequirementFinding(Base):
    """Un hallazgo de calidad sobre un requerimiento (o sobre el conjunto).

    ``scope = ITEM`` -> ``req_id`` apunta a RequirementItem; ``scope = SET`` ->
    ``req_id`` es NULL (hallazgo de cobertura o de conjunto). ``detected_by``
    distingue los pre-checks deterministas (``programmatic``) del juicio LLM
    (``agent``), igual que RequirementRelation.detected_by. ``rule_id`` es
    estable (ej. ``incose.r16_negation``, ``smell.vague_term``) y, junto con
    ``req_id``, define la unicidad del hallazgo.
    """

    __tablename__ = "requirement_findings"
    __table_args__ = (
        UniqueConstraint("req_id", "rule_id", name="uq_finding_req_rule"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # NULL para hallazgos de conjunto (scope = SET).
    req_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_items.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    scope: Mapped[FindingScope] = mapped_column(
        SAEnum(FindingScope, native_enum=False)
    )
    dimension: Mapped[FindingDimension] = mapped_column(
        SAEnum(FindingDimension, native_enum=False)
    )
    rule_id: Mapped[str] = mapped_column(String(64))
    severity: Mapped[FindingSeverity] = mapped_column(
        SAEnum(FindingSeverity, native_enum=False)
    )
    message: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[FindingStatus] = mapped_column(
        SAEnum(FindingStatus, native_enum=False), default=FindingStatus.OPEN
    )
    # Plantilla EARS detectada o esperada (ej. "event_driven").
    ears_pattern: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detected_by: Mapped[str] = mapped_column(String(16), default="agent")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# --- GORE goals -------------------------------------------------------------


class Goal(Base):
    """Un goal del proyecto (jerarquía GORE / KAOS).

    ``parent_id`` arma la jerarquía de refinamiento goal -> sub-goal.
    ``kind = OBSTACLE`` marca anti-goals / riesgos. ``source`` ancla el goal al
    documento de origen con la misma forma que RequirementItem.source.
    ``code`` es un handle opaque único por proyecto (ej. ``GOAL-7K3F``).
    """

    __tablename__ = "goals"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_goal_project_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # Handle opaque, único dentro del proyecto (ej. "GOAL-7K3F").
    code: Mapped[str] = mapped_column(String(32))
    statement: Mapped[str] = mapped_column(Text)
    kind: Mapped[GoalKind] = mapped_column(SAEnum(GoalKind, native_enum=False))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("goals.id", ondelete="SET NULL"), nullable=True
    )
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[GoalStatus] = mapped_column(
        SAEnum(GoalStatus, native_enum=False), default=GoalStatus.PROPOSED
    )
    created_by: Mapped[str] = mapped_column(String(16), default="agent")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class GoalLink(Base):
    """Vínculo goal <-> requerimiento (trazabilidad).

    Cada link declara cómo un RequirementItem se relaciona con un Goal
    (REALIZES / CONTRIBUTES / CONFLICTS). ``status`` permite curar los links
    inferidos por el agente. La terna (goal, req, relación) es única.
    """

    __tablename__ = "goal_links"
    __table_args__ = (
        UniqueConstraint(
            "goal_id", "req_id", "relation", name="uq_goal_link_edge"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), index=True
    )
    req_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_items.id", ondelete="CASCADE"), index=True
    )
    relation: Mapped[LinkRelation] = mapped_column(
        SAEnum(LinkRelation, native_enum=False)
    )
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[LinkStatus] = mapped_column(
        SAEnum(LinkStatus, native_enum=False), default=LinkStatus.PROPOSED
    )
    detected_by: Mapped[str] = mapped_column(String(16), default="agent")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# --- SRS versionado ---------------------------------------------------------


class SrsDocument(Base):
    """Una versión persistida y editable del SRS de un proyecto.

    ``structure`` es el árbol de secciones (29148/Volere); cada sección marca
    ``projected`` (proyección de RequirementItem, solo lectura en el SRS) o
    ``authored`` (prosa editable). ``narrative`` guarda la prosa editable por
    ``section_id``. ``markdown`` es el snapshot renderizado combinando narrativa
    + secciones proyectadas; se congela al pasar a LOCKED. ``traceability`` es
    la matriz goal <-> req <-> fuente (snapshot al generar).
    """

    __tablename__ = "srs_documents"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "version", name="uq_srs_project_version"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[SrsStatus] = mapped_column(
        SAEnum(SrsStatus, native_enum=False), default=SrsStatus.CANDIDATE
    )
    # Árbol de secciones: [{id, title, kind: projected|authored, ...}].
    structure: Mapped[list] = mapped_column(JSON, default=list)
    # Prosa editable por section_id: {section_id: markdown_text}.
    narrative: Mapped[dict] = mapped_column(JSON, default=dict)
    markdown: Mapped[str] = mapped_column(Text)
    quality_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    coverage: Mapped[dict] = mapped_column(JSON, default=dict)
    traceability: Mapped[dict] = mapped_column(JSON, default=dict)
    # Cola de pendientes: {section_id|req_code: motivo}.
    review_flags: Mapped[dict] = mapped_column(JSON, default=dict)
    requirement_codes: Mapped[list] = mapped_column(JSON, default=list)
    requirement_count: Mapped[int] = mapped_column(default=0)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    generated_by: Mapped[str] = mapped_column(String(16), default="agent")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
