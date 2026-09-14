"""Tests del núcleo anti-goteo del pipeline de SRS.

Cubre los mecanismos que cortan el ciclo «cura N blockers → el re-juez
descubre N nuevos» verificado en la sesión 16 de Planitrack2.0:

- ``merge_findings`` (curación con memoria): preserva fixed/waived cuando el
  enunciado no cambió, reabre si cambió, borra los desaparecidos.
- ``analyze_quality`` delta: la Fase B LLM solo re-juzga ítems nuevos o
  editados; los intactos reutilizan el veredicto persistido y el inventario
  (``blockers_detail``) sigue siendo COMPLETO.
- ``resolve_findings`` + merge: lo cerrado formalmente no reaparece.
- Cobertura: hallazgo agregado para reqs vivos sin goal.
- Ciclo DRAFT: materialización temprana + promoción in-place en commit, y
  ``get_latest_srs`` salta los DRAFT.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.models import Base, Priority, Project, ReqType, RequirementItem
from backend.models.srs import (
    FindingDimension,
    FindingSeverity,
    FindingScope,
    FindingStatus,
    Goal,
    GoalKind,
    GoalLink,
    GoalStatus,
    LinkRelation,
    RequirementFinding,
    SrsStatus,
)
from backend.services import srs_coverage, srs_quality, srs_store


async def _fresh_db():
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return sm, tmp, engine


async def _seed(session, n: int = 3) -> int:
    proj = Project(user_id=1, name="ad", slug="ad", description="t")
    session.add(proj)
    await session.flush()
    for i in range(n):
        session.add(
            RequirementItem(
                project_id=proj.id,
                code=f"REQ-{i:04d}",
                statement=f"El sistema debe hacer algo numero {i}.",
                type=ReqType.FUNCTIONAL,
                priority=Priority.MUST,
            )
        )
    await session.commit()
    return proj.id


def _finding(pid: int, req_id: int, rule: str = "llm.test_blocker", **over):
    d = {
        "scope": FindingScope.ITEM,
        "req_id": req_id,
        "dimension": FindingDimension.AMBIGUITY,
        "rule_id": rule,
        "severity": FindingSeverity.BLOCKER,
        "message": "Problema de prueba",
        "suggestion": None,
        "detected_by": "agent",
    }
    d.update(over)
    return d


async def _insert_finding(
    session, pid: int, req_id: int, *, rule: str = "llm.test_blocker",
    status: FindingStatus = FindingStatus.OPEN,
    req_fingerprint: str | None = None,
) -> RequirementFinding:
    row = RequirementFinding(
        project_id=pid,
        req_id=req_id,
        scope=FindingScope.ITEM,
        dimension=FindingDimension.AMBIGUITY,
        rule_id=rule,
        severity=FindingSeverity.BLOCKER,
        message="Problema de prueba",
        status=status,
        req_fingerprint=req_fingerprint,
        detected_by="agent",
    )
    session.add(row)
    await session.commit()
    return row


def _fp(statement: str) -> str:
    return srs_quality.quality_fingerprint(statement, "functional")


# ---------------------------------------------------------------------------
# merge_findings: curación con memoria
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_preserves_curation_when_statement_unchanged():
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=2)
            reqs = (
                await session.execute(
                    sa_select(RequirementItem).order_by(RequirementItem.id)
                )
            ).scalars().all()
            fp = _fp(reqs[0].statement)
            await _insert_finding(
                session, pid, reqs[0].id, status=FindingStatus.WAIVED,
                req_fingerprint=fp,
            )

            stats = await srs_store.merge_findings(
                session, pid, [_finding(pid, reqs[0].id)]
            )

            assert stats["preserved"] == 1
            assert stats["inserted"] == 0
            row = (
                await session.execute(
                    sa_select(RequirementFinding).where(
                        RequirementFinding.req_id == reqs[0].id
                    )
                )
            ).scalar_one()
            assert row.status == FindingStatus.WAIVED
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_merge_reopens_when_statement_changed():
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=1)
            req = (
                await session.execute(sa_select(RequirementItem))
            ).scalar_one()
            await _insert_finding(
                session, pid, req.id, status=FindingStatus.FIXED,
                req_fingerprint=_fp("enunciado viejo"),
            )

            await srs_store.merge_findings(
                session, pid, [_finding(pid, req.id)]
            )

            row = (
                await session.execute(sa_select(RequirementFinding))
            ).scalar_one()
            assert row.status == FindingStatus.OPEN
            assert row.resolved_at is None
            assert row.req_fingerprint == _fp(req.statement)
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_merge_deletes_absent_and_inserts_new():
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=2)
            reqs = (
                await session.execute(
                    sa_select(RequirementItem).order_by(RequirementItem.id)
                )
            ).scalars().all()
            # Hallazgo viejo sobre req0 con una regla que la tanda ya no trae.
            await _insert_finding(
                session, pid, reqs[0].id, rule="llm.viejo"
            )

            stats = await srs_store.merge_findings(
                session, pid, [_finding(pid, reqs[1].id)]
            )

            assert stats["deleted"] == 1
            assert stats["inserted"] == 1
            rows = (
                await session.execute(sa_select(RequirementFinding))
            ).scalars().all()
            assert len(rows) == 1
            assert rows[0].req_id == reqs[1].id
            assert rows[0].status == FindingStatus.OPEN
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_merge_stamps_fresh_judged_fingerprints_atomically():
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=2)
            reqs = (
                await session.execute(
                    sa_select(RequirementItem).order_by(RequirementItem.id)
                )
            ).scalars().all()

            await srs_store.merge_findings(
                session,
                pid,
                [_finding(pid, reqs[0].id), _finding(pid, reqs[1].id)],
                fresh_judged_req_ids=[reqs[0].id, reqs[1].id],
            )

            for req in reqs:
                assert req.quality_fingerprint == _fp(req.statement)
                assert req.quality_judged_at is not None
    finally:
        await engine.dispose()
        tmp.cleanup()


# ---------------------------------------------------------------------------
# analyze_quality delta + inventario completo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_quality_delta_rejudges_only_changed(monkeypatch):
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=3)
            judged: list[int] = []

            async def _fake_judge(batch):
                out = {}
                for it in batch:
                    judged.append(it.id)
                    out[it.id] = [
                        _finding(pid, it.id, rule="llm.test_blocker")
                    ]
                return out

            monkeypatch.setattr(srs_quality, "_judge_batch", _fake_judge)

            # Primera corrida: todos sin fingerprint -> juez completo.
            summary, findings = await srs_quality.analyze_quality(
                session, pid
            )
            assert summary["items_judged"] == 3
            assert summary["items_reused"] == 0
            assert summary["blockers"] == 3
            # El inventario de blockers es COMPLETO y habla en códigos.
            assert {b["req"] for b in summary["blockers_detail"]} == {
                "REQ-0000", "REQ-0001", "REQ-0002",
            }
            await srs_store.merge_findings(
                session, pid, findings,
                fresh_judged_req_ids=summary["fresh_judged_req_ids"],
            )

        # Segunda corrida SIN ediciones: reutiliza, cero llamadas al juez.
        judged.clear()
        async with sm() as session:
            summary2, findings2 = await srs_quality.analyze_quality(
                session, pid
            )
            assert judged == []
            assert summary2["items_reused"] == 3
            assert summary2["blockers"] == 3  # inventario completo igualmente
            await srs_store.merge_findings(session, pid, findings2)

        # Tercera corrida con UN req editado: delta de exactamente 1.
        async with sm() as session:
            req1 = (
                await session.execute(
                    sa_select(RequirementItem).where(
                        RequirementItem.code == "REQ-0001"
                    )
                )
            ).scalar_one()
            req1.statement = "El sistema debe hacer algo numero 1 mejorado."
            await session.commit()

        judged.clear()
        async with sm() as session:
            summary3, findings3 = await srs_quality.analyze_quality(
                session, pid
            )
            assert len(judged) == 1
            assert summary3["items_judged"] == 1
            assert summary3["items_reused"] == 2
            # El enunciado editado sigue teniendo blocker (re-juzgado fresco)
            # y los otros dos se conservan del store: inventario completo.
            assert summary3["blockers"] == 3
            await srs_store.merge_findings(
                session, pid, findings3,
                fresh_judged_req_ids=summary3["fresh_judged_req_ids"],
            )
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_resolved_blocker_survives_merge_of_unchanged_statement(
    monkeypatch,
):
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=1)
            req = (
                await session.execute(sa_select(RequirementItem))
            ).scalar_one()
            await _insert_finding(
                session, pid, req.id, req_fingerprint=_fp(req.statement)
            )

            async def _fake_judge(batch):
                return {
                    it.id: [_finding(pid, it.id)] for it in batch
                }

            monkeypatch.setattr(srs_quality, "_judge_batch", _fake_judge)

            # El usuario decide tolerar el hallazgo (cierre formal).
            res = await srs_store.resolve_findings(
                session, pid, targets=[(req.id, "llm.test_blocker")],
                status=FindingStatus.WAIVED, note="convivimos con el typo",
            )
            assert res["resolved"] == 1

            # Re-análisis que SIGUE detectando el hallazgo + merge: el waive
            # sobrevive porque el enunciado no cambió (antes el replace lo
            # borraba y reaparecía como OPEN en cada commit).
            _, findings = await srs_quality.analyze_quality(session, pid)
            await srs_store.merge_findings(session, pid, findings)

            row = (
                await session.execute(
                    sa_select(RequirementFinding).where(
                        RequirementFinding.rule_id == "llm.test_blocker"
                    )
                )
            ).scalar_one()
            assert row.status == FindingStatus.WAIVED
            assert row.resolution_note == "convivimos con el typo"
            assert row.resolved_by == "agent"
    finally:
        await engine.dispose()
        tmp.cleanup()


# ---------------------------------------------------------------------------
# Cobertura: reqs vivos sin goal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_reports_unlinked_requirements():
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=3)
            reqs = (
                await session.execute(
                    sa_select(RequirementItem).order_by(RequirementItem.id)
                )
            ).scalars().all()
            goal = Goal(
                project_id=pid, code="GOAL-AA01", kind=GoalKind.FUNCTIONAL_GOAL,
                statement="Goal de prueba", status=GoalStatus.PROPOSED,
            )
            session.add(goal)
            await session.flush()
            session.add(
                GoalLink(
                    goal_id=goal.id, req_id=reqs[0].id,
                    relation=LinkRelation.REALIZES,
                )
            )
            await session.commit()

            coverage, findings = await srs_coverage.compute_coverage(
                session, pid
            )

            assert coverage["goals"]["unlinked_requirements"] == 2
            assert set(coverage["goals"]["unlinked_codes"]) == {
                "REQ-0001", "REQ-0002",
            }
            unlinked = [
                f for f in findings
                if f["rule_id"] == "gore.unlinked_requirements"
            ]
            assert len(unlinked) == 1
            # 2/3 > 10% del catálogo sin trazabilidad -> MAJOR.
            assert (
                unlinked[0]["severity"] == FindingSeverity.MAJOR
            )
    finally:
        await engine.dispose()
        tmp.cleanup()


# ---------------------------------------------------------------------------
# Ciclo DRAFT: materialización temprana
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_lifecycle_promotion_and_latest_skips_draft():
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=1)

            draft = await srs_store.create_srs_draft(
                session, pid, {"markdown": "# draft", "requirement_count": 1}
            )
            assert draft.status == SrsStatus.DRAFT
            # El DRAFT no es «última versión» (no es base de seed).
            assert await srs_store.get_latest_srs(session, pid) is None

            promoted = await srs_store.promote_srs_draft(
                session, pid, draft.version,
                {"markdown": "# final", "requirement_count": 1},
            )
            assert promoted.status == SrsStatus.CANDIDATE
            assert promoted.version == draft.version  # in-place
            assert promoted.markdown == "# final"

            latest = await srs_store.get_latest_srs(session, pid)
            assert latest is not None and latest.version == draft.version

            # Promover algo que no es DRAFT es un error de programación.
            with pytest.raises(ValueError):
                await srs_store.promote_srs_draft(
                    session, pid, draft.version, {"markdown": "x"}
                )
    finally:
        await engine.dispose()
        tmp.cleanup()
