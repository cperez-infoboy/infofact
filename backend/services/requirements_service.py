"""Orchestrates the requirements-capture pipeline end-to-end (plan §1, §9.6).

Host-side only: Docling (parser), embeddings (sentence-transformers), the Z.ai
LLM calls, and SQLite all run in the FastAPI process. Documents are read from
the workspace path (a host-side bind mount), never via the DockerSandbox. This
matches Decision 1 of CLAUDE.md — the sandbox is reserved for phases that
execute agent-generated code (implementation/testing).

Stage order (each consumes the previous stage's typed output):

  1. INGEST     discover_documents -> ingest_document per doc -> chunks + smap
                enrich_structure_map (LLM annotates sections)
  2. EXTRACT    extract_all (multi-doc) + gap_pass (per doc) + implicit_pass
  3. CONSOLIDATE  consolidate (exact + two-tier semantic dedup + contradictions)
  4. CRITIQUE   critique_all (generator-critic loop, dangling pre-check)
  5. CLASSIFY   classify_all (type + MoSCoW + optional decomposition)
  6. PERSIST    RequirementItem rows (code sequential per project, source JSON,
                derived sub-items with parent_id)

`on_progress` is an optional async callback (stage, message) the caller wires to
SSE later (Paso 7b). It is the only outward-facing hook; everything else is a
pure data transform feeding the next stage.

The function is exposed to the DeepAgent subagent as a single @tool (Paso 7b);
it does not expose internal stage functions individually, so the LLM cannot
skip a stage.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.pipelines.classification import (
    ClassificationDecision,
    ClassificationResult,
    classify_all,
)
from backend.agents.pipelines.consolidation import (
    ConsolidationResult,
    consolidate,
)
from backend.agents.pipelines.critique import CritiqueResult, critique_all
from backend.agents.pipelines.extraction import (
    RawRequirement,
    enrich_structure_map,
    extract_all,
    gap_pass,
    implicit_pass,
)
from backend.agents.pipelines.ingestion import (
    StructureMap,
    discover_documents,
    ingest_document,
)
from backend.database import AsyncSessionLocal
from backend.models.requirement import (
    Priority,
    ReqStatus,
    ReqType,
    RequirementItem,
)

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str, str], Awaitable[None]]
EventCb = Callable[[str, dict], Awaitable[None]]


@dataclass
class CaptureReport:
    """End-to-end result of a /captura run."""
    documents: list[str] = field(default_factory=list)
    item_ids: list[int] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    duplicates: list = field(default_factory=list)     # soft (proposed merges)
    contradictions: list = field(default_factory=list)  # soft (proposed conflicts)
    flagged: list[str] = field(default_factory=list)    # critic residual flags
    rejected: list[str] = field(default_factory=list)    # critic hallucination drops


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

_CODE_NUM_RE = re.compile(r"(\d+)$")


async def _next_code_seq(session: AsyncSession, project_id: int) -> int:
    """Next available numeric suffix for REQ-NNN in this project.

    Parses existing codes (not a COUNT) so soft-deleted rows (REJECTED / MERGED
    / SUPERSEDED) do not cause collisions — they stay in the table.
    """
    rows = await session.execute(
        select(RequirementItem.code).where(
            RequirementItem.project_id == project_id
        )
    )
    max_n = 0
    for (code,) in rows.all():
        m = _CODE_NUM_RE.search(code or "")
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def _source_from_raw(it: RawRequirement) -> dict:
    """Build the JSON `source` payload from a raw item.

    `quote` is the verbatim source_span — the anti-hallucination anchor and the
    jump-to-source handle for the frontend (DocViewer, Paso 9).
    """
    return {
        "document_id": it.document_id,
        "section": it.section,
        "page": it.page,
        "quote": it.source_span,
    }


def _status_from_raw(it: RawRequirement) -> ReqStatus:
    """Map pipeline status to the store ReqStatus."""
    if not it.span_verified:
        return ReqStatus.UNVERIFIED
    return ReqStatus.DRAFT


async def _persist(
    session: AsyncSession,
    project_id: int,
    items: list[RawRequirement],
    classification: ClassificationResult,
    on_event: EventCb | None = None,
) -> list[int]:
    """Write RequirementItem rows. Returns the persisted parent ids.

    Codes are contiguous within a project: parent first, then its decomposition
    sub-items, then the next parent. Codes are PRE-ASSIGNED in one pass before
    any row is created — mixing the parent's `seq += 1` with the children's
    produced non-contiguous codes that collided on a later flush.

    Sub-items from decomposition are written as derived rows with parent_id set
    and the parent's source inherited (preserves traceability to the original
    span even though the sub-statement is LLM-derived).

    ``on_event`` (optional) receives a ``requirement.added`` event per row so the
    frontend can render items incrementally (live DocViewer, Paso 9).
    """
    async def _added(payload: dict) -> None:
        """Emit requirement.added; never break persistence on a callback failure."""
        if not on_event:
            return
        try:
            await on_event("requirement.added", payload)
        except Exception:
            logger.exception("on_event failed (requirement.added)")

    seq = await _next_code_seq(session, project_id)

    # Pass 0 — pre-assign contiguous codes (parent + its subs) per item.
    plan: list[tuple[str, RawRequirement, ClassificationDecision | None,
                     list, list[str]]] = []
    for it in items:
        decision = classification.decisions.get(it.id)
        parent_code = f"REQ-{seq:03d}"
        seq += 1
        sub_parts: list = (
            classification.decompositions.get(it.id, [])
            if decision and decision.decomposition_needed else []
        )
        sub_codes: list[str] = []
        for _ in sub_parts:
            sub_codes.append(f"REQ-{seq:03d}")
            seq += 1
        plan.append((parent_code, it, decision, sub_parts, sub_codes))

    created_ids: list[int] = []
    code_to_id: dict[str, int] = {}

    # Pass 1 — parents (flush so we know their ids for child parent_id).
    for parent_code, it, decision, _sub_parts, _sub_codes in plan:
        rtype = decision.type if decision else ReqType.FUNCTIONAL
        prio = decision.priority if decision else Priority.MUST
        row = RequirementItem(
            project_id=project_id,
            code=parent_code,
            statement=it.statement,
            type=rtype,
            priority=prio,
            status=_status_from_raw(it),
            source=_source_from_raw(it),
            explicit=it.explicit,
            derived=False,
            confidence=it.confidence,
            span_verified=it.span_verified,
            acceptance_criteria=[],
            created_by="agent",
        )
        session.add(row)
        await session.flush()
        created_ids.append(row.id)
        code_to_id[parent_code] = row.id
        await _added({
            "code": parent_code,
            "statement": it.statement,
            "type": rtype.value,
            "priority": prio.value,
            "explicit": it.explicit,
            "derived": False,
            "confidence": it.confidence,
            "span_verified": it.span_verified,
            "source": _source_from_raw(it),
        })

    # Pass 2 — derived sub-items (parent_id now known), inheriting parent source.
    for parent_code, it, decision, sub_parts, sub_codes in plan:
        if not sub_parts:
            continue
        parent_id = code_to_id[parent_code]
        rtype = decision.type if decision else ReqType.FUNCTIONAL
        prio = decision.priority if decision else Priority.MUST
        inherited = _source_from_raw(it)
        for part, sub_code in zip(sub_parts, sub_codes):
            session.add(RequirementItem(
                project_id=project_id,
                code=sub_code,
                statement=part.statement,
                type=rtype,
                priority=prio,
                status=ReqStatus.DRAFT,
                source=inherited,
                explicit=False,
                derived=True,
                parent_id=parent_id,
                confidence=it.confidence,
                span_verified=it.span_verified,
                acceptance_criteria=[],
                created_by="agent",
            ))
            await _added({
                "code": sub_code,
                "statement": part.statement,
                "type": rtype.value,
                "priority": prio.value,
                "explicit": False,
                "derived": True,
                "parent_code": parent_code,
                "confidence": it.confidence,
                "span_verified": it.span_verified,
                "source": inherited,
            })
    await session.commit()
    return created_ids


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def run_requirements_pipeline(
    project_id: int,
    target: Path,
    *,
    project_name: str = "",
    project_description: str = "",
    on_progress: ProgressCb | None = None,
    on_event: EventCb | None = None,
    session: AsyncSession | None = None,
) -> CaptureReport:
    """Run the full capture pipeline over the documents under `target`.

    Args:
        project_id: DB project id (rows attach here).
        target: workspace path already resolved + traversal-safe. Caller's job.
        project_name / project_description: fed to the extractor for context.
        on_progress: optional async (stage, message) hook for coarse stage
            events (relayed as ``extraction.progress`` over SSE).
        on_event: optional async (event_type, data) hook for fine-grained
            events: ``conflict.found`` (post-consolidate), ``validation.report``
            (post-critique) and ``requirement.added`` (per persisted row).
        session: optional session; if None a fresh AsyncSessionLocal is used.
    """
    async def _emit(stage: str, msg: str) -> None:
        if on_progress:
            try:
                await on_progress(stage, msg)
            except Exception:
                logger.exception("on_progress callback failed (stage=%s)", stage)

    async def _emit_event(event_type: str, data: dict) -> None:
        if on_event:
            try:
                await on_event(event_type, data)
            except Exception:
                logger.exception("on_event callback failed (type=%s)", event_type)

    owns_session = session is None
    if owns_session:
        session = AsyncSessionLocal()
    assert session is not None
    try:
        # ---- 1. INGEST -----------------------------------------------------
        await _emit("ingest", "discover")
        docs = discover_documents(target)
        await _emit("ingest", f"{len(docs)} documento(s)")
        if not docs:
            return CaptureReport(stats={"error": "no_documents", "input": 0})

        per_doc: list[tuple[list, StructureMap]] = []
        doc_texts: dict[str, str] = {}
        all_chunks = []
        for d in docs:
            # ingest_document is sync (Docling) — run off the event loop.
            chunks, smap = await asyncio.to_thread(ingest_document, d)
            # enrich_structure_map mutates smap in place (returns None).
            await enrich_structure_map(
                smap,
                project_name=project_name,
                project_description=project_description,
            )
            per_doc.append((chunks, smap))
            doc_texts[smap.document_id] = smap.full_text
            all_chunks.extend(chunks)
            await _emit("ingest", f"parseado {d.name}")

        # ---- 2. EXTRACT (multi-doc) + gap + implicit ----------------------
        await _emit("extract", "extraccion")
        extracted = await extract_all(
            all_chunks,
            doc_texts=doc_texts,
            project_name=project_name,
            project_description=project_description,
        )
        # Gap pass is per-document (signature takes one smap).
        for chunks, smap in per_doc:
            gaps = await gap_pass(
                chunks, smap, extracted,
                project_name=project_name,
                project_description=project_description,
            )
            extracted.extend(gaps)
        implicits = await implicit_pass(
            all_chunks,
            doc_texts=doc_texts,
            project_name=project_name,
            project_description=project_description,
        )
        extracted.extend(implicits)
        await _emit("extract", f"{len(extracted)} items crudos")

        # ---- 3. CONSOLIDATE ----------------------------------------------
        await _emit("consolidate", "dedup + contradicciones")
        cons: ConsolidationResult = await consolidate(extracted)
        # Conflictos detectados (soft): un evento por duplicado / contradicción.
        for dup in cons.duplicates:
            await _emit_event("conflict.found", {
                "kind": "duplicate",
                "kept_id": dup.kept_id,
                "member_ids": dup.member_ids,
                "kept_statement": dup.kept_statement,
            })
        for pair in cons.contradictions:
            await _emit_event("conflict.found", {
                "kind": "contradiction",
                "a_id": pair.a_id,
                "b_id": pair.b_id,
                "reason": pair.reason,
                "confidence": pair.confidence,
            })

        # ---- 4. CRITIQUE --------------------------------------------------
        await _emit("critique", "revision critica")
        crit: CritiqueResult = await critique_all(cons.items)
        await _emit_event("validation.report", {
            "total": len(cons.items),
            "kept": crit.stats.get("kept", len(crit.items)),
            "rejected": len(crit.rejected),
            "flagged": len(crit.flagged),
        })

        # ---- 5. CLASSIFY --------------------------------------------------
        await _emit("classify", "clasificacion")
        cls: ClassificationResult = await classify_all(crit.items)

        # ---- 6. PERSIST ---------------------------------------------------
        await _emit("persist", "guardando")
        ids = await _persist(session, project_id, crit.items, cls, on_event)

        stats = {
            "documents": [str(d) for d in docs],
            "raw_extracted": len(extracted),
            "after_consolidate": cons.stats.get("after_semantic", len(cons.items)),
            "after_critique": crit.stats.get("kept", len(crit.items)),
            "persisted": len(ids),
            "consolidation": cons.stats,
            "critique": crit.stats,
            "classification": cls.stats,
            "sub_items": cls.stats.get("sub_items", 0),
        }
        await _emit("done", f"{len(ids)} requerimientos")
        return CaptureReport(
            documents=[str(d) for d in docs],
            item_ids=ids,
            stats=stats,
            duplicates=cons.duplicates,
            contradictions=cons.contradictions,
            flagged=crit.flagged,
            rejected=crit.rejected,
        )
    finally:
        if owns_session:
            await session.close()
