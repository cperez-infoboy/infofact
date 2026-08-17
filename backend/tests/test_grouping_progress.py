"""Progreso del /agrupar: eventos por etapa/lote y pump SSE de la ruta directa.

El /agrupar directo era mudo mientras corria run_grouping_review (minutos sin
senal; incidente del Connection error). Ahora:

- ``build_grouping_plan`` emite etapas (load/dedup/embedding/judge/cluster)
  con phase start/end + elapsed_ms, y ``_judge_duplicates`` un evento por
  lote (current/total).
- ``run_grouping_review`` agrega persist + done (timings + total_ms +
  contadores).
- El router bombea esos eventos a SSE ``grouping.progress`` via una queue
  mientras el review corre en un task.

Stub pattern: espejo de test_grouping_judge_resilience (monkeypatch de
``structured_llm`` + fakes de build/gstore para el plumbing sin DB).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.agents.pipelines import consolidation, grouping
from backend.agents.pipelines.extraction import RawRequirement
from backend.models import (
    Base,
    Priority,
    Project,
    ReqStatus,
    ReqType,
    RequirementItem,
)


# --- helpers (mismo patron que test_grouping_judge_resilience) ---------------


class _JudgeStub:
    """Confirma como duplicados todos los pares del prompt del batch."""

    def __init__(self):
        self.calls: list[str] = []

    async def ainvoke(self, messages, config=None, **kwargs):
        self.calls.append(messages[-1][1])
        ids = re.findall(r"[AB] \(([^)]+)\):", self.calls[-1])
        pairs = [(ids[k], ids[k + 1]) for k in range(0, len(ids), 2)]
        return consolidation.DuplicateReport(
            verdicts=[
                consolidation.DuplicateVerdict(a_id=a, b_id=b, is_duplicate=True)
                for a, b in pairs
            ]
        )


def _items(n: int) -> list[RawRequirement]:
    return [
        RawRequirement(
            id=f"REQ-{i:03d}",
            statement=f"statement {i}",
            source_span="",
            section="",
            confidence=0.9,
        )
        for i in range(n)
    ]


def _frame_event(frame: str) -> str:
    m = re.match(r"event: (\S+)\ndata:", frame)
    return m.group(1) if m else ""


def _frame_data(frame: str) -> dict:
    return json.loads(frame.split("data: ", 1)[1].strip())


# --- juez: un evento por lote -------------------------------------------------


@pytest.mark.asyncio
async def test_judge_duplicates_emits_progress_per_batch(monkeypatch):
    """45 pares con batch 40 => dos eventos judge (1/2, 2/2) antes de cada
    llamada, con current/total para la barra del banner."""
    stub = _JudgeStub()
    monkeypatch.setattr(
        consolidation, "structured_llm", lambda schema, extra_body=None: stub
    )
    events: list[dict] = []

    async def on_progress(evt):
        events.append(evt)

    items = _items(90)
    candidates = [(i, i + 45) for i in range(45)]
    confirmed = await consolidation._judge_duplicates(
        items, candidates, on_progress=on_progress
    )

    judge_events = [e for e in events if e["stage"] == "judge"]
    assert [(e["current"], e["total"]) for e in judge_events] == [(1, 2), (2, 2)]
    assert all(e["phase"] == "progress" for e in judge_events)
    # La senal no rompio el resultado: los 45 pares siguen confirmados.
    assert len(confirmed) == 45


# --- pipeline: secuencia de etapas --------------------------------------------


@pytest.mark.asyncio
async def test_build_grouping_plan_emits_stage_sequence(monkeypatch, tmp_path):
    """El orden de etapas es el contrato del banner: load -> dedup ->
    embedding -> judge -> cluster, cada una con start y end+elapsed_ms."""
    monkeypatch.setattr(grouping, "embed_texts", lambda texts: np.eye(len(texts)))

    async def _no_judge(_reqs, _cands, on_progress=None):
        return []

    monkeypatch.setattr(grouping, "_judge_duplicates", _no_judge)

    async def _seed(session) -> int:
        proj = Project(user_id=1, name="p", slug="p", description="t")
        session.add(proj)
        await session.flush()
        pid = proj.id
        for code, stmt in [
            ("REQ-001", "alpha"),
            ("REQ-002", "alpha"),
            ("REQ-003", "beta"),
        ]:
            session.add(RequirementItem(
                project_id=pid, code=code, statement=stmt,
                type=ReqType.FUNCTIONAL, priority=Priority.MUST,
                status=ReqStatus.VALIDATED, confidence=0.9, source=None,
            ))
        await session.commit()
        return pid

    events: list[dict] = []

    async def on_progress(evt):
        events.append(evt)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sm() as session:
            pid = await _seed(session)
            plan = await grouping.build_grouping_plan(
                session, pid, project="p", on_progress=on_progress
            )
    finally:
        await engine.dispose()

    starts = [e["stage"] for e in events if e.get("phase") == "start"]
    assert starts == ["load", "dedup", "embedding", "judge", "cluster"]
    ends = {e["stage"]: e for e in events if e.get("phase") == "end"}
    assert set(ends) == {"load", "dedup", "embedding", "judge", "cluster"}
    assert all(isinstance(e["elapsed_ms"], int) for e in ends.values())
    # El plan lleva los timings del run (el done del banner los necesita).
    assert set(plan.timings) == set(ends)
    # Sanidad del plan: el par verbatim REQ-001/002 es el unico grupo.
    assert len(plan.groups) == 1


# --- run_grouping_review: persist + done --------------------------------------


@pytest.mark.asyncio
async def test_run_grouping_review_adds_persist_and_done(monkeypatch):
    """Sobre las etapas de build, el nucleo agrega persist y cierra con done
    (timings completos + total_ms + contadores para el banner)."""
    from backend.agents.tools import grouping_tools

    class _Plan:
        groups = [object(), object()]
        considered = 10
        scope = ""
        timings = {"load": 5, "embedding": 100}

    async def fake_build(session, project_id, **kwargs):
        cb = kwargs.get("on_progress")
        if cb is not None:
            await cb({"stage": "load", "message": "x", "phase": "start"})
        return _Plan()

    async def fake_persist(session, plan, project_id):
        return 7

    async def fake_get(session, plan_id):
        return {"groups": []}

    monkeypatch.setattr(grouping_tools, "build_grouping_plan", fake_build)
    monkeypatch.setattr(grouping_tools.gstore, "persist_plan", fake_persist)
    monkeypatch.setattr(grouping_tools.gstore, "get_plan", fake_get)

    events: list[dict] = []

    async def on_progress(evt):
        events.append(evt)

    result = await grouping_tools.run_grouping_review(
        object(), 1, on_progress=on_progress
    )

    assert [e["stage"] for e in events] == ["load", "persist", "done"]
    done = events[-1]
    assert done["timings"]["load"] == 5
    assert "persist" in done["timings"]
    assert done["total_ms"] == sum(done["timings"].values())
    assert done["group_count"] == 2
    assert done["considered"] == 10
    assert result["plan_id"] == 7
    assert result["group_count"] == 2


# --- ruta directa: pump SSE ----------------------------------------------------


@pytest.mark.asyncio
async def test_agrupar_direct_stream_pumps_grouping_progress(monkeypatch):
    """Mientras corre el review, cada evento de progreso sale como frame SSE
    ``grouping.progress`` ANTES del tool_end: eso es lo que anima el banner."""
    from backend.routers import chat

    async def fake_review(session, project_id, *, on_progress=None, **kwargs):
        if on_progress is not None:
            await on_progress({
                "stage": "load", "message": "cargando", "phase": "start",
            })
            await on_progress({
                "stage": "judge", "message": "lote 1/2", "phase": "progress",
                "current": 1, "total": 2,
            })
        return {
            "plan_id": 3, "group_count": 2, "considered": 8,
            "scope": "", "groups": [],
        }

    class _Ctx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return None

    monkeypatch.setattr(chat, "run_grouping_review", fake_review)
    monkeypatch.setattr(chat, "AsyncSessionLocal", lambda: _Ctx())

    stream = chat._agrupar_direct_stream(session_id=1, project_id=1, key="t1")
    frames = [f async for f in stream]

    names = [_frame_event(f) for f in frames]
    assert names[0] == "tool_start"
    assert names.count("grouping.progress") == 2
    # El progreso llega mientras corre el review, antes del tool_end.
    assert names.index("grouping.progress") < names.index("tool_end")
    assert "grouping.ready" in names
    assert names[-1] == "completed"

    first = _frame_data(frames[1])
    assert first["stage"] == "load"
    assert first["phase"] == "start"


# --- ruta agentica: progreso por el canal custom del grafo --------------------


def test_graph_on_progress_returns_none_outside_graph():
    """Sin contexto de grafo (tests/smokes) el review corre silencioso."""
    from backend.agents.tools import grouping_tools

    assert grouping_tools._graph_on_progress() is None


@pytest.mark.asyncio
async def test_graph_on_progress_emits_grouping_progress(monkeypatch):
    """Dentro del grafo, el callback emite grouping.progress por el writer
    (mismo canal custom que grouping.ready; el router lo relayea al SSE)."""
    import langgraph.config as lg_config
    from backend.agents.tools import grouping_tools

    emitted: list[dict] = []

    def fake_writer(payload):
        emitted.append(payload)

    monkeypatch.setattr(lg_config, "get_stream_writer", lambda: fake_writer)

    cb = grouping_tools._graph_on_progress()
    assert cb is not None
    await cb({"stage": "judge", "message": "lote 1/2", "current": 1, "total": 2})

    assert emitted == [{
        "event": "grouping.progress",
        "data": {"stage": "judge", "message": "lote 1/2", "current": 1, "total": 2},
    }]


@pytest.mark.asyncio
async def test_review_grouping_tool_forwards_on_progress(monkeypatch):
    """La tool pasa el callback del grafo a run_grouping_review: el banner
    anima también cuando el agrupamiento corre por la vía agéntica."""
    from backend.agents.tools import grouping_tools

    async def recorder_evt(evt):
        pass  # identidad del callback capturado por la fake

    monkeypatch.setattr(
        grouping_tools, "_graph_on_progress", lambda: recorder_evt
    )
    captured: dict = {}

    async def fake_review(session, project_id, **kwargs):
        captured.update(kwargs)
        return {"plan_id": 1, "group_count": 0}

    monkeypatch.setattr(grouping_tools, "run_grouping_review", fake_review)

    class _Ctx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return None

    monkeypatch.setattr(grouping_tools, "AsyncSessionLocal", lambda: _Ctx())

    tools = grouping_tools.make_grouping_tools(4)
    review_tool = next(t for t in tools if t.name == "review_grouping")
    result = await review_tool.ainvoke({})

    assert result["plan_id"] == 1
    assert captured.get("on_progress") is recorder_evt
