"""Batched execution of the MER and process pipelines at Planitrack2.0 scale.

With ~1400 requirements a single structured LLM call overflows the prompt or
truncates the JSON output, so both pipelines chunk their items into batches
(INFOFACT_MER_BATCH / INFOFACT_PROCESS_BATCH, default 25). These tests pin:

- Multiple LLM calls when the corpus exceeds one batch (per pass), and a
  single call when it does not.
- Cross-batch merge: entity/lifecycle names seen in several batches appear
  once (case-insensitive), with unioned traced_req_codes.
- Exact gap-pass coverage: only uncovered codes are re-scanned (the old 30%
  diagram-count heuristic is gone), and a fully-covered corpus makes zero
  LLM calls.
- Graceful degradation: one failed batch never aborts the pass — the other
  batches' candidates survive, and generate_mer still produces a model.
- The stage-tool guard: a MER that ends with 0 entities over a live corpus
  returns mer_empty=True so the agent does not continue an empty cascade.

No DB / no real LLM: ``structured_llm`` is stubbed per module with factories
that record every call and return queued schema objects.
"""
from __future__ import annotations

import pytest

import backend.agents.pipelines.mer_pipeline as mer_mod
import backend.agents.pipelines.process_pipeline as process_mod
import backend.agents.subagents.analysis_agent as agent_mod
import backend.services.requirement_store as req_store_mod
import backend.services.srs_store as srs_store_mod
from backend.agents.pipelines.mer_pipeline import (
    EntityCandidate,
    EntityDiscoverySchema,
    MerAttribute,
    MerCritiqueSchema,
    MerDescriptionSchema,
    MerDetailSchema,
    MerEntityDetail,
    MerEntitySchema,
    MerRelationshipSchema,
    MerResult,
)
from backend.agents.pipelines.process_pipeline import (
    InteractionCandidate,
    InteractionSchema,
    LifecycleCandidate,
    LifecycleSchema,
    ProcessGapSchema,
    ProcessResultSchema,
    SequenceDiagramSchema,
    StateMachineSchema,
    StateTransitionData,
)
from backend.agents.subagents import analysis_run_holder as holder
from backend.agents.subagents.analysis_run_holder import STAGE_MER
from backend.models.requirement import ReqStatus, ReqType

PROJECT_ID = 4491


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


class _FakeItem:
    def __init__(self, code: str):
        self.code = code
        self.statement = f"El sistema debe gestionar {code}"
        self.type = ReqType.FUNCTIONAL
        self.status = ReqStatus.VALIDATED
        self.source = None
        self.acceptance_criteria = []


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _make_items(n: int) -> list[_FakeItem]:
    return [_FakeItem(f"REQ-{i:04d}") for i in range(n)]


def _discovery(entity: str, codes: list[str]) -> EntityDiscoverySchema:
    return EntityDiscoverySchema(
        entities=[EntityCandidate(name=entity, traced_req_codes=codes)]
    )


class _LLMFactory:
    """Stand-in for ``structured_llm``: counts calls, returns queued results.

    Queue entries may be schema instances or exceptions. ``fail_after`` (if
    set) makes every call beyond that ordinal raise persistently — the shape
    a doomed batch really has (truncated output never parses on retry).
    When the queue runs dry, valid empty schema instances are produced.
    """

    def __init__(self, queue=None, fail_after: int | None = None):
        self.queue = list(queue or [])
        self.fail_after = fail_after
        self.calls: int = 0

    def __call__(self, schema, **kw):
        self._schema = schema
        return self

    async def ainvoke(self, msgs, **kw):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("persistent failure (truncated output)")
        if self.queue:
            result = self.queue.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return _empty_schema(self._schema)


