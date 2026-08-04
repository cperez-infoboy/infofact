"""Smoke test for reset_project_capture (hard-delete reset, fresh opaque codes).

Validates the reset path in isolation (no agent, no LLM, no pipeline):
- Seeds requirements + a relation + a grouping plan with a group.
- Seeds SRS quality findings, GORE goals + links, and an SRS document version.
- Calls reset_project_capture.
- Asserts every related table is empty for the project (items, relations,
  revisions, plans, groups, findings, goals, goal_links, srs_documents).
- Asserts the reset is SCOPED: a second project's data survives untouched.
- Asserts the store is empty after reset, and that add_requirement then
  produces a fresh opaque code.

Run: .venv/bin/python scripts/smoke_reset_capture.py
"""
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # so backend.* is importable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.models.project        # register tables on Base.metadata
import backend.models.requirement
import backend.models.srs
from backend.models.base import Base
from backend.models.project import Project
from backend.models.requirement import (
    GroupDecision,
    GroupingGroup,
    GroupingPlan,
    PlanStatus,
    RequirementItem,
    RequirementRelation,
    RequirementRevision,
)
from backend.models.srs import (
    FindingDimension,
    FindingScope,
    FindingSeverity,
    FindingStatus,
    Goal,
    GoalKind,
    GoalLink,
    GoalStatus,
    LinkRelation,
    LinkStatus,
    RequirementFinding,
    SrsDocument,
    SrsStatus,
)
from backend.services import requirement_store as store
from backend.services._req_codes import OPAQUE_CODE_RE, gen_opaque_code
from backend.services.requirements_service import reset_project_capture

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


async def _seed_project(Session, name: str, slug: str) -> tuple[int, dict[str, int]]:
    """Create a project with 3 requirements + 1 relation + 1 grouping plan/group.

    Also seeds 2 quality findings, 1 goal + 1 goal-link, and 1 SRS document
    version so the reset can prove it wipes ALL capture-derived data.

    Returns (project_id, {"REQ-XXXX": id, ...}).
    """
    async with Session() as session:
        proj = Project(
            user_id=1, name=name, slug=slug,
            description="reset smoke", phase="requirements",
        )
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        pid = proj.id

        # Codes are opaque; capture them in insertion order so we refer to
        # the first two by position (keeper + duplicate) instead of by literal.
        ordered: list[tuple[str, int]] = []
        for stmt in ("Login con SSO", "Logout seguro", "Auditoria de accesos"):
            item = await store.add_requirement(
                session, pid, statement=stmt,
                source={"quote": stmt, "section": "1", "page": 1},
                created_by="smoke",
            )
            ordered.append((item.code, item.id))
        keeper_id = ordered[0][1]
        dup_id = ordered[1][1]

        # A relation between the first two requirements (duplicate proposal).
        await store.link_requirements(
            session, keeper_id, dup_id,
            __import__("backend.models.requirement", fromlist=["RelationKind"]).RelationKind.DUPLICATE,
            detected_by="smoke",
        )

        # A grouping plan with one group (keeper absorbs the duplicate).
        plan = GroupingPlan(project_id=pid, status=PlanStatus.PROPOSED)
        session.add(plan)
        await session.flush()
        session.add(GroupingGroup(
            plan_id=plan.id,
            keeper_id=keeper_id,
            member_ids=[dup_id],
            reason="dup semantico",
            confidence=0.91,
            decision=GroupDecision.PENDING,
        ))

        # 2 quality findings on the keeper requirement.
        session.add(RequirementFinding(
            project_id=pid, req_id=keeper_id,
            scope=FindingScope.ITEM,
            dimension=FindingDimension.AMBIGUITY,
            rule_id="smoke.ambiguity_1",
            severity=FindingSeverity.BLOCKER,
            message="termino ambiguo",
            status=FindingStatus.OPEN,
            detected_by="smoke",
        ))
        session.add(RequirementFinding(
            project_id=pid, req_id=keeper_id,
            scope=FindingScope.ITEM,
            dimension=FindingDimension.INCOSE_RULE,
            rule_id="smoke.incose_1",
            severity=FindingSeverity.MAJOR,
            message="violacion INCOSE",
            status=FindingStatus.OPEN,
            detected_by="smoke",
        ))

        # 1 GORE goal + 1 goal-link to the keeper.
        goal = Goal(
            project_id=pid, code="GOAL-SMK1",
            statement="Autenticacion segura",
            kind=GoalKind.FUNCTIONAL_GOAL,
            status=GoalStatus.PROPOSED,
            created_by="smoke",
        )
        session.add(goal)
        await session.flush()
        session.add(GoalLink(
            goal_id=goal.id, req_id=keeper_id,
            relation=LinkRelation.REALIZES,
            status=LinkStatus.PROPOSED,
            detected_by="smoke",
        ))

        # 1 SRS document version (CANDIDATE).
        session.add(SrsDocument(
            project_id=pid, version=1,
            status=SrsStatus.CANDIDATE,
            structure=[], narrative={}, markdown="# SRS smoke",
            quality_summary={}, coverage={}, traceability={},
            review_flags={},
            requirement_codes=[c for c, _ in ordered],
            requirement_count=len(ordered),
            generated_by="smoke",
        ))

        await session.commit()
        return pid, {code: rid for code, rid in ordered}


