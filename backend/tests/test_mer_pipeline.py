"""Tests for the MER pipeline: deterministic parts + multi-pass logic.

Pins the Mermaid erDiagram renderer (entity blocks, PK markers, relationship
cardinality notation), the cardinality normalizer, the empty-input fast path,
the deterministic validation (Pass 4), and multi-pass orchestration with
mocked LLM calls. The real LLM-dependent path is exercised via smoke scripts.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.pipelines.mer_pipeline import (
    EntityCandidate,
    EntityDiscoverySchema,
    MerAttribute,
    MerEntitySchema,
    MerRelationshipSchema,
    MerResult,
    MerDetailSchema,
    MerCritiqueSchema,
    _normalize_cardinality,
    _render_mermaid,
    _validate_mer,
    generate_mer,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_entity(
    name: str = "User",
    attributes: list[MerAttribute] | None = None,
) -> MerEntitySchema:
    return MerEntitySchema(
        name=name,
        attributes=attributes
        or [MerAttribute(name="id", type="uuid", is_key=True)],
    )


def _make_relationship(
    from_entity: str = "User",
    to_entity: str = "Order",
    cardinality: str = "1:N",
    label: str = "has",
) -> MerRelationshipSchema:
    return MerRelationshipSchema(
        from_entity=from_entity,
        to_entity=to_entity,
        cardinality=cardinality,
        label=label,
    )


# --------------------------------------------------------------------------- #
# _render_mermaid                                                             #
# --------------------------------------------------------------------------- #


def test_render_mermaid_valid_erdiagram():
    entities = [
        _make_entity("User"),
        _make_entity(
            "Order",
            attributes=[
                MerAttribute(name="id", type="uuid", is_key=True),
                MerAttribute(name="total", type="float"),
            ],
        ),
    ]
    relationships = [_make_relationship(cardinality="1:N")]

    out = _render_mermaid(entities, relationships)

    lines = out.splitlines()
    assert lines[0] == "erDiagram"
    # Entity names appear uppercased.
    assert "USER {" in out
    assert "ORDER {" in out
    # PK attribute marker on the key attribute.
    assert "uuid id PK" in out
    # Non-key attribute has no PK suffix.
    assert "float total" in out
    # Relationship line with 1:N cardinality notation.
    assert 'USER ||--o{ ORDER : "has"' in out


def test_render_mermaid_empty_entities():
    out = _render_mermaid([], [])
    assert out == "erDiagram"


# --------------------------------------------------------------------------- #
# Cardinality notation                                                        #
# --------------------------------------------------------------------------- #


def test_mermaid_cardinality_notation():
    """Verify canonical cardinalities map to the right Mermaid notation."""
    entities = [_make_entity("A"), _make_entity("B")]

    one_to_one = _render_mermaid(
        entities, [_make_relationship(cardinality="1:1", label="one-to-one")]
    )
    assert "||--||" in one_to_one

    one_to_many = _render_mermaid(
        entities, [_make_relationship(cardinality="1:N", label="one-to-many")]
    )
    assert "||--o{" in one_to_many

    many_to_many = _render_mermaid(
        entities, [_make_relationship(cardinality="N:M", label="many-to-many")]
    )
    assert "}o--o{" in many_to_many


# --------------------------------------------------------------------------- #
# _normalize_cardinality                                                      #
# --------------------------------------------------------------------------- #


def test_normalize_cardinality_canonical_forms():
    # Already-canonical forms pass through unchanged.
    assert _normalize_cardinality("1:1") == "1:1"
    assert _normalize_cardinality("1:N") == "1:N"
    assert _normalize_cardinality("N:M") == "N:M"


def test_normalize_cardinality_common_variants():
    # Hyphenated forms.
    assert _normalize_cardinality("1-1") == "1:1"
    assert _normalize_cardinality("1-N") == "1:N"
    assert _normalize_cardinality("N-M") == "N:M"
    # M:N variant maps to N:M.
    assert _normalize_cardinality("M:N") == "N:M"
    # Word forms.
    assert _normalize_cardinality("one-to-one") == "1:1"
    assert _normalize_cardinality("one-to-many") == "1:N"
    assert _normalize_cardinality("many-to-many") == "N:M"


def test_normalize_cardinality_whitespace_and_case():
    assert _normalize_cardinality("  1 : 1  ") == "1:1"
    assert _normalize_cardinality("n:m") == "N:M"


def test_normalize_cardinality_unknown_defaults_to_one_to_many():
    assert _normalize_cardinality("zero-or-one") == "1:N"
    assert _normalize_cardinality("???") == "1:N"


# --------------------------------------------------------------------------- #
# _validate_mer (Pass 4)                                                      #
# --------------------------------------------------------------------------- #


def test_validate_mer_missing_pk():
    """Entity without any PK attribute -> warning."""
    entities = [
        MerEntitySchema(
            name="Order",
            attributes=[
                MerAttribute(name="total", type="float", is_key=False),
            ],
        ),
    ]
    warnings = _validate_mer(entities, [])
    assert any("sin PK" in w for w in warnings)


def test_validate_mer_unknown_entity_in_relationship():
    """Relationship references non-existent entity -> warning."""
    entities = [_make_entity("User")]
    rels = [
        _make_relationship(
            from_entity="User", to_entity="NonExistent", cardinality="1:N"
        )
    ]
    warnings = _validate_mer(entities, rels)
    assert any("inexistente" in w and "NonExistent" in w for w in warnings)


def test_validate_mer_duplicate_entities():
    """Two entities with same name (different case) -> warning."""
    entities = [
        _make_entity("Customer"),
        _make_entity("customer"),
    ]
    warnings = _validate_mer(entities, [])
    assert any("duplicada" in w.lower() for w in warnings)


def test_validate_mer_invalid_cardinality():
    """Cardinality 'foo' -> warning."""
    entities = [_make_entity("A"), _make_entity("B")]
    rels = [
        MerRelationshipSchema(
            from_entity="A", to_entity="B", cardinality="foo", label="rel"
        )
    ]
    warnings = _validate_mer(entities, rels)
    assert any("invalida" in w.lower() and "foo" in w for w in warnings)


def test_validate_mer_clean_model():
    """Valid model with PKs, valid cardinalities, existing refs -> no warnings."""
    entities = [_make_entity("User"), _make_entity("Order")]
    rels = [_make_relationship(cardinality="1:N")]
    warnings = _validate_mer(entities, rels)
    assert warnings == []


# --------------------------------------------------------------------------- #
# _gap_pass (Pass 2) with mocked LLM                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_gap_pass_no_uncovered_codes_returns_empty():
    """When all input codes are covered by candidates, gap pass returns []."""
    # Use a mock to ensure the LLM is NOT called.
    with patch(
        "backend.agents.pipelines.mer_pipeline.structured_llm"
    ) as mock_llm_factory:
        # Build dummy items with codes.
        from backend.models.requirement import ReqType

        class _FakeItem:
            def __init__(self, code, statement, type_=ReqType.FUNCTIONAL):
                self.code = code
                self.statement = statement
                self.type = type_
                self.source = None

        items = [
            _FakeItem("REQ-001", "The system shall do X"),
            _FakeItem("REQ-002", "The system shall do Y"),
        ]
        candidates = [
            EntityCandidate(
                name="X",
                traced_req_codes=["REQ-001", "REQ-002"],
            )
        ]

        from backend.agents.pipelines.mer_pipeline import _gap_pass

        result = await _gap_pass(items, candidates, "Proj", "")
        assert result == []
        # LLM was never called.
        mock_llm_factory.assert_not_called()


@pytest.mark.asyncio
async def test_gap_pass_detection():
    """Items with codes not covered by candidates trigger gap pass with LLM."""
    from backend.agents.pipelines.mer_pipeline import _gap_pass
    from backend.models.requirement import ReqType

    class _FakeItem:
        def __init__(self, code, statement, type_=ReqType.FUNCTIONAL):
            self.code = code
            self.statement = statement
            self.type = type_
            self.source = None

    items = [
        _FakeItem("REQ-001", "The system shall do X"),
        _FakeItem("REQ-002", "The system shall manage invoices"),
    ]
    # REQ-001 is covered; REQ-002 is not.
    candidates = [
        EntityCandidate(name="X", traced_req_codes=["REQ-001"]),
    ]

    # Mock structured_llm to return an EntityDiscoverySchema with one new entity.
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=EntityDiscoverySchema(
            entities=[
                EntityCandidate(
                    name="Invoice",
                    traced_req_codes=["REQ-002"],
                )
            ]
        )
    )
    with patch(
        "backend.agents.pipelines.mer_pipeline.structured_llm",
        return_value=mock_llm,
    ):
        result = await _gap_pass(items, candidates, "Proj", "")

    assert len(result) == 1
    assert result[0].name == "Invoice"
    assert result[0].traced_req_codes == ["REQ-002"]


# --------------------------------------------------------------------------- #
# generate_mer stats (mocked multi-pass)                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_generate_mer_stats_include_validation_and_critique():
    """generate_mer stats dict has validation_warnings and critique_findings."""
    from backend.models.requirement import ReqType

    class _FakeItem:
        def __init__(self, code, statement, type_=ReqType.FUNCTIONAL):
            self.code = code
            self.statement = statement
            self.type = type_
            self.source = None

    items = [
        _FakeItem("REQ-001", "The system shall manage users"),
        _FakeItem("REQ-002", "The system shall manage orders"),
    ]

    # Mock each pass function.
    candidates = [
        EntityCandidate(name="User", traced_req_codes=["REQ-001"]),
        EntityCandidate(name="Order", traced_req_codes=["REQ-002"]),
    ]

    entities_full = [
        MerEntitySchema(
            name="User",
            attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
            traced_req_codes=["REQ-001"],
        ),
        MerEntitySchema(
            name="Order",
            attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
            traced_req_codes=["REQ-002"],
        ),
    ]
    relationships = [
        MerRelationshipSchema(
            from_entity="User", to_entity="Order", cardinality="1:N", label="has"
        )
    ]

    with patch(
        "backend.agents.pipelines.mer_pipeline._discover_entities",
        return_value=candidates,
    ), patch(
        "backend.agents.pipelines.mer_pipeline._gap_pass",
        return_value=[],
    ), patch(
        "backend.agents.pipelines.mer_pipeline._detail_entities_relationships",
        return_value=(entities_full, relationships),
    ), patch(
        "backend.agents.pipelines.mer_pipeline._critique_mer",
        return_value=[],
    ):
        result = await generate_mer(items, project_name="Proj")

    assert "validation_warnings" in result.stats
    assert "critique_findings" in result.stats
    assert "gap_pass_found" in result.stats
    assert "req_coverage" in result.stats
    assert result.stats["validation_warnings"] == []  # clean model
    assert result.stats["entities"] == 2
    assert result.stats["relationships"] == 1


# --------------------------------------------------------------------------- #
# generate_mer empty-input fast path                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_generate_mer_empty_items_returns_empty():
    result = await generate_mer([])
    assert isinstance(result, MerResult)
    assert result.entities == []
    assert result.relationships == []
    assert result.mermaid == ""
    assert result.stats["input"] == 0


# --------------------------------------------------------------------------- #
# generate_mer disable_critique skips Pass 5                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_generate_mer_disable_critique_skips_pass5():
    """When enable_critique=False, _critique_mer is never called."""
    from backend.models.requirement import ReqType

    class _FakeItem:
        def __init__(self, code, statement, type_=ReqType.FUNCTIONAL):
            self.code = code
            self.statement = statement
            self.type = type_
            self.source = None

    items = [_FakeItem("REQ-001", "The system shall manage users")]

    candidates = [EntityCandidate(name="User", traced_req_codes=["REQ-001"])]
    entities_full = [
        MerEntitySchema(
            name="User",
            attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
            traced_req_codes=["REQ-001"],
        )
    ]

    with patch(
        "backend.agents.pipelines.mer_pipeline._discover_entities",
        return_value=candidates,
    ), patch(
        "backend.agents.pipelines.mer_pipeline._gap_pass",
        return_value=[],
    ), patch(
        "backend.agents.pipelines.mer_pipeline._detail_entities_relationships",
        return_value=(entities_full, []),
    ), patch(
        "backend.agents.pipelines.mer_pipeline._critique_mer"
    ) as mock_critique:
        result = await generate_mer(items, enable_critique=False)

    mock_critique.assert_not_called()
    assert result.stats["critique_findings"] == []
    assert result.stats["critique_blockers"] == 0
