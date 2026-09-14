"""LangChain tools that expose the requirement store to the capture subagent.

The pipeline writes rows (requirements_service._persist); these tools are the
editing surface the subagent (and, through chat, the human) uses afterwards:
add, update, delete, merge, split, link, resolve, list, get, approve, reject,
and attach acceptance criteria. Each maps 1:1 to a store function.

Design:
- Factory `make_requirements_tools(project_id)` closes over the project_id.
  The agent never sees project_id as an argument (it cannot spoof another
  project's rows). build_agent builds one set per request, like the agent.
- Each tool opens its own AsyncSessionLocal, calls the store, returns a plain
  dict. Errors become {"error": ...} dicts so the model sees structured
  feedback instead of a crashed tool call.
- Tools speak `code` (the human-visible "REQ-XXXX" handle), not the integer id:
  the model reads codes from list output and references them. Code->id
  resolution lives here (adapter concern), the store stays id-centric.
"""
from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.pipelines._quality_rules import (
    DETERMINISTIC_RULE_IDS,
    detect_actor,
    programmatic_findings_for_text,
)
from backend.database import AsyncSessionLocal
from backend.models.requirement import (
    Priority,
    RelationKind,
    RelationStatus,
    ReqStatus,
    ReqType,
    RequirementItem,
    RequirementRevision,
)
from backend.models.srs import FindingStatus, RequirementFinding
from backend.services import actor_store, requirement_store as store

# Lotes máximos por llamada: el antídoto del N+1 (la sesión 8 de Planitrack
# hizo 806 get_requirement + 296 update_requirement item-por-item).
MAX_BATCH_GET = 100
MAX_BULK_UPDATE = 50
DEFAULT_LIST_LIMIT = 100

ReqTypeValue = Literal[
    "functional", "performance", "security", "usability",
    "reliability", "maintainability", "compliance",
    "constraint", "process", "data",
]
PriorityValue = Literal["must", "should", "could", "wont"]
StatusValue = Literal[
    "unverified", "draft", "validated",
    "approved", "rejected", "merged", "superseded",
]
RelationKindValue = Literal["duplicate", "contradicts", "depends_on"]


# --- code <-> id resolution (adapter concern) -------------------------------


async def _code_to_id(
    session: AsyncSession, project_id: int, code: str
) -> int:
    """Resolve a REQ-XXXX code to its integer id within the project."""
    item_id = await session.scalar(
        select(RequirementItem.id).where(
            RequirementItem.project_id == project_id,
            RequirementItem.code == code,
        )
    )
    if item_id is None:
        raise KeyError(f"requirement {code} not found in project")
    return item_id


async def _code_to_goal_id(
    session: AsyncSession, project_id: int, code: str
) -> int:
    """Resolve a GOAL-XXXX code to its integer id within the project."""
    from backend.models.srs import Goal

    goal_id = await session.scalar(
        select(Goal.id).where(
            Goal.project_id == project_id,
            Goal.code == code,
        )
    )
    if goal_id is None:
        raise KeyError(f"goal {code} not found in project")
    return goal_id


def _source_documents(item: RequirementItem) -> list[str]:
    """Document ids que citan este item, deduplicados (source puede ser dict
    o lista de dicts tras un merge). Se omiten quotes/secciones a propósito:
    el listado tiene que seguir siendo compacto."""
    docs = {
        (src.get("document_id") or "").strip()
        for src in store._source_list(item)
    }
    docs.discard("")
    return sorted(docs)


def _item_in_document(item: RequirementItem, document: str) -> bool:
    """Match por substring (case-insensitive) sobre los documentos fuente del
    item — misma semántica que el filtro documents de un /agrupar scopeado."""
    needle = document.strip().lower()
    if not needle:
        return True
    return any(
        needle in (src.get("document_id") or "").lower()
        for src in store._source_list(item)
    )


async def _parent_code_map(
    session: AsyncSession, project_id: int, items: list[RequirementItem]
) -> dict[int, str]:
    """id -> code de los padres referenciados por ``items``, en UNA query,
    para que el listado muestre parent_code sin lookups por item."""
    parent_ids = {it.parent_id for it in items if it.parent_id is not None}
    if not parent_ids:
        return {}
    rows = await session.execute(
        select(RequirementItem.id, RequirementItem.code).where(
            RequirementItem.project_id == project_id,
            RequirementItem.id.in_(parent_ids),
        )
    )
    return {row_id: code for row_id, code in rows.all()}


async def _catalog_role_terms(
    session: AsyncSession, project_id: int
) -> list[str] | None:
    """Léxico de roles del catálogo ProjectActor (best-effort, None si vacío).

    Detect_actor y los pre-checks lo suman a su léxico base: un enunciado
    que arranca con un rol del catálogo cuenta como actor nombrado aunque el
    texto también contenga «usuario».
    """
    try:
        terms = await actor_store.actor_role_terms(session, project_id)
    except Exception:  # noqa: BLE001
        return None
    return terms or None


