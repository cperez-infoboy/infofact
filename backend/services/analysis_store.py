"""Analysis store: typed CRUD over AnalysisDocument and child rows.

Capa de persistencia para la Fase 2 (analisis y diseno). Los pipelines (MER,
NFR, procesos, ADRs, sub-proyectos) producen datos; el assembler los combina en
un payload y ``create_analysis`` persiste el AnalysisDocument + filas hijas
(DomainEntity, DomainRelationship, etc.).

Convenciones (espejo de ``srs_store``):
- Todas las funciones son async y toman una AsyncSession + project_id.
- Comitean ellas mismas (un AsyncSessionLocal fresco por llamada es el patron
  esperado en las herramientas del agente y en los endpoints del router).
- Los codigos ENT-XXXX son Crockford base32 (opaque, como REQ/GOAL).
- Los codigos ADR-NNN y SUB-NNN son secuenciales (convencion de industria).
"""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.analysis import (
    AdrStatus,
    AnalysisDocument,
    AnalysisProject,
    AnalysisStatus,
    ArchitectureDecision,
    DomainEntity,
    DomainRelationship,
    MerCardinality,
    SubProject,
    SubProjectContract,
)

# Mismo alfabeto opaque que REQ/GOAL (Crockford base32, sin I/L/O/U).
_CROCKFORD_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ENT_OPAQUE_LEN = 4
_MAX_ATTEMPTS = 10


# --- code generators --------------------------------------------------------


def gen_adr_code(seq: int) -> str:
    """Sequential ADR code: ADR-001, ADR-002, ..."""
    return f"ADR-{seq:03d}"


def gen_subproject_code(seq: int) -> str:
    """Sequential sub-project code: SUB-001, SUB-002, ..."""
    return f"SUB-{seq:03d}"


def gen_project_code(seq: int) -> str:
    """Sequential project area code: PROJ-001, PROJ-002, ..."""
    return f"PROJ-{seq:03d}"


async def gen_entity_code(
    session: AsyncSession, project_id: int, *, reserved: set[str] | None = None
) -> str:
    """Asigna un codigo opaque ``ENT-XXXX`` unico por proyecto.

    Replica de ``gen_goal_code`` para entidades de dominio: Crockford base32,
    mismo largo (4 chars), mismo mecanismo de reserva. Los gaps no se leen como
    entidades perdidas (mismo criterio que REQ/GOAL).
    """
    batch = reserved if reserved is not None else set()
    rows = await session.execute(
        select(DomainEntity.code).where(DomainEntity.project_id == project_id)
    )
    existing = {c for (c,) in rows.all() if c}
    for _ in range(_MAX_ATTEMPTS):
        seg = "".join(
            secrets.choice(_CROCKFORD_B32) for _ in range(_ENT_OPAQUE_LEN)
        )
        code = f"ENT-{seg}"
        if code not in existing and code not in batch:
            batch.add(code)
            return code
    raise RuntimeError("could not allocate a unique ENT code (10 attempts)")


# --- serializers ------------------------------------------------------------


def entity_to_dict(e: DomainEntity) -> dict[str, Any]:
    return {
        "id": e.id,
        "project_id": e.project_id,
        "analysis_id": e.analysis_id,
        "code": e.code,
        "name": e.name,
        "description": e.description,
        "attributes": e.attributes,
        "aggregate_root": e.aggregate_root,
        "bounded_context": e.bounded_context,
        "traced_req_codes": e.traced_req_codes,
    }


def relationship_to_dict(r: DomainRelationship) -> dict[str, Any]:
    return {
        "id": r.id,
        "project_id": r.project_id,
        "analysis_id": r.analysis_id,
        "from_entity_code": r.from_entity_code,
        "to_entity_code": r.to_entity_code,
        "cardinality": r.cardinality.value,
        "label": r.label,
        "description": r.description,
        "traced_req_codes": r.traced_req_codes,
    }


