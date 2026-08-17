"""Resiliencia del juez de duplicados: batching + retry transitorio.

Incidente real: un /agrupar sobre un store maduro terminó con
``{"error": "review_grouping failed: Connection error."}`` porque
``_judge_duplicates`` enviaba TODOS los pares borderline en un único prompt
gigante y sin retry a nivel de pipeline: la SDK agotó sus ``max_retries``
internos y la excepción canceló la revisión entera, sin rastro en docker logs.

Pines de esta suite:
- Los pares se batchean (``DEFAULT_DUPLICATE_JUDGE_BATCH`` por llamada) y los
  veredictos de todos los batches se agregan sin perder pares.
- Cada llamada pasa por ``_invoke_with_retry``: un error transitorio
  (``APIConnectionError``) reintenta con backoff en vez de abortar.
- Agotados los reintentos transitorios, la excepción propaga: abortar o
  aplicar un sentinel es decisión del caller, no del retry.
- ``run_grouping_review`` registra el traceback (``logger.exception``) para
  que ``docker logs`` tenga la evidencia que antes se perdía.

Stub pattern: monkeypatch de ``consolidation.structured_llm`` (mismo approach
que ``test_structured_runnable_nostream``); el backoff se anula parcheando
``transient_backoff_seconds`` a 0.0 para no dormir en los reintentos.
"""
from __future__ import annotations

import logging
import re

import httpx
import pytest
from openai import APIConnectionError

from backend.agents.llm import disable_thinking_body
from backend.agents.pipelines import _resilience, consolidation
from backend.agents.pipelines.consolidation import (
    DuplicateReport,
    DuplicateVerdict,
)
from backend.agents.pipelines.extraction import RawRequirement
from backend.config import settings


class _JudgeStub:
    """Registra cada llamada; confirma como duplicados todos los pares cuyos
    ids puede parsear del prompt del batch. Falla con APIConnectionError en
    las primeras ``failures`` llamadas (transitorio a propósito)."""

    def __init__(self, failures: int = 0):
        self._failures = failures
        self.calls: list[str] = []
        self.kwargs: list[dict] = []

    async def ainvoke(self, messages, config=None, **kwargs):
        self.calls.append(messages[-1][1])
        self.kwargs.append(dict(kwargs))
        if len(self.calls) <= self._failures:
            raise APIConnectionError(
                request=httpx.Request("POST", "http://test.local")
            )
        return _report_from_prompt(self.calls[-1])


