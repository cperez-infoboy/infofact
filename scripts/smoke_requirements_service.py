"""Smoke test for requirements_service persistence (step 7).

Validates _persist against a real in-memory SQLite DB:
- codes are opaque (REQ-XXXX, Crockford base32), unique per project.
- source JSON carries document_id / section / page / quote.
- decomposition sub-items get parent_id + derived=True + inherited source.
- unverified items land as UNVERIFIED, the rest as DRAFT.
- a second allocation never collides with existing codes.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.agents.pipelines.classification import (
    ClassificationDecision,
    ClassificationResult,
    DecomposedItem,
)
from backend.agents.pipelines.extraction import RawRequirement
from backend.models.base import Base
from backend.models.requirement import ReqStatus, RequirementItem
from backend.services._req_codes import OPAQUE_CODE_RE, gen_opaque_code
from backend.services.requirements_service import _persist

PROJECT_ID = 1


async def main():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    # Two raw items: d0 high-level (needs decomposition), d1 operational.
    items = [
        RawRequirement(
            statement="El sistema debe ser seguro.",
            source_span="seguridad obligatoria",
            section="3.1", page=2, explicit=True, confidence=0.9, id="d0",
            document_id="/ws/rfp.pdf", span_verified=True,
        ),
        RawRequirement(
            statement="latencia p95 < 200ms",
            source_span="p95 200ms",
            section="3.2", page=3, explicit=True, confidence=0.85, id="d1",
            document_id="/ws/rfp.pdf", span_verified=False,
        ),
    ]
    cls = ClassificationResult(
        decisions={
            "d0": ClassificationDecision(
                item_id="d0",
                type=__import__("backend.models.requirement", fromlist=["ReqType"]).ReqType.SECURITY,
                priority=__import__("backend.models.requirement", fromlist=["Priority"]).Priority.SHOULD,
                decomposition_needed=True,
            ),
            "d1": ClassificationDecision(
                item_id="d1",
                type=__import__("backend.models.requirement", fromlist=["ReqType"]).ReqType.PERFORMANCE,
                priority=__import__("backend.models.requirement", fromlist=["Priority"]).Priority.MUST,
            ),
        },
        decompositions={
            "d0": [
                DecomposedItem(statement="authn obligatoria", rationale="auth"),
                DecomposedItem(statement="cifrado en transito", rationale="tls"),
            ],
        },
    )

    async with Session() as session:
        ids = await _persist(session, PROJECT_ID, items, cls)

    assert len(ids) == 2, f"expected 2 parent ids, got {ids}"

    async with Session() as session:
        rows = (await session.execute(
            select(RequirementItem).where(RequirementItem.project_id == PROJECT_ID)
            .order_by(RequirementItem.id)
        )).scalars().all()

    # 2 parents + 2 derived sub-items = 4 rows.
    assert len(rows) == 4, f"expected 4 rows, got {len(rows)}"
    codes = [r.code for r in rows]
    assert all(OPAQUE_CODE_RE.match(c) for c in codes), codes
    assert len(set(codes)) == 4, f"code collision: {codes}"
    print("opaque unique codes OK:", codes)

    # Select by derived flag; parents in insertion order (by id).
    parents = [r for r in rows if not r.derived]
    subs = [r for r in rows if r.derived]
    assert len(parents) == 2 and len(subs) == 2, (len(parents), len(subs))
    parent0 = min(parents, key=lambda r: r.id)  # d0, inserted first
    parent1 = max(parents, key=lambda r: r.id)  # d1
    subs_of_d0 = [r for r in subs if r.parent_id == parent0.id]
    assert len(subs_of_d0) == 2

    assert parent0.type.value == "security"
    assert parent0.priority.value == "should"
    assert parent0.status == ReqStatus.DRAFT
    assert parent0.source["quote"] == "seguridad obligatoria"
    assert parent0.source["page"] == 2
    print("parent0 OK:", parent0.code, parent0.type.value, parent0.source["section"])

    # unverified span -> UNVERIFIED status.
    assert parent1.status == ReqStatus.UNVERIFIED, parent1.status
    print("parent1 unverified->UNVERIFIED OK:", parent1.code, parent1.type.value)

    # sub-items: derived, parent_id set, inherited source, opaque codes.
    sub_codes = sorted(r.code for r in subs_of_d0)
    assert all(OPAQUE_CODE_RE.match(c) for c in sub_codes), sub_codes
    for s in subs_of_d0:
        assert s.derived is True and s.parent_id == parent0.id
        assert s.source["quote"] == "seguridad obligatoria"  # inherited
    print("sub-items derived + parent_id + inherited source OK:", sub_codes)

    # Rerun: a fresh allocation never collides with the existing 4 codes.
    async with Session() as session:
        fresh = await gen_opaque_code(session, PROJECT_ID)
    assert OPAQUE_CODE_RE.match(fresh), fresh
    assert fresh not in codes, f"collision on rerun: {fresh} in {codes}"
    print("rerun allocation OK (no collision):", fresh)

    await engine.dispose()
    print("\nALL requirements_service persist smoke checks PASSED")


asyncio.run(main())