def _empty_schema(schema):
    """A syntactically valid empty instance for any pipeline schema."""
    if schema is EntityDiscoverySchema:
        return EntityDiscoverySchema(entities=[])
    if schema is MerDetailSchema:
        return MerDetailSchema(entities=[], relationships=[])
    if schema is MerCritiqueSchema:
        return MerCritiqueSchema(findings=[])
    if schema is MerDescriptionSchema:
        return MerDescriptionSchema(description="")
    if schema is LifecycleSchema:
        return LifecycleSchema(lifecycles=[])
    if schema is InteractionSchema:
        return InteractionSchema(interactions=[])
    if schema is ProcessGapSchema:
        return ProcessGapSchema()
    return ProcessResultSchema()


@pytest.fixture(autouse=True)
def _isolate_holder():
    holder.clear_run(PROJECT_ID)
    yield
    holder.clear_run(PROJECT_ID)


# --------------------------------------------------------------------------- #
# MER pipeline batching                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_mer_discovery_splits_large_corpus_into_batches(monkeypatch):
    """40 items with batch 25 => 2 discovery calls; same entity in both
    batches (different case) merges into one with unioned codes."""
    items = _make_items(40)
    factory = _LLMFactory(
        queue=[
            _discovery("User", [it.code for it in items[:25]]),
            _discovery("user", [it.code for it in items[25:]]),
        ]
    )
    monkeypatch.setattr(mer_mod, "structured_llm", factory)

    candidates = await mer_mod._discover_entities(items, "Proj", "")

    assert factory.calls == 2
    assert len(candidates) == 1
    assert len(candidates[0].traced_req_codes) == 40


@pytest.mark.asyncio
async def test_mer_gap_pass_batched_over_uncovered_only(monkeypatch):
    """60 items, 10 covered => 50 uncovered => 2 batched rescan calls."""
    items = _make_items(60)
    candidates = [
        EntityCandidate(name="A", traced_req_codes=[
            it.code for it in items[:10]
        ])
    ]
    factory = _LLMFactory(
        queue=[
            EntityDiscoverySchema(entities=[]),
            EntityDiscoverySchema(entities=[]),
        ]
    )
    monkeypatch.setattr(mer_mod, "structured_llm", factory)

    extra = await mer_mod._gap_pass(items, candidates, "Proj", "")

    assert factory.calls == 2
    assert extra == []


@pytest.mark.asyncio
async def test_mer_gap_pass_zero_calls_when_fully_covered(monkeypatch):
    items = _make_items(5)
    candidates = [
        EntityCandidate(name="A", traced_req_codes=[it.code for it in items])
    ]
    factory = _LLMFactory()
    monkeypatch.setattr(mer_mod, "structured_llm", factory)

    extra = await mer_mod._gap_pass(items, candidates, "Proj", "")

    assert factory.calls == 0
    assert extra == []


@pytest.mark.asyncio
async def test_mer_detail_sends_only_traced_reqs_per_batch(monkeypatch):
    """Each detail batch only carries requirements traced to its entities."""
    items = _make_items(4)
    candidates = [
        EntityCandidate(name=f"Ent{i}", traced_req_codes=[items[i].code])
        for i in range(4)
    ]
    detail = MerDetailSchema(
        entities=[
            MerEntityDetail(
                name=c.name,
                attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
            )
            for c in candidates
        ],
        relationships=[
            MerRelationshipSchema(
                from_entity="Ent0", to_entity="Ent1",
                cardinality="1:N", label="has",
            )
        ],
    )
    factory = _LLMFactory(queue=[detail])
    monkeypatch.setattr(mer_mod, "structured_llm", factory)

    entities_full, relationships = (
        await mer_mod._detail_entities_relationships(items, candidates, "Proj", "")
    )

    assert len(entities_full) == 4
    assert all(e.attributes for e in entities_full)
    assert len(relationships) == 1


@pytest.mark.asyncio
async def test_mer_failed_batch_degrades_gracefully(monkeypatch):
    """A failing discovery batch drops its candidates, not the whole pass."""
    items = _make_items(40)
    # Batch 1 succeeds; batch 2 fails persistently and is dropped.
    factory = _LLMFactory(
        queue=[
            _discovery("User", [it.code for it in items[:25]]),
        ],
        fail_after=2,
    )
    monkeypatch.setattr(mer_mod, "structured_llm", factory)

    candidates = await mer_mod._discover_entities(items, "Proj", "")

    assert len(candidates) == 1
    assert len(candidates[0].traced_req_codes) == 25