def adr_to_dict(a: ArchitectureDecision) -> dict[str, Any]:
    return {
        "id": a.id,
        "project_id": a.project_id,
        "analysis_id": a.analysis_id,
        "code": a.code,
        "title": a.title,
        "status": a.status.value,
        "context": a.context,
        "decision": a.decision,
        "alternatives": a.alternatives,
        "rationale": a.rationale,
        "nfr_codes": a.nfr_codes,
    }


def subproject_to_dict(s: SubProject) -> dict[str, Any]:
    return {
        "id": s.id,
        "project_id": s.project_id,
        "analysis_id": s.analysis_id,
        "code": s.code,
        "name": s.name,
        "responsibility": s.responsibility,
        "stack": s.stack,
        "bounded_contexts": s.bounded_contexts,
        "nfr_codes": s.nfr_codes,
        "entity_codes": s.entity_codes,
        "project_code": s.project_code,
    }


def project_to_dict(p: AnalysisProject) -> dict[str, Any]:
    return {
        "id": p.id,
        "project_id": p.project_id,
        "analysis_id": p.analysis_id,
        "code": p.code,
        "name": p.name,
        "description": p.description,
        "domain_type": p.domain_type,
        "bounded_contexts": p.bounded_contexts,
        "entity_codes": p.entity_codes,
        "traced_req_codes": p.traced_req_codes,
    }


def contract_to_dict(c: SubProjectContract) -> dict[str, Any]:
    return {
        "id": c.id,
        "project_id": c.project_id,
        "analysis_id": c.analysis_id,
        "from_subproject_code": c.from_subproject_code,
        "to_subproject_code": c.to_subproject_code,
        "contract_type": c.contract_type.value,
        "name": c.name,
        "spec": c.spec,
        "description": c.description,
    }


def analysis_to_dict(
    a: AnalysisDocument, *, with_diagrams: bool = True
) -> dict[str, Any]:
    """Serialize an AnalysisDocument.

    ``with_diagrams=False`` omits the large text/blob fields (mer_diagram,
    process_diagrams, nfr_analysis, component_diagram, traceability) for list
    views where they are not needed.
    """
    return {
        "id": a.id,
        "project_id": a.project_id,
        "version": a.version,
        "status": a.status.value,
        "srs_version": a.srs_version,
        "mer_diagram": a.mer_diagram if with_diagrams else None,
        "mer_diagram_description": (
            a.mer_diagram_description if with_diagrams else None
        ),
        "process_diagrams": a.process_diagrams if with_diagrams else None,
        "nfr_analysis": a.nfr_analysis if with_diagrams else None,
        "component_diagram": a.component_diagram if with_diagrams else None,
        "component_diagram_description": (
            a.component_diagram_description if with_diagrams else None
        ),
        "system_architecture_diagram": (
            a.system_architecture_diagram if with_diagrams else None
        ),
        "system_architecture_description": (
            a.system_architecture_description if with_diagrams else None
        ),
        "infrastructure_diagram": (
            a.infrastructure_diagram if with_diagrams else None
        ),
        "infrastructure_description": (
            a.infrastructure_description if with_diagrams else None
        ),
        "traceability": a.traceability if with_diagrams else None,
        "requirement_codes": a.requirement_codes,
        "requirement_count": a.requirement_count,
        "generated_at": a.generated_at.isoformat() if a.generated_at else None,
        "locked_at": a.locked_at.isoformat() if a.locked_at else None,
    }


# --- AnalysisDocument CRUD --------------------------------------------------


async def get_latest_analysis(
    session: AsyncSession, project_id: int
) -> AnalysisDocument | None:
    """La version mas reciente del analisis del proyecto (o None)."""
    return await session.scalar(
        select(AnalysisDocument)
        .where(AnalysisDocument.project_id == project_id)
        .order_by(AnalysisDocument.version.desc())
        .limit(1)
    )


