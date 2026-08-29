"""Tests del motor de calidad del SRS (``srs_quality``).

Regresión de la v6 de Planitrack2.0: un lote con salida truncada
(finish_reason=length; el thinking de Z.ai comparte el presupuesto de
salida) quemaba generaciones completas con thinking activo y caía al
fallback per-ítem, que a su vez hacía hasta 3 generaciones full-price por
ítem (~7 minutos por episodio, sin progreso visible). Pins de esta suite:

- el truncado detectado degrada a thinking desactivado ANTES del fallback,
- el fallback per-ítem se conserva como última red (comportamiento previo),
- el sentinel ``llm.eval_unavailable`` aterriza con su dimensión propia
  (``eval_unavailable``: construirlo como LlmFinding crashaba con
  ValidationError — bug latente destapado por esta suite) y se cuenta en el
  summary,
- el progreso de batches se reporta vía callback (en el último lote y cada N).

El LLM es stubado en todos los casos (sin red); la DB es sqlite temporal.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.agents.llm import StructuredOutputTruncatedError
from backend.agents.pipelines import _resilience as res
from backend.models import Base, Priority, Project, ReqType, RequirementItem
from backend.services import srs_quality as sq

_THINKING_OFF = {"thinking": {"type": "disabled"}}


class RateLimitError(Exception):
    """is_transient la clasifica por nombre de tipo (espejo del SDK)."""


# ---------------------------------------------------------------------------
# Fakes del LLM estructurado
# ---------------------------------------------------------------------------


def _patch_llm(monkeypatch, plans: dict) -> list[dict]:
    """Stub de sq.structured_llm con plan por schema.

    plans: {schema: [resultado | excepción, ...]}. El último elemento de la
    lista SE REPITE. Devuelve el registro de llamadas a la factory (una por
    construcción del runnable: los reintentos reutilizan la misma instancia).
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

    monkeypatch.setattr(sq, "structured_llm", factory)
    monkeypatch.setattr(sq, "disable_thinking_body", lambda: _THINKING_OFF)
    return calls


def _no_backoff(monkeypatch) -> None:
    """Los reintentos transient no duermen en tests."""
    monkeypatch.setattr(res, "transient_backoff_seconds", lambda fails: 0)


def _item(i: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=i,
        type=SimpleNamespace(value="FUNCTIONAL"),
        statement=f"El sistema debe registrar la operacion numero {i}.",
    )


def _verdict(item_id: str) -> sq.ItemQualityVerdict:
    return sq.ItemQualityVerdict(item_id=item_id, findings=[])


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


async def _seed(session, n: int = 2) -> int:
    proj = Project(user_id=1, name="q", slug="q", description="t")
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
# Tests: batch path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_truncation_degrades_to_no_thinking(monkeypatch):
    """Lote truncado -> reintento con thinking desactivado, sin fallback."""
    trunc = StructuredOutputTruncatedError("truncated")
    calls = _patch_llm(
        monkeypatch,
        {
            sq.QualityBatch: [
                trunc,
                trunc,
                sq.QualityBatch(verdicts=[_verdict("1"), _verdict("2")]),
            ],
            sq.ItemQualityVerdict: [],
        },
    )
    out = await sq._judge_batch([_item(1), _item(2)])

    assert set(out) == {1, 2}
    assert out[1] == []  # veredicto sin findings
    batch_calls = [c for c in calls if c["schema"] is sq.QualityBatch]
    # Primer runnable con thinking default; el que recupera, sin thinking.
    assert [c["extra_body"] for c in batch_calls] == [None, _THINKING_OFF]
    assert not any(c["schema"] is sq.ItemQualityVerdict for c in calls)


@pytest.mark.asyncio
async def test_persistent_batch_truncation_falls_back_per_item(monkeypatch):
    """Truncado persistente aun sin thinking -> fallback per-ítem (red previa)."""
    trunc = StructuredOutputTruncatedError("truncated")
    calls = _patch_llm(
        monkeypatch,
        {sq.QualityBatch: [trunc], sq.ItemQualityVerdict: [_verdict("1")]},
    )
    out = await sq._judge_batch([_item(1), _item(2)])

    assert set(out) == {1, 2}
    batch_calls = [c for c in calls if c["schema"] is sq.QualityBatch]
    # Un runnable con thinking y otro sin thinking antes del fallback.
    assert [c["extra_body"] for c in batch_calls] == [None, _THINKING_OFF]
    assert len([c for c in calls if c["schema"] is sq.ItemQualityVerdict]) == 2


