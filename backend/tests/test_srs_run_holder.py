"""Tests del rearmado de contadores del run SRS (incidente 2026-08-29).

El cap del stage loop acota el loop de una etapa DENTRO de un episodio de
razonamiento; sin rearmado, un run reanudado en un turno posterior heredaba
el contador agotado y el guard bloqueaba la etapa instantáneamente (los dos
«reintenta» del usuario chocaron contra 4/3 y 5/3 sin ni llamar al LLM).
Pins de esta suite:

- ``rearm_stages`` limpia SOLO los contadores: salidas de etapas y
  ``stages_done`` quedan intactas (el run continúa donde quedó),
- ``rearm_run`` (router-owned) rearma únicamente el run activo del proyecto.
"""
from __future__ import annotations

import pytest

from backend.agents.subagents import srs_run_holder as holder
from backend.agents.subagents.capture_run_holder import StageLoopExceeded


@pytest.fixture(autouse=True)
def _clean_active_runs():
    holder._ACTIVE_RUNS.clear()
    yield
    holder._ACTIVE_RUNS.clear()


def test_rearm_stages_clears_counters_keeps_outputs():
    run = holder.SrsRun(project_id=1)
    run.quality_summary = {"items": 807}
    run.findings = [{"id": 1}]
    run.stages_done.add(holder.STAGE_QUALITY)

    for _ in range(holder.DEFAULT_STAGE_CAP):
        run.bump(holder.STAGE_GOALS)
    with pytest.raises(StageLoopExceeded):
        run.bump(holder.STAGE_GOALS)

    run.rearm_stages()

    # Los contadores arrancan de cero y las salidas siguen donde estaban.
    assert run.calls == {}
    assert run.bump(holder.STAGE_GOALS) == 1
    assert run.quality_summary == {"items": 807}
    assert run.findings == [{"id": 1}]
    assert run.stages_done == {holder.STAGE_QUALITY}


def test_rearm_run_touches_only_the_active_run():
    run = holder.get_or_create_run(9, project_name="p")
    run.bump(holder.STAGE_GOALS)
    run.bump(holder.STAGE_GOALS)

    assert holder.rearm_run(9) is run
    assert run.calls == {}
    # Proyecto sin run: no crea nada ni falla.
    assert holder.rearm_run(123) is None
    assert 123 not in holder._ACTIVE_RUNS