@pytest.mark.asyncio
async def test_generate_mer_end_to_end_batched(monkeypatch):
    """generate_mer over a >batch corpus produces a valid 30-entity model."""
    items = _make_items(30)
    discovery = [
        EntityDiscoverySchema(entities=[
            EntityCandidate(name=f"Ent{i}", traced_req_codes=[items[i].code])
            for i in range(0, 15)
        ]),
        EntityDiscoverySchema(entities=[
            EntityCandidate(name=f"Ent{i}", traced_req_codes=[items[i].code])
            for i in range(15, 30)
        ]),
    ]
    detail_results = [
        MerDetailSchema(entities=[
            MerEntityDetail(
                name=f"Ent{i}",
                attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
            )
            for i in range(0, 8)
        ], relationships=[]),
        MerDetailSchema(entities=[
            MerEntityDetail(
                name=f"Ent{i}",
                attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
            )
            for i in range(8, 16)
        ], relationships=[]),
        MerDetailSchema(entities=[
            MerEntityDetail(
                name=f"Ent{i}",
                attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
            )
            for i in range(16, 24)
        ], relationships=[]),
        MerDetailSchema(entities=[
            MerEntityDetail(
                name=f"Ent{i}",
                attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
            )
            for i in range(24, 30)
        ], relationships=[]),
    ]
    calls_by_schema: dict[str, int] = {}

    class _Router:
        def __call__(self, schema, **kw):
            self._schema = schema
            return self

        async def ainvoke(self, msgs, **kw):
            schema = self._schema
            calls_by_schema[schema.__name__] = (
                calls_by_schema.get(schema.__name__, 0) + 1
            )
            if schema is EntityDiscoverySchema:
                return discovery.pop(0)
            if schema is MerDetailSchema:
                return detail_results.pop(0)
            if schema is MerCritiqueSchema:
                return MerCritiqueSchema(findings=[])
            if schema is MerDescriptionSchema:
                return MerDescriptionSchema(description="modelo de dominio")
            raise AssertionError(f"unexpected schema {schema}")

    monkeypatch.setattr(mer_mod, "structured_llm", _Router())

    result = await mer_mod.generate_mer(items, project_name="Proj")

    # 2 discovery calls (30 items / batch 25) + 4 detail calls (30 entities /
    # detail batch 8); the gap pass is a no-op (full coverage).
    assert calls_by_schema.get("EntityDiscoverySchema") == 2
    assert calls_by_schema.get("MerDetailSchema") == 4
    assert calls_by_schema.get("MerCritiqueSchema") == 1
    assert len(result.entities) == 30
    assert result.stats["req_coverage"] == "30/30"


# --------------------------------------------------------------------------- #
# Process pipeline batching                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_process_lifecycle_identification_is_batched(monkeypatch):
    items = _make_items(40)
    factory = _LLMFactory(
        queue=[
            LifecycleSchema(lifecycles=[
                LifecycleCandidate(
                    entity_name="Order",
                    has_lifecycle=True,
                    traced_req_codes=[it.code for it in items[:25]],
                )
            ]),
            LifecycleSchema(lifecycles=[
                LifecycleCandidate(
                    entity_name="order",
                    has_lifecycle=True,
                    traced_req_codes=[it.code for it in items[25:]],
                )
            ]),
        ]
    )
    monkeypatch.setattr(process_mod, "structured_llm", factory)

    lifecycles = await process_mod._identify_lifecycles(items, None)

    assert factory.calls == 2
    assert len(lifecycles) == 1
    assert len(lifecycles[0].traced_req_codes) == 40


@pytest.mark.asyncio
async def test_process_gap_pass_uses_exact_coverage(monkeypatch):
    """With every code traced, the gap pass makes zero LLM calls (no 30% rule)."""
    items = _make_items(10)
    lifecycles = [
        LifecycleCandidate(
            entity_name="Order",
            has_lifecycle=True,
            traced_req_codes=[it.code for it in items],
        )
    ]
    factory = _LLMFactory()
    monkeypatch.setattr(process_mod, "structured_llm", factory)

    extra_lc, extra_int = await process_mod._gap_pass_process(
        items, lifecycles, [], None
    )

    assert factory.calls == 0
    assert extra_lc == []
    assert extra_int == []


