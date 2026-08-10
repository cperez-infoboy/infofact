"""Analysis models: domain entities, relationships, ADRs, sub-projects, contracts.

These tables are the ANALYSIS LAYER of the pipeline: they consume the locked SRS
+ live requirements and produce architectural artifacts (MER entity model,
architecture decisions, sub-project decomposition with inter-project contracts).

RequirementItem remains the source of truth for requirement statements.
AnalysisDocument is the versioned container (candidate -> in review -> locked),
mirroring SrsDocument. DomainEntity / DomainRelationship / ArchitectureDecision
/ SubProject / SubProjectContract are child rows linked to an AnalysisDocument.

No orm.relationship() — just FK columns (convention across all InfoFact models).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


# --- Enums ------------------------------------------------------------------


class AnalysisStatus(str, Enum):
    """Lifecycle of an analysis document (mirrors SrsStatus)."""

    CANDIDATE = "candidate"
    IN_REVIEW = "in_review"
    LOCKED = "locked"


class MerCardinality(str, Enum):
    """Cardinality of a domain relationship in the MER entity model."""

    ONE_TO_ONE = "1:1"
    ONE_TO_MANY = "1:N"
    MANY_TO_MANY = "N:M"


class AdrStatus(str, Enum):
    """Status of an Architecture Decision Record."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"


class ContractType(str, Enum):
    """Type of inter-project contract specification."""

    OPENAPI = "openapi"
    ASYNCAPI = "asyncapi"
    EVENT_SCHEMA = "event_schema"


# --- AnalysisDocument (versioned container) ---------------------------------


class AnalysisDocument(Base):
    """A versioned, editable analysis document for a project.

    Mirrors SrsDocument: candidate -> in_review -> locked. Stores the Mermaid
    diagrams (MER erDiagram, process state/sequence, component graph), the NFR
    analysis, and the traceability matrices as snapshots. Child rows
    (DomainEntity, DomainRelationship, ArchitectureDecision, SubProject,
    SubProjectContract) are linked via analysis_id.
    """

    __tablename__ = "analysis_documents"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "version", name="uq_analysis_project_version"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[AnalysisStatus] = mapped_column(
        SAEnum(AnalysisStatus, native_enum=False),
        default=AnalysisStatus.CANDIDATE,
    )
    # Version of the SRS LOCKED consumed for this analysis (nullable: the
    # analysis can run on live requirements without a locked SRS).
    srs_version: Mapped[int | None] = mapped_column(nullable=True)
    # Mermaid erDiagram string (rendered in code from the structured LLM output).
    mer_diagram: Mapped[str] = mapped_column(Text, default="")
    # Descripcion narrativa del MER (generada por Pass 6 del pipeline).
    mer_diagram_description: Mapped[str] = mapped_column(Text, default="")
    # [{name, type, mermaid, entity_code, traced_req_codes}] — Sprint 3.
    process_diagrams: Mapped[list] = mapped_column(JSON, default=list)
    # {decisions, stack, data_consistency, patterns} — Sprint 2.
    nfr_analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    # Mermaid graph TD of sub-projects — Sprint 3.
    component_diagram: Mapped[str] = mapped_column(Text, default="")
    # Descripcion narrativa del diagrama de componentes.
    component_diagram_description: Mapped[str] = mapped_column(Text, default="")
    # Diagrama de arquitectura del sistema (graph TD por capas).
    system_architecture_diagram: Mapped[str] = mapped_column(Text, default="")
    # Descripcion narrativa del diagrama de arquitectura.
    system_architecture_description: Mapped[str] = mapped_column(Text, default="")
    # Diagrama de infraestructura sugerida (graph TD por redes/containers).
    infrastructure_diagram: Mapped[str] = mapped_column(Text, default="")
    # Descripcion narrativa del diagrama de infraestructura.
    infrastructure_description: Mapped[str] = mapped_column(Text, default="")
    # Traceability matrices (entity/adr/sub-project -> req codes).
    traceability: Mapped[dict] = mapped_column(JSON, default=dict)
    requirement_codes: Mapped[list] = mapped_column(JSON, default=list)
    requirement_count: Mapped[int] = mapped_column(default=0)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --- DomainEntity (MER entity) ----------------------------------------------


class DomainEntity(Base):
    """A domain entity identified in the MER (Modelo Entidad-Relacion).

    ``attributes`` is a JSON list of {name, type, required, is_key, description}.
    ``aggregate_root`` flags DDD aggregate roots. ``bounded_context`` groups
    entities into DDD contexts. ``traced_req_codes`` lists the REQ-XXXX codes
    that justify this entity's existence (traceability anchor).
    """

    __tablename__ = "domain_entities"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_entity_project_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_documents.id", ondelete="CASCADE"), index=True
    )
    # Opaque handle, unique within a project (e.g. "ENT-7K3F").
    code: Mapped[str] = mapped_column(String(32))
    # PascalCase name (e.g. "SalesOrder", "UserAccount").
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    # [{name, type, required, is_key, description}].
    attributes: Mapped[list] = mapped_column(JSON, default=list)
    aggregate_root: Mapped[bool] = mapped_column(Boolean, default=False)
    bounded_context: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    traced_req_codes: Mapped[list] = mapped_column(JSON, default=list)