# Enunciado acotado en los summaries de listado (la herramienta reporta
# statement_chars cuando trunca): con enunciados de varios KB, una página de
# 100 items pesaba cientos de KB en el thread del agente (sesión 18: write
# de 471 KB). El texto completo sigue en get_requirement.
SUMMARY_STATEMENT_CHARS = 500


def _item_summary(
    item: RequirementItem,
    parent_codes: dict[int, str] | None = None,
    role_terms: list[str] | None = None,
) -> dict[str, Any]:
    statement = item.statement or ""
    out = {
        "id": item.id,
        "code": item.code,
        "type": item.type.value,
        "priority": item.priority.value,
        "status": item.status.value,
        "span_verified": item.span_verified,
        # Trazabilidad compacta: sin estos campos el agente terminaba en un
        # get_requirement por item (N+1) para preguntas de fuente/jerarquía.
        "source_documents": _source_documents(item),
        "parent_code": (parent_codes or {}).get(item.parent_id),
        "derived": bool(item.derived),
        "confidence": item.confidence,
        # named (rol específico) | generic («el usuario») | none («el sistema»
        # o nada): permite al agente ubicar y corregir los actores en lote.
        "actor": detect_actor(item.statement, role_terms=role_terms),
    }
    if len(statement) > SUMMARY_STATEMENT_CHARS:
        out["statement"] = statement[:SUMMARY_STATEMENT_CHARS]
        out["statement_chars"] = len(statement)
    else:
        out["statement"] = statement
    return out