def _report_from_prompt(user: str) -> DuplicateReport:
    """Un veredicto (duplicado) por cada par ``[n] A (id): ... B (id): ...``."""
    ids = re.findall(r"[AB] \(([^)]+)\):", user)
    pairs = [(ids[k], ids[k + 1]) for k in range(0, len(ids), 2)]
    return DuplicateReport(
        verdicts=[
            DuplicateVerdict(a_id=a, b_id=b, is_duplicate=True)
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


def _pairs_per_call(user: str) -> int:
    return len(re.findall(r"^\[\d+\] A \(", user, re.MULTILINE))


@pytest.fixture
def no_backoff(monkeypatch):
    """Anula el backoff real del retry compartido (duerme 0.0s)."""
    monkeypatch.setattr(
        _resilience, "transient_backoff_seconds", lambda fails: 0.0
    )


# --- batching -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_pairs_are_batched_and_verdicts_aggregated(monkeypatch):
    """45 pares con batch 40 => dos llamadas (40 + 5) y los 45 pares
    confirmados: ningún par se pierde en la frontera del batch."""
    stub = _JudgeStub()
    monkeypatch.setattr(
        consolidation, "structured_llm", lambda schema, extra_body=None: stub
    )

    items = _items(90)
    candidates = [(i, i + 45) for i in range(45)]
    confirmed = await consolidation._judge_duplicates(items, candidates)

    assert len(stub.calls) == 2
    assert [_pairs_per_call(c) for c in stub.calls] == [40, 5]
    assert len(confirmed) == 45
    assert (0, 45) in confirmed
    assert (44, 89) in confirmed


@pytest.mark.asyncio
async def test_small_candidate_sets_stay_single_call(monkeypatch):
    stub = _JudgeStub()
    monkeypatch.setattr(
        consolidation, "structured_llm", lambda schema, extra_body=None: stub
    )

    confirmed = await consolidation._judge_duplicates(
        _items(4), [(0, 1), (2, 3)]
    )

    assert len(stub.calls) == 1
    assert sorted(confirmed) == [(0, 1), (2, 3)]


# --- retry transitorio ---------------------------------------------------------

@pytest.mark.asyncio
async def test_transient_connection_error_is_retried(monkeypatch, no_backoff):
    """El incidente real: la llamada al juez muere con APIConnectionError.
    Con el retry compartido el batch sobrevive fallos transitorios."""
    stub = _JudgeStub(failures=2)
    monkeypatch.setattr(
        consolidation, "structured_llm", lambda schema, extra_body=None: stub
    )

    confirmed = await consolidation._judge_duplicates(
        _items(4), [(0, 1), (2, 3)]
    )

    assert len(stub.calls) == 3  # 2 fallos + 1 éxito
    assert sorted(confirmed) == [(0, 1), (2, 3)]


@pytest.mark.asyncio
async def test_transient_exhaustion_propagates(monkeypatch, no_backoff):
    """Agotados los reintentos, la excepción propaga: decide el caller."""
    stub = _JudgeStub(failures=_resilience._TRANSIENT_RETRIES)
    monkeypatch.setattr(
        consolidation, "structured_llm", lambda schema, extra_body=None: stub
    )

    with pytest.raises(APIConnectionError):
        await consolidation._judge_duplicates(_items(2), [(0, 1)])

    assert len(stub.calls) == _resilience._TRANSIENT_RETRIES


# --- evidencia en logs ---------------------------------------------------------

@pytest.mark.asyncio
async def test_run_grouping_review_logs_the_traceback(monkeypatch, caplog):
    """El error llega al chat como dict, pero docker logs debe llevar el
    traceback: antes la excepción se tragaba sin registrar nada."""
    # Import lazy: grouping_tools arrastra langchain + database; el resto de
    # la suite no los necesita.
    from backend.agents.tools import grouping_tools

    async def _boom(session, project_id, **kwargs):
        raise RuntimeError("boom del pipeline")

    monkeypatch.setattr(grouping_tools, "build_grouping_plan", _boom)

    with caplog.at_level(
        logging.ERROR, logger="backend.agents.tools.grouping_tools"
    ):
        result = await grouping_tools.run_grouping_review(object(), 1)

    assert result == {"error": "review_grouping failed: boom del pipeline"}
    records = [
        r for r in caplog.records if "review_grouping failed" in r.getMessage()
    ]
    assert records, "debe quedar registro del fallo en logs"
    assert records[0].exc_info is not None  # con traceback


# --- salida minima: thinking off + techo de tokens -----------------------------


@pytest.mark.asyncio
async def test_invoke_with_retry_forwards_extra_kwargs():
    """``extra`` llega verbatim a cada ``llm.ainvoke`` (max_tokens /
    extra_body), y sin ``extra`` la llamada queda exactamente igual que
    siempre (paridad con las pasadas directas de critique/classification)."""
    seen: list[dict] = []

    class _Recorder:
        async def ainvoke(self, messages, **kwargs):
            seen.append(dict(kwargs))
            return "ok"

    out = await _resilience._invoke_with_retry(
        _Recorder(),
        [("human", "hi")],
        context_label="test",
        extra={"max_tokens": 900, "extra_body": {"thinking": {"type": "disabled"}}},
    )
    assert out == "ok"
    assert seen == [
        {"max_tokens": 900, "extra_body": {"thinking": {"type": "disabled"}}}
    ]

    seen.clear()
    await _resilience._invoke_with_retry(
        _Recorder(), [("human", "x")], context_label="test"
    )
    assert seen == [{}]


@pytest.mark.parametrize(
    "env,base_url,expected",
    [
        (
            None,
            "https://api.z.ai/api/coding/paas/v4",
            {"thinking": {"type": "disabled"}},
        ),
        (None, "https://api.openai.com/v1", None),
        ("0", "https://api.z.ai/api/coding/paas/v4", None),
        ("1", "https://api.deepseek.com/v1", {"thinking": {"type": "disabled"}}),
    ],
)
def test_disable_thinking_body_is_scoped_to_zai(
    monkeypatch, env, base_url, expected
):
    """``thinking`` es una extensión propietaria de Z.ai: en modo auto solo
    viaja a hosts z.ai (un body desconocido puede dar 400 en otros
    proveedores); el env lo fuerza en cualquier dirección."""
    monkeypatch.delenv("INFOFACT_JUDGE_DISABLE_THINKING", raising=False)
    if env is not None:
        monkeypatch.setenv("INFOFACT_JUDGE_DISABLE_THINKING", env)
    monkeypatch.setattr(settings, "llm_base_url", base_url)
    assert disable_thinking_body() == expected


@pytest.mark.asyncio
async def test_judge_sends_max_tokens_only_when_thinking_is_off(monkeypatch):
    """Con thinking desactivado, cada lote viaja con un techo ``max_tokens``
    (pares * 50 + 500); con thinking activo no viaja ningún techo, porque
    truncaría el JSON y quemaría los reintentos de parseo."""
    stub = _JudgeStub()
    seen: dict = {}

    def fake_structured(schema, extra_body=None):
        seen["extra_body"] = extra_body
        return stub

    monkeypatch.setattr(consolidation, "structured_llm", fake_structured)
    monkeypatch.setattr(
        consolidation,
        "disable_thinking_body",
        lambda: {"thinking": {"type": "disabled"}},
    )

    await consolidation._judge_duplicates(_items(4), [(0, 1), (2, 3)])

    assert seen["extra_body"] == {"thinking": {"type": "disabled"}}
    assert stub.kwargs == [{"max_tokens": 2 * 50 + 500}]

    stub2 = _JudgeStub()
    seen.clear()

    def fake_structured_none(schema, extra_body=None):
        seen["extra_body"] = extra_body
        return stub2

    monkeypatch.setattr(consolidation, "structured_llm", fake_structured_none)
    monkeypatch.setattr(consolidation, "disable_thinking_body", lambda: None)

    await consolidation._judge_duplicates(_items(4), [(0, 1), (2, 3)])

    assert seen["extra_body"] is None
    assert stub2.kwargs == [{}]
