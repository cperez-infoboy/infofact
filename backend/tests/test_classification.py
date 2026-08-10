"""Tests for classification pipeline (Pass A + Pass B).

Verifies that OCR/vision-extracted items are NEVER sent to Pass B
(decomposition), even when the classifier flags them as high-level.

Vision-extracted items lack the rich textual context that structured
document sections provide. Decomposing them tends to hallucinate unrelated
sub-items (e.g. a color-palette caption from an OCR'd diagram yielding
sub-items about login, logo, architecture).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.pipelines.classification import (
    ClassificationDecision,
    DecomposedItem,
    classify_all,
)
from backend.agents.pipelines.extraction import RawRequirement
from backend.models.requirement import Priority, ReqType


def _raw(statement: str, *, section: str, id: str) -> RawRequirement:
    return RawRequirement(
        statement=statement,
        source_span=statement,
        section=section,
        confidence=0.9,
        id=id,
    )


def _decompose_needed(id: str) -> ClassificationDecision:
    return ClassificationDecision(
        item_id=id,
        type=ReqType.FUNCTIONAL,
        priority=Priority.MUST,
        rationale="high-level",
        decomposition_needed=True,
    )


# ---------------------------------------------------------------------------
# Vision-extraction guard: OCR items must skip Pass B (decomposition).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("section", [
    "Imagen (OCR)",
    "Imagen embebida",
])
@pytest.mark.asyncio
async def test_vision_extracted_items_skip_decomposition(section: str):
    """Items from OCR/vision sections are not decomposed even when flagged."""
    vision_item = _raw("Paleta de colores corporativa.", section=section, id="v1")
    normal_item = _raw("El sistema debe ser seguro.", section="Seguridad", id="n1")

    fake_parts = [DecomposedItem(statement="sub", rationale="derived")]

    with patch(
        "backend.agents.pipelines.classification._classify_item",
        new_callable=AsyncMock,
        side_effect=lambda item, **kw: _decompose_needed(item.id),
    ), patch(
        "backend.agents.pipelines.classification._decompose_item",
        new_callable=AsyncMock,
        return_value=fake_parts,
    ) as mock_decompose:
        res = await classify_all(
            [vision_item, normal_item],
            decompose=True,
            batch_size=1,
            concurrency=1,
        )

    # Normal item IS decomposed.
    assert "n1" in res.decompositions
    assert len(res.decompositions["n1"]) == 1

    # Vision item is NOT decomposed — no entry in decompositions.
    assert "v1" not in res.decompositions, (
        f"Item from section '{section}' must not be decomposed"
    )

    # _decompose_item was called only once (for the normal item).
    assert mock_decompose.call_count == 1, (
        f"Expected 1 decompose call, got {mock_decompose.call_count}"
    )

    # Stats reflect the skip.
    assert res.stats["decomposed_items"] == 1
    assert res.stats["sub_items"] == 1


@pytest.mark.asyncio
async def test_normal_section_items_still_decompose():
    """Sanity: items from regular sections are still decomposed when flagged."""
    item = _raw("El sistema debe ser seguro.", section="Seguridad", id="s1")

    fake_parts = [DecomposedItem(statement="authn sub", rationale="")]

    with patch(
        "backend.agents.pipelines.classification._classify_item",
        new_callable=AsyncMock,
        side_effect=lambda item, **kw: _decompose_needed(item.id),
    ), patch(
        "backend.agents.pipelines.classification._decompose_item",
        new_callable=AsyncMock,
        return_value=fake_parts,
    ):
        res = await classify_all([item], decompose=True, batch_size=1)

    assert "s1" in res.decompositions
    assert res.stats["decomposed_items"] == 1