async def _count(session, Model, *, project_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(Model)
    if project_id is not None and hasattr(Model, "project_id"):
        stmt = stmt.where(Model.project_id == project_id)
    return int((await session.scalar(stmt)) or 0)


async def main() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    pid, codes = await _seed_project(Session, "ResetA", "reset-a")
    pid2, _ = await _seed_project(Session, "ResetB", "reset-b")

    print("\n== SEED ==")
    print(f"  project {pid} (ResetA): 3 reqs, 1 relation, 1 plan/1 group,")
    print(f"    2 findings, 1 goal+link, 1 SRS version")
    print(f"  project {pid2} (ResetB): same (must survive reset of ResetA)")

    print("\n== RESET ResetA ==")
    async with Session() as session:
        result = await reset_project_capture(session, pid)
    print(f"  result = {result}")
    check(
        "reset devuelve counts correctos",
        result == {
            "deleted_requirements": 3,
            "deleted_plans": 1,
            "deleted_findings": 2,
            "deleted_goals": 1,
            "deleted_srs_versions": 1,
        },
        str(result),
    )

    print("\n== ASSERT vacio para ResetA ==")
    async with Session() as session:
        check("RequirementItem=0", await _count(session, RequirementItem, project_id=pid) == 0)
        check("RequirementFinding=0", await _count(session, RequirementFinding, project_id=pid) == 0)
        check("Goal=0", await _count(session, Goal, project_id=pid) == 0)
        check("SrsDocument=0", await _count(session, SrsDocument, project_id=pid) == 0)
        check("GroupingPlan=0", await _count(session, GroupingPlan, project_id=pid) == 0)
        # GoalLink carries no project_id column, so the global count equals
        # what ResetB (the un-reset project) still owns: 1 link.
        check("GoalLink: solo ResetB (1)", await _count(session, GoalLink) == 1)
        # Relation/Revision/Group carry no project_id column, so the global
        # count equals what ResetB (the un-reset project) still owns: 1
        # relation, 3 revisions (one per seeded requirement), 1 group. ResetA
        # contributed zero survivors -- that is what these checks validate.
        check("Relation: solo ResetB (1)", await _count(session, RequirementRelation) == 1)
        check("Revision: solo ResetB (3)", await _count(session, RequirementRevision) == 3)
        check("Group: solo ResetB (1)", await _count(session, GroupingGroup) == 1)

    print("\n== ASSERT scoped: ResetB intacto ==")
    async with Session() as session:
        n = await _count(session, RequirementItem, project_id=pid2)
        check("ResetB conserva sus 3 requerimientos", n == 3, f"got {n}")
        np = await _count(session, GroupingPlan, project_id=pid2)
        check("ResetB conserva su plan", np == 1, f"got {np}")
        nf = await _count(session, RequirementFinding, project_id=pid2)
        check("ResetB conserva sus 2 findings", nf == 2, f"got {nf}")
        ng = await _count(session, Goal, project_id=pid2)
        check("ResetB conserva su goal", ng == 1, f"got {ng}")
        ns = await _count(session, SrsDocument, project_id=pid2)
        check("ResetB conserva su SRS", ns == 1, f"got {ns}")

    print("\n== ASSERT store vacio -> proximo codigo opaco fresco ==")
    async with Session() as session:
        fresh = await gen_opaque_code(session, pid)
        check("codigo opaco valido", bool(OPAQUE_CODE_RE.match(fresh)), fresh)

    print("\n== ASSERT proximo add = codigo opaco ==")
    async with Session() as session:
        item = await store.add_requirement(
            session, pid, statement="Post-reset req",
            created_by="smoke",
        )
        check("codigo opaco valido", bool(OPAQUE_CODE_RE.match(item.code)), item.code)

    print(f"\n{'=' * 48}")
    print(f"  PASS={PASS}  FAIL={FAIL}")
    print("=" * 48)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
