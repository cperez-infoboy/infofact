"""Characterization test for the Phase-1 critique scheduling refactor.

Pins that `critique_all` runs the per-item REFINEMENT concurrently (via
`asyncio.gather`, each `critique_item` acquiring the semaphore individually),
NOT as a sequential `for` loop inside a held semaphore slot. We assert this by
tracking the peak number of in-flight `_judge_item` calls within one batch: a
sequential loop peaks at 1; the gathered refinement peaks at >=2 (up to
`concurrency`).

The LLM is stubbed everywhere (no network).
"""
from __future__ import annotations

import asyncio

import pytest

from backend.agents.pipelines import critique as crt
from backend.agents.pipelines.critique import CritiqueVerdict
from backend.agents.pipelines.extraction import RawRequirement


def _item(i: int) -> RawRequirement:
    return RawRequirement(
        id=f"r{i}",
        statement=f"req number {i}",
        source_span=f"span number {i}",
        document_id="doc1",
        page=1,
        section="Sec",
        confidence=0.9,
    )


def _non_clean(it: RawRequirement) -> CritiqueVerdict:
    """A verdict that forces the refinement loop to fire once (fix + rewrite)."""
    return CritiqueVerdict(
        item_id=it.id,
        nature="requirement",
        fidelity="fix",
        atomic="pass",
        verifiable="pass",
        reasons={"fidelity": "incomplete"},
        suggested_rewrite=f"{it.statement} (completo)",
    )


def _clean(it: RawRequirement) -> CritiqueVerdict:
    return CritiqueVerdict(
        item_id=it.id,
        nature="requirement",
        fidelity="pass",
        atomic="pass",
        verifiable="pass",
    )


@pytest.mark.asyncio
async def test_critique_refinement_runs_concurrently(monkeypatch):
    """Within one batch, refinement _judge_item calls overlap in time."""
    items = [_item(i) for i in range(5)]

    async def fake_judge_batch(batch, neighbors_by_id, *, rules=None):
        return [_non_clean(it) for it in batch], crt.BatchStats(batch_calls=1)

    monkeypatch.setattr(crt, "_judge_batch", fake_judge_batch)

    in_flight = 0
    max_in_flight = 0

    async def fake_judge_item(item, neighbors=None, *, attempts=3,
                              transient_retries=8, rules=None):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return _clean(item)

    monkeypatch.setattr(crt, "_judge_item", fake_judge_item)

    result = await crt.critique_all(items, concurrency=4, batch_size=5)

    # Sequential refinement would keep max_in_flight == 1. The gathered
    # refinement must overlap (up to `concurrency` in flight).
    assert max_in_flight >= 2, (
        f"refinement ran sequentially (max_in_flight={max_in_flight})"
    )
    # All 5 items were refined exactly once (1 _judge_item call each).
    assert len(result.items) == 5
    assert result.stats["input"] == 5


@pytest.mark.asyncio
async def test_critique_peak_concurrency_bounded_by_semaphore(monkeypatch):
    """The decoupled refinement still respects `concurrency` as a hard cap."""
    items = [_item(i) for i in range(8)]

    async def fake_judge_batch(batch, neighbors_by_id, *, rules=None):
        return [_non_clean(it) for it in batch], crt.BatchStats(batch_calls=1)

    monkeypatch.setattr(crt, "_judge_batch", fake_judge_batch)

    in_flight = 0
    max_in_flight = 0

    async def fake_judge_item(item, neighbors=None, *, attempts=3,
                              transient_retries=8, rules=None):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return _clean(item)

    monkeypatch.setattr(crt, "_judge_item", fake_judge_item)

    await crt.critique_all(items, concurrency=3, batch_size=4)

    # The shared semaphore (concurrency=3) must cap peak in-flight refinement
    # calls at 3 — the decoupling does NOT raise peak concurrency.
    assert max_in_flight <= 3, (
        f"peak concurrency exceeded semaphore: max_in_flight={max_in_flight}"
    )