@pytest.mark.asyncio
async def test_batch_parse_exhaustion_goes_straight_to_fallback(monkeypatch):
    """Parse sin truncar NO degrada a thinking-off: fallback directo."""
    boom = ValueError("json roto")
    calls = _patch_llm(
        monkeypatch,
        {sq.QualityBatch: [boom], sq.ItemQualityVerdict: [_verdict("1")]},
    )
    out = await sq._judge_batch([_item(1)])

    batch_calls = [c for c in calls if c["schema"] is sq.QualityBatch]
    assert len(batch_calls) == 1  # solo el runnable con thinking default
    assert out[1] == []


# ---------------------------------------------------------------------------
# Tests: per-item path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_item_truncation_degrades_before_sentinel(monkeypatch):
    """Per-ítem truncado en todo el camino con thinking -> thinking-off recupera."""
    trunc = StructuredOutputTruncatedError("truncated")
    calls = _patch_llm(
        monkeypatch,
        # 3 truncados agotan max_parse=_JUDGE_ATTEMPTS con thinking activo;
        # el 4to intento (runnable sin thinking) recupera.
        {
            sq.QualityBatch: [trunc],
            sq.ItemQualityVerdict: [trunc, trunc, trunc, _verdict("7")],
        },
    )
    out = await sq._judge_batch([_item(7)])

    assert out[7] == []  # veredicto real, no sentinel llm.eval_unavailable
    item_calls = [c for c in calls if c["schema"] is sq.ItemQualityVerdict]
    assert [c["extra_body"] for c in item_calls] == [None, _THINKING_OFF]


@pytest.mark.asyncio
async def test_item_parse_exhaustion_keeps_sentinel(monkeypatch):
    boom = ValueError("json roto")
    _patch_llm(
        monkeypatch, {sq.QualityBatch: [boom], sq.ItemQualityVerdict: [boom]}
    )
    out = await sq._judge_batch([_item(3)])

    assert len(out[3]) == 1
    f = out[3][0]
    assert f["rule_id"] == "llm.eval_unavailable"
    assert f["dimension"].value == "eval_unavailable"
    assert "parse_error" in f["message"]
    assert f["detected_by"] == "agent"


@pytest.mark.asyncio
async def test_item_transient_exhaustion_marks_rate_limited(monkeypatch):
    _no_backoff(monkeypatch)
    boom = RateLimitError("429")
    _patch_llm(
        monkeypatch, {sq.QualityBatch: [boom], sq.ItemQualityVerdict: [boom]}
    )
    out = await sq._judge_batch([_item(4)])

    assert len(out[4]) == 1
    assert "rate_limited" in out[4][0]["message"]


# ---------------------------------------------------------------------------
# Tests: analyze_quality end-to-end (progreso + summary)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_quality_reports_progress_and_unavailable_count(monkeypatch):
    """El callback de progreso dispara y el summary cuenta los sentinels."""
    monkeypatch.setattr(sq, "DEFAULT_CONCURRENCY", 1)
    boom = ValueError("json roto")
    _patch_llm(
        monkeypatch, {sq.QualityBatch: [boom], sq.ItemQualityVerdict: [boom]}
    )
    progress: list[tuple[int, int]] = []

    async def on_progress(done: int, total: int) -> None:
        progress.append((done, total))

    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=2)
            summary, findings = await sq.analyze_quality(
                session, pid, on_progress=on_progress
            )

        assert summary["items_analyzed"] == 2
        assert summary["llm_eval_unavailable"] == 2
        assert progress == [(1, 1)]  # único lote: emite en el último
        unavailable = [
            f for f in findings if f["rule_id"] == "llm.eval_unavailable"
        ]
        assert len(unavailable) == 2
    finally:
        await engine.dispose()
        tmp.cleanup()


@pytest.mark.asyncio
async def test_analyze_quality_happy_path_skips_progress_noise(monkeypatch):
    """Sin callback no hay emisión y el summary no registra sentinels."""
    monkeypatch.setattr(sq, "DEFAULT_CONCURRENCY", 1)
    _patch_llm(
        monkeypatch,
        {
            sq.QualityBatch: [
                sq.QualityBatch(verdicts=[_verdict("1"), _verdict("2")])
            ],
            sq.ItemQualityVerdict: [],
        },
    )
    sm, tmp, engine = await _fresh_db()
    try:
        async with sm() as session:
            pid = await _seed(session, n=2)
            summary, findings = await sq.analyze_quality(session, pid)

        assert summary["llm_eval_unavailable"] == 0
        assert summary["items_analyzed"] == 2
        assert not any(
            f["rule_id"] == "llm.eval_unavailable" for f in findings
        )
    finally:
        await engine.dispose()
        tmp.cleanup()
