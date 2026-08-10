"""Tests for the analysis run holder (Phase 2 analysis & design).

Pins: single-active-run-per-project, loop cap semantics (per stage, surviving
reset), stage completion bookkeeping, reset behavior, and the commit gate
(which stages must have run before commit_analysis is allowed to persist).

Mirrors test_capture_run_holder.py in structure and scope.
"""
from __future__ import annotations

import pytest

from backend.agents.subagents.analysis_run_holder import (
    STAGE_ADR,
    STAGE_COMMIT,
    STAGE_MER,
    STAGE_NFR,
    STAGE_PROCESS,
    STAGE_SUBPROJECT,
    AnalysisRun,
    StageLoopExceeded,
    clear_run,
    get_or_create_run,
    get_run,
)

_ALL_PRE_COMMIT = (
    STAGE_MER,
    STAGE_NFR,
    STAGE_PROCESS,
    STAGE_ADR,
    STAGE_SUBPROJECT,
)


def _fresh_run(project_id: int = 1) -> AnalysisRun:
    clear_run(project_id)
    return get_or_create_run(project_id, project_name="x")


# --------------------------------------------------------------------------- #
# get_or_create / get / clear                                                 #
# --------------------------------------------------------------------------- #


def test_get_or_create_returns_same_run_for_same_project():
    a = _fresh_run(10)
    b = get_or_create_run(10, project_name="other")
    assert a is b  # no reset on second call


def test_get_or_create_creates_new_run_for_new_project():
    clear_run(20)
    run = get_or_create_run(20, project_name="Proj", project_description="desc")
    assert run.project_id == 20
    assert run.project_name == "Proj"
    assert run.project_description == "desc"
    assert run.mer_result is None
    clear_run(20)


def test_get_run_returns_none_for_unknown_project():
    clear_run(999)
    assert get_run(999) is None


def test_clear_run_returns_and_drops_the_run():
    run = _fresh_run(30)
    assert clear_run(30) is run
    assert get_run(30) is None


def test_clear_run_returns_none_when_no_run_exists():
    clear_run(40)
    assert clear_run(40) is None


# --------------------------------------------------------------------------- #
# bump / loop cap                                                             #
# --------------------------------------------------------------------------- #


def test_bump_increments_and_returns_count():
    run = _fresh_run(50)
    assert run.bump(STAGE_MER) == 1
    assert run.bump(STAGE_MER) == 2
    assert run.bump(STAGE_MER) == 3


def test_bump_raises_past_default_cap():
    run = _fresh_run(51)
    run.bump(STAGE_NFR)
    run.bump(STAGE_NFR)
    run.bump(STAGE_NFR)
    with pytest.raises(StageLoopExceeded) as exc:
        run.bump(STAGE_NFR)
    assert exc.value.stage == STAGE_NFR
    assert exc.value.calls == 4
    assert exc.value.cap == 3


def test_bump_cap_is_per_stage():
    run = _fresh_run(52)
    # Exhaust MER cap.
    run.bump(STAGE_MER)
    run.bump(STAGE_MER)
    run.bump(STAGE_MER)
    # NFR is independent — still has room.
    assert run.bump(STAGE_NFR) == 1
    assert run.calls[STAGE_MER] == 3
    assert run.calls[STAGE_NFR] == 1


# --------------------------------------------------------------------------- #
# reset_pipeline_outputs                                                      #
# --------------------------------------------------------------------------- #


def test_reset_clears_results_but_preserves_call_counters():
    run = _fresh_run(60)
    run.bump(STAGE_MER)
    run.bump(STAGE_MER)
    run.mer_result = object()  # type: ignore[assignment]
    run.nfr_result = object()  # type: ignore[assignment]
    run.stages_done.add(STAGE_MER)

    run.reset_pipeline_outputs()

    assert run.mer_result is None
    assert run.nfr_result is None
    assert STAGE_MER not in run.stages_done
    # Counters survive so a re-run cannot dodge the cap.
    assert run.calls[STAGE_MER] == 2


def test_reset_clears_stages_done():
    run = _fresh_run(61)
    run.stages_done.update(_ALL_PRE_COMMIT)
    assert len(run.stages_done) == 5

    run.reset_pipeline_outputs()

    assert run.stages_done == set()


# --------------------------------------------------------------------------- #
# missing_stages_before_commit                                                #
# --------------------------------------------------------------------------- #


def test_missing_stages_returns_all_five_when_nothing_done():
    run = _fresh_run(70)
    missing = run.missing_stages_before_commit()
    assert set(missing) == set(_ALL_PRE_COMMIT)


def test_missing_stages_returns_empty_when_all_done():
    run = _fresh_run(71)
    run.stages_done.update(_ALL_PRE_COMMIT)
    assert run.missing_stages_before_commit() == []


def test_missing_stages_returns_only_missing_in_order():
    run = _fresh_run(72)
    run.stages_done.add(STAGE_MER)
    run.stages_done.add(STAGE_ADR)
    missing = run.missing_stages_before_commit()
    # Pipeline order preserved: NFR, PROCESS, SUBPROJECT remain.
    assert missing == [STAGE_NFR, STAGE_PROCESS, STAGE_SUBPROJECT]


def test_commit_stage_is_not_required_before_commit():
    run = _fresh_run(73)
    run.stages_done.update(_ALL_PRE_COMMIT)
    # commit has not run yet but the gate is OK.
    assert run.missing_stages_before_commit() == []
    assert STAGE_COMMIT not in run.stages_done
