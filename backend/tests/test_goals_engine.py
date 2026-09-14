"""Tests del motor de goals en dos fases (goals + links por lotes).

Regresión del incidente v6 de Planitrack2.0: la inferencia monolítica
(catálogo completo + goals + links en un único JSON) truncaba la salida
(finish_reason=length), el JSON no parseaba y la etapa devolvía 0 goals en
silencio tras quemar ~72 minutos por intento. Pins de esta suite:

- la salida se acota por diseño: fase goals sin links + links por lotes,
- el truncado detectado degrada a un LLM con thinking desactivado,
- el fallo de la fase goals es un error EXPLÍCITO, nunca un modelo vacío,
- un lote de links caído no arrastra al resto (persistencia parcial),
- dedupe de goals por alias y de aristas repetidas del LLM,
- el avance por unidad (chunk de goals / lote de links) se reporta vía
  ``on_progress``, contando también las unidades que fallan.

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
from backend.models.srs import (
    Goal,
    GoalKind,
    GoalLink,
    GoalStatus,
    LinkRelation,
)
from backend.services import goals_engine as ge

_THINKING_OFF = {"thinking": {"type": "disabled"}}


# ---------------------------------------------------------------------------
# Fakes del LLM estructurado
# ---------------------------------------------------------------------------


def _patch_llm(monkeypatch, plans: dict) -> list[dict]:
    """Stub de ge.structured_llm con plan por schema.

    plans: {schema: [resultado | excepción, ...]}. El último elemento de la
    lista SE REPITE (para planes de fallo persistente no hace falta enumerar
    cada reintento). Devuelve el registro de llamadas a la factory; cada
    entrada lleva ``ainvoke_kwargs`` con los kwargs extra que viajaron a la
    invocación (p. ej. ``max_tokens``).
    """
    calls: list[dict] = []

    def factory(schema, *, temperature=0.0, extra_body=None):
        calls.append({"schema": schema, "extra_body": extra_body})
        plan = plans[schema]

        async def ainvoke(msgs, config=None, **kwargs):
            calls[-1]["ainvoke_kwargs"] = dict(kwargs)
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


def _goal_stmt(code: str, statement: str, parent: str | None = None):
    return ge.InferredGoal(
        code=code, kind="functional_goal", statement=statement, parent_code=parent
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
        # Lote 2 (REQ-0002..0003) agota sus reintentos de parse, cae, y el
        # RETRY externo también falla (plan final = boom persistente); los
        # lotes 1 y 3 aportan sus links igualmente. Consumo determinista con
        # concurrencia 1: lote1=ok, lote2=boom+boom, lote3=ok, retry=boom.
        [
            _links([("G1", "REQ-0000")]),
            boom,
            boom,
            _links([("G1", "REQ-0003")]),
            boom,
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
        assert summary["link_batches_retried"] == 0  # el retry no logró revivirlo
        async with sm() as session:
            rows = (await session.execute(sa_select(GoalLink))).scalars().all()
            assert len(rows) == 2
            # Los reqs del lote caído NO quedan sellados: la próxima corrida
            # los re-vincula (delta de goals_fingerprint).
            unsel = (
                await session.execute(
                    sa_select(RequirementItem).where(
                        RequirementItem.goals_fingerprint.is_(None)
                    )
                )
            ).scalars().all()
            assert {r.code for r in unsel} == {"REQ-0002", "REQ-0003"}
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_failed_link_batch_recovers_on_retry(monkeypatch):
    """El retry externo revive un lote caído por congestión transitoria."""
    monkeypatch.setattr(ge, "_LINK_BATCH_SIZE", 2)
    monkeypatch.setattr(ge, "DEFAULT_CONCURRENCY", 1)
    boom = ValueError("transient gateway error")
    plans = _plans(
        ge.GoalGoals(goals=[_goal("G1")]),
        # lote1=ok, lote2=boom+boom (cae), lote3=ok, retry del lote2=ok.
        [
            _links([("G1", "REQ-0000")]),
            boom,
            boom,
            _links([("G1", "REQ-0002")]),
            _links([("G1", "REQ-0004")]),
        ],
    )
    _patch_llm(monkeypatch, plans)
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=6)
            summary = await ge.infer_goals(session, pid)

        assert summary["link_batches_retried"] == 1
        assert summary["link_batches_failed"] == 0
        assert summary["links_partial"] is False
        async with sm() as session:
            rows = (await session.execute(sa_select(GoalLink))).scalars().all()
            assert len(rows) == 3
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


# ---------------------------------------------------------------------------
# Fase 1 por chunks (incidente 2026-08-29: la llamada monolítica con el
# catálogo completo no completaba en el gateway del proveedor).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase1_chunked_merge_renumbers_and_dedupes(monkeypatch):
    """Chunks separados: códigos renumerados globalmente, statement casi igual
    entre chunks colapsa al canónico y el hijo del duplicado lo referencia."""
    monkeypatch.setattr(ge, "GOALS_CHUNK_SIZE", 2)  # 4 reqs => 2 chunks
    monkeypatch.setattr(ge, "DEFAULT_CONCURRENCY", 1)  # orden determinista
    # Chunk 1 (REQ-0000..0001): raíz + hijo.
    # Chunk 2 (REQ-0002..0003): el mismo "Servir pedidos" con otro código
    # local + un goal nuevo.
    plans = _plans(None, [])
    plans[ge.GoalGoals] = [
        ge.GoalGoals(
            goals=[
                _goal_stmt("G1", "Servir pedidos"),
                _goal_stmt("G2", "Registrar altas", parent="G1"),
            ]
        ),
        ge.GoalGoals(
            goals=[
                _goal_stmt("G1", "Servir  pedidos"),  # duplicado del chunk 1
                _goal_stmt("G3", "Reportar métricas"),
            ]
        ),
    ]
    plans[ge.GoalLinks] = [_links([("G1", "REQ-0000")])]
    calls = _patch_llm(monkeypatch, plans)
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=4)
            summary = await ge.infer_goals(session, pid)

        # 3 goals: servir (canónico), altas (hijo), reportar. El duplicado no
        # se emite dos veces.
        assert summary["goals"] == 3
        assert summary["goals_chunks_failed"] == 0
        goal_calls = [c for c in calls if c["schema"] is ge.GoalGoals]
        assert len(goal_calls) == 2  # un chunk, una llamada
        async with sm() as session:
            rows = (
                await session.execute(sa_select(Goal).order_by(Goal.id))
            ).scalars().all()
            # El store asigna códigos opacos propios (GOAL-XXXX) y persiste
            # raíces primero: los alias G1..Gn solo resuelven referencias
            # internas (parent/links).
            assert sorted(g.statement for g in rows) == sorted(
                [
                    "Servir pedidos",
                    "Registrar altas",
                    "Reportar métricas",
                ]
            )
            by_stmt = {g.statement: g for g in rows}
            # El hijo del chunk 1 cuelga del canónico; "Reportar métricas" es
            # raíz (y el duplicado no se emitió dos veces).
            assert by_stmt["Registrar altas"].parent_id == (
                by_stmt["Servir pedidos"].id
            )
            assert by_stmt["Reportar métricas"].parent_id is None
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_phase1_chunk_failure_is_partial_not_fatal(monkeypatch):
    """Un chunk caído no arrastra la fase: goals parciales con contador explícito."""
    monkeypatch.setattr(ge, "GOALS_CHUNK_SIZE", 1)  # 2 reqs => 2 chunks
    monkeypatch.setattr(ge, "DEFAULT_CONCURRENCY", 1)
    boom = ValueError("gateway 500")
    plans = _plans(None, [])
    plans[ge.GoalGoals] = [
        ge.GoalGoals(goals=[_goal_stmt("G1", "Servir pedidos")]),
        boom,
    ]
    plans[ge.GoalLinks] = [_links([("G1", "REQ-0000")])]
    _patch_llm(monkeypatch, plans)
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=2)
            summary = await ge.infer_goals(session, pid)

        assert summary["goals"] == 1
        assert summary["goals_chunks_failed"] == 1
        assert summary["goals_partial"] is True
        async with sm() as session:
            assert len((await session.execute(sa_select(Goal))).scalars().all()) == 1
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_all_chunks_failed_is_explicit_error(monkeypatch):
    """Sin chunks sobrevivientes: error explícito, nunca goals parciales vacíos."""
    monkeypatch.setattr(ge, "GOALS_CHUNK_SIZE", 1)
    monkeypatch.setattr(ge, "DEFAULT_CONCURRENCY", 1)
    plans = {ge.GoalGoals: [ValueError("gateway 500")], ge.GoalLinks: []}
    _patch_llm(monkeypatch, plans)
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=2)
            with pytest.raises(ge.GoalsInferenceError, match="fase goals falló"):
                await ge.infer_goals(session, pid)
            assert (
                len((await session.execute(sa_select(Goal))).scalars().all()) == 0
            )
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_max_tokens_travels_to_every_invoke(monkeypatch):
    """max_tokens por llamada (fallback asequible) en thinking y sin-thinking."""
    trunc = StructuredOutputTruncatedError("truncated")
    plans = _plans(None, [])
    plans[ge.GoalGoals] = [
        trunc,
        trunc,
        ge.GoalGoals(goals=[_goal("G1")]),
    ]
    plans[ge.GoalLinks] = [_links([("G1", "REQ-0000")])]
    calls = _patch_llm(monkeypatch, plans)
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=1)
            summary = await ge.infer_goals(session, pid)

        assert summary["goals"] == 1
        # Ambas pasadas de la fase goals (con y sin thinking) llevaron el cap
        # (el runnable de cada pasada se construye una vez y reusa sus
        # reintentos de parse; dos factory calls, todas con max_tokens).
        goal_calls = [c for c in calls if c["schema"] is ge.GoalGoals]
        assert len(goal_calls) == 2
        assert all(
            c["ainvoke_kwargs"].get("max_tokens") == ge.GOALS_MAX_TOKENS
            for c in goal_calls
        )
        # Y el lote de links también.
        link_calls = [c for c in calls if c["schema"] is ge.GoalLinks]
        assert link_calls[0]["ainvoke_kwargs"].get("max_tokens") == (
            ge.GOALS_MAX_TOKENS
        )
    finally:
        await engine.dispose()
        tmp.cleanup()


# ---------------------------------------------------------------------------
# Progreso por unidad (la etapa goals deja de quedar muda tras el mensaje
# inicial: mismo reporte de avance que srs_quality).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_reported_per_unit(monkeypatch):
    """Cada chunk de goals y cada lote de links emite (fase, hecho, total)."""
    monkeypatch.setattr(ge, "GOALS_CHUNK_SIZE", 2)  # 4 reqs => 2 chunks
    monkeypatch.setattr(ge, "_LINK_BATCH_SIZE", 2)  # 4 reqs => 2 lotes
    # Concurrencia 1: el orden de emisión queda determinista.
    monkeypatch.setattr(ge, "DEFAULT_CONCURRENCY", 1)
    plans = _plans(
        ge.GoalGoals(goals=[_goal("G1")]),
        [_links([("G1", "REQ-0000")]), _links([("G1", "REQ-0002")])],
    )
    _patch_llm(monkeypatch, plans)
    sm, tmp, engine = await _fresh_db()
    events: list[tuple[str, int, int]] = []

    async def on_progress(phase: str, done: int, total: int) -> None:
        events.append((phase, done, total))

    try:
        async with sm() as session:
            pid = await _seed(session, n=4)
            await ge.infer_goals(session, pid, on_progress=on_progress)

        assert events == [
            ("chunks de goals", 1, 2),
            ("chunks de goals", 2, 2),
            ("lotes de links", 1, 2),
            ("lotes de links", 2, 2),
        ]
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_progress_counts_failed_units_too(monkeypatch):
    """El chunk que falla también cuenta: el avance llega a total y no se congela."""
    monkeypatch.setattr(ge, "GOALS_CHUNK_SIZE", 1)  # 2 reqs => 2 chunks
    monkeypatch.setattr(ge, "DEFAULT_CONCURRENCY", 1)
    plans = _plans(None, [])
    plans[ge.GoalGoals] = [
        ge.GoalGoals(goals=[_goal("G1")]),
        ValueError("gateway 500"),
    ]
    plans[ge.GoalLinks] = [_links([("G1", "REQ-0000")])]
    _patch_llm(monkeypatch, plans)
    sm, tmp, engine = await _fresh_db()
    events: list[tuple[str, int, int]] = []

    async def on_progress(phase: str, done: int, total: int) -> None:
        events.append((phase, done, total))

    try:
        async with sm() as session:
            pid = await _seed(session, n=2)
            summary = await ge.infer_goals(session, pid, on_progress=on_progress)

        assert summary["goals_partial"] is True
        assert events == [
            ("chunks de goals", 1, 2),
            ("chunks de goals", 2, 2),  # el caído también se reporta
            ("lotes de links", 1, 1),
        ]
    finally:
        await engine.dispose()
        tmp.cleanup()


# ---------------------------------------------------------------------------
# Upsert + camino incremental (sesión 9 de Planitrack: re-correr la etapa
# reasignaba códigos GOAL-XXXX y huérfanaba los links hechos a mano).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reinfer_preserves_goal_codes(monkeypatch):
    """Dos corridas con la misma inferencia: los códigos GOAL-XXXX no cambian."""
    plans = _plans(
        ge.GoalGoals(goals=[_goal("G1"), _goal("G2")]),
        [_links([("G1", "REQ-0000"), ("G2", "REQ-0001")])],
    )
    _patch_llm(monkeypatch, plans)
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=2)
            first = await ge.infer_goals(session, pid)
        async with sm() as session:
            codes_1 = {
                g.code: g for g in (
                    await session.execute(sa_select(Goal))
                ).scalars()
            }
        async with sm() as session:
            second = await ge.infer_goals(session, pid)
            rows = {
                g.code: g for g in (
                    await session.execute(sa_select(Goal))
                ).scalars()
            }

        assert first["goals"] == 2
        assert second["goals"] == 2
        assert set(rows) == set(codes_1)  # SIN churn de código
        # La fila preservada no se recrea: mismo id, status intacto.
        assert all(
            rows[c].id == codes_1[c].id
            and rows[c].status == GoalStatus.PROPOSED
            for c in rows
        )
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_infer_goal_links_incremental_is_idempotent(monkeypatch):
    """Solo los reqs pedidos; la arista que ya existe no se duplica."""
    monkeypatch.setattr(ge, "_LINK_BATCH_SIZE", 2)
    monkeypatch.setattr(ge, "DEFAULT_CONCURRENCY", 1)
    plans = {
        ge.GoalLinks: [
            ge.GoalLinks(
                links=[
                    # REQ-0001 ya está linkeado: created=False (existing).
                    ge.InferredLink(
                        goal_code="GOAL-ZZ", req_code="REQ-0001",
                        relation="realizes",
                    ),
                    ge.InferredLink(
                        goal_code="GOAL-ZZ", req_code="REQ-0002",
                        relation="realizes",
                    ),
                    # Duplicada dentro de la misma salida: fuera.
                    ge.InferredLink(
                        goal_code="GOAL-ZZ", req_code="REQ-0002",
                        relation="realizes",
                    ),
                ]
            )
        ]
    }
    _patch_llm(monkeypatch, plans)
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=4)
            goal = Goal(
                project_id=pid,
                code="GOAL-ZZ",
                statement="Servir pedidos",
                kind=GoalKind.FUNCTIONAL_GOAL,
                status=GoalStatus.PROPOSED,
                confidence=0.7,
                created_by="agent",
            )
            session.add(goal)
            await session.flush()
            req1 = (
                await session.execute(
                    sa_select(RequirementItem.id).where(
                        RequirementItem.code == "REQ-0001"
                    )
                )
            ).scalar_one()
            session.add(
                GoalLink(
                    goal_id=goal.id, req_id=req1,
                    relation=LinkRelation.REALIZES,
                )
            )
            await session.commit()
            gid = goal.id

            summary = await ge.infer_goal_links_incremental(
                session, pid, ["REQ-0001", "REQ-0002", "REQ-9999"]
            )

        assert summary["reqs"] == 2  # REQ-9999 no existe
        assert summary["reqs_missing"] == ["REQ-9999"]
        assert summary["links_added"] == 1  # solo REQ-0002
        assert summary["links_existing"] == 1  # REQ-0001 ya estaba
        assert summary["batches_failed"] == 0
        async with sm() as session:
            links = (
                await session.execute(sa_select(GoalLink))
            ).scalars().all()
            assert len(links) == 2  # preexistente + el nuevo; sin duplicados
            assert all(l.goal_id == gid for l in links)
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_incremental_links_require_existing_goals():
    """Sin goals en el proyecto: error explícito, no una inferencia vacía."""
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=2)
            with pytest.raises(ge.GoalsInferenceError, match="infer_goals"):
                await ge.infer_goal_links_incremental(
                    session, pid, ["REQ-0000"]
                )
    finally:
        await engine.dispose()
        tmp.cleanup()


# ---------------------------------------------------------------------------
# Consolidación del catálogo (acotado por instrucción, no por corte)
# ---------------------------------------------------------------------------


# 20 enunciados genuinamente distintos (el dedupe fuzzy fusiona a >=90:
# frases que solo cambian un dígito colapsan a una).
_DISTINCT_STATEMENTS = [
    "Gestionar logistica", "Gestionar facturacion", "Controlar inventario",
    "Optimizar ruteo", "Emitir alertas", "Reportar metricas",
    "Blindar seguridad", "Auditar operaciones", "Integrar sistemas externos",
    "Notificar eventos", "Operar sin conexion", "Soportar multiempresa",
    "Facturar electronicamente", "Coordinar carriers", "Rastrear envios",
    "Gestionar devoluciones", "Garantizar slas", "Parametrizar plantillas",
    "Administrar permisos", "Respaldar informacion",
]


def test_merge_does_not_cut_at_fifteen():
    """El merge ya no recorta a 15: el acotado lo hace la consolidación LLM."""
    chunks = [
        [
            _goal_stmt(f"G{i}", stmt)
            for i, stmt in enumerate(_DISTINCT_STATEMENTS)
        ]
    ]
    merged = ge._merge_chunked_goals(chunks)
    assert len(merged) == 20


@pytest.mark.asyncio
async def test_consolidation_fuses_catalog_above_target(monkeypatch):
    """Sobre el rango objetivo, UNA pasada LLM fusiona; re-run sin ediciones es no-op."""
    calls: list[str] = []

    async def _fake_unit(schema, msgs, *, label, extra=None):
        calls.append(label)
        human = msgs[1][1]
        if schema is ge.GoalGoals and "PROJECT REQUIREMENTS:" in human:
            # Fase 1 (chunk): emite 20 candidatos distintos.
            return ge.GoalGoals(
                goals=[
                    _goal_stmt(f"G{i}", stmt)
                    for i, stmt in enumerate(_DISTINCT_STATEMENTS)
                ]
            )
        if schema is ge.GoalGoals:
            # Consolidación: fusiona a 12 renumerados.
            assert "DRAFT GOAL CATALOG" in human
            return ge.GoalGoals(
                goals=[
                    _goal_stmt(f"G{i}", f"Objetivo fusionado {i}")
                    for i in range(12)
                ]
            )
        return ge.GoalLinks(links=[])

    monkeypatch.setattr(ge, "_invoke_unit", _fake_unit)
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=4)
            summary = await ge.infer_goals(session, pid)
            assert "goals consolidation" in calls
            assert summary["goals"] == 12

            # Re-run sin ediciones: cero llamadas LLM (delta de fingerprints).
            n_calls = len(calls)
            summary2 = await ge.infer_goals(session, pid)
            assert len(calls) == n_calls
            assert summary2["link_scope"] == "none"
            assert summary2["goals"] == 12
    finally:
        await engine.dispose()
        tmp.cleanup()