async def get_analysis_version(
    session: AsyncSession, project_id: int, version: int
) -> AnalysisDocument | None:
    return await session.scalar(
        select(AnalysisDocument).where(
            AnalysisDocument.project_id == project_id,
            AnalysisDocument.version == version,
        )
    )


async def list_analysis_versions(
    session: AsyncSession, project_id: int
) -> list[AnalysisDocument]:
    rows = await session.scalars(
        select(AnalysisDocument)
        .where(AnalysisDocument.project_id == project_id)
        .order_by(AnalysisDocument.version.desc())
    )
    return list(rows)


async def update_analysis_status(
    session: AsyncSession,
    analysis_id: int,
    status: AnalysisStatus,
) -> AnalysisDocument:
    """Transition the lifecycle status of an analysis document.

    candidate -> in_review -> locked. Passing to LOCKED freezes the document
    (sets locked_at, prevents further mutations).
    """
    a = await session.get(AnalysisDocument, analysis_id)
    if a is None:
        raise KeyError(f"analysis {analysis_id} not found")
    if a.status == AnalysisStatus.LOCKED:
        raise ValueError("LOCKED analysis is immutable")
    a.status = status
    if status == AnalysisStatus.LOCKED:
        a.locked_at = datetime.utcnow()
    await session.commit()
    await session.refresh(a)
    return a


async def update_analysis_diagrams(
    session: AsyncSession,
    analysis_id: int,
    updates: dict,
) -> AnalysisDocument:
    """Partial update of diagram fields (mer_diagram, process_diagrams, etc.).

    Only allowed on CANDIDATE status — LOCKED and IN_REVIEW are immutable.
    """
    a = await session.get(AnalysisDocument, analysis_id)
    if a is None:
        raise KeyError(f"analysis {analysis_id} not found")
    if a.status != AnalysisStatus.CANDIDATE:
        raise ValueError(
            f"analysis {analysis_id} is {a.status.value}, "
            "only CANDIDATE allows diagram edits"
        )
    diagram_fields = (
        "mer_diagram",
        "mer_diagram_description",
        "process_diagrams",
        "component_diagram",
        "component_diagram_description",
        "system_architecture_diagram",
        "system_architecture_description",
        "infrastructure_diagram",
        "infrastructure_description",
    )
    for field in diagram_fields:
        if field in updates and updates[field] is not None:
            setattr(a, field, updates[field])
    await session.commit()
    await session.refresh(a)
    return a


async def delete_all_analysis(
    session: AsyncSession, project_id: int
) -> int:
    """Hard-delete every analysis document + child rows for a project.

    Child rows (entities, relationships, ADRs, sub-projects, contracts) are
    deleted via CASCADE. Does NOT commit; the caller owns the transaction.
    Returns the version count.
    """
    count = await session.scalar(
        select(func.count()).select_from(AnalysisDocument).where(
            AnalysisDocument.project_id == project_id
        )
    )
    total = int(count or 0)
    if total:
        await session.execute(
            delete(AnalysisDocument).where(
                AnalysisDocument.project_id == project_id
            )
        )
        await session.flush()
    return total


# --- create_analysis (the only writer) --------------------------------------


async def _max_sequential_suffix(
    session: AsyncSession, stmt
) -> int:
    """Extract the highest numeric suffix from codes like ADR-016 or SUB-003.

    Used by ``create_analysis`` to continue the sequential numbering without
    colliding with codes from previous analysis versions. Returns 0 if no
    existing codes match the ``PREFIX-NNN`` pattern.
    """
    rows = await session.scalars(stmt)
    max_seq = 0
    for code in rows:
        if not code or "-" not in code:
            continue
        try:
            max_seq = max(max_seq, int(code.rsplit("-", 1)[-1]))
        except ValueError:
            continue
    return max_seq