# --- DomainRelationship (MER edge) ------------------------------------------


class DomainRelationship(Base):
    """A directed edge between two domain entities in the MER.

    Cardinality uses the closed MerCardinality enum (1:1, 1:N, N:M). ``label``
    is the verb describing the relationship (e.g. "has", "belongs_to").
    """

    __tablename__ = "domain_relationships"
    __table_args__ = (
        UniqueConstraint(
            "analysis_id",
            "from_entity_code",
            "to_entity_code",
            "label",
            name="uq_relationship_edge",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_documents.id", ondelete="CASCADE"), index=True
    )
    from_entity_code: Mapped[str] = mapped_column(String(32))
    to_entity_code: Mapped[str] = mapped_column(String(32))
    cardinality: Mapped[MerCardinality] = mapped_column(
        SAEnum(MerCardinality, native_enum=False)
    )
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    traced_req_codes: Mapped[list] = mapped_column(JSON, default=list)


# --- ArchitectureDecision (ADR) ---------------------------------------------


class ArchitectureDecision(Base):
    """An Architecture Decision Record (ADR).

    Follows the Nygard ADR format: context, decision, alternatives, rationale.
    ``code`` is sequential (ADR-001, ADR-002, ...) following industry
    convention. ``nfr_codes`` lists the NFR codes that motivate this ADR.
    """

    __tablename__ = "architecture_decisions"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_adr_project_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_documents.id", ondelete="CASCADE"), index=True
    )
    # Sequential handle (e.g. "ADR-001").
    code: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[AdrStatus] = mapped_column(
        SAEnum(AdrStatus, native_enum=False), default=AdrStatus.PROPOSED
    )
    context: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(Text)
    # [{name, pros, cons}] — alternatives considered.
    alternatives: Mapped[list] = mapped_column(JSON, default=list)
    rationale: Mapped[str] = mapped_column(Text, default="")
    nfr_codes: Mapped[list] = mapped_column(JSON, default=list)


# --- SubProject (proposed decomposition) ------------------------------------


class SubProject(Base):
    """A proposed sub-project from the architectural decomposition.

    ``stack`` is {language, framework, database}. ``bounded_contexts`` lists
    the DDD contexts this sub-project owns. ``entity_codes`` lists the
    DomainEntity codes assigned to this sub-project.
    """

    __tablename__ = "sub_projects"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_subproject_project_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_documents.id", ondelete="CASCADE"), index=True
    )
    # Sequential handle (e.g. "SUB-001").
    code: Mapped[str] = mapped_column(String(32))
    # kebab-case name (e.g. "order-service", "billing-api").
    name: Mapped[str] = mapped_column(String(120))
    responsibility: Mapped[str] = mapped_column(Text, default="")
    # {language, framework, database}.
    stack: Mapped[dict] = mapped_column(JSON, default=dict)
    bounded_contexts: Mapped[list] = mapped_column(JSON, default=list)
    nfr_codes: Mapped[list] = mapped_column(JSON, default=list)
    entity_codes: Mapped[list] = mapped_column(JSON, default=list)
    # PROJ-NNN code of the parent project area (nullable for legacy data).
    project_code: Mapped[str | None] = mapped_column(String(32), nullable=True)


# --- SubProjectContract (inter-project contract) ----------------------------


class SubProjectContract(Base):
    """A contract between two sub-projects (API, event schema, etc.).

    ``contract_type`` determines the spec format: OpenAPI YAML for REST APIs,
    AsyncAPI for event-driven channels, or JSON Schema for event payloads.
    ``spec`` holds the raw specification text.
    """

    __tablename__ = "sub_project_contracts"
    __table_args__ = (
        UniqueConstraint(
            "analysis_id",
            "from_subproject_code",
            "to_subproject_code",
            "name",
            name="uq_contract_edge",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_documents.id", ondelete="CASCADE"), index=True
    )
    from_subproject_code: Mapped[str] = mapped_column(String(32))
    to_subproject_code: Mapped[str] = mapped_column(String(32))
    contract_type: Mapped[ContractType] = mapped_column(
        SAEnum(ContractType, native_enum=False)
    )
    # Endpoint or event name (e.g. "POST /orders", "order.created").
    name: Mapped[str] = mapped_column(String(200))
    spec: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


# --- AnalysisProject (DDD subdomain grouping above sub-projects) -------------


class AnalysisProject(Base):
    """A project area: groups bounded contexts into a DDD subdomain.

    Represents the intermediate decomposition level between the system and
    sub-projects. Each project area groups one or more bounded contexts
    discovered by the MER pipeline and is classified as core, supporting,
    or generic (DDD subdomain taxonomy).
    """

    __tablename__ = "analysis_projects"
    __table_args__ = (
        UniqueConstraint(
            "analysis_id", "code", name="uq_proj_analysis_code"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_documents.id", ondelete="CASCADE"), index=True
    )
    # Sequential handle (e.g. "PROJ-001").
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    # core | supporting | generic (DDD subdomain type).
    domain_type: Mapped[str] = mapped_column(String(16), default="core")
    bounded_contexts: Mapped[list] = mapped_column(JSON, default=list)
    entity_codes: Mapped[list] = mapped_column(JSON, default=list)
    traced_req_codes: Mapped[list] = mapped_column(JSON, default=list)
