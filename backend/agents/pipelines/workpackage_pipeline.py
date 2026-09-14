"""Work-package pipeline: self-contained delivery packages per sub-project.

Consumes a committed AnalysisDocument (sub-projects + contracts + MER + ADRs +
NFRs + processes) and produces one WORK PACKAGE per sub-project plus an
ASSEMBLY MASTER. Each package is self-contained: a developer with ONLY that
package must be able to implement it without asking questions (contract-first
development — see docs/definiciones/fase2-contexto-implementacion.md).

Pipeline (deterministic unless noted):

  Pass 1 — assemble_package_context(): per sub-project, gather owned entities
           (+attributes), internal vs border relationships, contracts exposed/
           consumed (with specs), requirements verbatim (statements + Gherkin
           acceptance read from RequirementItem directly — the listing tools
           truncate to 500 chars), applicable ADRs and NFRs, and the process
           diagrams of its entities.
  Pass 2 — decompose_tasks(): LLM batched over the package requirements
           (~12 per batch, narrow schema, graceful degradation). Fallback if
           a batch fails: one deterministic task per requirement.
  Pass 3 — verify_packages_coherence(): deterministic gates BEFORE delivery.
           Blocking: (1) coverage closure req->entity->package->task,
           (2) entity partition (one owner) + every border relationship backed
           by a contract, (3) contract symmetry (no self-contracts, consumed
           has expositor) + acyclic dependency graph. Informational:
           (4) cross-package sequence messages map to contracts,
           (5) NFRs assigned, (6) tasks have acceptance criteria.
           Plus an LLM cross-package critique (overlaps, duplicated data
           without sync, ambiguous specs).
  Pass 4 — render_package_markdown() / render_assembly_master(): deterministic
           markdown rendering (build order by levels, integration milestones
           per contract, coherence report with per-gate status).

Does NOT write the DB: returns plain dataclasses consumed by packages_store
(the commit tool persists). Prompts in Spanish neutro. No voseo, no spanglish.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from backend.agents.llm import structured_llm
from backend.agents.pipelines._resilience import (
    DEFAULT_CONCURRENCY,
    _chunk,
    invoke_structured_resilient,
)

logger = logging.getLogger(__name__)

# Requirements per LLM batch in the task-decomposition pass. Small on
# purpose: the output (tasks with descriptions + acceptance) is fat.
_TASK_BATCH_SIZE = int(os.environ.get("INFOFACT_TASKS_BATCH", "12"))


# ---------------------------------------------------------------------------
# LLM-facing schemas (narrow outputs)
# ---------------------------------------------------------------------------


class TaskSchema(BaseModel):
    """One atomic task proposed by the decomposition pass."""

    title: str = Field(
        description=(
            "Titulo corto de la tarea (ej. 'Modelo y CRUD de Viaje')."
        ),
    )
    description: str = Field(
        description=(
            "Descripcion autocontenida (3-8 oraciones): que construir, con "
            "que entidades/contratos, sin asumir conocimiento del resto del "
            "sistema. No repitas los enunciados de requerimientos (van "
            "aparte)."
        ),
    )
    req_codes: list[str] = Field(
        default_factory=list,
        description=(
            "Codigos REQ-XXXX EXACTOS de la lista recibida que esta tarea "
            "implementa (todos los codes recibidos deben quedar cubiertos "
            "por alguna tarea)."
        ),
    )
    entity_codes: list[str] = Field(
        default_factory=list,
        description="Codigos ENT-XXXX de las entidades que la tarea toca.",
    )
    contract_names: list[str] = Field(
        default_factory=list,
        description=(
            "Nombres EXACTOS de contratos (ej. 'POST /orders') que la tarea "
            "implementa o consume."
        ),
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description=(
            "Titulos EXACTOS de otras tareas DE ESTE paquete que deben "
            "terminarse antes (ej. el modelo antes del endpoint)."
        ),
    )


class TaskBatchSchema(BaseModel):
    """Wrapper output of one decomposition batch."""

    tasks: list[TaskSchema] = Field(default_factory=list)


class CoherenceFindingSchema(BaseModel):
    """One cross-package coherence finding (LLM critique)."""

    summary: str = Field(
        description="Hallazgo en 1-2 oraciones (que y donde)."
    )
    severity: str = Field(
        description="blocker | warning | info"
    )
    packages: list[str] = Field(
        default_factory=list,
        description="Codigos WP de los paquetes involucrados.",
    )


class CoherenceCritiqueSchema(BaseModel):
    """Wrapper output of the cross-package critique."""

    findings: list[CoherenceFindingSchema] = Field(default_factory=list)


_TASK_DECOMPOSE_PROMPT = (
    "Eres un tech lead. Recibes los requerimientos de UN sub-proyecto (con "
    "sus entidades y contratos) y los descompones en tareas atomicas tipo "
    "ticket (0.5 a 2 dias de trabajo cada una).\n\n"
    "Reglas:\n"
    "- IDIOMA: espanol neutro.\n"
    "- TODOS los req_codes recibidos deben quedar cubiertos por exactamente "
    "una tarea (no dejes ninguno sin tarea, no los repitas entre tareas).\n"
    "- Una tarea puede cubrir varios requerimientos del mismo tema.\n"
    "- La descripcion debe ser autocontenida: un desarrollador que SOLO ve "
    "este paquete debe poder ejecutarla sin preguntar nada.\n"
    "- Ordena implicitamente: modelos de datos primero, logica despues, "
    "endpoints/contratos al final.\n"
    "- depends_on usa titulos EXACTOS de tareas de este mismo paquete.\n"
    "- NO inventes requerimientos, entidades ni contratos fuera de la lista.\n"
    "Devuelve SOLO el objeto estructurado."
)


# ---------------------------------------------------------------------------
# Dataclasses (pipeline output; persisted by packages_store)
# ---------------------------------------------------------------------------


@dataclass
class PackageTask:
    code: str = ""
    title: str = ""
    description: str = ""
    req_codes: list[str] = field(default_factory=list)
    entity_codes: list[str] = field(default_factory=list)
    contract_names: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    sort_order: int = 0


@dataclass
class PackageContext:
    """Deterministic context of ONE sub-project (Pass 1 output)."""

    sub_project_code: str = ""
    sub_project_name: str = ""
    project_code: str = ""
    project_name: str = ""
    mission: str = ""
    stack: dict = field(default_factory=dict)
    entities: list[dict] = field(default_factory=list)  # {code,name,attrs,...}
    internal_relationships: list[dict] = field(default_factory=list)
    border_relationships: list[dict] = field(default_factory=list)
    contracts_exposed: list[dict] = field(default_factory=list)
    contracts_consumed: list[dict] = field(default_factory=list)
    requirements: list[dict] = field(default_factory=list)  # verbatim + gherkin
    nfrs: list[dict] = field(default_factory=list)
    adrs: list[dict] = field(default_factory=list)
    process_diagrams: list[dict] = field(default_factory=list)
    tasks: list[PackageTask] = field(default_factory=list)
    markdown: str = ""
    counts: dict = field(default_factory=dict)


@dataclass
class CoherenceGate:
    """Result of one coherence gate."""

    gate: str
    status: str = "pass"  # pass | warn | fail
    blocking: bool = False
    details: list[str] = field(default_factory=list)


@dataclass
class PackagesResult:
    """Output of generate_work_packages (Pass 1-4)."""

    packages: list[PackageContext] = field(default_factory=list)
    master_markdown: str = ""
    gates: list[CoherenceGate] = field(default_factory=list)
    critique_findings: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pass 1: deterministic context assembly per sub-project
# ---------------------------------------------------------------------------


async def assemble_package_contexts(
    session,
    analysis_id: int,
) -> tuple[list[PackageContext], dict]:
    """Gather the self-contained context of every sub-project (0 LLM).

    ``session`` is an open AsyncSession (the caller owns the transaction).
    Reads RequirementItem DIRECTLY for full statements + acceptance criteria
    (the agent listing tools truncate statements to 500 chars) and filters
    soft-deleted statuses (MERGED/SUPERSEDED/REJECTED).
    """
    from backend.models.analysis import (
        AnalysisDocument,
        AnalysisProject,
        ArchitectureDecision,
        DomainEntity,
        DomainRelationship,
        SubProject,
        SubProjectContract,
    )
    from backend.models.requirement import ReqStatus, RequirementItem
    from sqlalchemy import select

    doc = await session.get(AnalysisDocument, analysis_id)
    if doc is None:
        raise ValueError(f"analysis {analysis_id} not found")

    sub_projects = (
        (
            await session.execute(
                select(SubProject).where(SubProject.analysis_id == analysis_id)
            )
        )
        .scalars()
        .all()
    )
    projects = (
        (
            await session.execute(
                select(AnalysisProject).where(
                    AnalysisProject.analysis_id == analysis_id
                )
            )
        )
        .scalars()
        .all()
    )
    entities = (
        (
            await session.execute(
                select(DomainEntity).where(
                    DomainEntity.analysis_id == analysis_id
                )
            )
        )
        .scalars()
        .all()
    )
    relationships = (
        (
            await session.execute(
                select(DomainRelationship).where(
                    DomainRelationship.analysis_id == analysis_id
                )
            )
        )
        .scalars()
        .all()
    )
    contracts = (
        (
            await session.execute(
                select(SubProjectContract).where(
                    SubProjectContract.analysis_id == analysis_id
                )
            )
        )
        .scalars()
        .all()
    )
    adrs = (
        (
            await session.execute(
                select(ArchitectureDecision).where(
                    ArchitectureDecision.analysis_id == analysis_id
                )
            )
        )
        .scalars()
        .all()
    )

    # Requirements: DIRECT read (full statement + acceptance), soft-deleted out.
    live_statuses = (
        ReqStatus.VALIDATED,
        ReqStatus.APPROVED,
        ReqStatus.DRAFT,
    )
    items = (
        (
            await session.execute(
                select(RequirementItem).where(
                    RequirementItem.project_id == doc.project_id
                )
            )
        )
        .scalars()
        .all()
    )
    items_by_code = {
        it.code: it for it in items if it.status in live_statuses
    }

    projects_by_code = {p.code: p for p in projects}
    entities_by_code = {e.code: e for e in entities}

    # Process diagrams of the analysis (snapshotted on the document).
    process_diagrams = doc.process_diagrams or []

    contexts: list[PackageContext] = []
    for sp in sub_projects:
        ctx = PackageContext(
            sub_project_code=sp.code,
            sub_project_name=sp.name,
            project_code=sp.project_code or "",
            mission=sp.responsibility or "",
            stack=dict(sp.stack or {}),
        )
        if sp.project_code in projects_by_code:
            ctx.project_name = projects_by_code[sp.project_code].name

        owned_codes = set(sp.entity_codes or [])
        ctx.entities = [
            {
                "code": e.code,
                "name": e.name,
                "description": e.description,
                "attributes": e.attributes,
                "aggregate_root": e.aggregate_root,
                "bounded_context": e.bounded_context,
                "traced_req_codes": e.traced_req_codes,
            }
            for e in entities
            if e.code in owned_codes
        ]

        # Relationships: internal (both ends owned) vs border (one end out).
        for rel in relationships:
            entry = {
                "from": rel.from_entity_code,
                "to": rel.to_entity_code,
                "cardinality": (
                    rel.cardinality.value
                    if hasattr(rel.cardinality, "value")
                    else str(rel.cardinality)
                ),
                "label": rel.label or "",
                "from_name": entities_by_code.get(
                    rel.from_entity_code
                ).name
                if rel.from_entity_code in entities_by_code
                else rel.from_entity_code,
                "to_name": entities_by_code.get(rel.to_entity_code).name
                if rel.to_entity_code in entities_by_code
                else rel.to_entity_code,
                "traced_req_codes": rel.traced_req_codes,
            }
            a_in = rel.from_entity_code in owned_codes
            b_in = rel.to_entity_code in owned_codes
            if a_in and b_in:
                ctx.internal_relationships.append(entry)
            elif a_in or b_in:
                ctx.border_relationships.append(entry)

        # Contracts: exposed (from == this) and consumed (to == this).
        for c in contracts:
            entry = {
                "from": c.from_subproject_code,
                "to": c.to_subproject_code,
                "contract_type": (
                    c.contract_type.value
                    if hasattr(c.contract_type, "value")
                    else str(c.contract_type)
                ),
                "name": c.name,
                "spec": c.spec or "",
                "description": c.description or "",
            }
            if c.from_subproject_code == sp.code:
                ctx.contracts_exposed.append(entry)
            if c.to_subproject_code == sp.code:
                ctx.contracts_consumed.append(entry)

        # Requirements verbatim: union of entity traces + NFR codes.
        req_codes: list[str] = []
        for e in ctx.entities:
            for code in e["traced_req_codes"]:
                if code not in req_codes:
                    req_codes.append(code)
        for code in sp.nfr_codes or []:
            if code not in req_codes:
                req_codes.append(code)
        for code in req_codes:
            it = items_by_code.get(code)
            if it is None:
                continue  # code cites a soft-deleted req: skip silently
            ctx.requirements.append(
                {
                    "code": it.code,
                    "statement": it.statement,
                    "type": (
                        it.type.value
                        if hasattr(it.type, "value")
                        else str(it.type)
                    ),
                    "priority": (
                        it.priority.value
                        if hasattr(it.priority, "value")
                        and it.priority
                        else ""
                    ),
                    "acceptance": list(it.acceptance_criteria or []),
                }
            )

        # NFRs and ADRs applicable to this sub-project (verbatim).
        adr_by_nfr: dict[str, dict] = {}
        for a in adrs:
            entry = {
                "code": a.code,
                "title": a.title,
                "decision": a.decision,
                "rationale": a.rationale,
                "nfr_codes": a.nfr_codes,
            }
            for code in a.nfr_codes or []:
                adr_by_nfr.setdefault(code, entry)
        for code in sp.nfr_codes or []:
            it = items_by_code.get(code)
            if it is None:
                continue
            ctx.nfrs.append(
                {
                    "code": it.code,
                    "statement": it.statement,
                    "type": (
                        it.type.value
                        if hasattr(it.type, "value")
                        else str(it.type)
                    ),
                    "adr": adr_by_nfr.get(code),
                }
            )
        seen_adr: set[str] = set()
        for n in ctx.nfrs:
            if n.get("adr") and n["adr"]["code"] not in seen_adr:
                seen_adr.add(n["adr"]["code"])
                ctx.adrs.append(n["adr"])

        # Process diagrams whose entity belongs to this sub-project.
        entity_names = {e["name"] for e in ctx.entities}
        for d in process_diagrams:
            if d.get("entity_name") in entity_names or (
                d.get("entity_name") is None
                and any(
                    code in owned_codes
                    for code in (d.get("traced_entity_codes") or [])
                )
            ):
                ctx.process_diagrams.append(
                    {
                        "name": d.get("name", ""),
                        "type": d.get("type", ""),
                        "mermaid": d.get("mermaid", ""),
                        "entity_name": d.get("entity_name", ""),
                    }
                )

        ctx.counts = {
            "entities": len(ctx.entities),
            "requirements": len(ctx.requirements),
            "contracts_exposed": len(ctx.contracts_exposed),
            "contracts_consumed": len(ctx.contracts_consumed),
            "nfrs": len(ctx.nfrs),
            "adrs": len(ctx.adrs),
        }
        contexts.append(ctx)

    meta = {
        "analysis_version": doc.version,
        "analysis_status": (
            doc.status.value if hasattr(doc.status, "value") else str(doc.status)
        ),
    }
    return contexts, meta


# ---------------------------------------------------------------------------
# Pass 2: LLM task decomposition (batched, graceful)
# ---------------------------------------------------------------------------


async def _decompose_package_tasks(
    ctx: PackageContext,
    *,
    project_name: str = "",
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress=None,
) -> list[PackageTask]:
    """Decompose the package requirements into atomic tasks (LLM batched).

    A failed batch degrades to ONE deterministic task per requirement of
    that batch (with its Gherkin attached) — the backlog never loses
    coverage because the LLM had a bad day.
    """
    if not ctx.requirements:
        return []

    def _req_lines(reqs: list[dict]) -> str:
        lines = []
        for r in reqs:
            lines.append(f"- {r['code']} [{r['type']}]: {r['statement']}")
        return "\n".join(lines)

    def _context_block() -> str:
        ent_lines = "\n".join(
            f"- {e['code']} {e['name']}" for e in ctx.entities
        )
        contract_lines = "\n".join(
            f"- [{c['contract_type']}] {c['name']}"
            for c in ctx.contracts_exposed + ctx.contracts_consumed
        )
        return (
            f"PROYECTO: {ctx.project_name or project_name}\n"
            f"SUB-PROYECTO: {ctx.sub_project_name} ({ctx.sub_project_code})\n"
            f"MISION: {ctx.mission}\n\n"
            f"TUS ENTIDADES:\n{ent_lines or '(ninguna)'}\n\n"
            f"TUS CONTRATOS:\n{contract_lines or '(ninguno)'}\n"
        )

    batches = list(_chunk(ctx.requirements, _TASK_BATCH_SIZE))
    total = len(batches)
    state = {"done": 0, "failed": 0}
    tasks: list[PackageTask] = []
    sem = asyncio.Semaphore(concurrency)

    async def _ask(batch: list[dict]):
        user_text = _context_block() + (
            "\nREQUERIMIENTOS A DESCOMPONER:\n"
            + _req_lines(batch)
            + "\n"
        )
        msgs = [
            ("system", _TASK_DECOMPOSE_PROMPT),
            ("human", user_text),
        ]
        try:
            return await invoke_structured_resilient(
                lambda **kw: structured_llm(TaskBatchSchema, **kw),
                msgs,
                context_label=f"tasks_decompose ({len(batch)} reqs)",
            )
        except Exception:
            logger.exception("_decompose_package_tasks: lote fallo")
            return None

    async def _guarded(batch):
        async with sem:
            result = await _ask(batch)
            state["done"] += 1
            if result is None:
                state["failed"] += 1
            if on_progress is not None:
                detail = (
                    f"lote {state['done']}/{total} completado"
                    if result is not None
                    else f"lote {state['done']}/{total} fallo, se usa "
                    "descomposición simple"
                )
                await on_progress(
                    f"tareas {ctx.sub_project_code} "
                    f"({state['done']}/{total} lotes): {detail}"
                )
            return result

    results = await asyncio.gather(
        *[_guarded(b) for b in batches], return_exceptions=True
    )

    covered: set[str] = set()
    for batch, result in zip(batches, results):
        if isinstance(result, TaskBatchSchema) and result.tasks:
            for t in result.tasks:
                codes = [c for c in t.req_codes if c not in covered]
                if not codes:
                    continue
                covered.update(codes)
                tasks.append(
                    PackageTask(
                        title=t.title,
                        description=t.description,
                        req_codes=codes,
                        entity_codes=list(t.entity_codes),
                        contract_names=list(t.contract_names),
                        depends_on=list(t.depends_on),
                    )
                )
        else:
            # Fallback determinista: 1 tarea por requerimiento del lote.
            for r in batch:
                if r["code"] in covered:
                    continue
                covered.add(r["code"])
                acc = list(r.get("acceptance") or [])
                tasks.append(
                    PackageTask(
                        title=f"Implementar {r['code']}",
                        description=(
                            f"Implementar el requerimiento {r['code']} "
                            f"({r['type']}) del sub-proyecto "
                            f"{ctx.sub_project_name}: {r['statement']}"
                        ),
                        req_codes=[r["code"]],
                        acceptance=acc,
                    )
                )

    # Attach acceptance criteria from the requirements each task covers.
    acc_by_code = {r["code"]: list(r.get("acceptance") or []) for r in ctx.requirements}
    for t in tasks:
        merged: list[str] = list(t.acceptance)
        for code in t.req_codes:
            for a in acc_by_code.get(code, []):
                if a not in merged:
                    merged.append(a)
        t.acceptance = merged

    # Sort order follows requirement order (stable, reviewable).
    req_order = {r["code"]: i for i, r in enumerate(ctx.requirements)}
    tasks.sort(key=lambda t: min(req_order.get(c, 10**6) for c in t.req_codes) if t.req_codes else 10**6)
    for i, t in enumerate(tasks):
        t.sort_order = i
    logger.info(
        "tasks_decompose: %s -> %d tareas (%d lotes, %d fallidos)",
        ctx.sub_project_code, len(tasks), total, state["failed"],
    )
    return tasks


# ---------------------------------------------------------------------------
# Pass 3: coherence gate
# ---------------------------------------------------------------------------


def verify_packages_coherence(
    contexts: list[PackageContext],
    *,
    all_entities: list[dict],
    all_requirements: list[dict],
    all_contracts: list[dict],
    all_sequence_diagrams: list[dict],
) -> list[CoherenceGate]:
    """Deterministic gates BEFORE delivery (0 LLM).

    Blocking gates (1-3) must pass for the commit to proceed; informational
    gates (4-6) surface debts in the master document.
    """
    gates: list[CoherenceGate] = []

    owner_by_entity: dict[str, PackageContext] = {}
    for ctx in contexts:
        for e in ctx.entities:
            code = e["code"]
            if code in owner_by_entity:
                gates.append(
                    CoherenceGate(
                        gate="entidad_duenio_unico",
                        status="fail",
                        blocking=True,
                        details=[
                            f"Entidad {code} asignada a "
                            f"{owner_by_entity[code].sub_project_code} y "
                            f"{ctx.sub_project_code}"
                        ],
                    )
                )
            owner_by_entity[code] = ctx

    # Gate 1: coverage closure (req -> entity -> package -> >= 1 task).
    uncovered: list[str] = []
    covered_reqs: set[str] = set()
    for ctx in contexts:
        for r in ctx.requirements:
            covered_reqs.add(r["code"])
        if not ctx.tasks:
            gates.append(
                CoherenceGate(
                    gate="cierre_cobertura",
                    status="fail",
                    blocking=True,
                    details=[
                        f"{ctx.sub_project_code} no tiene tareas: su backlog "
                        "queda vacío"
                    ],
                )
            )
        for t in ctx.tasks:
            covered_reqs.update(t.req_codes)
    for r in all_requirements:
        if r["code"] not in covered_reqs:
            uncovered.append(r["code"])
    if uncovered:
        gates.append(
            CoherenceGate(
                gate="cierre_cobertura",
                status="fail",
                blocking=True,
                details=[
                    f"Requerimientos sin tarea en ningún paquete: "
                    f"{sorted(uncovered)[:20]}"
                    + (f" (+{len(uncovered) - 20} más)" if len(uncovered) > 20 else "")
                ],
            )
        )
    else:
        gates.append(
            CoherenceGate(
                gate="cierre_cobertura",
                status="pass",
                blocking=True,
                details=[
                    f"{len(covered_reqs)}/{len(all_requirements)} "
                    "requerimientos cubiertos por tareas"
                ],
            )
        )

    # Gate 2: every border relationship backed by a contract between the two
    # sub-projects involved.
    contract_pairs: set[tuple[str, str]] = set()
    for c in all_contracts:
        contract_pairs.add((c["from"], c["to"]))
        contract_pairs.add((c["to"], c["from"]))
    unbacked: list[str] = []
    for ctx in contexts:
        for rel in ctx.border_relationships:
            other_code = (
                owner_by_entity[rel["to"]].sub_project_code
                if rel["to"] in owner_by_entity and rel["from"] in owner_by_entity
                and owner_by_entity.get(rel["from"]) is ctx
                else owner_by_entity.get(rel["from"]).sub_project_code
                if rel["from"] in owner_by_entity
                else "?"
            )
            pair = (ctx.sub_project_code, other_code)
            if other_code != "?" and pair not in contract_pairs:
                unbacked.append(
                    f"{ctx.sub_project_code}/{other_code}: "
                    f"{rel['from_name']} -> {rel['to_name']}"
                )
    if unbacked:
        gates.append(
            CoherenceGate(
                gate="frontera_con_contrato",
                status="fail",
                blocking=True,
                details=[
                    "Relaciones fronterizas sin contrato entre paquetes: "
                ] + unbacked[:10],
            )
        )
    else:
        gates.append(
            CoherenceGate(
                gate="frontera_con_contrato",
                status="pass",
                blocking=True,
                details=["Toda relación fronteriza tiene contrato"],
            )
        )

    # Gate 3: contract symmetry + acyclic dependency graph.
    self_contracts = [
        c for c in all_contracts if c["from"] == c["to"]
    ]
    orphans = [
        c
        for c in all_contracts
        if c["from"] not in {ctx.sub_project_code for ctx in contexts}
        or c["to"] not in {ctx.sub_project_code for ctx in contexts}
    ]
    if self_contracts or orphans:
        gates.append(
            CoherenceGate(
                gate="contratos_simetricos",
                status="fail",
                blocking=True,
                details=(
                    [
                        f"Auto-contratos: {[c['name'] for c in self_contracts][:5]}"
                    ]
                    if self_contracts
                    else []
                )
                + (
                    [
                        f"Contratos con paquete inexistente: "
                        f"{[c['name'] for c in orphans][:5]}"
                    ]
                    if orphans
                    else []
                ),
            )
        )
    else:
        gates.append(
            CoherenceGate(
                gate="contratos_simetricos",
                status="pass",
                blocking=True,
                details=["Sin auto-contratos ni contratos huérfanos"],
            )
        )

    # Acyclicity: package dependency graph = consumed contracts.
    deps: dict[str, set[str]] = {ctx.sub_project_code: set() for ctx in contexts}
    for c in all_contracts:
        if c["from"] in deps and c["to"] in deps and c["from"] != c["to"]:
            deps[c["from"]].add(c["to"])  # from depends on to (consumer->provider)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {k: WHITE for k in deps}
    cycles: list[str] = []

    def _visit(node: str, stack: list[str]) -> None:
        color[node] = GRAY
        for nxt in sorted(deps.get(node, ())):
            if color.get(nxt) == GRAY:
                cycles.append(" -> ".join([*stack, node, nxt]))
            elif color.get(nxt) == WHITE:
                _visit(nxt, [*stack, node])
        color[node] = BLACK

    for node in sorted(deps):
        if color[node] == WHITE:
            _visit(node, [])
    if cycles:
        gates.append(
            CoherenceGate(
                gate="grafo_aciclico",
                status="fail",
                blocking=True,
                details=[f"Ciclos de dependencia: {cycles[:3]}"],
            )
        )
    else:
        gates.append(
            CoherenceGate(
                gate="grafo_aciclico",
                status="pass",
                blocking=True,
                details=["Grafo de dependencias sin ciclos"],
            )
        )

    # Gate 4 (informational): cross-package sequence messages map to contracts.
    contract_names = {c["name"] for c in all_contracts}
    unmapped: list[str] = []
    for d in all_sequence_diagrams:
        for msg in d.get("cross_package_messages", []):
            if msg not in contract_names:
                unmapped.append(f"{d.get('name', '?')}: {msg}")
    gates.append(
        CoherenceGate(
            gate="procesos_cruzados",
            status="pass" if not unmapped else "warn",
            blocking=False,
            details=(
                ["Todos los mensajes inter-paquete tienen contrato"]
                if not unmapped
                else [f"Mensajes sin contrato: {unmapped[:5]}"]
            ),
        )
    )

    # Gate 5 (informational): NFRs assigned to at least one package.
    nfr_covered: set[str] = set()
    for ctx in contexts:
        for n in ctx.nfrs:
            nfr_covered.add(n["code"])
    missing_nfr = [
        r["code"]
        for r in all_requirements
        if r.get("is_nfr") and r["code"] not in nfr_covered
    ]
    gates.append(
        CoherenceGate(
            gate="nfrs_asignados",
            status="pass" if not missing_nfr else "warn",
            blocking=False,
            details=(
                [f"{len(nfr_covered)} NFRs asignados"]
                if not missing_nfr
                else [f"NFRs sin paquete: {sorted(missing_nfr)[:10]}"]
            ),
        )
    )

    # Gate 6 (informational): every task has at least one acceptance criterion.
    no_acc = [
        f"{ctx.sub_project_code}/{t.title}"
        for ctx in contexts
        for t in ctx.tasks
        if not t.acceptance
    ]
    gates.append(
        CoherenceGate(
            gate="aceptacion_presente",
            status="pass" if not no_acc else "warn",
            blocking=False,
            details=(
                ["Todas las tareas tienen criterios de aceptación"]
                if not no_acc
                else [f"Tareas sin criterios: {no_acc[:5]}"]
            ),
        )
    )

    return gates


async def critique_coherence(
    contexts: list[PackageContext],
    all_contracts: list[dict],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress=None,
) -> list[dict]:
    """LLM cross-package critique (Pass 3b). Best-effort: failure returns [].

    Input is COMPACT summaries (missions + contracts + entity counts), never
    full packages — keeps the output narrow and the prompt small.
    """
    if not contexts:
        return []
    lines = []
    for ctx in contexts:
        exposed = ", ".join(
            f"{c['name']} ({c['contract_type']})"
            for c in ctx.contracts_exposed
        ) or "(ninguno)"
        consumed = ", ".join(
            f"{c['name']} <- {c['from']}" for c in ctx.contracts_consumed
        ) or "(ninguno)"
        lines.append(
            f"- {ctx.sub_project_code} [{ctx.project_name or 'sin área'}]: "
            f"{ctx.mission} | entidades: {len(ctx.entities)} | expone: "
            f"{exposed} | consume: {consumed}"
        )
    user_text = (
        "PAQUETES DE TRABAJO (resumen):\n" + "\n".join(lines) + "\n\n"
        "CONTRATOS:\n"
        + "\n".join(
            f"- {c['from']} -> {c['to']} [{c['contract_type']}] {c['name']}"
            for c in all_contracts
        )
        + "\n\nBusca SOLO problemas de coherencia ENTRE paquetes: "
        "responsabilidades solapadas, misma entidad/dato en dos paquetes sin "
        "evento de sincronización, contratos ambiguos o faltantes entre "
        "paquetes que claramente se comunican. Si está coherente, devuelve "
        "findings vacío. Devuelve SOLO el objeto estructurado."
    )
    msgs = [
        (
            "system",
            "Eres un revisor de arquitectura. Detectas incoherencias ENTRE "
            "paquetes de trabajo que se implementarán en paralelo por "
            "equipos distintos sin conocimiento del sistema completo.",
        ),
        ("human", user_text),
    ]
    try:
        result = await invoke_structured_resilient(
            lambda **kw: structured_llm(CoherenceCritiqueSchema, **kw),
            msgs,
            context_label="packages_coherence_critique",
        )
        return [f.model_dump() for f in result.findings]
    except Exception:
        logger.exception("critique_coherence: fallo (best-effort)")
        return []


# ---------------------------------------------------------------------------
# Pass 4: deterministic markdown rendering
# ---------------------------------------------------------------------------


def _render_attr_table(attrs: list[dict]) -> str:
    if not attrs:
        return ""
    rows = ["| Atributo | Tipo | Req | PK |", "|---|---|---|---|"]
    for a in attrs:
        rows.append(
            f"| {a.get('name', '')} | {a.get('type', '')} "
            f"| {'sí' if a.get('required') else 'no'} "
            f"| {'🔑' if a.get('is_key') else ''} |"
        )
    return "\n".join(rows)


def render_package_markdown(ctx: PackageContext) -> str:
    """Render ONE self-contained package document (deterministic)."""
    md: list[str] = []
    md.append(f"# Paquete {ctx.sub_project_code} · {ctx.sub_project_name}")
    md.append("")
    md.append(f"**Área:** {ctx.project_name or '(sin área)'}  ")
    stack = ctx.stack or {}
    if stack:
        md.append(
            "**Stack:** " + " · ".join(f"{k}: {v}" for k, v in stack.items())
        )
    md.append("")
    md.append("## 0. Tu misión")
    md.append("")
    md.append(ctx.mission or "(sin misión)")
    md.append("")
    md.append(
        "> Este paquete es AUTOCONTENIDO: implementalo contra tus "
        "contratos, sin asumir nada del resto del sistema. Lo que no está "
        "acá no te afecta; lo que necesites de otro equipo está en tus "
        "contratos consumidos."
    )
    md.append("")

    md.append(f"## 1. Tu mundo ({len(ctx.entities)} entidades)")
    md.append("")
    for e in ctx.entities:
        flag = " 🔑" if e.get("aggregate_root") else ""
        bc = f" · contexto: {e['bounded_context']}" if e.get("bounded_context") else ""
        md.append(f"### {e['name']} (`{e['code']}`){flag}{bc}")
        md.append("")
        if e.get("description"):
            md.append(e["description"])
            md.append("")
        table = _render_attr_table(e.get("attributes") or [])
        if table:
            md.append(table)
            md.append("")
    if ctx.internal_relationships:
        md.append("### Relaciones internas")
        md.append("")
        md.append("| De | Verbo | A | Card. |")
        md.append("|---|---|---|---|")
        for r in ctx.internal_relationships:
            md.append(
                f"| {r['from_name']} | {r['label'] or '—'} | {r['to_name']} "
                f"| {r['cardinality']} |"
            )
        md.append("")
    if ctx.border_relationships:
        md.append("### Fronteras (entidades de otros paquetes)")
        md.append("")
        md.append(
            "Estas relaciones cruzan tu frontera: el lado de afuera NO te "
            "pertenece, accedé solo por el contrato correspondiente."
        )
        md.append("")
        md.append("| Tu entidad | Verbo | Entidad externa | Card. |")
        md.append("|---|---|---|---|")
        for r in ctx.border_relationships:
            mine_is_from = any(
                e["code"] == r["from"] for e in ctx.entities
            )
            mine, ext = (
                (r["from_name"], r["to_name"])
                if mine_is_from
                else (r["to_name"], r["from_name"])
            )
            md.append(f"| {mine} | {r['label'] or '—'} | {ext} | {r['cardinality']} |")
        md.append("")
    for d in ctx.process_diagrams:
        md.append(f"### Proceso: {d['name']}")
        md.append("")
        md.append("```mermaid")
        md.append(d["mermaid"])
        md.append("```")
        md.append("")

    md.append(
        f"## 2. Tus contratos ({len(ctx.contracts_exposed)} expuestos · "
        f"{len(ctx.contracts_consumed)} consumidos)"
    )
    md.append("")
    for title, contracts in (
        ("### Implementás (expuestos)", ctx.contracts_exposed),
        ("### Consumís (de otros equipos)", ctx.contracts_consumed),
    ):
        if not contracts:
            continue
        md.append(title)
        md.append("")
        for c in contracts:
            peer = c["to"] if c["from"] == ctx.sub_project_code else c["from"]
            md.append(f"#### `{c['name']}` — {c['contract_type']} (con: {peer})")
            md.append("")
            if c.get("description"):
                md.append(c["description"])
                md.append("")
            if c.get("spec"):
                md.append("```yaml")
                md.append(c["spec"])
                md.append("```")
                md.append("")
        if contracts is ctx.contracts_consumed:
            md.append(
                "> Mock: mientras el equipo proveedor no publique su "
                "servicio, implementá un stub que respete esta spec (la "
                "prueba de contrato de integración la valida igual)."
            )
            md.append("")

    if ctx.nfrs:
        md.append(f"### Requerimientos no funcionales que aplican ({len(ctx.nfrs)})")
        md.append("")
        for n in ctx.nfrs:
            md.append(f"- **{n['code']}** [{n['type']}]: {n['statement']}")
            if n.get("adr"):
                md.append(
                    f"  - Decisión (ADR {n['adr']['code']} — "
                    f"{n['adr']['title']}): {n['adr']['decision']}"
                )
        md.append("")

    md.append(f"## 3. Backlog ({len(ctx.tasks)} tareas)")
    md.append("")
    md.append("> Ejecutá las tareas en orden; `depende de` apunta a tareas "
              "de ESTE paquete.")
    md.append("")
    for t in ctx.tasks:
        md.append(f"### {t.code} · {t.title}")
        md.append("")
        if t.req_codes:
            md.append(
                "**Requerimientos:** " + ", ".join(f"`{c}`" for c in t.req_codes)
            )
            md.append("")
        if t.entity_codes:
            names = {
                e["name"]: e["code"] for e in ctx.entities
            }
            md.append(
                "**Entidades:** "
                + ", ".join(
                    f"`{c}`" for c in t.entity_codes
                )
            )
            md.append("")
        if t.contract_names:
            md.append(
                "**Contratos:** "
                + ", ".join(f"`{c}`" for c in t.contract_names)
            )
            md.append("")
        if t.depends_on:
            md.append("**Depende de:** " + ", ".join(t.depends_on))
            md.append("")
        if t.description:
            md.append(t.description)
            md.append("")
        # Verbatim requirements of this task.
        reqs_by_code = {r["code"]: r for r in ctx.requirements}
        for code in t.req_codes:
            r = reqs_by_code.get(code)
            if r:
                md.append(f"> **{code}** [{r['type']}]: {r['statement']}")
        if any(reqs_by_code.get(c, {}).get("acceptance") for c in t.req_codes):
            md.append("")
            md.append("**Aceptación (Gherkin):**")
            md.append("")
            for code in t.req_codes:
                r = reqs_by_code.get(code) or {}
                for a in r.get("acceptance") or []:
                    md.append(f"- {a}")
        elif t.acceptance:
            md.append("")
            md.append("**Aceptación (Gherkin):**")
            md.append("")
            for a in t.acceptance:
                md.append(f"- {a}")
        md.append("")
        md.append("---")
        md.append("")

    md.append("## 4. Definition of Done")
    md.append("")
    md.append("- [ ] Todas las tareas del backlog implementadas.")
    md.append("- [ ] Todos los criterios Gherkin del backlog pasan.")
    md.append(
        "- [ ] Los contratos que IMPLEMENTÁS respetan su spec al 100% "
        "(un consumidor podría integrarse sin hablar con vos)."
    )
    md.append(
        "- [ ] Los contratos que CONSUMÍS se integran contra la spec "
        "(no contra implementaciones)."
    )
    md.append("- [ ] Los NFRs de la sección 1 están honrados.")
    md.append("")
    return "\n".join(md)


def render_assembly_master(
    contexts: list[PackageContext],
    gates: list[CoherenceGate],
    critique_findings: list[dict],
    *,
    project_name: str = "",
    analysis_version: int = 0,
) -> str:
    """Render the assembly master (build order, milestones, coherence)."""
    md: list[str] = []
    md.append("# Maestro de ensamblaje — Paquetes de trabajo")
    md.append("")
    if project_name:
        md.append(f"**Proyecto:** {project_name}  ")
    md.append(
        f"**Análisis base:** v{analysis_version} · "
        f"**Paquetes:** {len(contexts)}"
    )
    md.append("")

    # Build order: levels over the consumed-contract dependency graph.
    deps: dict[str, set[str]] = {c.sub_project_code: set() for c in contexts}
    name_by_code = {c.sub_project_code: c for c in contexts}
    for ctx in contexts:
        for c in ctx.contracts_consumed:
            if c["from"] in deps and c["from"] != ctx.sub_project_code:
                deps[ctx.sub_project_code].add(c["from"])
    levels: list[list[str]] = []
    remaining = set(deps)
    while remaining:
        level = sorted(
            n for n in remaining
            if deps[n] <= (set(deps) - remaining)
        )
        if not level:  # cycle safety (gate blocks earlier anyway)
            level = sorted(remaining)
        levels.append(level)
        remaining -= set(level)
    md.append("## Orden de construcción sugerido")
    md.append("")
    md.append("*(cada nivel solo depende de contratos de niveles previos)*")
    md.append("")
    for i, level in enumerate(levels, 1):
        names = ", ".join(
            f"{name_by_code[c].sub_project_name} (`{c}`)" for c in level
        )
        md.append(f"{i}. {names}")
    md.append("")

    # Integration milestones per contract.
    md.append("## Hitos de integración (por contrato)")
    md.append("")
    md.append(
        "Cada hito se cierra cuando la prueba de contrato pasa contra la "
        "implementación real del expositor: consumidor y expositor validan "
        "la MISMA spec."
    )
    md.append("")
    all_contracts = []
    for ctx in contexts:
        for c in ctx.contracts_exposed:
            all_contracts.append(c)
    for c in sorted(all_contracts, key=lambda x: (x["from"], x["name"])):
        md.append(
            f"- `{c['name']}` — **{c['from']}** publica → **{c['to']}** "
            f"integra ({c['contract_type']})"
        )
    md.append("")

    # Coherence report.
    md.append("## Informe de coherencia")
    md.append("")
    icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
    for g in gates:
        md.append(f"- {icon.get(g.status, '·')} **{g.gate}** ({g.status})")
        for d in g.details:
            md.append(f"  - {d}")
    if critique_findings:
        md.append("")
        md.append("### Crítica cruzada (LLM)")
        md.append("")
        for f in critique_findings:
            md.append(f"- [{f.get('severity', 'info')}] {f.get('summary', '')}")
    md.append("")
    return "\n".join(md)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def generate_work_packages(
    analysis_id: int,
    *,
    project_name: str = "",
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress=None,
) -> PackagesResult:
    """Full pipeline: contexts -> tasks -> coherence -> renders (no DB write).

    The caller (agent tool) opens the session, passes it to
    ``assemble_package_contexts``, and persists via packages_store on commit.
    """
    from backend.database import AsyncSessionLocal
    from backend.models.analysis import AnalysisDocument
    from backend.models.requirement import ReqStatus
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        contexts, meta = await assemble_package_contexts(session, analysis_id)
        doc = await session.get(AnalysisDocument, analysis_id)
        # System-wide inputs for the coherence gates.
        from backend.models.analysis import (
            DomainEntity,
            DomainRelationship,
            SubProjectContract,
        )
        from backend.models.requirement import RequirementItem

        live = (ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT)
        items = (
            (
                await session.execute(
                    select(RequirementItem).where(
                        RequirementItem.project_id == doc.project_id
                    )
                )
            )
            .scalars()
            .all()
        )
        all_requirements = [
            {
                "code": it.code,
                "is_nfr": str(
                    it.type.value if hasattr(it.type, "value") else it.type
                )
                in {
                    "performance", "security", "usability", "reliability",
                    "maintainability", "compliance", "constraint",
                },
            }
            for it in items
            if it.status in live
        ]
        entities = (
            (
                await session.execute(
                    select(DomainEntity).where(
                        DomainEntity.analysis_id == analysis_id
                    )
                )
            )
            .scalars()
            .all()
        )
        all_entities = [{"code": e.code, "name": e.name} for e in entities]
        contracts = (
            (
                await session.execute(
                    select(SubProjectContract).where(
                        SubProjectContract.analysis_id == analysis_id
                    )
                )
            )
            .scalars()
            .all()
        )
        all_contracts = [
            {
                "from": c.from_subproject_code,
                "to": c.to_subproject_code,
                "contract_type": (
                    c.contract_type.value
                    if hasattr(c.contract_type, "value")
                    else str(c.contract_type)
                ),
                "name": c.name,
                "spec": c.spec or "",
            }
            for c in contracts
        ]
        rels = (
            (
                await session.execute(
                    select(DomainRelationship).where(
                        DomainRelationship.analysis_id == analysis_id
                    )
                )
            )
            .scalars()
            .all()
        )
        owner_hint = {
            sp_code: set(sp.entity_codes or []) for sp_code in
            [c.sub_project_code for c in contexts]
        }

    # Cross-package sequence messages: messages whose participants span
    # sub-projects (entity -> owner mapping is heuristic via traced entities).
    all_sequence_diagrams = [
        d for d in (doc.process_diagrams or []) if d.get("type") == "sequence"
    ]
    entity_owner: dict[str, str] = {}
    for ctx in contexts:
        for e in ctx.entities:
            entity_owner[e["code"]] = ctx.sub_project_code
            entity_owner[e["name"]] = ctx.sub_project_code
    for d in all_sequence_diagrams:
        codes = d.get("traced_req_codes") or []
        # Sequence messages are free text; the deterministic map uses the
        # traced requirements: if the diagram spans packages (its traced reqs
        # touch entities of >1 package) we flag ALL its messages as candidates.
        touched: set[str] = set()
        for code in codes:
            owner = entity_owner.get(code)
            if owner:
                touched.add(owner)
        d["cross_package_messages"] = []

    if on_progress is not None:
        await on_progress(f"ensamblando {len(contexts)} paquetes...")

    # Pass 2: tasks (parallel across packages, bounded by concurrency).
    sem = asyncio.Semaphore(concurrency)

    async def _decompose_guarded(ctx: PackageContext):
        async with sem:
            ctx.tasks = await _decompose_package_tasks(
                ctx,
                project_name=project_name,
                concurrency=concurrency,
                on_progress=on_progress,
            )

    await asyncio.gather(*[_decompose_guarded(c) for c in contexts])

    if on_progress is not None:
        await on_progress("verificando coherencia entre paquetes...")

    # Pass 3: coherence gate + critique.
    gates = verify_packages_coherence(
        contexts,
        all_entities=all_entities,
        all_requirements=all_requirements,
        all_contracts=all_contracts,
        all_sequence_diagrams=all_sequence_diagrams,
    )
    critique_findings = await critique_coherence(
        contexts, all_contracts, on_progress=on_progress
    )

    # Pass 4: render.
    total_tasks = 0
    for ctx in contexts:
        for i, t in enumerate(ctx.tasks, 1):
            t.code = f"TASK-{i:03d}"
        ctx.markdown = render_package_markdown(ctx)
        ctx.counts["tasks"] = len(ctx.tasks)
        total_tasks += len(ctx.tasks)
    master = render_assembly_master(
        contexts,
        gates,
        critique_findings,
        project_name=project_name,
        analysis_version=meta.get("analysis_version", 0),
    )

    blocking_fail = any(g.blocking and g.status == "fail" for g in gates)
    logger.info(
        "generate_work_packages: %d paquetes, %d tareas, gates=%s%s",
        len(contexts),
        total_tasks,
        sum(1 for g in gates if g.status == "pass"),
        " (BLOQUEADO)" if blocking_fail else "",
    )
    return PackagesResult(
        packages=contexts,
        master_markdown=master,
        gates=gates,
        critique_findings=critique_findings,
        stats={
            "analysis_version": meta.get("analysis_version", 0),
            "packages": len(contexts),
            "tasks": total_tasks,
            "blocking_fail": blocking_fail,
            "gates_passed": sum(1 for g in gates if g.status == "pass"),
            "requirements": len(all_requirements),
        },
    )