def _normalize_cardinality(value: str) -> MerCardinality:
    """Normalize a cardinality string to the MerCardinality enum."""
    v = str(value).strip().upper().replace(" ", "")
    mapping = {
        "1:1": MerCardinality.ONE_TO_ONE,
        "1-1": MerCardinality.ONE_TO_ONE,
        "ONE-TO-ONE": MerCardinality.ONE_TO_ONE,
        "1:N": MerCardinality.ONE_TO_MANY,
        "1-N": MerCardinality.ONE_TO_MANY,
        "1:M": MerCardinality.ONE_TO_MANY,
        "ONE-TO-MANY": MerCardinality.ONE_TO_MANY,
        "N:M": MerCardinality.MANY_TO_MANY,
        "M:N": MerCardinality.MANY_TO_MANY,
        "N-M": MerCardinality.MANY_TO_MANY,
        "M-N": MerCardinality.MANY_TO_MANY,
        "MANY-TO-MANY": MerCardinality.MANY_TO_MANY,
    }
    return mapping.get(v, MerCardinality.ONE_TO_MANY)


async def create_analysis(
    session: AsyncSession, project_id: int, payload: dict[str, Any]
) -> AnalysisDocument:
    """Crea una nueva version CANDIDATE del analisis con todas sus filas hijas.

    ``payload`` es la salida del assembler:
    - mer_diagram, process_diagrams, nfr_analysis, component_diagram,
      traceability, requirement_codes, requirement_count, srs_version.
    - entities: list[dict] con name, description, attributes, aggregate_root,
      bounded_context, traced_req_codes.
    - relationships: list[dict] con from_entity_code, to_entity_code,
      cardinality, label, description, traced_req_codes.

    La version se autoincrementa por proyecto. Los codigos ENT-XXXX se asignan
    aqui (Crockford base32, unico por proyecto). Los codigos ADR-NNN y SUB-NNN
    son secuenciales por proyecto, continuando desde el maximo sufijo numerico
    existente para evitar colisiones con versiones previas.
    """
    # 1. Auto-increment version.
    cur = await session.scalar(
        select(func.max(AnalysisDocument.version)).where(
            AnalysisDocument.project_id == project_id
        )
    )
    version = (cur or 0) + 1

    # 2. Create the AnalysisDocument.
    doc = AnalysisDocument(
        project_id=project_id,
        version=version,
        status=AnalysisStatus.CANDIDATE,
        srs_version=payload.get("srs_version"),
        mer_diagram=payload.get("mer_diagram", ""),
            mer_diagram_description=payload.get("mer_diagram_description", ""),
        process_diagrams=payload.get("process_diagrams", []),
        nfr_analysis=payload.get("nfr_analysis", {}),
        component_diagram=payload.get("component_diagram", ""),
            component_diagram_description=payload.get(
                "component_diagram_description", ""
            ),
        system_architecture_diagram=payload.get(
            "system_architecture_diagram", ""
        ),
        system_architecture_description=payload.get(
            "system_architecture_description", ""
        ),
        infrastructure_diagram=payload.get("infrastructure_diagram", ""),
        infrastructure_description=payload.get("infrastructure_description", ""),
        traceability=payload.get("traceability", {}),
        requirement_codes=payload.get("requirement_codes", []),
        requirement_count=payload.get("requirement_count", 0),
    )
    session.add(doc)
    await session.flush()  # needs the id for child FKs

    analysis_id = doc.id

    # 3. Create DomainEntity rows (assign ENT-XXXX codes).
    entity_name_to_code: dict[str, str] = {}
    reserved_codes: set[str] = set()
    for ed in payload.get("entities", []):
        code = await gen_entity_code(
            session, project_id, reserved=reserved_codes
        )
        name = ed.get("name", "Unknown")
        entity_name_to_code[name] = code
        session.add(DomainEntity(
            project_id=project_id,
            analysis_id=analysis_id,
            code=code,
            name=name,
            description=ed.get("description", ""),
            attributes=ed.get("attributes", []),
            aggregate_root=ed.get("aggregate_root", False),
            bounded_context=ed.get("bounded_context") or None,
            traced_req_codes=ed.get("traced_req_codes", []),
        ))

    # 4. Create DomainRelationship rows.
    # Dedupe by (from_entity_code, to_entity_code, label) to respect the
    # UniqueConstraint; the LLM may emit duplicate edges.
    seen_edges: set[tuple[str, str, str]] = set()
    for rd in payload.get("relationships", []):
        # Resolve entity names to codes; skip if entity not found.
        from_name = rd.get("from_entity", "")
        to_name = rd.get("to_entity", "")
        from_code = rd.get("from_entity_code") or entity_name_to_code.get(from_name, from_name)
        to_code = rd.get("to_entity_code") or entity_name_to_code.get(to_name, to_name)
        label = rd.get("label") or ""
        edge = (from_code, to_code, label)
        if edge in seen_edges:
            continue
        seen_edges.add(edge)
        session.add(DomainRelationship(
            project_id=project_id,
            analysis_id=analysis_id,
            from_entity_code=from_code,
            to_entity_code=to_code,
            cardinality=_normalize_cardinality(rd.get("cardinality", "1:N")),
            label=label or None,
            description=rd.get("description"),
            traced_req_codes=rd.get("traced_req_codes", []),
        ))

    # 5. Create ArchitectureDecision rows.
    # Find the max numeric suffix across ALL existing ADR codes for this
    # project (including previous analysis versions) to avoid
    # UniqueConstraint collisions on (project_id, code).
    max_adr_seq = await _max_sequential_suffix(
        session,
        select(ArchitectureDecision.code).where(
            ArchitectureDecision.project_id == project_id
        ),
    )
    for i, ad in enumerate(payload.get("adrs", []), start=1):
        seq = max_adr_seq + i
        session.add(ArchitectureDecision(
            project_id=project_id,
            analysis_id=analysis_id,
            code=gen_adr_code(seq),
            title=ad.get("title", ""),
            status=AdrStatus.PROPOSED,
            context=ad.get("context", ""),
            decision=ad.get("decision", ""),
            alternatives=ad.get("alternatives", []),
            rationale=ad.get("rationale", ""),
            nfr_codes=ad.get("nfr_codes", []),
        ))

    # 6. Create AnalysisProject rows (assign PROJ-NNN codes).
    # Persist project areas (DDD subdomains) discovered by project_pipeline.
    max_proj_seq = await _max_sequential_suffix(
        session,
        select(AnalysisProject.code).where(
            AnalysisProject.project_id == project_id
        ),
    )
    project_name_to_code: dict[str, str] = {}
    for i, pa in enumerate(payload.get("projects", []), start=1):
        seq = max_proj_seq + i
        pcode = gen_project_code(seq)
        pname = pa.get("name", "")
        project_name_to_code[pname] = pcode
        session.add(AnalysisProject(
            project_id=project_id,
            analysis_id=analysis_id,
            code=pcode,
            name=pname,
            description=pa.get("description", ""),
            domain_type=pa.get("domain_type", "core"),
            bounded_contexts=pa.get("bounded_contexts", []),
            entity_codes=pa.get("entity_codes", []),
            traced_req_codes=pa.get("traced_req_codes", []),
        ))

    # 7. Create SubProject rows (assign SUB-NNN codes + build name→code map).
    # Same max-suffix approach as ADRs to avoid cross-version collisions.
    max_sp_seq = await _max_sequential_suffix(
        session,
        select(SubProject.code).where(
            SubProject.project_id == project_id
        ),
    )
    subproject_name_to_code: dict[str, str] = {}
    for i, sp in enumerate(payload.get("sub_projects", []), start=1):
        seq = max_sp_seq + i
        code = gen_subproject_code(seq)
        name = sp.get("name", "")
        subproject_name_to_code[name] = code
        # Resolve project_name → project_code if available.
        sp_project_name = sp.get("project_name", "")
        sp_project_code = project_name_to_code.get(sp_project_name)
        session.add(SubProject(
            project_id=project_id,
            analysis_id=analysis_id,
            code=code,
            name=name,
            responsibility=sp.get("responsibility", ""),
            stack=sp.get("stack", {}),
            bounded_contexts=sp.get("bounded_contexts", []),
            nfr_codes=sp.get("nfr_codes", []),
            entity_codes=sp.get("entity_codes", []),
            project_code=sp_project_code,
        ))

    # 8. Create SubProjectContract rows.
    # Resolve sub-project NAMES (from the pipeline output) to CODES (assigned
    # just above). Dedupe by (from_code, to_code, name) to respect the
    # UniqueConstraint.
    seen_contracts: set[tuple[str, str, str]] = set()
    for cd in payload.get("contracts", []):
        # Prefer explicit codes if the caller already resolved them; otherwise
        # fall back to the name→code map built during SubProject creation.
        from_code = (
            cd.get("from_subproject_code")
            or subproject_name_to_code.get(cd.get("from_subproject", ""), "")
        )
        to_code = (
            cd.get("to_subproject_code")
            or subproject_name_to_code.get(cd.get("to_subproject", ""), "")
        )
        name = cd.get("name", "")
        edge = (from_code, to_code, name)
        if edge in seen_contracts:
            continue
        seen_contracts.add(edge)
        session.add(SubProjectContract(
            project_id=project_id,
            analysis_id=analysis_id,
            from_subproject_code=from_code,
            to_subproject_code=to_code,
            contract_type=cd.get("contract_type", "openapi"),
            name=name,
            spec=cd.get("spec", ""),
            description=cd.get("description"),
        ))

    await session.commit()
    await session.refresh(doc)
    return doc