@pytest.mark.asyncio
async def test_process_gap_pass_old_heuristic_would_have_skipped(monkeypatch):
    """3 diagrams over 30 items = 10% (under the old 30% rule the pass was
    skipped): exact coverage now detects the 27 uncovered codes and rescans."""
    items = _make_items(30)
    lifecycles = [
        LifecycleCandidate(
            entity_name="Order",
            has_lifecycle=True,
            traced_req_codes=[items[0].code],
        )
    ]
    interactions = [
        InteractionCandidate(
            name="Checkout",
            traced_req_codes=[items[1].code, items[2].code],
        )
    ]
    factory = _LLMFactory(queue=[ProcessGapSchema(lifecycles=[
        LifecycleCandidate(
            entity_name="Invoice",
            has_lifecycle=True,
            traced_req_codes=[items[29].code],
        )
    ])])
    monkeypatch.setattr(process_mod, "structured_llm", factory)
    monkeypatch.setattr(process_mod, "_PROCESS_BATCH_SIZE", 50)

    extra_lc, extra_int = await process_mod._gap_pass_process(
        items, lifecycles, interactions, None
    )

    assert factory.calls == 1
    assert len(extra_lc) == 1
    assert extra_lc[0].entity_name == "Invoice"
    assert extra_int == []


@pytest.mark.asyncio
async def test_process_generation_sends_only_traced_reqs(monkeypatch):
    """Generation batches carry only the requirements traced to their diagrams."""
    items = _make_items(3)
    lifecycles = [
        LifecycleCandidate(
            entity_name="Order",
            has_lifecycle=True,
            lifecycle_summary="draft -> paid",
            traced_req_codes=[items[0].code],
        )
    ]
    interactions = [
        InteractionCandidate(
            name="Checkout",
            traced_req_codes=[items[1].code, items[2].code],
        )
    ]
    generated = ProcessResultSchema(
        state_machines=[
            StateMachineSchema(
                entity_name="Order",
                states=["draft", "paid"],
                transitions=[
                    StateTransitionData(
                        from_state="draft", to_state="paid", label="pay"
                    )
                ],
            )
        ],
        sequence_diagrams=[],
    )

    sent_texts: list[str] = []

    class _Router:
        def __call__(self, schema, **kw):
            return self

        async def ainvoke(self, msgs, **kw):
            sent_texts.append(str(msgs))
            return generated

    monkeypatch.setattr(process_mod, "structured_llm", _Router())

    result = await process_mod._generate_mermaid_diagrams(
        items, lifecycles, interactions, None
    )

    assert result is not None
    assert len(result.state_machines) == 1
    # One batch call carrying all three traced items.
    assert len(sent_texts) == 1
    for it in items:
        assert it.code in sent_texts[0]


@pytest.mark.asyncio
async def test_process_generation_merge_dedupes_diagrams(monkeypatch):
    """The same state machine emitted by two batches appears once (batch 1)."""
    items = _make_items(2)
    lifecycles = [
        LifecycleCandidate(
            entity_name=f"Ent{i}",
            has_lifecycle=True,
            traced_req_codes=[items[i].code],
        )
        for i in range(2)
    ]

    def _schema(entity: str) -> ProcessResultSchema:
        return ProcessResultSchema(
            state_machines=[
                StateMachineSchema(
                    entity_name=entity,
                    states=["a", "b"],
                    transitions=[
                        StateTransitionData(from_state="a", to_state="b")
                    ],
                )
            ],
            sequence_diagrams=[],
        )

    results = [_schema("Ent0"), _schema("ent0")]

    class _Router:
        def __call__(self, schema, **kw):
            return self

        async def ainvoke(self, msgs, **kw):
            return results.pop(0)

    monkeypatch.setattr(process_mod, "structured_llm", _Router())
    monkeypatch.setattr(process_mod, "_PROCESS_BATCH_SIZE", 1)

    out = await process_mod._generate_mermaid_diagrams(items, lifecycles, [], None)

    assert len(out.state_machines) == 1


