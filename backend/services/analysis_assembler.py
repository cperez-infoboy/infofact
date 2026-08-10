"""Analysis assembler: runs analysis motors in order, returns payload for create_analysis.

Punto unico que coordina la generacion de un documento de analisis candidato.
Sprint 1: solo MER. Sprints siguientes anaden NFR, procesos, ADRs y
sub-proyectos en orden de dependencia.

Vive aparte de ``analysis_store`` (que es la persistencia) para no acoplar la
sintesis del entregable con la escritura a DB, igual que ``srs_assembler``.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.pipelines.adr_pipeline import generate_adrs
from backend.agents.pipelines.architecture_pipeline import generate_architecture
from backend.agents.pipelines.mer_pipeline import generate_mer
from backend.agents.pipelines.nfr_pipeline import analyze_nfrs
from backend.agents.pipelines.process_pipeline import generate_processes
from backend.agents.pipelines.project_pipeline import discover_projects
from backend.agents.pipelines.subproject_pipeline import propose_subprojects
from backend.models.requirement import ReqStatus, ReqType
from backend.models.srs import GoalKind, GoalStatus
from backend.services.requirement_store import list_requirements
from backend.services.srs_store import list_goal_links, list_goals

logger = logging.getLogger(__name__)

# Estados de requerimiento que cuentan como "vivos" para el analisis
# (consistente con srs_assembler._LIVE_STATUSES).
_LIVE_STATUSES = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)

# Tipos de requerimiento que alimentan el MER (funcionales, de datos y de
# proceso). Los NFR alimentan el pipeline NFR.
_FUNCTIONAL_TYPES = frozenset(
    {ReqType.FUNCTIONAL, ReqType.DATA, ReqType.PROCESS}
)

# Tipos de requerimiento no funcional que alimentan el pipeline NFR (Sprint 2).
_NFR_TYPES = frozenset(
    {
        ReqType.PERFORMANCE,
        ReqType.SECURITY,
        ReqType.USABILITY,
        ReqType.RELIABILITY,
        ReqType.MAINTAINABILITY,
        ReqType.COMPLIANCE,
        ReqType.CONSTRAINT,
    }
)


async def assemble_analysis(
    session: AsyncSession,
    project_id: int,
    *,
    project_name: str = "",
    project_description: str = "",
) -> dict[str, Any]:
    """Run analysis motors in order, return payload for ``create_analysis``.

    Sprint 1: only MER. Later sprints add NFR, processes, ADRs, sub-projects
    in dependency order (MER -> NFR -> processes -> ADRs -> sub-projects).

    The payload dict is consumed by ``analysis_store.create_analysis`` to
    persist the AnalysisDocument + child rows.
    """
    # 1. Load live requirements.
    items = await list_requirements(session, project_id, include_deleted=True)
    live = [it for it in items if it.status in _LIVE_STATUSES]
    codes = [it.code for it in live]

    # 2. Filter functional items for the MER pipeline.
    functional = [it for it in live if it.type in _FUNCTIONAL_TYPES]
    # NFRs feed the NFR pipeline (Sprint 2).
    nfrs = [it for it in live if it.type in _NFR_TYPES]

    logger.info(
        "assemble_analysis: %d live requirements (%d functional, %d NFR)",
        len(live), len(functional), len(nfrs),
    )

    # 2b. Load goals from SRS phase for enrichment.
    all_goals = await list_goals(session, project_id)
    active_goals = [g for g in all_goals if g.status != GoalStatus.REJECTED]
    functional_goals = [
        g for g in active_goals if g.kind == GoalKind.FUNCTIONAL_GOAL
    ]
    softgoals = [g for g in active_goals if g.kind == GoalKind.SOFTGOAL]
    goal_links = await list_goal_links(session, project_id)

    # 3. Run MER pipeline.
    mer_result = await generate_mer(
        functional,
        project_name=project_name,
        project_description=project_description,
        goals=functional_goals,
    )

    # 3b. Run NFR pipeline (Sprint 2).
    nfr_result = (
        await analyze_nfrs(
            nfrs,
            project_name=project_name,
            project_description=project_description,
            softgoals=softgoals,
        )
        if nfrs
        else None
    )

    # 3c. Run ADR pipeline (Sprint 2) — depends on NFR decisions.
    adr_result = (
        await generate_adrs(
            nfr_result,
            mer_result,
            project_name=project_name,
            project_description=project_description,
            softgoals=softgoals,
        )
        if nfr_result
        else None
    )

    # 3d. Run process pipeline (Sprint 3) — depends on MER (entity context).
    process_result = (
        await generate_processes(
            functional,
            mer_result=mer_result,
            project_name=project_name,
            project_description=project_description,
            goals=functional_goals,
        )
        if functional and mer_result
        else None
    )

    # 3e. Run project pipeline — discovers project areas (DDD subdomains).
    # Depends on MER (bounded contexts) + process (transactional coupling) + ADR.
    project_result = (
        await discover_projects(
            mer_result=mer_result,
            process_result=process_result,
            adr_result=adr_result,
            project_name=project_name,
            project_description=project_description,
            goals=active_goals,
        )
        if mer_result
        else None
    )

    # 3f. Run sub-project pipeline (Sprint 3) — depends on MER + ADR + NFR + projects.
    subproject_result = (
        await propose_subprojects(
            mer_result=mer_result,
            adr_result=adr_result,
            nfr_result=nfr_result,
            project_result=project_result,
            project_name=project_name,
            project_description=project_description,
            goals=active_goals,
        )
        if mer_result
        else None
    )

    # 3g. Run architecture pipeline — depends on NFR + ADR + sub-projects.
    architecture_result = (
        await generate_architecture(
            mer_result=mer_result,
            nfr_result=nfr_result,
            adr_result=adr_result,
            subproject_result=subproject_result,
            project_name=project_name,
            project_description=project_description,
            goals=active_goals,
        )
        if (nfr_result or subproject_result)
        else None
    )

    # 4. Build payload for create_analysis.
    # Entities: convert MerEntitySchema to plain dicts.
    entities: list[dict[str, Any]] = []
    for ent in mer_result.entities:
        entities.append({
            "name": ent.name,
            "description": ent.description,
            "attributes": [a.model_dump() for a in ent.attributes],
            "aggregate_root": ent.aggregate_root,
            "bounded_context": ent.bounded_context,
            "traced_req_codes": ent.traced_req_codes,
        })

    # Relationships: convert MerRelationshipSchema to plain dicts.
    # from_entity/to_entity are names that create_analysis will resolve to
    # ENT-XXXX codes via entity_name_to_code.
    relationships: list[dict[str, Any]] = []
    for rel in mer_result.relationships:
        relationships.append({
            "from_entity": rel.from_entity,
            "to_entity": rel.to_entity,
            "cardinality": rel.cardinality,
            "label": rel.label,
            "description": rel.description,
            "traced_req_codes": rel.traced_req_codes,
        })

    nfr_analysis = {
        "decisions": (
            [d.model_dump() for d in nfr_result.decisions] if nfr_result else []
        ),
        "stack": (
            [s.model_dump() for s in nfr_result.stack] if nfr_result else []
        ),
        "data_consistency": nfr_result.data_consistency if nfr_result else "",
        "patterns": nfr_result.patterns if nfr_result else "",
    }
    adr_payloads = [a.model_dump() for a in adr_result.adrs] if adr_result else []

    # Process diagrams: state machines + sequence diagrams.
    process_diagrams = [
        {
            "name": sm.entity_name,
            "type": "state_machine",
            "mermaid": sm.mermaid,
            "entity_name": sm.entity_name,
            "description": sm.description,
            "traced_req_codes": sm.traced_req_codes,
        }
        for sm in (process_result.state_machines if process_result else [])
    ] + [
        {
            "name": sq.name,
            "type": "sequence",
            "mermaid": sq.mermaid,
            "description": sq.description,
            "traced_req_codes": sq.traced_req_codes,
        }
        for sq in (
            process_result.sequence_diagrams if process_result else []
        )
    ]

    # Sub-projects + contracts.
    project_payloads = (
        [p.model_dump() for p in project_result.projects]
        if project_result
        else []
    )
    sub_project_payloads = (
        [sp.model_dump() for sp in subproject_result.sub_projects]
        if subproject_result
        else []
    )
    contract_payloads = (
        [c.model_dump() for c in subproject_result.contracts]
        if subproject_result
        else []
    )
    component_diagram = (
        subproject_result.component_diagram_mermaid if subproject_result else ""
    )

    # Build goal traceability for the analysis payload.
    goal_traceability: dict[str, Any] = {
        "goals": [
            {
                "code": g.code,
                "statement": g.statement,
                "kind": g.kind.value,
            }
            for g in active_goals
        ],
    }
    if goal_links:
        goal_map = {g.id: g.code for g in all_goals}
        req_map = {it.id: it.code for it in live}
        goal_traceability["goal_links"] = [
            {
                "goal_code": goal_map.get(lk.goal_id, ""),
                "req_code": req_map.get(lk.req_id, ""),
                "relation": (
                    lk.relation.value
                    if hasattr(lk.relation, "value")
                    else str(lk.relation)
                ),
            }
            for lk in goal_links
            if lk.goal_id in goal_map and lk.req_id in req_map
        ]

    return {
        "srs_version": None,  # Sprint 4: resolve from locked SRS.
        "mer_diagram": mer_result.mermaid,
        "mer_diagram_description": mer_result.description,
        "process_diagrams": process_diagrams,
        "nfr_analysis": nfr_analysis,
        "component_diagram": component_diagram,
        "component_diagram_description": (
            subproject_result.component_diagram_description
            if subproject_result
            else ""
        ),
        "system_architecture_diagram": (
            architecture_result.system_architecture_diagram
            if architecture_result
            else ""
        ),
        "system_architecture_description": (
            architecture_result.system_architecture_description
            if architecture_result
            else ""
        ),
        "infrastructure_diagram": (
            architecture_result.infrastructure_diagram
            if architecture_result
            else ""
        ),
        "infrastructure_description": (
            architecture_result.infrastructure_description
            if architecture_result
            else ""
        ),
        "traceability": goal_traceability,
        "requirement_codes": codes,
        "requirement_count": len(live),
        "entities": entities,
        "relationships": relationships,
        "adrs": adr_payloads,
        "projects": project_payloads,
        "sub_projects": sub_project_payloads,
        "contracts": contract_payloads,
    }
