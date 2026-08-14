#!/usr/bin/env python
"""Smoke: grouping review E2E over a SEEDED store (no /captura needed).

Builds a throwaway SQLite DB, seeds a project with duplicate requirements
(verbatim + semantic + manual + multi-document), and exercises
build_grouping_plan (unfiltered + scope filters) -> review_grouping tool
(unfiltered and document-filtered) -> set_group_decision -> apply_grouping_plan
-> re-apply (idempotency). Validates:

  - the RequirementItem -> RawRequirement adapter (code as id),
  - verbatim + semantic detection over the live store,
  - the scope filters: ``types`` (ReqType) and ``documents``
    (filename/rel_path/absolute path), including the exclusion of manual
    items (source=None) when a document filter is active,
  - the merge path (soft-delete MERGED, merged_into pointer),
  - idempotency (re-applying an applied plan reports already_applied, no
    re-merge that would duplicate the revision trail).

Isolation: a temp engine + monkeypatched AsyncSessionLocal (in database and
grouping_tools) so the real infofact.db is never touched. Needs the host env
(embeddings + LLM key for the borderline judge) but NOT a real capture run.
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
    ProjectDocument,
    ReqStatus,
    ReqType,
    RequirementItem,
)
from backend.agents.pipelines.grouping import build_grouping_plan
from backend.agents.tools.grouping_tools import make_grouping_tools
from backend.services import requirement_store as store

# Container paths de los documentos sembrados (source["document_id"] guarda
# el path absoluto dentro del container del agente).
SPEC_ABS = "/workspaces/smoke/docs/spec.pdf"
RFQ_ABS = "/workspaces/smoke/other/rfq.docx"

# Seeded requirements. REQ-001==REQ-002 (verbatim), REQ-004==REQ-005
# (verbatim), REQ-003 ~ REQ-001 (semantic, near-paraphrase), REQ-006 unique;
# REQ-101/102 verbatim SECURITY desde rfq.docx; REQ-201/202 verbatim manual
# (source=None, creados a mano por el usuario).
SEEDED = [
    ("REQ-001", "El sistema debe autenticar usuarios mediante Google OAuth.", 0.90, ReqType.FUNCTIONAL, {"document_id": SPEC_ABS}),
    ("REQ-002", "El sistema debe autenticar usuarios mediante Google OAuth.", 0.80, ReqType.FUNCTIONAL, {"document_id": SPEC_ABS}),
    ("REQ-003", "Autenticacion de usuarios con Google OAuth 2.0.", 0.85, ReqType.FUNCTIONAL, {"document_id": SPEC_ABS}),
    ("REQ-004", "El sistema debe exportar reportes en formato PDF.", 0.90, ReqType.FUNCTIONAL, {"document_id": SPEC_ABS}),
    ("REQ-005", "El sistema debe exportar reportes en formato PDF.", 0.70, ReqType.FUNCTIONAL, {"document_id": SPEC_ABS}),
    ("REQ-006", "El sistema debe enviar notificaciones por correo electronico.", 0.90, ReqType.FUNCTIONAL, {"document_id": SPEC_ABS}),
    ("REQ-101", "El sistema debe cifrar el trafico con TLS 1.3.", 0.90, ReqType.SECURITY, {"document_id": RFQ_ABS}),
    ("REQ-102", "El sistema debe cifrar el trafico con TLS 1.3.", 0.80, ReqType.SECURITY, {"document_id": RFQ_ABS}),
    ("REQ-201", "El sistema debe permitir buscar requerimientos por codigo.", 0.90, ReqType.FUNCTIONAL, None),
    ("REQ-202", "El sistema debe permitir buscar requerimientos por codigo.", 0.80, ReqType.FUNCTIONAL, None),
]


async def _seed(session) -> int:
    proj = Project(user_id=1, name="smoke", slug="smoke", description="seeded")
    session.add(proj)
    await session.flush()
    pid = proj.id
    for code, stmt, conf, rtype, source in SEEDED:
        session.add(RequirementItem(
            project_id=pid, code=code, statement=stmt,
            type=rtype, priority=Priority.MUST,
            status=ReqStatus.VALIDATED, confidence=conf, source=source,
        ))
    session.add(ProjectDocument(
        project_id=pid, rel_path="docs/spec.pdf", filename="spec.pdf",
        extension="pdf", size_bytes=10, sha256="a" * 64,
    ))
    session.add(ProjectDocument(
        project_id=pid, rel_path="other/rfq.docx", filename="rfq.docx",
        extension="docx", size_bytes=20, sha256="b" * 64,
    ))
    await session.commit()
    return pid


def _has_group(plan_or_groups, a: str, b: str) -> bool:
    """True if some group contains both codes (one as keeper, other as member).

    Accepts a GroupingPlan (``.groups`` of Group dataclasses) or the tool
    result's ``groups`` list (dicts with keeper/members code payloads).
    """
    groups = getattr(plan_or_groups, "groups", plan_or_groups)
    for g in groups:
        if hasattr(g, "keeper_code"):
            codes = {g.keeper_code, *g.member_codes}
        else:
            keeper = (g.get("keeper") or {}).get("code")
            members = [m.get("code") for m in g.get("members") or []]
            codes = {keeper, *members}
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

            # --- build_grouping_plan over the live store (unfiltered) ---
            async with sm() as session:
                plan = await build_grouping_plan(session, pid, project="smoke")
            print(
                f"[build] grupos detectados: {len(plan.groups)} "
                f"(considered={plan.considered} scope={plan.scope!r})"
            )
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
            assert plan.considered == len(SEEDED)

            # --- scope filter: types ---
            async with sm() as session:
                plan_f = await build_grouping_plan(
                    session, pid, project="smoke", types=["functional"]
                )
            print(
                f"[build types=functional] grupos: {len(plan_f.groups)} "
                f"(considered={plan_f.considered} scope={plan_f.scope!r})"
            )
            assert plan_f.considered < plan.considered, \
                "el filtro types debe reducir el alcance"
            assert plan_f.considered == 8  # fuera el par SECURITY de rfq.docx
            assert not _has_group(plan_f, "REQ-101", "REQ-102")

            # --- scope filter: documents via la tool (args plumbing) ---
            tools = make_grouping_tools(pid)
            review = next(t for t in tools if t.name == "review_grouping")
            apply = next(t for t in tools if t.name == "apply_grouping_plan")
            decide = next(t for t in tools if t.name == "set_group_decision")

            res_filtered = await review.ainvoke({"documents": ["spec.pdf"]})
            print(
                f"[review documents=spec.pdf] plan={res_filtered['plan_id']} "
                f"groups={res_filtered['group_count']} "
                f"considered={res_filtered['considered']} "
                f"scope={res_filtered['scope']!r}"
            )
            assert res_filtered["considered"] < plan.considered, \
                "el filtro documents debe reducir el alcance"
            assert res_filtered["considered"] == 6  # solo items con source en spec.pdf
            assert res_filtered["scope"] == "documentos: docs/spec.pdf"
            assert not _has_group(res_filtered["groups"], "REQ-201", "REQ-202"), \
                "items manuales (source=None) quedan fuera con filtro de docs"
            assert not _has_group(res_filtered["groups"], "REQ-101", "REQ-102")

            # --- review_grouping (sin filtros) persiste el plan en la DB ---
            res_review = await review.ainvoke({})
            print(
                f"[review] plan={res_review['plan_id']} "
                f"groups={res_review['group_count']} "
                f"considered={res_review['considered']}"
            )
            assert res_review["group_count"] >= 1
            plan_id = res_review["plan_id"]

            # --- curación: aceptar todos los grupos y aplicar ---
            # apply_plan solo fusiona grupos con decision 'accept' (pending se
            # salta), así que la curación es parte del flujo real.
            for g in res_review["groups"]:
                res_dec = await decide.ainvoke(
                    {"group_id": g["id"], "decision": "accept"}
                )
                assert "error" not in res_dec, res_dec

            res_apply = await apply.ainvoke({"plan_id": plan_id})
            print(
                f"[apply] status={res_apply['status']} "
                f"applied={res_apply['applied']} "
                f"already_applied={res_apply['already_applied']} "
                f"invalid={res_apply['invalid']} skipped={res_apply['skipped']}"
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
            keepers_live_ids = {
                g["keeper_id"]
                for g in res_apply["groups"]
                if g["status"] == "applied"
            }
            by_id = {it.id: it for it in items}
            for kid in keepers_live_ids:
                assert by_id[kid].status != ReqStatus.MERGED, \
                    f"keeper {kid} no deberia quedar MERGED"

            # --- re-apply the same plan -> idempotent ---
            res_reapply = await apply.ainvoke({"plan_id": plan_id})
            print(
                f"[re-apply] applied={res_reapply['applied']} "
                f"already_applied={res_reapply['already_applied']}"
            )
            assert res_reapply["applied"] == 0, \
                "re-aplicar no debe fusionar nada nuevo (merge no idempotente)"
            assert res_reapply["already_applied"] >= 1

            print("OK: grouping build + filtros + review + apply + idempotencia")
        finally:
            db_mod.AsyncSessionLocal = orig_db
            gt_mod.AsyncSessionLocal = orig_gt
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
