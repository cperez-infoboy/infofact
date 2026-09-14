"""Store of work-package documents (molde analysis_store).

Create/get/list versioned WorkPackageDocuments with their WorkPackage and
WorkTask child rows. WP-NNN codes continue across versions (max sequential
suffix, like ADR/SUB in analysis_store); TASK-NNN codes restart per package.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select

from backend.agents.pipelines.workpackage_pipeline import (
    CoherenceGate,
    PackageContext,
    PackagesResult,
)
from backend.models.base import Base
from backend.models.packages import (
    PackageStatus,
    WorkPackage,
    WorkPackageDocument,
    WorkTask,
)


def gen_wp_code(seq: int) -> str:
    return f"WP-{seq:03d}"


def gen_task_code(seq: int) -> str:
    return f"TASK-{seq:03d}"


async def _max_wp_suffix(session: AsyncSession, project_id: int) -> int:
    rows = await session.execute(
        select(WorkPackage.code).where(WorkPackage.project_id == project_id)
    )
    suffixes = [
        int(m.group(1))
        for (c,) in rows.all()
        if c and (m := re.fullmatch(r"WP-(\d+)", c))
    ]
    return max(suffixes, default=0)


async def create_packages_document(
    session: AsyncSession,
    project_id: int,
    *,
    analysis_id: int,
    analysis_version: int,
    result: PackagesResult,
    requirement_count: int = 0,
) -> WorkPackageDocument:
    """Persist one CANDIDATE packages document with all child rows."""
    cur = await session.scalar(
        select(func.max(WorkPackageDocument.version)).where(
            WorkPackageDocument.project_id == project_id
        )
    )
    version = (cur or 0) + 1

    doc = WorkPackageDocument(
        project_id=project_id,
        version=version,
        status=PackageStatus.CANDIDATE,
        analysis_id=analysis_id,
        analysis_version=analysis_version,
        master_markdown=result.master_markdown,
        coherence_report={
            "gates": [
                {
                    "gate": g.gate,
                    "status": g.status,
                    "blocking": g.blocking,
                    "details": g.details,
                }
                for g in result.gates
            ],
            "critique_findings": result.critique_findings,
        },
        requirement_count=requirement_count,
    )
    session.add(doc)
    await session.flush()  # doc.id

    wp_seq = await _max_wp_suffix(session, project_id)
    for ctx in result.packages:
        wp_seq += 1
        wp = WorkPackage(
            project_id=project_id,
            document_id=doc.id,
            code=gen_wp_code(wp_seq),
            sub_project_code=ctx.sub_project_code,
            sub_project_name=ctx.sub_project_name,
            project_code=ctx.project_code or None,
            mission=ctx.mission,
            stack=dict(ctx.stack or {}),
            markdown=ctx.markdown,
            counts=dict(ctx.counts or {}),
        )
        session.add(wp)
        await session.flush()  # wp.id
        for t in ctx.tasks:
            session.add(
                WorkTask(
                    project_id=project_id,
                    package_id=wp.id,
                    code=t.code or gen_task_code(t.sort_order + 1),
                    title=t.title,
                    description=t.description,
                    req_codes=list(t.req_codes),
                    entity_codes=list(t.entity_codes),
                    contract_names=list(t.contract_names),
                    depends_on=list(t.depends_on),
                    acceptance=list(t.acceptance),
                    sort_order=t.sort_order,
                )
            )
    await session.flush()
    return doc


async def get_latest_document(
    session: AsyncSession, project_id: int
) -> WorkPackageDocument | None:
    """Latest non-discarded document (CANDIDATE preferred, then LOCKED)."""
    rows = (
        (
            await session.execute(
                select(WorkPackageDocument)
                .where(WorkPackageDocument.project_id == project_id)
                .order_by(WorkPackageDocument.version.desc())
            )
        )
        .scalars()
        .all()
    )
    for r in rows:
        if r.status != PackageStatus.CANDIDATE:
            continue
        return r
    return rows[0] if rows else None


async def list_documents(
    session: AsyncSession, project_id: int
) -> list[WorkPackageDocument]:
    rows = (
        (
            await session.execute(
                select(WorkPackageDocument)
                .where(WorkPackageDocument.project_id == project_id)
                .order_by(WorkPackageDocument.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def get_document_by_version(
    session: AsyncSession, project_id: int, version: int
) -> WorkPackageDocument | None:
    return (
        await session.execute(
            select(WorkPackageDocument).where(
                WorkPackageDocument.project_id == project_id,
                WorkPackageDocument.version == version,
            )
        )
    ).scalar_one_or_none()


async def list_packages(
    session: AsyncSession, document_id: int
) -> list[WorkPackage]:
    rows = (
        (
            await session.execute(
                select(WorkPackage)
                .where(WorkPackage.document_id == document_id)
                .order_by(WorkPackage.code)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def list_tasks(
    session: AsyncSession, package_id: int
) -> list[WorkTask]:
    rows = (
        (
            await session.execute(
                select(WorkTask)
                .where(WorkTask.package_id == package_id)
                .order_by(WorkTask.sort_order)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def update_status(
    session: AsyncSession,
    document_id: int,
    status: PackageStatus,
) -> WorkPackageDocument | None:
    doc = await session.get(WorkPackageDocument, document_id)
    if doc is None:
        return None
    doc.status = status
    if status == PackageStatus.LOCKED:
        from datetime import datetime, timezone

        doc.locked_at = datetime.now(timezone.utc)
    await session.flush()
    return doc


# --- serializers -------------------------------------------------------------


def document_to_dict(doc: WorkPackageDocument) -> dict[str, Any]:
    return {
        "id": doc.id,
        "project_id": doc.project_id,
        "version": doc.version,
        "status": doc.status.value if hasattr(doc.status, "value") else str(doc.status),
        "analysis_id": doc.analysis_id,
        "analysis_version": doc.analysis_version,
        "coherence_report": doc.coherence_report or {},
        "requirement_count": doc.requirement_count,
        "generated_at": doc.generated_at.isoformat() if doc.generated_at else None,
        "locked_at": doc.locked_at.isoformat() if doc.locked_at else None,
    }


def package_to_dict(
    wp: WorkPackage, *, include_markdown: bool = False
) -> dict[str, Any]:
    out = {
        "id": wp.id,
        "code": wp.code,
        "sub_project_code": wp.sub_project_code,
        "sub_project_name": wp.sub_project_name,
        "project_code": wp.project_code,
        "mission": wp.mission,
        "stack": wp.stack or {},
        "counts": wp.counts or {},
    }
    if include_markdown:
        out["markdown"] = wp.markdown
    return out


def task_to_dict(t: WorkTask) -> dict[str, Any]:
    return {
        "id": t.id,
        "code": t.code,
        "title": t.title,
        "description": t.description,
        "req_codes": t.req_codes,
        "entity_codes": t.entity_codes,
        "contract_names": t.contract_names,
        "depends_on": t.depends_on,
        "acceptance": t.acceptance,
        "sort_order": t.sort_order,
    }


from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402