# --- child-row queries ------------------------------------------------------


async def list_domain_entities(
    session: AsyncSession, analysis_id: int
) -> list[DomainEntity]:
    rows = await session.scalars(
        select(DomainEntity)
        .where(DomainEntity.analysis_id == analysis_id)
        .order_by(DomainEntity.aggregate_root.desc(), DomainEntity.name)
    )
    return list(rows)


async def list_domain_relationships(
    session: AsyncSession, analysis_id: int
) -> list[DomainRelationship]:
    rows = await session.scalars(
        select(DomainRelationship)
        .where(DomainRelationship.analysis_id == analysis_id)
        .order_by(DomainRelationship.from_entity_code, DomainRelationship.to_entity_code)
    )
    return list(rows)


async def list_adrs(
    session: AsyncSession, analysis_id: int
) -> list[ArchitectureDecision]:
    rows = await session.scalars(
        select(ArchitectureDecision)
        .where(ArchitectureDecision.analysis_id == analysis_id)
        .order_by(ArchitectureDecision.code)
    )
    return list(rows)


async def list_sub_projects(
    session: AsyncSession, analysis_id: int
) -> list[SubProject]:
    rows = await session.scalars(
        select(SubProject)
        .where(SubProject.analysis_id == analysis_id)
        .order_by(SubProject.code)
    )
    return list(rows)


async def list_contracts(
    session: AsyncSession, analysis_id: int
) -> list[SubProjectContract]:
    rows = await session.scalars(
        select(SubProjectContract)
        .where(SubProjectContract.analysis_id == analysis_id)
        .order_by(
            SubProjectContract.from_subproject_code,
            SubProjectContract.to_subproject_code,
        )
    )
    return list(rows)


async def list_projects(
    session: AsyncSession, analysis_id: int
) -> list[AnalysisProject]:
    rows = await session.scalars(
        select(AnalysisProject)
        .where(AnalysisProject.analysis_id == analysis_id)
        .order_by(AnalysisProject.code)
    )
    return list(rows)