def make_requirements_read_tools(project_id: int) -> list:
    """Build the read-only tool subset (list / get / build_srs / capture_status).

    Registered with the orchestrator so it can answer store queries directly,
    without delegating to the capture subagent. ``project_id`` is closed over
    (same isolation contract as the editing tools); no mutation tool is exposed,
    so the orchestrator cannot change the store.

    The read tool bodies are duplicated from ``make_requirements_tools`` on
    purpose: the editing factory interleaves reads with mutations in its return
    list, and restructuring it risks the mutation tools. If you change a read
    tool here, change its twin there too (including capture_status).
    """

    @tool
    async def list_requirements(
        status: StatusValue | None = None,
        type: ReqTypeValue | None = None,
        priority: PriorityValue | None = None,
        document: str | None = None,
        query: str | None = None,
        has_actor: bool | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        include_deleted: bool = False,
    ) -> dict:
        """List requirements in the project, optionally filtered.

        Soft-deleted rows (rejected / merged / superseded) are hidden unless
        include_deleted is true. `document` filters by source document
        (substring, case-insensitive). `query` filters by statement substring
        (case-insensitive). `has_actor` keeps only items whose statement names
        a specific role (true), only items without any actor or with a bare
        «usuario» (false), or all (unset). `limit` caps the returned items
        while `count` keeps the filter total: when truncated is true, refine
        the filters instead of paging blindly. Each summary carries code,
        statement, type, priority, status, actor (named/generic/none),
        source_documents, parent_code/derived and confidence — traceability
        questions do NOT require one get_requirement per item. The statement
        is capped at 500 chars (statement_chars carries the real length when
        truncated): for full text use get_requirement on that code.
        """
        try:
            async with AsyncSessionLocal() as session:
                items = await store.list_requirements(
                    session,
                    project_id,
                    status=ReqStatus(status) if status else None,
                    type=ReqType(type) if type else None,
                    priority=Priority(priority) if priority else None,
                    include_deleted=include_deleted,
                )
                if document:
                    items = [
                        it for it in items if _item_in_document(it, document)
                    ]
                if query:
                    needle = query.strip().lower()
                    items = [
                        it for it in items
                        if needle in (it.statement or "").lower()
                    ]
                role_terms = await _catalog_role_terms(session, project_id)
                if has_actor is not None:
                    # true = solo los que nombran un rol específico; false =
                    # la cola de trabajo (actor genérico o ausente).
                    items = [
                        it
                        for it in items
                        if (
                            detect_actor(it.statement, role_terms=role_terms)
                            == "named"
                        )
                        == has_actor
                    ]
                total = len(items)
                truncated = total > limit
                items = items[:limit]
                parents = await _parent_code_map(session, project_id, items)
                return {
                    "count": total,
                    "truncated": truncated,
                    "items": [
                        _item_summary(it, parents, role_terms=role_terms)
                        for it in items
                    ],
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"list_requirements failed: {exc}"}

    @tool
    async def get_requirement(
        code: str, include_revisions: bool = False
    ) -> dict:
        """Full detail of one requirement: fields, relations, parent, source.

        The revision history is the heaviest part of the payload: it is only
        included when include_revisions is true. Use this for ONE item that
        needs deep inspection — for overviews and traceability use
        list_requirements (its summaries already carry source and hierarchy).
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                detail = await store.get_requirement(session, req_id)
                if not include_revisions:
                    detail.pop("revisions", None)
                return detail
        except Exception as exc:  # noqa: BLE001
            return {"error": f"get_requirement failed: {exc}"}

    @tool
    async def get_requirements(
        codes: list[str], include_revisions: bool = False
    ) -> dict:
        """Batch detail of several requirements in ONE call.

        Returns the same summary shape as list_requirements (statement, type,
        priority, status, actor, source_documents, parent_code/derived) plus
        acceptance criteria; unknown codes come back in `not_found`. The batch
        is capped at 100 codes — for anything bigger, filter with
        list_requirements first. Use this whenever more than one requirement
        needs inspection; set include_revisions only when the change history
        is actually needed (compact, without snapshots).
        """
        try:
            if not codes:
                return {"items": [], "not_found": []}
            if len(codes) > MAX_BATCH_GET:
                return {
                    "error": (
                        f"máximo {MAX_BATCH_GET} códigos por llamada "
                        f"(llegaron {len(codes)}): acotar con "
                        "list_requirements antes"
                    )
                }
            async with AsyncSessionLocal() as session:
                rows = (
                    await session.scalars(
                        select(RequirementItem).where(
                            RequirementItem.project_id == project_id,
                            RequirementItem.code.in_(codes),
                        )
                    )
                ).all()
                by_code = {it.code: it for it in rows}
                parents = await _parent_code_map(
                    session, project_id, list(by_code.values())
                )
                items = [
                    {
                        **_item_summary(it, parents),
                        "acceptance_criteria": list(
                            it.acceptance_criteria or []
                        ),
                    }
                    for it in (by_code[c] for c in codes if c in by_code)
                ]
                if include_revisions and by_code:
                    revs = (
                        await session.scalars(
                            select(RequirementRevision)
                            .where(
                                RequirementRevision.req_id.in_(
                                    [it.id for it in by_code.values()]
                                )
                            )
                            .order_by(
                                RequirementRevision.req_id,
                                RequirementRevision.version,
                            )
                        )
                    ).all()
                    by_req: dict[int, list[dict[str, Any]]] = {}
                    for rev in revs:
                        by_req.setdefault(rev.req_id, []).append(
                            {
                                "version": rev.version,
                                "changed_by": rev.changed_by,
                                "change_reason": rev.change_reason,
                            }
                        )
                    for entry in items:
                        entry["revisions"] = by_req.get(entry["id"], [])
                return {
                    "items": items,
                    "not_found": [c for c in codes if c not in by_code],
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"get_requirements failed: {exc}"}

    @tool
    async def build_srs() -> dict:
        """Generate the SRS Markdown from the current requirement store.

        The SRS is built deterministically from RequirementItem rows (never
        hand-written). Returns the markdown text plus a counts summary so you
        can paste an excerpt to the user and offer the document for download.

        Call this after capture + validation, when the user asks for the SRS,
        the requirements matrix, or the deliverable document.
        """
        try:
            async with AsyncSessionLocal() as session:
                from backend.services.srs_builder import build_srs as _build
                from backend.models import Project as _Project
                proj = await session.get(_Project, project_id)
                name = getattr(proj, "name", "") if proj else ""
                desc = getattr(proj, "description", "") if proj else ""
                result = await _build(
                    session,
                    project_id,
                    project_name=name,
                    project_description=desc,
                )
                return {
                    "markdown": result["markdown"],
                    "generated_at": result["generated_at"],
                    "counts": result["counts"],
                    # Preview: primer KB para que el modelo pueda citar sin
                    # volcar el markdown completo en el contexto.
                    "preview": result["markdown"][:1024],
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"build_srs failed: {exc}"}

    @tool
    async def capture_status() -> dict:
        """Census of THIS project: requirement + grouping plan counts, last
        code, and by_document — how many LIVE (non soft-deleted) requirements
        cite each source document. A post-merge item can cite several
        documents (each one counts); items without source fall under
        "(sin fuente)". This is the compact scoping view: use it instead of
        reading documents or counting listings by hand.

        Call this BEFORE a capture. If requirements > 0, tell the
        user how many exist and the last code, then ask whether to reset
        everything or append. Never reset without explicit user confirmation.
        """
        try:
            async with AsyncSessionLocal() as session:
                from sqlalchemy import func, select
                from backend.models.requirement import (
                    GroupingPlan,
                    RequirementItem,
                )
                req_count = await session.scalar(
                    select(func.count())
                    .select_from(RequirementItem)
                    .where(RequirementItem.project_id == project_id)
                )
                plan_count = await session.scalar(
                    select(func.count())
                    .select_from(GroupingPlan)
                    .where(GroupingPlan.project_id == project_id)
                )
                last_code = await session.scalar(
                    select(RequirementItem.code)
                    .where(RequirementItem.project_id == project_id)
                    .order_by(RequirementItem.id.desc())
                    .limit(1)
                )
                # GROUP BY documento, empaquetado: conteos por fuente sobre los
                # items vivos, en una sola llamada diminuta (la alternativa del
                # agente era volcar 541 statements y contarlos por contexto).
                live_items = await store.list_requirements(session, project_id)
                doc_counts: dict[str, int] = {}
                for it in live_items:
                    docs = _source_documents(it)
                    if docs:
                        for doc in docs:
                            doc_counts[doc] = doc_counts.get(doc, 0) + 1
                    else:
                        doc_counts["(sin fuente)"] = (
                            doc_counts.get("(sin fuente)", 0) + 1
                        )
                by_document = [
                    {"document": doc, "count": n}
                    for doc, n in sorted(
                        doc_counts.items(), key=lambda kv: (-kv[1], kv[0])
                    )
                ]
                return {
                    "requirements": int(req_count or 0),
                    "grouping_plans": int(plan_count or 0),
                    "last_code": last_code,
                    "by_document": by_document,
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"capture_status failed: {exc}"}

    return [
        list_requirements,
        get_requirements,
        get_requirement,
        build_srs,
        capture_status,
    ]


def make_requirements_tools(project_id: int) -> list:
    """Build the editing tool set bound to one project.

    Returned tools close over `project_id`; each opens a fresh session, so they
    are safe to call in any order from the agent loop.
    """

    @tool
    async def add_requirement(
        statement: str,
        type: ReqTypeValue = "functional",
        priority: PriorityValue = "must",
        source_quote: str | None = None,
        source_section: str | None = None,
        source_page: int | None = None,
    ) -> dict:
        """Create a new DRAFT requirement with a fresh opaque code.

        Use this to record a requirement that the pipeline missed or that a
        human contributes during review. If source_quote is provided it becomes
        the traceability anchor (source span); otherwise the item has no source
        and stays unverified until validated.
        """
        source: dict[str, Any] | None = None
        if source_quote:
            source = {
                "quote": source_quote,
                "section": source_section,
                "page": source_page,
            }
        try:
            async with AsyncSessionLocal() as session:
                item = await store.add_requirement(
                    session,
                    project_id,
                    statement=statement,
                    type=ReqType(type),
                    priority=Priority(priority),
                    source=source,
                    explicit=source is None,
                    created_by="agent",
                )
                return _item_summary(item)
        except Exception as exc:  # noqa: BLE001 — surface to the model
            return {"error": f"add_requirement failed: {exc}"}

    @tool
    async def update_requirement(
        code: str,
        statement: str | None = None,
        type: ReqTypeValue | None = None,
        priority: PriorityValue | None = None,
        reason: str = "update",
    ) -> dict:
        """Edit a requirement's statement, type, or priority.

        Only the fields you pass are changed. Status, source, and pointers are
        not editable here — use approve / reject / merge / split for those so
        each transition is auditable on its own.
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                item = await store.update_requirement(
                    session,
                    req_id,
                    statement=statement,
                    type=ReqType(type) if type else None,
                    priority=Priority(priority) if priority else None,
                    reason=reason,
                    changed_by="agent",
                )
                return _item_summary(item)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"update_requirement failed: {exc}"}

    @tool
    async def update_requirements(updates: list[dict]) -> dict:
        """Edit several requirements in ONE call (statement / type / priority).

        Each entry: {code, statement?, type?, priority?, reason?}; only the
        passed fields change, one audit revision is appended per edited item,
        and the whole batch commits in a single transaction — always prefer
        this over looping update_requirement one code at a time. Entries with
        an invalid value or an unknown code do NOT abort the batch: they come
        back in `errors`. When a statement changes, OPEN deterministic
        findings that no longer apply to the new text (pronoun, vague term,
        combinator, missing actor, ...) are closed automatically; LLM-only
        findings stay open for the quality review to decide. Batch cap: 50.
        """
        try:
            if not updates:
                return {"updated": [], "errors": [], "findings_closed": 0}
            if len(updates) > MAX_BULK_UPDATE:
                return {
                    "error": (
                        f"máximo {MAX_BULK_UPDATE} ediciones por llamada "
                        f"(llegaron {len(updates)}): dividir en tandas"
                    )
                }
            errors: list[dict[str, Any]] = []
            resolved: list[dict[str, Any]] = []
            async with AsyncSessionLocal() as session:
                codes = [u.get("code") for u in updates if u.get("code")]
                rows = (
                    await session.scalars(
                        select(RequirementItem).where(
                            RequirementItem.project_id == project_id,
                            RequirementItem.code.in_(codes),
                        )
                    )
                ).all()
                by_code = {it.code: it for it in rows}
                # Fase 1: resolver y validar TODO antes de tocar nada.
                for u in updates:
                    code = u.get("code")
                    item = by_code.get(code)
                    if item is None:
                        errors.append(
                            {
                                "code": code,
                                "error": (
                                    f"requirement {code} not found in project"
                                ),
                            }
                        )
                        continue
                    try:
                        new_type = (
                            ReqType(u["type"]) if u.get("type") else None
                        )
                    except ValueError:
                        errors.append(
                            {
                                "code": code,
                                "error": f"type invalido: {u['type']!r}",
                            }
                        )
                        continue
                    try:
                        new_priority = (
                            Priority(u["priority"])
                            if u.get("priority")
                            else None
                        )
                    except ValueError:
                        errors.append(
                            {
                                "code": code,
                                "error": f"priority invalido: {u['priority']!r}",
                            }
                        )
                        continue
                    entry: dict[str, Any] = {
                        "req_id": item.id,
                        "code": code,
                        "reason": u.get("reason") or "bulk_update",
                    }
                    if u.get("statement") is not None:
                        entry["statement"] = str(u["statement"])
                    if new_type is not None:
                        entry["type"] = new_type
                    if new_priority is not None:
                        entry["priority"] = new_priority
                    resolved.append(entry)

                # Fase 2: una transacción para todo el lote.
                result = await store.update_requirements_bulk(
                    session, project_id, resolved, changed_by="agent"
                )

                # Fase 3: autocierre determinista. Re-evaluar las reglas sobre
                # el enunciado NUEVO y cerrar los OPEN cuya regla determinista
                # dejó de disparar. Los hallazgos que solo puede juzgar el LLM
                # (o el humano) no se tocan.
                findings_closed = 0
                llm_findings_pending: list[dict[str, str]] = []
                role_terms = await _catalog_role_terms(session, project_id)
                for entry, res in zip(resolved, result["updated"]):
                    if entry.get("statement") is None:
                        continue
                    firing = {
                        f.rule_id
                        for f in programmatic_findings_for_text(
                            res["statement"],
                            req_type=res["type"],
                            role_terms=role_terms,
                        )
                    }
                    open_findings = (
                        await session.scalars(
                            select(RequirementFinding).where(
                                RequirementFinding.project_id == project_id,
                                RequirementFinding.req_id == res["req_id"],
                                RequirementFinding.status
                                == FindingStatus.OPEN,
                            )
                        )
                    ).all()
                    for f in open_findings:
                        if (
                            f.rule_id in DETERMINISTIC_RULE_IDS
                            and f.rule_id not in firing
                        ):
                            f.status = FindingStatus.FIXED
                            findings_closed += 1
                        elif f.rule_id not in DETERMINISTIC_RULE_IDS:
                            # Hallazgo de juez LLM sobre el texto re-escrito:
                            # su veredicto quedó obsoleto y NO hay forma de
                            # cerrarlo sin re-juzgar. Contarlo en el retorno
                            # para que el agente reporte la deuda y no crea
                            # que la cura "limpió" el ítem (sesión 18: la
                            # cura del capture-agent dejó los sem.* sin
                            # veredicto y tuvo que inferirlos de revisiones).
                            llm_findings_pending.append(
                                {"code": res.get("code"), "rule_id": f.rule_id}
                            )
                if findings_closed:
                    await session.commit()

                errors.extend(result["errors"])
                return {
                    "updated": result["updated"],
                    "errors": errors,
                    "findings_closed": findings_closed,
                    "llm_findings_pending": llm_findings_pending,
                    "message": (
                        f"{len(llm_findings_pending)} hallazgo(s) de juez LLM "
                        "quedan SIN veredicto sobre los enunciados editados: "
                        "el veredicto llega con el re-análisis delta (/srs -> "
                        "analyze_quality o close_curation_wave)."
                    )
                    if llm_findings_pending
                    else "",
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"update_requirements failed: {exc}"}

    @tool
    async def delete_requirement(code: str, reason: str) -> dict:
        """Soft-delete a requirement (status -> REJECTED).

        The row is kept for audit; it disappears from the default list view but
        stays in history. `reason` is required and recorded.
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                item = await store.delete_requirement(
                    session, req_id, reason=reason, changed_by="agent"
                )
                return _item_summary(item)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"delete_requirement failed: {exc}"}

    @tool
    async def merge_requirements(
        codes: list[str],
        keep_statement: str | None = None,
        reason: str = "merge_duplicates",
    ) -> dict:
        """Fold duplicate requirements into one.

        The first code in the list is the keeper (unless it is rejected, in
        which case pick a live one). Sources from every duplicate are UNIONED
        onto the keeper (traceability to all originals is preserved); the rest
        become MERGED with a pointer to the keeper.
        """
        try:
            async with AsyncSessionLocal() as session:
                ids = [
                    await _code_to_id(session, project_id, c) for c in codes
                ]
                kept = await store.merge_requirements(
                    session,
                    ids,
                    keep_statement=keep_statement,
                    reason=reason,
                    changed_by="agent",
                )
                return {
                    "kept": _item_summary(kept),
                    "merged_count": len(ids) - 1,
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"merge_requirements failed: {exc}"}

    @tool
    async def split_requirement(
        code: str, parts: list[str], reason: str = "split_non_atomic"
    ) -> dict:
        """Split a non-atomic requirement into operational sub-requirements.

        The original becomes SUPERSEDED; each part is a new DRAFT with parent_id
        pointing back to the original, inheriting its source span.
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                created = await store.split_requirement(
                    session, req_id, parts, reason=reason, changed_by="agent"
                )
                return {
                    "new_codes": [_item_summary(c) for c in created],
                    "count": len(created),
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"split_requirement failed: {exc}"}

    @tool
    async def link_requirements(
        code: str,
        other_code: str,
        relation: RelationKindValue,
        note: str | None = None,
    ) -> dict:
        """Flag a relationship between two requirements.

        relation is one of: duplicate, contradicts, depends_on. The pair is
        recorded as PROPOSED for later resolution; nothing is deleted.
        """
        try:
            async with AsyncSessionLocal() as session:
                from_id = await _code_to_id(session, project_id, code)
                to_id = await _code_to_id(session, project_id, other_code)
                rel = await store.link_requirements(
                    session,
                    from_id,
                    to_id,
                    RelationKind(relation),
                    note=note,
                    detected_by="agent",
                )
                return {
                    "relation_id": rel.id,
                    "from": code,
                    "to": other_code,
                    "kind": rel.kind.value,
                    "status": rel.status.value,
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"link_requirements failed: {exc}"}

    @tool
    async def resolve_conflict(
        relation_id: int, winner_code: str, note: str | None = None
    ) -> dict:
        """Resolve a contradiction by recording the winning requirement.

        The relation moves to RESOLVED with the winner noted. The loser is NOT
        auto-rejected — rejecting it is a separate explicit action.
        """
        try:
            async with AsyncSessionLocal() as session:
                winner_id = await _code_to_id(
                    session, project_id, winner_code
                )
                rel = await store.resolve_conflict(
                    session,
                    relation_id,
                    winner_id=winner_id,
                    note=note,
                    changed_by="agent",
                )
                return {
                    "relation_id": rel.id,
                    "status": rel.status.value,
                    "note": rel.note,
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"resolve_conflict failed: {exc}"}

    @tool
    async def set_relation_status(
        relation_id: int, status: str, note: str | None = None
    ) -> dict:
        """Confirm a documented relation (or re-open it) — no winner semantics.

        status is 'confirmed' (the human approved a documented link:
        depends_on, duplicate) or 'proposed' (re-open for review). The
        original note is PRESERVED — the optional note is appended, never
        overwritten. 'resolved' is NOT accepted here: it belongs to
        resolve_conflict, which records a winner (contradiction semantics
        that would distort the audit of a depends_on link).
        """
        if status not in ("confirmed", "proposed"):
            return {
                "error": (
                    f"status invalido: {status!r} (usá confirmed | proposed; "
                    "resolved es exclusivo de resolve_conflict)"
                )
            }
        try:
            async with AsyncSessionLocal() as session:
                rel = await store.set_relation_status(
                    session,
                    relation_id,
                    RelationStatus(status),
                    note=note,
                )
                return {
                    "relation_id": rel.id,
                    "status": rel.status.value,
                    "note": rel.note,
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"set_relation_status failed: {exc}"}

    @tool
    async def list_requirements(
        status: StatusValue | None = None,
        type: ReqTypeValue | None = None,
        priority: PriorityValue | None = None,
        document: str | None = None,
        query: str | None = None,
        has_actor: bool | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        include_deleted: bool = False,
    ) -> dict:
        """List requirements in the project, optionally filtered.

        Soft-deleted rows (rejected / merged / superseded) are hidden unless
        include_deleted is true. `document` filters by source document
        (substring, case-insensitive). `query` filters by statement substring
        (case-insensitive). `has_actor` keeps only items whose statement names
        a specific role (true), only items without any actor or with a bare
        «usuario» (false), or all (unset). `limit` caps the returned items
        while `count` keeps the filter total: when truncated is true, refine
        the filters instead of paging blindly. Each summary carries code,
        statement, type, priority, status, actor (named/generic/none),
        source_documents, parent_code/derived and confidence — traceability
        questions do NOT require one get_requirement per item. The statement
        is capped at 500 chars (statement_chars carries the real length when
        truncated): for full text use get_requirement on that code.
        """
        try:
            async with AsyncSessionLocal() as session:
                items = await store.list_requirements(
                    session,
                    project_id,
                    status=ReqStatus(status) if status else None,
                    type=ReqType(type) if type else None,
                    priority=Priority(priority) if priority else None,
                    include_deleted=include_deleted,
                )
                if document:
                    items = [
                        it for it in items if _item_in_document(it, document)
                    ]
                if query:
                    needle = query.strip().lower()
                    items = [
                        it for it in items
                        if needle in (it.statement or "").lower()
                    ]
                role_terms = await _catalog_role_terms(session, project_id)
                if has_actor is not None:
                    # true = solo los que nombran un rol específico; false =
                    # la cola de trabajo (actor genérico o ausente).
                    items = [
                        it
                        for it in items
                        if (
                            detect_actor(it.statement, role_terms=role_terms)
                            == "named"
                        )
                        == has_actor
                    ]
                total = len(items)
                truncated = total > limit
                items = items[:limit]
                parents = await _parent_code_map(session, project_id, items)
                return {
                    "count": total,
                    "truncated": truncated,
                    "items": [
                        _item_summary(it, parents, role_terms=role_terms)
                        for it in items
                    ],
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"list_requirements failed: {exc}"}

    @tool
    async def get_requirement(
        code: str, include_revisions: bool = False
    ) -> dict:
        """Full detail of one requirement: fields, relations, parent, source.

        The revision history is the heaviest part of the payload: it is only
        included when include_revisions is true. Use this for ONE item that
        needs deep inspection — for overviews and traceability use
        list_requirements (its summaries already carry source and hierarchy).
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                detail = await store.get_requirement(session, req_id)
                if not include_revisions:
                    detail.pop("revisions", None)
                return detail
        except Exception as exc:  # noqa: BLE001
            return {"error": f"get_requirement failed: {exc}"}

    @tool
    async def get_requirements(
        codes: list[str], include_revisions: bool = False
    ) -> dict:
        """Batch detail of several requirements in ONE call.

        Returns the same summary shape as list_requirements (statement, type,
        priority, status, actor, source_documents, parent_code/derived) plus
        acceptance criteria; unknown codes come back in `not_found`. The batch
        is capped at 100 codes — for anything bigger, filter with
        list_requirements first. Use this whenever more than one requirement
        needs inspection; set include_revisions only when the change history
        is actually needed (compact, without snapshots).
        """
        try:
            if not codes:
                return {"items": [], "not_found": []}
            if len(codes) > MAX_BATCH_GET:
                return {
                    "error": (
                        f"máximo {MAX_BATCH_GET} códigos por llamada "
                        f"(llegaron {len(codes)}): acotar con "
                        "list_requirements antes"
                    )
                }
            async with AsyncSessionLocal() as session:
                rows = (
                    await session.scalars(
                        select(RequirementItem).where(
                            RequirementItem.project_id == project_id,
                            RequirementItem.code.in_(codes),
                        )
                    )
                ).all()
                by_code = {it.code: it for it in rows}
                parents = await _parent_code_map(
                    session, project_id, list(by_code.values())
                )
                items = [
                    {
                        **_item_summary(it, parents),
                        "acceptance_criteria": list(
                            it.acceptance_criteria or []
                        ),
                    }
                    for it in (by_code[c] for c in codes if c in by_code)
                ]
                if include_revisions and by_code:
                    revs = (
                        await session.scalars(
                            select(RequirementRevision)
                            .where(
                                RequirementRevision.req_id.in_(
                                    [it.id for it in by_code.values()]
                                )
                            )
                            .order_by(
                                RequirementRevision.req_id,
                                RequirementRevision.version,
                            )
                        )
                    ).all()
                    by_req: dict[int, list[dict[str, Any]]] = {}
                    for rev in revs:
                        by_req.setdefault(rev.req_id, []).append(
                            {
                                "version": rev.version,
                                "changed_by": rev.changed_by,
                                "change_reason": rev.change_reason,
                            }
                        )
                    for entry in items:
                        entry["revisions"] = by_req.get(entry["id"], [])
                return {
                    "items": items,
                    "not_found": [c for c in codes if c not in by_code],
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"get_requirements failed: {exc}"}

    @tool
    async def approve_requirement(
        code: str, mark_span_verified: bool = False,
    ) -> dict:
        """Mark a requirement APPROVED (human accepts it into the SRS).

        Set mark_span_verified=True when the human has also manually confirmed
        the source span against the original document — the approval will carry
        span_verified=True in the same auditable revision.
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                item = await store.approve_requirement(
                    session, req_id,
                    changed_by="agent",
                    mark_span_verified=mark_span_verified,
                )
                return _item_summary(item)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"approve_requirement failed: {exc}"}

    @tool
    async def verify_span(code: str) -> dict:
        """Mark a requirement's source span as human-verified.

        Use this when span_verified is False but a human has confirmed the
        requirement traces to its quoted source. Records a manual verification
        revision so the audit trail reflects WHO checked it and WHEN.
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                item = await store.verify_span(
                    session, req_id, changed_by="agent"
                )
                return _item_summary(item)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"verify_span failed: {exc}"}

    @tool
    async def reject_requirement(code: str, reason: str) -> dict:
        """Mark a requirement REJECTED (human discards it). Soft-delete.

        `reason` is required and recorded in the revision trail.
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                item = await store.reject_requirement(
                    session, req_id, reason=reason, changed_by="agent"
                )
                return _item_summary(item)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"reject_requirement failed: {exc}"}

    @tool
    async def add_acceptance_criterion(
        code: str, criterion: str
    ) -> dict:
        """Attach a Gherkin acceptance criterion to a requirement.

        Use this when the critic flagged an item as not directly testable; the
        criterion makes it verifiable without changing its statement.
        """
        try:
            async with AsyncSessionLocal() as session:
                req_id = await _code_to_id(session, project_id, code)
                item = await store.add_acceptance_criterion(
                    session, req_id, criterion, changed_by="agent"
                )
                return {
                    "code": item.code,
                    "acceptance_criteria": list(
                        item.acceptance_criteria or []
                    ),
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"add_acceptance_criterion failed: {exc}"}

    @tool
    async def build_srs() -> dict:
        """Generate the SRS Markdown from the current requirement store.

        The SRS is built deterministically from RequirementItem rows (never
        hand-written). Returns the markdown text plus a counts summary so you
        can paste an excerpt to the user and offer the document for download.

        Call this after capture + validation, when the user asks for the SRS,
        the requirements matrix, or the deliverable document.
        """
        try:
            async with AsyncSessionLocal() as session:
                from backend.services.srs_builder import build_srs as _build
                from backend.models import Project as _Project
                proj = await session.get(_Project, project_id)
                name = getattr(proj, "name", "") if proj else ""
                desc = getattr(proj, "description", "") if proj else ""
                result = await _build(
                    session,
                    project_id,
                    project_name=name,
                    project_description=desc,
                )
                return {
                    "markdown": result["markdown"],
                    "generated_at": result["generated_at"],
                    "counts": result["counts"],
                    # Preview: primer KB para que el modelo pueda citar sin
                    # volcar el markdown completo en el contexto.
                    "preview": result["markdown"][:1024],
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"build_srs failed: {exc}"}

    @tool
    async def capture_status() -> dict:
        """Census of THIS project: requirement + grouping plan counts, last
        code, and by_document — how many LIVE (non soft-deleted) requirements
        cite each source document. A post-merge item can cite several
        documents (each one counts); items without source fall under
        "(sin fuente)". This is the compact scoping view: use it instead of
        reading documents or counting listings by hand.

        Call this BEFORE a capture. If requirements > 0, tell the
        user how many exist and the last code, then ask whether to reset
        everything or append. Never reset without explicit user confirmation.
        """
        try:
            async with AsyncSessionLocal() as session:
                from sqlalchemy import func, select
                from backend.models.requirement import (
                    GroupingPlan,
                    RequirementItem,
                )
                req_count = await session.scalar(
                    select(func.count())
                    .select_from(RequirementItem)
                    .where(RequirementItem.project_id == project_id)
                )
                plan_count = await session.scalar(
                    select(func.count())
                    .select_from(GroupingPlan)
                    .where(GroupingPlan.project_id == project_id)
                )
                last_code = await session.scalar(
                    select(RequirementItem.code)
                    .where(RequirementItem.project_id == project_id)
                    .order_by(RequirementItem.id.desc())
                    .limit(1)
                )
                # GROUP BY documento, empaquetado: conteos por fuente sobre los
                # items vivos, en una sola llamada diminuta (la alternativa del
                # agente era volcar 541 statements y contarlos por contexto).
                live_items = await store.list_requirements(session, project_id)
                doc_counts: dict[str, int] = {}
                for it in live_items:
                    docs = _source_documents(it)
                    if docs:
                        for doc in docs:
                            doc_counts[doc] = doc_counts.get(doc, 0) + 1
                    else:
                        doc_counts["(sin fuente)"] = (
                            doc_counts.get("(sin fuente)", 0) + 1
                        )
                by_document = [
                    {"document": doc, "count": n}
                    for doc, n in sorted(
                        doc_counts.items(), key=lambda kv: (-kv[1], kv[0])
                    )
                ]
                return {
                    "requirements": int(req_count or 0),
                    "grouping_plans": int(plan_count or 0),
                    "last_code": last_code,
                    "by_document": by_document,
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"capture_status failed: {exc}"}

    @tool
    async def reset_capture() -> dict:
        """Delete ALL capture-derived data for THIS project (hard).

        Destructive and irreversible: removes every requirement, relation,
        revision, grouping plan/group, quality finding, GORE goal + link,
        and SRS document version so the next capture starts truly fresh
        with no stale findings or orphan SRS versions.
        ONLY call this after the user explicitly confirmed they want
        to reset — never on your own initiative. When unsure, do NOT reset.
        """
        try:
            async with AsyncSessionLocal() as session:
                from backend.services.requirements_service import (
                    reset_project_capture,
                )
                result = await reset_project_capture(session, project_id)
            from backend.agents.subagents.capture_run_holder import (
                clear_capture_scope,
            )
            # A confirmed wipe also drops the pending folder scope: the next
            # /captura starts from a clean slate (session-13 scope registry).
            clear_capture_scope(project_id)
            return result
        except Exception as exc:  # noqa: BLE001
            return {"error": f"reset_capture failed: {exc}"}

    return [
        add_requirement,
        update_requirement,
        delete_requirement,
        merge_requirements,
        split_requirement,
        link_requirements,
        resolve_conflict,
        set_relation_status,
        list_requirements,
        get_requirements,
        get_requirement,
        update_requirements,
        approve_requirement,
        verify_span,
        reject_requirement,
        add_acceptance_criterion,
        build_srs,
        capture_status,
        reset_capture,
    ]
