"""Smoke test for the requirement store + editing tools.

Validates the editing layer in isolation (no LangChain agent, no LLM):
- Store CRUD: add / update / merge / split / approve / reject / link / list /
  get, with the hard rules from plan section 10.2:
    * soft-delete only (REJECTED / MERGED / SUPERSEDED rows stay)
    * every mutation appends a RequirementRevision (versioned)
    * merge unions source spans (multi-traceability)
    * split keeps parent_id + inherits source, original SUPERSEDED
- Tools: the LangChain @tool wrappers return plain dicts and resolve code->id.

Run: .venv/bin/python scripts/smoke_requirement_store.py
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
from backend.models.base import Base
from backend.models.project import Project
from backend.models.requirement import (
    ReqStatus,
    RequirementItem,
    RequirementRelation,
    RequirementRevision,
)
from backend.services import requirement_store as store

# Import the tools module to monkeypatch its session factory in the tools phase.
from backend.agents.tools import requirements_tools


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


async def main() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        proj = Project(
            user_id=1, name="Smoke", slug="smoke",
            description="store smoke", phase="requirements",
        )
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        pid = proj.id

        print("\n== STORE: add ==")
        a = await store.add_requirement(
            session, pid, statement="Req A",
            source={"quote": "span A", "section": "1.1"},
        )
        b = await store.add_requirement(
            session, pid, statement="Req B",
            source={"quote": "span B", "section": "1.2"},
        )
        c = await store.add_requirement(
            session, pid, statement="Compound: X and Y",
            source={"quote": "span C", "section": "2"},
        )
        check("add assigns contiguous codes",
              a.code == "REQ-001" and b.code == "REQ-002" and c.code == "REQ-003",
              f"{a.code}/{b.code}/{c.code}")
        check("add sets DRAFT status", a.status == ReqStatus.DRAFT)
        check("add appends revision v1", await _rev_version(session, a.id) == 1)

        print("\n== STORE: update (revision bump) ==")
        await store.update_requirement(
            session, a.id, statement="Req A (edited)", reason="clarify",
        )
        check("update changes statement",
              await _statement(session, a.id) == "Req A (edited)")
        check("update bumps revision to v2",
              await _rev_version(session, a.id) == 2)

        print("\n== STORE: merge (source union + soft-delete) ==")
        kept = await store.merge_requirements(
            session, [a.id, b.id], keep_statement="Req A + B merged",
            reason="dup",
        )
        check("merge keeps first item id", kept.id == a.id)
        check("merge unions sources into a list",
              isinstance(kept.source, list) and len(kept.source) == 2,
              f"source={kept.source}")
        check("merge sets other to MERGED",
              await _status(session, b.id) == ReqStatus.MERGED)
        check("merge sets merged_into pointer",
              await _merged_into(session, b.id) == a.id)

        print("\n== STORE: split (parent_id + SUPERSEDED) ==")
        children = await store.split_requirement(
            session, c.id, ["Part X", "Part Y"], reason="non-atomic",
        )
        check("split creates 2 children", len(children) == 2)
        check("split children carry parent_id",
              all(ch.parent_id == c.id for ch in children))
        check("split children are derived",
              all(ch.derived for ch in children))
        check("split children inherit source",
              all(ch.source == c.source for ch in children))
        check("split children get contiguous codes",
              children[0].code == "REQ-004" and children[1].code == "REQ-005")
        check("split supersedes original",
              await _status(session, c.id) == ReqStatus.SUPERSEDED)
        check("split sets superseded_by pointer",
              await _superseded_by(session, c.id) == children[0].id)

        print("\n== STORE: link + resolve_conflict ==")
        rel = await store.link_requirements(
            session, children[0].id, children[1].id,
            store.RelationKind.DEPENDS_ON, note="X needs Y",
        )
        check("link creates PROPOSED relation", rel.status == store.RelationStatus.PROPOSED)
        resolved = await store.resolve_conflict(
            session, rel.id, winner_id=children[0].id, note="X wins",
        )
        check("resolve marks relation RESOLVED",
              resolved.status == store.RelationStatus.RESOLVED)

        print("\n== STORE: lifecycle transitions ==")
        await store.approve_requirement(session, children[0].id)
        check("approve -> APPROVED",
              await _status(session, children[0].id) == ReqStatus.APPROVED)
        await store.add_acceptance_criterion(
            session, children[0].id, "Given X When Y Then Z",
        )
        check("acceptance criterion appended",
              "Given X When Y Then Z"
              in await _criteria(session, children[0].id))
        await store.reject_requirement(
            session, children[1].id, reason="out of scope",
        )
        check("reject -> REJECTED",
              await _status(session, children[1].id) == ReqStatus.REJECTED)

        print("\n== STORE: list (soft-delete filtering) ==")
        live = await store.list_requirements(session, pid)
        live_codes = {it.code for it in live}
        check("list hides soft-deleted by default",
              "REQ-002" not in live_codes and "REQ-003" not in live_codes
              and "REQ-005" not in live_codes,
              f"live={live_codes}")
        all_rows = await store.list_requirements(session, pid, include_deleted=True)
        check("list include_deleted shows everything",
              len(all_rows) == 5, f"count={len(all_rows)}")

        print("\n== STORE: get (detail + revision history) ==")
        detail = await store.get_requirement(session, a.id)
        check("get returns relations + revisions keys",
              "relations" in detail and "revisions" in detail)
        # a: v1 add, v2 update, v3 merge (as kept item) -> 3 revisions
        check("get revision history length matches mutations",
              len(detail["revisions"]) == 3,
              f"revisions={len(detail['revisions'])}")

    # ---- TOOLS phase: monkeypatch the session factory the tools use --------
    print("\n== TOOLS: factory + dict output ==")
    # Tools close over the module global AsyncSessionLocal; rebind it to the
    # test session factory so tool calls hit the in-memory DB.
    requirements_tools.AsyncSessionLocal = Session
    tools = requirements_tools.make_requirements_tools(pid)
    check("factory returns 12 tools", len(tools) == 12, f"count={len(tools)}")

    by_name = {t.name: t for t in tools}

    add_res = await by_name["add_requirement"].ainvoke({
        "statement": "Tool-added req",
        "type": "security",
        "priority": "should",
    })
    check("add_requirement tool returns code",
          "code" in add_res and add_res["code"].startswith("REQ-"),
          f"res={add_res}")
    new_code = add_res.get("code")

    list_res = await by_name["list_requirements"].ainvoke({})
    check("list_requirements tool returns items list",
          "items" in list_res and list_res["count"] >= 1,
          f"count={list_res.get('count')}")

    get_res = await by_name["get_requirement"].ainvoke({"code": new_code})
    check("get_requirement tool returns detail",
          "statement" in get_res and get_res["statement"] == "Tool-added req",
          f"keys={list(get_res.keys())}")

    print(f"\n{'='*40}\nRESULT: {PASS} passed, {FAIL} failed")
    await engine.dispose()
    sys.exit(1 if FAIL else 0)


# --- tiny query helpers (avoid reloading the whole item) --------------------


async def _status(session, req_id: int) -> ReqStatus:
    v = await session.scalar(
        select(RequirementItem.status).where(RequirementItem.id == req_id)
    )
    return v


async def _statement(session, req_id: int) -> str:
    return await session.scalar(
        select(RequirementItem.statement).where(RequirementItem.id == req_id)
    )


async def _merged_into(session, req_id: int):
    return await session.scalar(
        select(RequirementItem.merged_into).where(RequirementItem.id == req_id)
    )


async def _superseded_by(session, req_id: int):
    return await session.scalar(
        select(RequirementItem.superseded_by).where(RequirementItem.id == req_id)
    )


async def _criteria(session, req_id: int) -> list:
    v = await session.scalar(
        select(RequirementItem.acceptance_criteria)
        .where(RequirementItem.id == req_id)
    )
    return v or []


async def _rev_version(session, req_id: int) -> int:
    return await session.scalar(
        select(func.max(RequirementRevision.version))
        .where(RequirementRevision.req_id == req_id)
    )


if __name__ == "__main__":
    asyncio.run(main())
