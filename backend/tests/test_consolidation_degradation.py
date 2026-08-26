"""Consolidation degrades gracefully when the LLM provider fails.

Incidente real (Planitrack2.0, sesion 47): el proveedor devolvia content=''
durante ~15 min; el juez de duplicados agotaba sus parse-retries y la
excepcion de ``model_validate_json`` escapaba de ``consolidate`` → el agente
re-llamaba la etapa 3 veces → ``stage_loop_exceeded`` y captura abortada.

Aqui se fija el contrato nuevo: un lote/juez agotado NO revienta la etapa.
El lote fallido se descarta de forma conservadora (los pares borderline quedan
DISTINTOS — nunca se fusiona sin confirmacion) y la degradacion se reporta via
``stats`` para que la etapa la muestre al agente/usuario.

El tope de loop por etapa tambien es ahora env-tunable
(``INFOFACT_STAGE_CAP``): un cap fijo de 3 es demasiado chico cuando cada
intento ya consumio 3 parse-retries internos contra un proveedor caido.
"""
from __future__ import annotations

import numpy as np
import pytest

import backend.agents.pipelines.consolidation as mod
from backend.agents.pipelines.extraction import RawRequirement


def _item(i: int, statement: str = None) -> RawRequirement:
    return RawRequirement(
        id=f"raw-{i}",
        statement=statement or f"el sistema debe hacer algo {i}",
        source_span="verbatim",
        section="s",
        confidence=0.9,
    )


class _BrokenLLM:
    """LLM whose structured response always fails to parse (empty content)."""

    async def ainvoke(self, msgs, **kw):
        raise ValueError("Invalid JSON: EOF while parsing value")


class _OkLLM:
    """LLM that returns an empty (valid) report for any prompt."""

    async def ainvoke(self, msgs, **kw):
        return mod.DuplicateReport(verdicts=[])


@pytest.mark.asyncio
async def test_judge_duplicates_degrades_when_provider_fails(monkeypatch):
    """Exhausted parse-retries in one batch → no exception; the batch is
    reported as failed and contributes NO confirmed pairs (conservative)."""
    monkeypatch.setattr(mod, "structured_llm", lambda *a, **kw: _BrokenLLM())
    items = [_item(0), _item(1)]
    candidates = [(0, 1)]
    confirmed, failed = await mod._judge_duplicates(items, candidates)
    assert confirmed == []
    assert failed == 1


@pytest.mark.asyncio
async def test_judge_duplicates_success_keeps_zero_failures(monkeypatch):
    monkeypatch.setattr(mod, "structured_llm", lambda *a, **kw: _OkLLM())
    items = [_item(0), _item(1)]
    confirmed, failed = await mod._judge_duplicates(items, [(0, 1)])
    assert confirmed == []
    assert failed == 0


@pytest.mark.asyncio
async def test_judge_contradictions_degrades_when_provider_fails(monkeypatch):
    monkeypatch.setattr(mod, "structured_llm", lambda *a, **kw: _BrokenLLM())
    items = [_item(0), _item(1)]
    pairs, failed = await mod._judge_contradictions(items, [(0, 1)])
    assert pairs == []
    assert failed is True


@pytest.mark.asyncio
async def test_consolidate_completes_degraded_and_surfaces_stats(monkeypatch):
    """Provider down across both judges → consolidate() still returns a result
    (auto-merge tier from embeddings unaffected) with degradation in stats."""
    monkeypatch.setattr(mod, "structured_llm", lambda *a, **kw: _BrokenLLM())

    def fake_embed(texts):
        # v0·v1 ≈ 0.93 → borderline [0.85, 0.95): obliga a pasar por el juez
        # LLM de duplicados (que está roto) en vez del auto-merge estricto.
        vecs = np.array([[1.0, 0.0], [0.93, 0.37], [0.0, 1.0]], dtype="float32")
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs

    monkeypatch.setattr(mod, "embed_texts", fake_embed)
    items = [
        _item(0, "exportar reportes en pdf"),
        _item(1, "exportar los reportes en formato pdf"),
        _item(2, "autenticar usuarios con google"),
    ]
    result = await mod.consolidate(items)
    assert result.items  # exact-dedup + strict tier still resolved items
    assert result.stats["dup_judge_failed_batches"] >= 1
    assert result.stats["contradict_judge_failed"] == 1


def test_stage_cap_env_tunable(monkeypatch):
    """INFOFACT_STAGE_CAP overrides the per-stage loop cap at call time."""
    from backend.agents.subagents import capture_run_holder as holder

    run = holder.CaptureRun(project_id=1, target=None)
    monkeypatch.setenv("INFOFACT_STAGE_CAP", "1")
    assert run.bump("ingest") == 1
    with pytest.raises(holder.StageLoopExceeded):
        run.bump("ingest")
    monkeypatch.delenv("INFOFACT_STAGE_CAP")
    # Default cap applies again (fresh stage counter unaffected by the env).
    assert run.bump("conventions") == 1