# --------------------------------------------------------------------------- #
# Stage-tool guard: MER vacío                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_generate_mer_tool_flags_empty_result(monkeypatch):
    """0 entities over a live corpus => tool returns mer_empty=True + message."""

    async def fake_list_requirements(session, project_id, include_deleted=False):
        return [_FakeItem("REQ-0001")]

    async def fake_list_goals(session, project_id):
        return []

    async def fake_generate_mer(items, **kw):
        return MerResult(mermaid="erDiagram")  # 0 entities

    monkeypatch.setattr(req_store_mod, "list_requirements", fake_list_requirements)
    monkeypatch.setattr(srs_store_mod, "list_goals", fake_list_goals)
    monkeypatch.setattr(mer_mod, "generate_mer", fake_generate_mer)
    monkeypatch.setattr(agent_mod, "AsyncSessionLocal", lambda: _FakeSession())

    tools = agent_mod._make_stage_tools(PROJECT_ID, "Proj", "desc")
    out = await tools[0].ainvoke({})

    assert out["mer_empty"] is True
    assert "0 entidades" in out["message"]
    assert STAGE_MER in out["stages_done"]


@pytest.mark.asyncio
async def test_generate_mer_tool_clean_result_has_no_flag(monkeypatch):
    """A normal MER result does not carry mer_empty (no prompt noise)."""

    async def fake_list_requirements(session, project_id, include_deleted=False):
        return [_FakeItem("REQ-0001")]

    async def fake_list_goals(session, project_id):
        return []

    async def fake_generate_mer(items, **kw):
        return MerResult(
            mermaid="erDiagram",
            entities=[MerEntitySchema(
                name="Order",
                attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
            )],
        )

    monkeypatch.setattr(req_store_mod, "list_requirements", fake_list_requirements)
    monkeypatch.setattr(srs_store_mod, "list_goals", fake_list_goals)
    monkeypatch.setattr(mer_mod, "generate_mer", fake_generate_mer)
    monkeypatch.setattr(agent_mod, "AsyncSessionLocal", lambda: _FakeSession())

    tools = agent_mod._make_stage_tools(PROJECT_ID, "Proj", "desc")
    out = await tools[0].ainvoke({})

    assert "mer_empty" not in out
    assert "message" not in out
    assert out["entities"] == 1


# Silence unused-import linters for symbols referenced only in docstrings.
_ = (InteractionSchema, ReqType)


# --------------------------------------------------------------------------- #
# Detail/generation batch sizes + live progress callback                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_mer_detail_uses_small_dedicated_batch(monkeypatch):
    """30 entities with detail batch 8 => 4 detail calls (session 19 fix:
    the OUTPUT budget, not the input, is what truncates this pass)."""
    items = _make_items(30)
    candidates = [
        EntityCandidate(name=f"Ent{i}", traced_req_codes=[items[i].code])
        for i in range(30)
    ]

    def _detail_for(names: list[str]) -> MerDetailSchema:
        return MerDetailSchema(
            entities=[
                MerEntityDetail(
                    name=n,
                    attributes=[
                        MerAttribute(name="id", type="uuid", is_key=True)
                    ],
                )
                for n in names
            ],
            relationships=[],
        )

    factory = _LLMFactory(
        queue=[
            _detail_for([f"Ent{i}" for i in range(0, 8)]),
            _detail_for([f"Ent{i}" for i in range(8, 16)]),
            _detail_for([f"Ent{i}" for i in range(16, 24)]),
            _detail_for([f"Ent{i}" for i in range(24, 30)]),
        ]
    )
    monkeypatch.setattr(mer_mod, "structured_llm", factory)

    entities_full, _ = await mer_mod._detail_entities_relationships(
        items, candidates, "Proj", ""
    )

    assert factory.calls == 4
    assert len(entities_full) == 30


