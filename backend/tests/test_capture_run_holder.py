"""Tests for the staged-capture run holder (Phase 2).

Pins: single-active-run-per-project, loop cap semantics (per stage, surviving
re-ingest), stage completion bookkeeping, and the commit gate (which stages
must have run before commit_capture is allowed to persist).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.agents.subagents.capture_run_holder import (
    STAGE_CLASSIFY,
    STAGE_COMMIT,
    STAGE_CONSOLIDATE,
    STAGE_CONVENTIONS,
    STAGE_CRITIQUE,
    STAGE_EXTRACT,
    STAGE_INGEST,
    CaptureRun,
    StageLoopExceeded,
    clear_run,
    get_or_create_run,
    get_run,
)


def _fresh_run(project_id: int = 1, target: Path | None = None) -> CaptureRun:
    clear_run(project_id)
    return get_or_create_run(
        project_id, target or Path("/ws/proj"), project_name="x"
    )


def test_get_or_create_returns_same_run_for_same_project():
    a = _fresh_run()
    # A second call with the same id returns the SAME object (no reset).
    b = get_or_create_run(1, Path("/ws/other"))
    assert a is b


def test_get_run_is_none_for_unknown_project():
    clear_run(999)
    assert get_run(999) is None


def test_clear_run_returns_and_drops_the_run():
    run = _fresh_run(2)
    assert clear_run(2) is run
    assert get_run(2) is None
    # idempotent
    assert clear_run(2) is None


def test_bump_counts_and_enforces_cap():
    run = _fresh_run(3)
    assert run.bump(STAGE_EXTRACT) == 1
    assert run.bump(STAGE_EXTRACT) == 2
    assert run.bump(STAGE_EXTRACT) == 3
    with pytest.raises(StageLoopExceeded) as exc:
        run.bump(STAGE_EXTRACT)
    assert exc.value.stage == STAGE_EXTRACT
    assert exc.value.calls == 4
    assert exc.value.cap == 3


def test_bump_counts_are_independent_per_stage():
    run = _fresh_run(4)
    run.bump(STAGE_EXTRACT, cap=2)
    run.bump(STAGE_EXTRACT, cap=2)
    # extract is at its cap, but ingest still has room.
    run.bump(STAGE_INGEST)
    assert run.calls[STAGE_EXTRACT] == 2
    assert run.calls[STAGE_INGEST] == 1


def test_reset_pipeline_outputs_keeps_call_counters():
    run = _fresh_run(5)
    run.bump(STAGE_EXTRACT)
    run.bump(STAGE_EXTRACT)
    run.extracted = [object()]  # type: ignore[list-item]
    run.document_rules = object()  # type: ignore[assignment]
    run.stages_done.add(STAGE_EXTRACT)
    run.reset_pipeline_outputs()
    assert run.extracted == []
    assert run.cons is None
    assert run.document_rules is None
    assert STAGE_EXTRACT not in run.stages_done
    # Counters survive so a re-ingest cannot dodge the cap.
    assert run.calls[STAGE_EXTRACT] == 2


def test_missing_stages_lists_all_until_every_stage_ran():
    run = _fresh_run(6)
    missing = run.missing_stages_before_commit()
    assert set(missing) == {
        STAGE_INGEST,
        STAGE_CONVENTIONS,
        STAGE_EXTRACT,
        STAGE_CONSOLIDATE,
        STAGE_CRITIQUE,
        STAGE_CLASSIFY,
    }
    # Order follows the pipeline.
    assert missing[0] == STAGE_INGEST
    assert missing[-1] == STAGE_CLASSIFY
    run.stages_done.update(
        {
            STAGE_INGEST,
            STAGE_CONVENTIONS,
            STAGE_EXTRACT,
            STAGE_CONSOLIDATE,
            STAGE_CRITIQUE,
            STAGE_CLASSIFY,
        }
    )
    assert run.missing_stages_before_commit() == []


def test_commit_stage_is_not_required_before_commit():
    run = _fresh_run(7)
    run.stages_done.update(
        {
            STAGE_INGEST,
            STAGE_CONVENTIONS,
            STAGE_EXTRACT,
            STAGE_CONSOLIDATE,
            STAGE_CRITIQUE,
            STAGE_CLASSIFY,
        }
    )
    # commit has not run yet (this is the first commit call) but the gate is OK.
    assert run.missing_stages_before_commit() == []
    assert STAGE_COMMIT not in run.stages_done
