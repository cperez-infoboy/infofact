"""Tests del motor de goals en dos fases (goals + links por lotes).

Regresión del incidente v6 de Planitrack2.0: la inferencia monolítica
(catálogo completo + goals + links en un único JSON) truncaba la salida
(finish_reason=length), el JSON no parseaba y la etapa devolvía 0 goals en
silencio tras quemar ~72 minutos por intento. Pins de esta suite:

- la salida se acota por diseño: fase goals sin links + links por lotes,
- el truncado detectado degrada a un LLM con thinking desactivado,
- el fallo de la fase goals es un error EXPLÍCITO, nunca un modelo vacío,
- un lote de links caído no arrastra al resto (persistencia parcial),
- dedupe de goals por alias y de aristas repetidas del LLM.

El LLM es stubado en todos los casos (sin red); la DB es sqlite temporal.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.agents.llm import StructuredOutputTruncatedError
from backend.models import Base, Priority, Project, ReqType, RequirementItem
from backend.models.srs import Goal, GoalLink
from backend.services import goals_engine as ge

_THINKING_OFF = {"thinking": {"type": "disabled"}}


# ---------------------------------------------------------------------------
# Fakes del LLM estructurado
# ---------------------------------------------------------------------------


def _patch_llm(monkeypatch, plans: dict) -> list[dict]:
    """Stub de ge.structured_llm con plan por schema.

    plans: {schema: [resultado | excepción, ...]}. El último elemento de la
    lista SE REPITE (para planes de fallo persistente no hace falta enumerar
    cada reintento). Devuelve el registro de llamadas a la factory.
    """
    calls: list[dict] = []

    def factory(schema, *, temperature=0.0, extra_body=None):
        calls.append({"schema": schema, "extra_body": extra_body})
        plan = plans[schema]

        async def ainvoke(msgs, config=None, **kwargs):
            item = plan.pop(0) if len(plan) > 1 else plan[0]
            if isinstance(item, Exception):
                raise item
            return item

        return SimpleNamespace(ainvoke=ainvoke)

    monkeypatch.setattr(ge, "structured_llm", factory)
    monkeypatch.setattr(ge, "disable_thinking_body", lambda: _THINKING_OFF)
    return calls


def _goal(code: str, kind: str = "functional_goal", parent: str | None = None):
    return ge.InferredGoal(
        code=code, kind=kind, statement=f"Goal {code}", parent_code=parent
    )


def _links(pairs: list[tuple[str, str]]) -> ge.GoalLinks:
    return ge.GoalLinks(
        links=[
            ge.InferredLink(goal_code=g, req_code=r, relation="realizes")
            for g, r in pairs
        ]
    )


def _plans(goals_out, links_outs) -> dict:
    return {ge.GoalGoals: [goals_out], ge.GoalLinks: list(links_outs)}


# ---------------------------------------------------------------------------
# Fixture de DB
# ---------------------------------------------------------------------------


async def _fresh_db():
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return sm, tmp, engine


async def _seed(session, n: int = 6) -> int:
    proj = Project(user_id=1, name="g", slug="g", description="t")
    session.add(proj)
    await session.flush()
    for i in range(n):
        session.add(
            RequirementItem(
                project_id=proj.id,
                code=f"REQ-{i:04d}",
                statement=f"Requerimiento de prueba numero {i}.",
                type=ReqType.FUNCTIONAL,
                priority=Priority.MUST,
            )
        )
    await session.commit()
    return proj.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_phases_split_and_link_batches(monkeypatch):
    """Fase 1 infiere solo goals; fase 2 lotea links (2 reqs por lote => 3 lotes)."""
    monkeypatch.setattr(ge, "_LINK_BATCH_SIZE", 2)
    calls = _patch_llm(
        monkeypatch,
        _plans(
            ge.GoalGoals(goals=[_goal("G1"), _goal("G2")]),
            [
                _links([("G1", "REQ-0000"), ("G2", "REQ-0001")]),
                _links([("G1", "REQ-0002")]),
                _links([]),
            ],
        ),
    )
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=6)
            summary = await ge.infer_goals(session, pid)

        assert summary["goals"] == 2
        assert summary["links"] == 3
        assert summary["links_partial"] is False
        assert summary["link_batches_failed"] == 0
        # Primera llamada: fase goals, sin extra_body (thinking default).
        assert calls[0]["schema"] is ge.GoalGoals
        assert calls[0]["extra_body"] is None
        # El resto: exactamente 3 lotes de links, sin thinking desactivado.
        link_calls = [c for c in calls[1:] if c["schema"] is ge.GoalLinks]
        assert len(link_calls) == 3
        assert all(c["extra_body"] is None for c in link_calls)

        async with sm() as session:
            assert len((await session.execute(sa_select(Goal))).scalars().all()) == 2
            assert (
                len((await session.execute(sa_select(GoalLink))).scalars().all()) == 3
            )
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_truncated_goals_switch_to_no_thinking(monkeypatch):
    """Truncado en fase goals -> reintento con thinking desactivado."""
    trunc = StructuredOutputTruncatedError("truncated")
    plans = _plans(None, [])
    # Dos truncados: la fase con thinking (max_parse=2) se agota y recién
    # ahí se cambia al camino sin thinking, que recupera con el 3er plan.
    plans[ge.GoalGoals] = [trunc, trunc, ge.GoalGoals(goals=[_goal("G1")])]
    plans[ge.GoalLinks] = [_links([("G1", "REQ-0000")])]
    calls = _patch_llm(monkeypatch, plans)
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=1)
            summary = await ge.infer_goals(session, pid)

        assert summary["goals"] == 1
        assert calls[0]["extra_body"] is None
        assert calls[1]["extra_body"] == _THINKING_OFF
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_truncated_links_batch_also_degrades(monkeypatch):
    """El mismo remedio aplica por lote: un lote truncado reintenta sin thinking."""
    monkeypatch.setattr(ge, "_LINK_BATCH_SIZE", 1)
    # Concurrencia 1: el orden de consumo del plan compartido queda determinista.
    monkeypatch.setattr(ge, "DEFAULT_CONCURRENCY", 1)
    trunc = StructuredOutputTruncatedError("truncated")
    plans = _plans(
        ge.GoalGoals(goals=[_goal("G1")]),
        # Lote 1: dos truncados agotan el camino con thinking; el reintento
        # sin thinking recupera. Lote 2: feliz directo.
        [trunc, trunc, _links([("G1", "REQ-0000")]), _links([("G1", "REQ-0001")])],
    )
    calls = _patch_llm(monkeypatch, plans)
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=2)
            summary = await ge.infer_goals(session, pid)

        assert summary["links"] == 2
        assert summary["links_partial"] is False
        link_calls = [c for c in calls if c["schema"] is ge.GoalLinks]
        # El primer intento del lote 1 usa thinking default; el reintento que
        # lo recupera es el único con thinking desactivado.
        assert sum(
            1 for c in link_calls if c["extra_body"] == _THINKING_OFF
        ) == 1
        assert link_calls[0]["extra_body"] is None
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_persistent_truncation_in_goals_raises(monkeypatch):
    """Truncado persistente (aun sin thinking) => error explícito, nunca goals=0."""
    trunc = StructuredOutputTruncatedError("truncated")
    plans = {ge.GoalGoals: [trunc], ge.GoalLinks: []}
    _patch_llm(monkeypatch, plans)
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=3)
            with pytest.raises(ge.GoalsInferenceError):
                await ge.infer_goals(session, pid)
            # Nada persistido.
            assert (
                len((await session.execute(sa_select(Goal))).scalars().all()) == 0
            )
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_zero_goals_inferred_raises(monkeypatch):
    """El absurdo «989 reqs, 0 goals» ahora es un error, no un modelo vacío."""
    _patch_llm(monkeypatch, _plans(ge.GoalGoals(goals=[]), []))
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=2)
            with pytest.raises(ge.GoalsInferenceError, match="ningún goal"):
                await ge.infer_goals(session, pid)
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_failed_link_batch_persists_partial(monkeypatch):
    """Un lote de links caído no arrastra al resto: persiste parcial y avisa."""
    monkeypatch.setattr(ge, "_LINK_BATCH_SIZE", 2)
    # Concurrencia 1: el orden de consumo del plan compartido queda determinista.
    monkeypatch.setattr(ge, "DEFAULT_CONCURRENCY", 1)
    boom = ValueError("parse broke")
    plans = _plans(
        ge.GoalGoals(goals=[_goal("G1")]),
        # Lote 2 (REQ-0002..0003) agota sus reintentos de parse y cae; los
        # lotes 1 y 3 aportan sus links igualmente.
        [
            _links([("G1", "REQ-0000")]),
            boom,
            boom,
            _links([("G1", "REQ-0003")]),
        ],
    )
    _patch_llm(monkeypatch, plans)
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=6)
            summary = await ge.infer_goals(session, pid)

        assert summary["links"] == 2
        assert summary["links_partial"] is True
        assert summary["link_batches_failed"] == 1
        async with sm() as session:
            rows = (await session.execute(sa_select(GoalLink))).scalars().all()
            assert len(rows) == 2
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_no_live_requirements_returns_zero_without_llm(monkeypatch):
    calls = _patch_llm(monkeypatch, _plans(None, []))
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=0)
            summary = await ge.infer_goals(session, pid)
            assert summary["goals"] == 0
            assert summary["links"] == 0
            assert calls == []  # ni una llamada con el store vacío
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_goal_alias_dedupe_and_dangling_parent_dropped(monkeypatch):
    """Alias repetido se deduplica; parent_code hacia un goal no emitido se corta."""
    plans = _plans(
        ge.GoalGoals(
            goals=[
                _goal("G1"),
                _goal("G1"),  # alias repetido: fuera
                _goal("G2", parent="G1"),
                _goal("G3", parent="GHOST"),  # padre inexistente -> raíz
            ]
        ),
        [_links([("G1", "REQ-0000"), ("G1", "REQ-0000")])],  # arista duplicada
    )
    _patch_llm(monkeypatch, plans)
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=1)
            summary = await ge.infer_goals(session, pid)

        assert summary["goals"] == 3  # G1, G2, G3 (alias repetido descartado)
        assert summary["links"] == 1  # duplicada descartada
        async with sm() as session:
            rows = {
                g.code: g.parent_id
                for g in (await session.execute(sa_select(Goal))).scalars()
            }
            # G1, G2 (hijo de G1), G3 (raíz: padre fantasma cortado).
            assert len(rows) == 3
            parents = list(rows.values())
            assert parents.count(None) == 2
            assert len([p for p in parents if p is not None]) == 1
    finally:
        await engine.dispose()
        tmp.cleanup()
