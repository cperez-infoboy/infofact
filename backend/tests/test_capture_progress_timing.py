"""Tests del contrato de profiling por etapa en el stream de progreso.

Cubre el cambio que enriquece el evento coarse ``extraction.progress`` con un
payload opcional (``phase``, ``elapsed_ms`` y, en la etapa ``done``, el dict
``timings`` + ``total_ms``) para que el banner muestre un desglose por etapa.

Se prueba el contrato del emisor compartido (``_make_emitters``):

- ``_emit_progress`` fusiona ``extra`` en el payload ``data`` del evento SSE.
- La firma de 2 argumentos (sin ``extra``) sigue siendo compatible (no agrega
  claves extrañas al ``data``).
- Fuera de un contexto de grafo LangGraph, el emisor es silencioso (captura la
  excepción y retorna sin romper el pipeline).

Además se verifica que el holder de captura (``CaptureRun``) exponga el campo
``timings`` y que ``reset_pipeline_outputs`` lo limpie (la instrumentación de las
seis tools acumula ahí el wall-clock por etapa).

No se invoca el pipeline completo (necesitaría mocks del LLM): el contrato de
fusión del emisor es lo que conecta los ``perf_counter`` del pipeline con el
evento SSE; la verificación end-to-end con documentos reales es manual (plan
``ya-tenemos-las-herramientas``).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def _capturing_writer(sink: list) -> "object":
    """Devuelve un callable que guarda cada payload escrito en ``sink``."""

    def _writer(payload: dict) -> None:
        sink.append(payload)

    return _writer


def test_emit_progress_merges_extra_into_data():
    """El ``extra`` (phase/elapsed_ms/timings) se fusiona en ``data``."""
    from backend.agents.subagents.requirements_capture_agent import _make_emitters

    sink: list = []
    with patch(
        "langgraph.config.get_stream_writer",
        return_value=_capturing_writer(sink),
    ):
        on_progress, _on_event = _make_emitters()
        import asyncio

        asyncio.run(on_progress(
            "extract", "extracción lista",
            {"phase": "end", "elapsed_ms": 1234.0},
        ))

    assert len(sink) == 1
    payload = sink[0]
    assert payload["event"] == "extraction.progress"
    data = payload["data"]
    assert data["stage"] == "extract"
    assert data["message"] == "extracción lista"
    assert data["phase"] == "end"
    assert data["elapsed_ms"] == 1234.0


def test_emit_progress_without_extra_is_backward_compatible():
    """La llamada de 2 argumentos (sin ``extra``) no añade claves al ``data``."""
    from backend.agents.subagents.requirements_capture_agent import _make_emitters

    sink: list = []
    with patch(
        "langgraph.config.get_stream_writer",
        return_value=_capturing_writer(sink),
    ):
        on_progress, _on_event = _make_emitters()
        import asyncio

        asyncio.run(on_progress("ingest", "discover"))

    assert len(sink) == 1
    data = sink[0]["data"]
    assert set(data.keys()) == {"stage", "message"}
    assert data["stage"] == "ingest"


def test_emit_progress_silent_outside_graph_context():
    """Sin contexto de grafo, el emisor no rompe ni escribe nada."""
    from backend.agents.subagents.requirements_capture_agent import _make_emitters

    # Forzamos el mismo comportamiento que get_stream_writer fuera de contexto:
    # levanta -> el emisor lo atrapa y retorna None silenciosamente.
    sink: list = []
    with patch(
        "langgraph.config.get_stream_writer",
        side_effect=RuntimeError("no graph context"),
    ):
        on_progress, _on_event = _make_emitters()
        import asyncio

        result = asyncio.run(on_progress("done", "fin", {"timings": {}, "total_ms": 0}))

    assert result is None
    assert sink == []


def test_done_event_carries_full_timings_dict():
    """La etapa ``done`` emite el dict ``timings`` completo + ``total_ms``."""
    from backend.agents.subagents.requirements_capture_agent import _make_emitters

    sink: list = []
    timings = {
        "ingest": 1000.0, "extract": 5000.0, "consolidate": 2000.0,
        "critique": 180000.0, "classify": 9000.0, "persist": 4000.0,
    }
    total_ms = round(sum(timings.values()))
    with patch(
        "langgraph.config.get_stream_writer",
        return_value=_capturing_writer(sink),
    ):
        on_progress, _on_event = _make_emitters()
        import asyncio

        asyncio.run(on_progress(
            "done", "201 requerimientos",
            {"timings": timings, "total_ms": total_ms},
        ))

    data = sink[0]["data"]
    assert data["stage"] == "done"
    assert data["timings"] == timings
    assert data["total_ms"] == total_ms
    # La crítica debe ser la etapa dominante (la hipótesis que este profiling
    # busca confirmar con datos reales): su porcentaje supera el 50% del total.
    assert data["timings"]["critique"] / data["total_ms"] > 0.5


# --- Holder: el campo ``timings`` existe y se reinicia al re-ingerir --------


def test_capture_run_timings_field_starts_empty():
    from backend.agents.subagents.capture_run_holder import CaptureRun

    run = CaptureRun(project_id=1, target=Path("/tmp"))
    assert run.timings == {}


def test_reset_pipeline_outputs_clears_timings():
    from backend.agents.subagents.capture_run_holder import CaptureRun

    run = CaptureRun(project_id=1, target=Path("/tmp"))
    run.timings["ingest"] = 1234.0
    run.stages_done.add("ingest")
    run.reset_pipeline_outputs()
    assert run.timings == {}
    assert "ingest" not in run.stages_done
