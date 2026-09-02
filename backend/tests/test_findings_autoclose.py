"""Cierre automático de hallazgos en las transiciones del ciclo de vida.

Síntoma que motiva esto (sesiones 7-9 de Planitrack): 2.203 hallazgos, el
100% en estado OPEN. El agente corregía o descartaba requerimientos y los
hallazgos quedaban abiertos para siempre porque ``replace_findings`` (la otra
vía de cierre) solo corre cuando se re-ejecuta el análisis completo del SRS.

Pines de esta suite:

- ``reject_requirement`` cierra los hallazgos OPEN del item rechazado (el
  problema desaparece junto con el requerimiento; los hijos desprendidos
  siguen vivos y conservan sus propios hallazgos).
- ``merge_requirements`` cierra los hallazgos OPEN de los items absorbidos;
  los del keeper siguen abiertos (siguen aplicando).
- ``split_requirement`` cierra los hallazgos OPEN del original SUPERSEDED
  (el split ES la corrección del smell de atomicidad).
- Nada distinto de OPEN se toca: FIXED/WAIVED conservan su estado de curación.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.models import (
    Base,
    FindingStatus,
    Priority,
    Project,
    ReqType,
    RequirementFinding,
    RequirementItem,
)
from backend.services import requirement_store as store


async def _setup() -> tuple[async_sessionmaker, int]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sm() as session:
        proj = Project(user_id=1, name="p", slug="p", description="t")
        session.add(proj)
        await session.flush()
        pid = proj.id
        await session.commit()
    return sm, pid


def _req(pid: int, code: str, statement: str = "s") -> RequirementItem:
    return RequirementItem(
        project_id=pid,
        code=code,
        statement=statement,
        type=ReqType.FUNCTIONAL,
        priority=Priority.MUST,
        status="draft",
        confidence=0.9,
        explicit=True,
        created_by="test",
    )


def _finding(pid: int, req_id: int, rule_id: str,
             status: FindingStatus = FindingStatus.OPEN) -> RequirementFinding:
    return RequirementFinding(
        project_id=pid,
        req_id=req_id,
        scope="ITEM",
        dimension="requirement_smell",
        rule_id=rule_id,
        severity="minor",
        message="m",
        status=status,
        detected_by="programmatic",
    )


async def _statuses(sm, pid: int) -> dict[str, str]:
    async with sm() as session:
        rows = (await session.execute(
            select(RequirementFinding).where(
                RequirementFinding.project_id == pid)
        )).scalars().all()
        return {f.rule_id: f.status.value for f in rows}


@pytest.mark.asyncio
async def test_reject_closes_open_findings():
    sm, pid = await _setup()
    async with sm() as session:
        a = _req(pid, "REQ-A", "El sistema debe exportar su reporte.")
        b = _req(pid, "REQ-B", "Otro requerimiento vivo.")
        session.add_all([a, b])
        await session.flush()
        session.add(_finding(pid, a.id, "smell.pronoun"))
        session.add(_finding(pid, b.id, "smell.vague_term"))
        await session.commit()
        id_a, id_b = a.id, b.id

    async with sm() as session:
        await store.reject_requirement(
            session, id_a, reason="duplicado", changed_by="test")

    statuses = await _statuses(sm, pid)
    assert statuses["smell.pronoun"] == "fixed"  # el req rechazado cerró
    assert statuses["smell.vague_term"] == "open"  # el req vivo sigue abierto


@pytest.mark.asyncio
async def test_merge_closes_findings_of_absorbed_items_only():
    sm, pid = await _setup()
    async with sm() as session:
        a = _req(pid, "REQ-A", "keeper")
        b = _req(pid, "REQ-B", "duplicado uno")
        c = _req(pid, "REQ-C", "duplicado dos")
        session.add_all([a, b, c])
        await session.flush()
        session.add(_finding(pid, a.id, "rule.keeper"))
        session.add(_finding(pid, b.id, "rule.dup1"))
        session.add(_finding(pid, c.id, "rule.dup2"))
        # Curación humana previa sobre un absorbido: no se pisa.
        session.add(_finding(pid, c.id, "rule.waived",
                             status=FindingStatus.WAIVED))
        await session.commit()
        ids = [a.id, b.id, c.id]

    async with sm() as session:
        await store.merge_requirements(
            session, ids, changed_by="test")

    statuses = await _statuses(sm, pid)
    assert statuses["rule.dup1"] == "fixed"
    assert statuses["rule.dup2"] == "fixed"
    assert statuses["rule.keeper"] == "open"  # el keeper conserva sus hallazgos
    assert statuses["rule.waived"] == "waived"


@pytest.mark.asyncio
async def test_split_closes_findings_of_superseded_original():
    sm, pid = await _setup()
    async with sm() as session:
        a = _req(pid, "REQ-A", "El administrador debe crear, editar y eliminar usuarios.")
        session.add(a)
        await session.flush()
        session.add(_finding(pid, a.id, "smell.umbrella"))
        await session.commit()
        id_a = a.id

    async with sm() as session:
        await store.split_requirement(
            session, id_a, ["crear usuarios", "editar usuarios",
                            "eliminar usuarios"],
            changed_by="test")

    statuses = await _statuses(sm, pid)
    assert statuses["smell.umbrella"] == "fixed"  # el split ES la corrección
