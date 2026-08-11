"""Tests for the NFR pipeline: constraint tagging, stack derivation with
constraints, batch constraint injection, and prompt content guards.

Verifies that:
- _build_nfr_items_text tags CONSTRAINT-type items with [RESTRICCION].
- _build_constraints_block extracts constraint items into a priority block.
- _derive_stack surfaces constraint requirements in the LLM context when
  items are passed.
- _discover_nfr_batch and _enrich_nfr_batch inject the constraints block
  into the LLM context so batches see global technology mandates.
- All NFR prompts mention constraint-honoring rules (regression guard).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.pipelines.nfr_pipeline import (
    NfrDecision,
    NfrStackSchema,
    _build_constraints_block,
    _build_nfr_items_text,
    _derive_stack,
    _NFR_BATCH_DETAIL_PROMPT,
    _NFR_CRITIQUE_PROMPT,
    _NFR_DETAIL_PROMPT,
    _NFR_DISCOVERY_PROMPT,
    _NFR_GAP_PASS_PROMPT,
    _NFR_STACK_PROMPT,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


class _FakeItem:
    """Minimal stand-in for RequirementItem used by pipeline helpers."""

    def __init__(self, code, statement, type_):
        self.code = code
        self.statement = statement
        self.type = type_
        self.source = None


# --------------------------------------------------------------------------- #
# _build_nfr_items_text: constraint tagging                                   #
# --------------------------------------------------------------------------- #


def test_build_nfr_items_text_tags_constraints():
    """Constraint-type items get [RESTRICCION] marker on REQ_CODE line."""
    from backend.models.requirement import ReqType

    items = [
        _FakeItem(
            "REQ-001",
            "The system shall use SQL Server",
            ReqType.CONSTRAINT,
        ),
        _FakeItem(
            "REQ-002",
            "The system shall be fast",
            ReqType.PERFORMANCE,
        ),
    ]
    text = _build_nfr_items_text(items)
    assert "REQ-001 [RESTRICCION]" in text
    assert "REQ-002 [RESTRICCION]" not in text


def test_build_nfr_items_text_no_constraint_no_tag():
    """Non-constraint items never get the [RESTRICCION] tag."""
    from backend.models.requirement import ReqType

    items = [
        _FakeItem("REQ-001", "Fast response", ReqType.PERFORMANCE),
        _FakeItem("REQ-002", "Secure auth", ReqType.SECURITY),
    ]
    text = _build_nfr_items_text(items)
    assert "[RESTRICCION]" not in text


# --------------------------------------------------------------------------- #
# _build_constraints_block                                                    #
# --------------------------------------------------------------------------- #


def test_build_constraints_block_extracts_constraints():
    """_build_constraints_block formats constraint items as priority text."""
    from backend.models.requirement import ReqType

    items = [
        _FakeItem("REQ-W057", "Usar Windows Server", ReqType.CONSTRAINT),
        _FakeItem("REQ-001", "Fast response", ReqType.PERFORMANCE),
        _FakeItem("REQ-61ZJ", "App nativa Android", ReqType.CONSTRAINT),
    ]
    block = _build_constraints_block(items)
    assert "RESTRICCIONES TECNOLOGICAS OBLIGATORIAS" in block
    assert "REQ-W057" in block
    assert "Windows Server" in block
    assert "REQ-61ZJ" in block
    assert "Android" in block
    assert "REQ-001" not in block


def test_build_constraints_block_empty_when_no_constraints():
    """Returns empty string when no constraint items exist."""
    from backend.models.requirement import ReqType

    items = [
        _FakeItem("REQ-001", "Fast", ReqType.PERFORMANCE),
    ]
    assert _build_constraints_block(items) == ""


# --------------------------------------------------------------------------- #
# _derive_stack: constraint items in LLM context                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_derive_stack_includes_constraint_items_in_context():
    """_derive_stack surfaces constraint requirements in LLM context."""
    from backend.models.requirement import ReqType

    decisions = [
        NfrDecision(
            req_code="REQ-001",
            category="security",
            decision="Use SQL Server for data at rest encryption",
            stack_component="SQL Server",
            rationale="Required by constraint",
        ),
    ]
    items = [
        _FakeItem(
            "REQ-001",
            "The system shall use SQL Server as its database",
            ReqType.CONSTRAINT,
        ),
    ]

    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=NfrStackSchema(
            stack=[], data_consistency="", patterns=""
        )
    )
    with patch(
        "backend.agents.pipelines.nfr_pipeline.structured_llm",
        return_value=mock_llm,
    ):
        await _derive_stack(decisions, "Proj", "", items=items)

    call_args = mock_llm.ainvoke.call_args
    msgs = call_args.args[0]
    user_msg = msgs[1][1]
    assert "REQUERIMIENTOS DE RESTRICCION" in user_msg
    assert "SQL Server" in user_msg


@pytest.mark.asyncio
async def test_derive_stack_without_items_omits_constraints_block():
    """_derive_stack without items does not include constraints block."""
    decisions = [
        NfrDecision(
            req_code="REQ-001",
            category="performance",
            decision="Add caching layer",
            stack_component="Redis",
            rationale="Reduce DB load",
        ),
    ]

    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=NfrStackSchema(
            stack=[], data_consistency="", patterns=""
        )
    )
    with patch(
        "backend.agents.pipelines.nfr_pipeline.structured_llm",
        return_value=mock_llm,
    ):
        await _derive_stack(decisions, "Proj", "")

    call_args = mock_llm.ainvoke.call_args
    msgs = call_args.args[0]
    user_msg = msgs[1][1]
    assert "REQUERIMIENTOS DE RESTRICCION" not in user_msg


# --------------------------------------------------------------------------- #
# Batch constraint injection                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_discover_nfr_batch_injects_constraints_block():
    """_discover_nfr_batch prepends constraints_block to user_text."""
    from backend.models.requirement import ReqType

    items_batch = [
        _FakeItem("REQ-002", "Fast response", ReqType.PERFORMANCE),
    ]
    constraints_block = (
        "RESTRICCIONES TECNOLOGICAS OBLIGATORIAS "
        "(aplican a TODAS las decisiones de este lote):\n"
        "- [REQ-001] Use SQL Server\n"
    )

    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=__import__(
            "backend.agents.pipelines.nfr_pipeline", fromlist=["NfrDiscoverySchema"]
        ).NfrDiscoverySchema(decisions=[])
    )
    with patch(
        "backend.agents.pipelines.nfr_pipeline.structured_llm",
        return_value=mock_llm,
    ):
        from backend.agents.pipelines.nfr_pipeline import _discover_nfr_batch
        await _discover_nfr_batch(
            items_batch, "Proj", "",
            constraints_block=constraints_block,
        )

    call_args = mock_llm.ainvoke.call_args
    user_msg = call_args.args[0][1][1]
    assert "RESTRICCIONES TECNOLOGICAS OBLIGATORIAS" in user_msg
    assert "SQL Server" in user_msg


@pytest.mark.asyncio
async def test_enrich_nfr_batch_injects_constraints_block():
    """_enrich_nfr_batch prepends constraints_block to user_text."""
    from backend.agents.pipelines.nfr_pipeline import (
        NfrBatchDetailSchema,
        NfrDecisionCandidate,
        _enrich_nfr_batch,
    )
    from backend.models.requirement import ReqType

    items_batch = [
        _FakeItem("REQ-002", "High availability", ReqType.RELIABILITY),
    ]
    candidates = [
        NfrDecisionCandidate(
            req_code="REQ-002",
            category="reliability",
            decision_summary="Implement HA cluster",
        ),
    ]
    constraints_block = (
        "RESTRICCIONES TECNOLOGICAS OBLIGATORIAS "
        "(aplican a TODAS las decisiones de este lote):\n"
        "- [REQ-001] Use SQL Server\n"
    )

    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=NfrBatchDetailSchema(decisions=[])
    )
    with patch(
        "backend.agents.pipelines.nfr_pipeline.structured_llm",
        return_value=mock_llm,
    ):
        await _enrich_nfr_batch(
            items_batch, candidates, "Proj", "",
            constraints_block=constraints_block,
        )

    call_args = mock_llm.ainvoke.call_args
    user_msg = call_args.args[0][1][1]
    assert "RESTRICCIONES TECNOLOGICAS OBLIGATORIAS" in user_msg
    assert "SQL Server" in user_msg


# --------------------------------------------------------------------------- #
# Prompt content guards (regression)                                          #
# --------------------------------------------------------------------------- #


def test_nfr_prompts_mention_constraint_rules():
    """All key NFR prompts must mention constraint-honoring rules."""
    for prompt in (
        _NFR_DISCOVERY_PROMPT,
        _NFR_BATCH_DETAIL_PROMPT,
        _NFR_STACK_PROMPT,
        _NFR_DETAIL_PROMPT,
    ):
        assert "RESTRICCION" in prompt.upper(), (
            "Prompt missing constraint-honoring rule"
        )


def test_nfr_stack_prompt_has_priority_absolute():
    """_NFR_STACK_PROMPT must mark constraint-honoring as absolute priority."""
    assert "PRIORIDAD ABSOLUTA" in _NFR_STACK_PROMPT


def test_nfr_critique_prompt_has_requirement_contradiction():
    """Critique prompt must include requirement_contradiction check type."""
    assert "requirement_contradiction" in _NFR_CRITIQUE_PROMPT


def test_nfr_gap_pass_prompt_mentions_constraints():
    """Gap pass prompt must mention respecting mandated technologies."""
    assert "mandateadas" in _NFR_GAP_PASS_PROMPT.lower() or \
        "restriccion" in _NFR_GAP_PASS_PROMPT.lower()
