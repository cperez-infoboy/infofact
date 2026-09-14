"""Guardas anti doble despacho del analysis-agent (corrida 2026-09-11).

Con el mensaje de /analisis despachado dos veces, dos runs del agente
compartían el MISMO holder por proyecto: cada etapa corrió 2-3 veces en
paralelo (generate_mer 2x, ~40 minutos cada una), un propose_subprojects con
25 contratos fue pisado por otro con 0, y el commit del run A borró el holder
que el run B seguía usando — cuyo commit falló con no_active_analysis_run y
el modelo anunció una tercera cascada completa. Estas guardas hacen que la
segunda llamada concurrente a cualquier etapa se rechace al instante con
``stage_in_progress``, y que la limpieza del holder tras un commit solo
aplique si el run que commitea sigue siendo el registrado.
"""
from __future__ import annotations

import asyncio
import types

import pytest

import backend.agents.pipelines.mer_pipeline as mer_mod
import backend.agents.subagents.analysis_agent as mod
import backend.services.requirement_store as req_store_mod
import backend.services.srs_store as srs_store_mod
from backend.agents.pipelines.mer_pipeline import MerEntitySchema, MerResult
from backend.agents.subagents import analysis_run_holder as holder
from backend.models.requirement import ReqStatus, ReqType

PROJECT_ID = 4402


class _FakeSession:
    """Async context manager standing in for AsyncSessionLocal()."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _fake_item(code: str = "REQ-0001"):
    return types.SimpleNamespace(
        code=code, status=ReqStatus.VALIDATED, type=ReqType.FUNCTIONAL
    )


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    holder.clear_run(PROJECT_ID)
    # AsyncSessionLocal se importa al tope de analysis_agent: parchar allí.
    monkeypatch.setattr(mod, "AsyncSessionLocal", lambda: _FakeSession())

    async def fake_list_requirements(session, project_id, include_deleted=False):
        return [_fake_item()]

    async def fake_list_goals(session, project_id):
        return []

    monkeypatch.setattr(
        req_store_mod, "list_requirements", fake_list_requirements
    )
    monkeypatch.setattr(srs_store_mod, "list_goals", fake_list_goals)
    yield
    holder.clear_run(PROJECT_ID)


def test_begin_stage_end_stage_semantics():
    """begin_stage reserva en exclusiva; end_stage libera para reintentos."""
    run = holder.get_or_create_run(PROJECT_ID, project_name="Proj")
    assert run.begin_stage("mer") is True
    # Segunda llamada mientras la primera está en vuelo: rechazada.
    assert run.begin_stage("mer") is False
    assert run.begin_stage("nfr") is False  # la exclusión es del holder
    run.end_stage("mer")
    # Liberada: un reintento legítimo vuelve a pasar.
    assert run.begin_stage("nfr") is True


@pytest.mark.asyncio
async def test_second_concurrent_generate_mer_is_rejected(monkeypatch):
    """Dos invocaciones en paralelo: la segunda responde stage_in_progress.

    Reproduce el doble despacho de la corrida 2026-09-11 (dos POSTs al mismo
    session_id): antes, ambas corridas ejecutaban generate_mer completo
    (~40 min cada una) sobre el mismo holder.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_generate_mer(items, **kw):
        started.set()
        await release.wait()
        return MerResult(
            entities=[MerEntitySchema(name="A")],
            mermaid="erDiagram",
        )

    monkeypatch.setattr(mer_mod, "generate_mer", slow_generate_mer)
    tools = mod._make_stage_tools(PROJECT_ID, "Proj", "desc")
    generate_mer = tools[0]

    first = asyncio.create_task(generate_mer.ainvoke({}))
    await asyncio.wait_for(started.wait(), timeout=5)

    second = await generate_mer.ainvoke({})
    assert second["error"] == "stage_in_progress"
    assert second["stage"] == "mer"
    # El pipeline de la primera NO fue interrumpido.
    assert holder.get_run(PROJECT_ID).in_flight is True

    release.set()
    out = await asyncio.wait_for(first, timeout=5)
    assert out["stage"] == "mer"
    # El finally liberó el holder: reintentos legítimos siguen posibles.
    assert holder.get_run(PROJECT_ID).in_flight is False


@pytest.mark.asyncio
async def test_stage_released_after_pipeline_failure(monkeypatch):
    """Si el pipeline explota, el finally libera el holder (sin lock eterno)."""
    released = asyncio.Event()

    async def failing_generate_mer(items, **kw):
        released.set()
        raise RuntimeError("upstream 429")

    monkeypatch.setattr(mer_mod, "generate_mer", failing_generate_mer)
    tools = mod._make_stage_tools(PROJECT_ID, "Proj", "desc")

    with pytest.raises(RuntimeError):
        await tools[0].ainvoke({})
    await asyncio.wait_for(released.wait(), timeout=5)
    assert holder.get_run(PROJECT_ID).in_flight is False