@pytest.mark.asyncio
async def test_mer_detail_reports_progress_per_batch(monkeypatch):
    """The on_progress callback fires once per completed batch (incl. failed)."""
    items = _make_items(4)
    candidates = [
        EntityCandidate(name=f"Ent{i}", traced_req_codes=[items[i].code])
        for i in range(4)
    ]
    detail = MerDetailSchema(
        entities=[
            MerEntityDetail(name=c.name, attributes=[])
            for c in candidates
        ],
        relationships=[],
    )
    # Batch 1 succeeds; batch 2 fails persistently (parse retries exhausted,
    # the doomed-batch shape), is dropped, and the pass continues.
    factory = _LLMFactory(queue=[detail], fail_after=1)
    monkeypatch.setattr(mer_mod, "structured_llm", factory)
    monkeypatch.setattr(mer_mod, "_MER_DETAIL_BATCH_SIZE", 2)

    messages: list[str] = []

    async def _progress(msg: str) -> None:
        messages.append(msg)

    await mer_mod._detail_entities_relationships(
        items, candidates, "Proj", "", on_progress=_progress
    )

    # Batch 1 succeeds; batch 2 raises a parse-class error once and fails on
    # the retry (invoke_structured_resilient retries parse failures), then the
    # batch is dropped and the pass continues.
    assert factory.calls == 3
    assert len(messages) == 2
    assert "2/2 lotes" in messages[-1]
    assert "falló" in messages[-1]


@pytest.mark.asyncio
async def test_mer_detail_without_callback_stays_quiet(monkeypatch):
    """Default path (deterministic assembler) passes no callback: no breakage."""
    items = _make_items(2)
    candidates = [
        EntityCandidate(name=f"Ent{i}", traced_req_codes=[items[i].code])
        for i in range(2)
    ]
    factory = _LLMFactory(
        queue=[MerDetailSchema(entities=[
            MerEntityDetail(name=c.name, attributes=[])
            for c in candidates
        ], relationships=[])]
    )
    monkeypatch.setattr(mer_mod, "structured_llm", factory)
    monkeypatch.setattr(mer_mod, "_MER_DETAIL_BATCH_SIZE", 1)

    entities_full, _ = await mer_mod._detail_entities_relationships(
        items, candidates, "Proj", ""
    )

    assert len(entities_full) == 2
    assert factory.calls == 2


@pytest.mark.asyncio
async def test_process_generation_uses_small_dedicated_batch(monkeypatch):
    """3 diagram units with gen batch 2 => 2 generation calls + progress."""
    items = _make_items(3)
    lifecycles = [
        LifecycleCandidate(
            entity_name="Order",
            has_lifecycle=True,
            traced_req_codes=[items[0].code],
        )
    ]
    interactions = [
        InteractionCandidate(
            name=f"Flow{i}",
            traced_req_codes=[items[1].code],
        )
        for i in range(2)
    ]

    def _schema(entity: str) -> ProcessResultSchema:
        return ProcessResultSchema(
            state_machines=[],
            sequence_diagrams=[
                SequenceDiagramSchema(name=entity, participants=["A"], messages=[])
            ],
        )

    results = [_schema("Flow0"), _schema("flow0")]

    class _Router:
        def __call__(self, schema, **kw):
            return self

        async def ainvoke(self, msgs, **kw):
            return results.pop(0)

    monkeypatch.setattr(process_mod, "structured_llm", _Router())
    monkeypatch.setattr(process_mod, "_PROCESS_GEN_BATCH_SIZE", 2)

    messages: list[str] = []

    async def _progress(msg: str) -> None:
        messages.append(msg)

    out = await process_mod._generate_mermaid_diagrams(
        items, lifecycles, interactions, None, on_progress=_progress
    )

    assert out is not None
    assert len(out.sequence_diagrams) == 1  # duplicate Flow0/flow0 merged
    assert len(messages) == 2
    assert "1/2 lotes" in messages[0]
    assert "2/2 lotes" in messages[1]
