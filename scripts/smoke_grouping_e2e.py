#!/usr/bin/env python
"""Smoke: grouping review E2E over a SEEDED store (no /captura needed).

Builds a throwaway SQLite DB, seeds a project with duplicate requirements
(verbatim + semantic), and exercises build_grouping_plan -> review_grouping ->
apply_grouping_plan -> re-apply (idempotency). Validates:

  - the RequirementItem -> RawRequirement adapter (code as id),
  - verbatim + semantic detection over the live store,
  - the merge path (soft-delete MERGED, merged_into pointer),
  - idempotency (re-applying an applied plan reports already_applied, no
    re-merge that would duplicate the revision trail).

Isolation: a temp engine + monkeypatched AsyncSessionLocal (in database and
grouping_tools) so the real infofact.db is never touched. Needs the container
env (embeddings + LLM key) but NOT a real capture run.
"""
import asyncio
import sys
import tempfile
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import backend.database as db_mod
import backend.agents.tools.grouping_tools as gt_mod
import backend.models  # noqa: F401 — registers all tables on Base.metadata
from backend.models import (
    Base,
    Priority,
    Project,
    ReqStatus,
    ReqType,
    RequirementItem,
)
from backend.agents.pipelines.grouping import build_grouping_plan
from backend.agents.tools.grouping_tools import make_grouping_tools
from backend.services import requirement_store as store

# Seeded requirements. REQ-001==REQ-002 (verbatim), REQ-004==REQ-005 (verbatim),
# REQ-003 ~ REQ-001 (semantic, near-paraphrase), REQ-006 unique.
SEEDED = [
    ("REQ-001", "El sistema debe autenticar usuarios mediante Google OAuth.", 0.90),
    ("REQ-002", "El sistema debe autenticar usuarios mediante Google OAuth.", 0.80),
    ("REQ-003", "Autenticacion de usuarios con Google OAuth 2.0.", 0.85),
    ("REQ-004", "El sistema debe exportar reportes en formato PDF.", 0.90),
    ("REQ-005", "El sistema debe exportar reportes en formato PDF.", 0.70),
    ("REQ-006", "El sistema debe enviar notificaciones por correo electronico.", 0.90),
]


async def _seed(session) -> int:
    proj = Project(user_id=1, name="smoke", slug="smoke", description="seeded")
    session.add(proj)
    await session.flush()
    pid = proj.id
    for code, stmt, conf in SEEDED:
        session.add(RequirementItem(
            project_id=pid, code=code, statement=stmt,
            type=ReqType.FUNCTIONAL, priority=Priority.MUST,
            status=ReqStatus.VALIDATED, confidence=conf,
        ))
    await session.commit()
    return pid


def _has_group(plan, a: str, b: str) -> bool:
    """True if some group contains both codes (one as keeper, other as member)."""
    for g in plan.groups:
        codes = {g.keeper_code, *g.member_codes}
        if a in codes and b in codes:
            return True
    return False


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp / 'smoke.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        # Point both the database module and the grouping tools at the temp DB
        # so review_grouping / apply_grouping_plan (which open their own sessions)
        # read/write the seeded store, not the real infofact.db.
        orig_db, orig_gt = db_mod.AsyncSessionLocal, gt_mod.AsyncSessionLocal
        db_mod.AsyncSessionLocal = sm
        gt_mod.AsyncSessionLocal = sm
        try:
            async with sm() as session:
                pid = await _seed(session)

            # --- build_grouping_plan over the live store ---
            async with sm() as session:
                plan = await build_grouping_plan(session, pid, project="smoke")
            print(f"[build] grupos detectados: {len(plan.groups)}")
            for g in plan.groups:
                print(
                    f"  keeper={g.keeper_code} members={g.member_codes} "
                    f"reason={g.reason!r} confidence={g.confidence}"
                )
            # Verbatim dups are deterministic (exact_dedup, no LLM judge).
            assert _has_group(plan, "REQ-001", "REQ-002"), \
                "esperaba grupo verbatim {REQ-001, REQ-002}"
            assert _has_group(plan, "REQ-004", "REQ-005"), \
                "esperaba grupo verbatim {REQ-004, REQ-005}"

            # --- review_grouping writes the plan ---
            ws = tmp / "workspace"
            ws.mkdir()
            tools = make_grouping_tools(pid, ws)
            review = next(t for t in tools if t.name == "review_grouping")
            apply = next(t for t in tools if t.name == "apply_grouping_plan")

            res_review = await review.ainvoke({})
            print(f"[review] plan={res_review['plan_path']} "
                  f"groups={res_review['group_count']}")
            assert res_review["group_count"] >= 1
            plan_path = res_review["plan_path"]

            # --- apply_grouping_plan merges ---
            res_apply = await apply.ainvoke({"plan_path": plan_path})
            print(
                f"[apply] status={res_apply['status']} "
                f"applied={res_apply['applied']} "
                f"already_applied={res_apply['already_applied']} "
                f"invalid={res_apply['invalid']}"
            )
            assert res_apply["applied"] >= 1, "esperaba al menos 1 grupo aplicado"

            # Verify DB state: members flipped to MERGED with a merged_into pointer.
            async with sm() as session:
                items = await store.list_requirements(session, pid, include_deleted=True)
            by_code = {it.code: it for it in items}
            merged = sorted(c for c, it in by_code.items() if it.status == ReqStatus.MERGED)
            print(f"[db] merged codes: {merged}")
            assert len(merged) >= 1, "esperaba al menos un item MERGED tras aplicar"
            for code in merged:
                assert by_code[code].merged_into is not None, \
                    f"{code} esta MERGED pero merged_into es None"
            # Keepers of applied groups must still be live.
            keepers_live = [
                g["keeper"] for g in res_apply["groups"] if g["status"] == "applied"
            ]
            for code in keepers_live:
                assert by_code[code].status != ReqStatus.MERGED, \
                    f"keeper {code} no deberia quedar MERGED"

            # --- re-apply the same plan -> idempotent ---
            res_reapply = await apply.ainvoke({"plan_path": plan_path})
            print(
                f"[re-apply] applied={res_reapply['applied']} "
                f"already_applied={res_reapply['already_applied']}"
            )
            assert res_reapply["applied"] == 0, \
                "re-aplicar no debe fusionar nada nuevo (merge no idempotente)"
            assert res_reapply["already_applied"] >= 1

            print("OK: grouping build + review + apply + idempotency sobre store sembrado")
        finally:
            db_mod.AsyncSessionLocal = orig_db
            gt_mod.AsyncSessionLocal = orig_gt
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
