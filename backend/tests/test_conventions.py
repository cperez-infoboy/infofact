"""Unit tests for the CONVENTIONS stage (Component 1-3 of the plan).

Pins:
- ``merge_conventions`` is a pure deterministic data merge (no LLM). Covers
  empty input, single doc, multi-doc concatenation, and the
  first-non-None ``priority_field_label`` rule.
- ``extract_conventions`` graceful-degradation contract: empty text short
  circuits; LLM success caches on ``smap.conventions``; LLM failure returns an
  empty :class:`DocumentRules` (pipeline never aborts).
- ``_truncate_for_conventions`` head-biased bound.
- ``extract_chunk`` surfaces ``PRIORITY_FIELD_LABEL:`` only when ``rules`` is
  passed with a non-empty label (so the extractor knows what the labeled
  per-requirement field is).

The LLM is stubbed everywhere — these tests pin data flow, not model behavior.
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock

import pytest

from backend.agents.pipelines import extraction as exc


# ---------------------------------------------------------------------------
# merge_conventions (pure, no LLM)
# ---------------------------------------------------------------------------


def test_merge_conventions_empty_returns_empty_rules():
    merged = exc.merge_conventions([])
    assert isinstance(merged, exc.DocumentRules)
    assert merged.priority_legend == []
    assert merged.priority_field_label is None
    assert merged.scope_markers == []
    assert merged.glossary == {}


def test_merge_conventions_single_doc_returned_as_is():
    rules = exc.DocumentRules(
        priority_legend=[exc.LegendEntry(label="Alta", source_span="Alta")],
        priority_field_label="Prioridad",
        scope_markers=["fuera de alcance"],
        glossary={"SRS": "Software Requirements Specification"},
    )
    merged = exc.merge_conventions([rules])
    assert len(merged.priority_legend) == 1
    assert merged.priority_field_label == "Prioridad"
    assert merged.scope_markers == ["fuera de alcance"]
    assert merged.glossary == {"SRS": "Software Requirements Specification"}


def test_merge_conventions_concatenates_legend_scope_and_glossary():
    a = exc.DocumentRules(
        priority_legend=[exc.LegendEntry(label="Alta", source_span="Alta")],
        scope_markers=["fase 2"],
        glossary={"API": "Application Programming Interface"},
    )
    b = exc.DocumentRules(
        priority_legend=[exc.LegendEntry(label="Baja", source_span="Baja")],
        scope_markers=["futuro"],
        glossary={"UI": "User Interface"},
    )
    merged = exc.merge_conventions([a, b])
    assert [e.label for e in merged.priority_legend] == ["Alta", "Baja"]
    assert merged.scope_markers == ["fase 2", "futuro"]
    assert merged.glossary == {
        "API": "Application Programming Interface",
        "UI": "User Interface",
    }


def test_merge_conventions_picks_first_non_none_priority_field_label():
    a = exc.DocumentRules(priority_field_label=None)
    b = exc.DocumentRules(priority_field_label="Prioridad")
    c = exc.DocumentRules(priority_field_label="Importancia")
    merged = exc.merge_conventions([a, b, c])
    # First non-None wins; later labels are ignored (deterministic).
    assert merged.priority_field_label == "Prioridad"


def test_merge_conventions_glossary_later_doc_overrides_earlier():
    """``dict.update`` semantics: same key in a later doc overrides earlier."""
    a = exc.DocumentRules(glossary={"SRS": "old definition"})
    b = exc.DocumentRules(glossary={"SRS": "new definition"})
    merged = exc.merge_conventions([a, b])
    assert merged.glossary["SRS"] == "new definition"


# ---------------------------------------------------------------------------
# _truncate_for_conventions (pure)
# ---------------------------------------------------------------------------


def test_truncate_for_conventions_short_text_unchanged():
    text = "short document text"
    assert exc._truncate_for_conventions(text) == text


def test_truncate_for_conventions_long_text_truncated_to_budget():
    text = "x" * (exc._CONVENTIONS_TEXT_BUDGET + 500)
    out = exc._truncate_for_conventions(text)
    assert len(out) == exc._CONVENTIONS_TEXT_BUDGET
    # Head preserved (head-biased).
    assert out == "x" * exc._CONVENTIONS_TEXT_BUDGET


def test_truncate_for_conventions_exact_budget_unchanged():
    text = "y" * exc._CONVENTIONS_TEXT_BUDGET
    assert exc._truncate_for_conventions(text) == text


# ---------------------------------------------------------------------------
# extract_conventions (LLM stubbed)
# ---------------------------------------------------------------------------


def _smap(full_text: str = "Leyenda: Alta = crítico") -> types.SimpleNamespace:
    """Build a lightweight stand-in for StructureMap with the fields the
    conventions reader touches."""
    ns = types.SimpleNamespace(
        document_id="doc1",
        full_text=full_text,
        name="doc1.pdf",
        conventions=None,
    )
    return ns


@pytest.mark.asyncio
async def test_extract_conventions_empty_text_returns_empty_without_llm(monkeypatch):
    """No full_text -> short circuit, LLM never constructed or called."""
    smap = _smap(full_text="")
    # If the LLM were constructed, this would blow up (proving the short circuit).
    monkeypatch.setattr(exc, "_structured_llm", lambda schema: pytest.fail(
        "LLM should not be constructed when full_text is empty"))
    rules = await exc.extract_conventions(
        smap, project_name="P", project_description="")
    assert isinstance(rules, exc.DocumentRules)
    assert rules.priority_legend == []


@pytest.mark.asyncio
async def test_extract_conventions_whitespace_text_returns_empty():
    smap = _smap(full_text="   \n\t  ")
    rules = await exc.extract_conventions(
        smap, project_name="P", project_description="")
    assert rules.priority_legend == []


@pytest.mark.asyncio
async def test_extract_conventions_success_caches_on_smap(monkeypatch):
    canned = exc.DocumentRules(
        priority_legend=[exc.LegendEntry(label="Alta", source_span="Alta")],
        priority_field_label="Prioridad",
    )
    stub = AsyncMock()
    stub.ainvoke = AsyncMock(return_value=canned)
    monkeypatch.setattr(exc, "_structured_llm", lambda schema: stub)

    smap = _smap()
    rules = await exc.extract_conventions(
        smap, project_name="P", project_description="")

    assert rules is canned  # returned verbatim
    # Cached on the smap for later reads without re-running the stage.
    assert smap.conventions is canned
    stub.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_conventions_llm_failure_returns_empty_and_does_not_raise(
    monkeypatch,
):
    """Graceful degradation: LLM error -> empty rules, pipeline continues."""
    stub = AsyncMock()
    stub.ainvoke = AsyncMock(side_effect=RuntimeError("LLM timed out"))
    monkeypatch.setattr(exc, "_structured_llm", lambda schema: stub)

    smap = _smap()
    rules = await exc.extract_conventions(
        smap, project_name="P", project_description="")

    assert isinstance(rules, exc.DocumentRules)
    assert rules.priority_legend == []
    assert rules.priority_field_label is None
    # smap.conventions left untouched (not cached on failure).
    assert smap.conventions is None


# ---------------------------------------------------------------------------
# extract_chunk PRIORITY_FIELD_LABEL surfacing (LLM stubbed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_chunk_surfaces_priority_field_label_when_rules_given(
    monkeypatch,
):
    """When rules.priority_field_label is set, the user message to the LLM
    includes ``PRIORITY_FIELD_LABEL: <label>`` so the extractor knows what
    the labeled per-requirement field is."""
    captured: dict = {}

    class _CapturingRunnable:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return types.SimpleNamespace(items=[])

    monkeypatch.setattr(exc, "_structured_llm", lambda schema: _CapturingRunnable())

    chunk = types.SimpleNamespace(
        document_id="doc1",
        text="El sistema debe validar passwords.",
        page=1,
        section_path="Seguridad",
    )
    rules = exc.DocumentRules(priority_field_label="Prioridad del ítem")
    await exc.extract_chunk(
        chunk,
        project_name="P",
        project_description="",
        rules=rules,
    )

    user_text = captured["messages"][1][1]
    assert "PRIORITY_FIELD_LABEL: Prioridad del ítem" in user_text


@pytest.mark.asyncio
async def test_extract_chunk_no_priority_field_label_line_when_rules_none(
    monkeypatch,
):
    """Without rules (None), the user message must NOT include the
    PRIORITY_FIELD_LABEL line (day-1 verb-based path unchanged)."""
    captured: dict = {}

    class _CapturingRunnable:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return types.SimpleNamespace(items=[])

    monkeypatch.setattr(exc, "_structured_llm", lambda schema: _CapturingRunnable())

    chunk = types.SimpleNamespace(
        document_id="doc1",
        text="El sistema debe validar passwords.",
        page=1,
        section_path="Seguridad",
    )
    await exc.extract_chunk(
        chunk,
        project_name="P",
        project_description="",
        rules=None,
    )

    user_text = captured["messages"][1][1]
    assert "PRIORITY_FIELD_LABEL" not in user_text


@pytest.mark.asyncio
async def test_extract_chunk_no_priority_field_label_when_label_is_none(
    monkeypatch,
):
    """Rules given but priority_field_label is None -> no line surfaced."""
    captured: dict = {}

    class _CapturingRunnable:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return types.SimpleNamespace(items=[])

    monkeypatch.setattr(exc, "_structured_llm", lambda schema: _CapturingRunnable())

    chunk = types.SimpleNamespace(
        document_id="doc1",
        text="El sistema debe validar passwords.",
        page=1,
        section_path="Seguridad",
    )
    rules = exc.DocumentRules(priority_field_label=None)
    await exc.extract_chunk(
        chunk,
        project_name="P",
        project_description="",
        rules=rules,
    )

    user_text = captured["messages"][1][1]
    assert "PRIORITY_FIELD_LABEL" not in user_text


# ---------------------------------------------------------------------------
# Classification consumer: _format_conventions_block + PRIORITY_HINT threading.
# Pure data-shape tests; the LLM is stubbed so we can assert what lands in the
# user message.
# ---------------------------------------------------------------------------

from backend.agents.pipelines import classification as cls  # noqa: E402


def test_classify_format_conventions_block_empty_when_no_rules():
    assert cls._format_conventions_block(None) == ""


def test_classify_format_conventions_block_empty_when_rules_have_no_signals():
    rules = exc.DocumentRules()  # all defaults (empty legend + scope)
    assert cls._format_conventions_block(rules) == ""


def test_classify_format_conventions_block_includes_legend_labels():
    rules = exc.DocumentRules(
        priority_legend=[
            exc.LegendEntry(label="Alta", source_span="Alta"),
            exc.LegendEntry(label="Media", source_span="Media"),
        ],
    )
    block = cls._format_conventions_block(rules)
    assert block.startswith("DOCUMENT_CONVENTIONS:")
    assert "'Alta'" in block and "'Media'" in block
    assert "Priority legend" in block


def test_classify_format_conventions_block_includes_scope_markers():
    rules = exc.DocumentRules(scope_markers=["fuera de alcance", "fase 2"])
    block = cls._format_conventions_block(rules)
    assert block.startswith("DOCUMENT_CONVENTIONS:")
    assert "'fuera de alcance'" in block and "'fase 2'" in block
    assert "Scope markers" in block
    # No legend section when only scope markers are present.
    assert "Priority legend" not in block


def test_classify_format_conventions_block_includes_both_when_both_present():
    rules = exc.DocumentRules(
        priority_legend=[exc.LegendEntry(label="Baja", source_span="Baja")],
        scope_markers=["futuro"],
    )
    block = cls._format_conventions_block(rules)
    assert "Priority legend" in block
    assert "Scope markers" in block


@pytest.mark.asyncio
async def test_classify_item_threads_priority_hint_and_conventions(monkeypatch):
    """The single-item user message must include PRIORITY_HINT from the item
    and the DOCUMENT_CONVENTIONS block from the rules."""
    captured: dict = {}

    class _CapturingRunnable:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return cls.ClassificationDecision(
                item_id="r1",
                type=cls.ReqType.FUNCTIONAL,
                priority=cls.Priority.MUST,
                rationale="test",
            )

    monkeypatch.setattr(cls, "structured_llm", lambda schema: _CapturingRunnable())

    item = exc.RawRequirement(
        id="r1",
        statement="El sistema debe validar passwords.",
        source_span="El sistema debe validar passwords.",
        document_id="doc1",
        page=1,
        section="Seguridad",
        confidence=0.9,
        priority_hint="Alta",
    )
    rules = exc.DocumentRules(
        priority_legend=[exc.LegendEntry(label="Alta", source_span="Alta")],
        scope_markers=["fuera de alcance"],
    )
    await cls._classify_item(item, rules=rules)

    user_text = captured["messages"][1][1]
    assert "PRIORITY_HINT: Alta" in user_text
    assert "DOCUMENT_CONVENTIONS:" in user_text
    assert "'Alta'" in user_text
    assert "'fuera de alcance'" in user_text


@pytest.mark.asyncio
async def test_classify_item_no_conventions_block_when_rules_empty(monkeypatch):
    """Without rules signals, the user message must NOT include the
    DOCUMENT_CONVENTIONS block (historical verb-based path preserved)."""
    captured: dict = {}

    class _CapturingRunnable:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return cls.ClassificationDecision(
                item_id="r1",
                type=cls.ReqType.FUNCTIONAL,
                priority=cls.Priority.SHOULD,
                rationale="verb-based fallback",
            )

    monkeypatch.setattr(cls, "structured_llm", lambda schema: _CapturingRunnable())

    item = exc.RawRequirement(
        id="r1",
        statement="El sistema debería soportar temas.",
        source_span="El sistema debería soportar temas.",
        document_id="doc1",
        page=1,
        section="UI",
        confidence=0.8,
        priority_hint="",  # no hint
    )
    await cls._classify_item(item, rules=None)

    user_text = captured["messages"][1][1]
    assert "DOCUMENT_CONVENTIONS:" not in user_text
    # PRIORITY_HINT line still surfaces (empty value, no legend to resolve).
    assert "PRIORITY_HINT:" in user_text


@pytest.mark.asyncio
async def test_classify_batch_appends_conventions_block_once(monkeypatch):
    """Batch mode (M>=2): the DOCUMENT_CONVENTIONS block is appended exactly
    ONCE after the last item's block, while every item keeps its own
    PRIORITY_HINT line. The returned decisions carry the items' own ids
    (no omission -> no fallback)."""
    captured: dict = {}

    class _CapturingRunnable:
        async def ainvoke(self, messages, max_tokens=None):
            captured["messages"] = messages
            return cls.ClassificationBatch(
                decisions=[
                    cls.ClassificationDecision(
                        item_id="r1",
                        type=cls.ReqType.FUNCTIONAL,
                        priority=cls.Priority.MUST,
                        rationale="legend 'Alta' -> MUST",
                    ),
                    cls.ClassificationDecision(
                        item_id="r2",
                        type=cls.ReqType.FUNCTIONAL,
                        priority=cls.Priority.SHOULD,
                        rationale="legend 'Media' -> SHOULD",
                    ),
                ],
            )

    monkeypatch.setattr(cls, "structured_llm", lambda schema: _CapturingRunnable())

    items = [
        exc.RawRequirement(
            id="r1",
            statement="El sistema debe validar passwords.",
            source_span="El sistema debe validar passwords.",
            document_id="doc1",
            page=1,
            section="Seguridad",
            confidence=0.9,
            priority_hint="Alta",
        ),
        exc.RawRequirement(
            id="r2",
            statement="El sistema debería soportar temas.",
            source_span="El sistema debería soportar temas.",
            document_id="doc1",
            page=1,
            section="UI",
            confidence=0.8,
            priority_hint="Media",
        ),
    ]
    rules = exc.DocumentRules(
        priority_legend=[exc.LegendEntry(label="Alta", source_span="Alta")],
        scope_markers=["fuera de alcance"],
    )
    decisions, stats = await cls._classify_batch(items, rules=rules)

    user_text = captured["messages"][1][1]
    # Each item keeps its own PRIORITY_HINT line.
    assert "PRIORITY_HINT: Alta" in user_text
    assert "PRIORITY_HINT: Media" in user_text
    # Conventions block appears exactly once, after the last item's hint.
    assert user_text.count("DOCUMENT_CONVENTIONS:") == 1
    conv_idx = user_text.index("DOCUMENT_CONVENTIONS:")
    last_hint_idx = user_text.rfind("PRIORITY_HINT:")
    assert conv_idx > last_hint_idx
    # No item omitted -> no fallback routing.
    assert {d.item_id for d in decisions} == {"r1", "r2"}
    assert stats.fallback == 0
    assert stats.omitted == 0


# ---------------------------------------------------------------------------
# Critique consumer: _format_conventions_block surfaces signals so the critic
# does NOT flag the client's own priority legend / scope markers as noise.
# ---------------------------------------------------------------------------

from backend.agents.pipelines import critique as crt  # noqa: E402


def test_critique_format_conventions_block_includes_priority_field_label():
    """Critique surfaces priority_field_label (which classify does NOT) so the
    critic recognizes the labeled field as a known signal."""
    rules = exc.DocumentRules(priority_field_label="Prioridad del requerimiento")
    block = crt._format_conventions_block(rules)
    assert "DOCUMENT_CONVENTIONS" in block
    assert "Prioridad del requerimiento" in block


def test_critique_format_conventions_block_empty_when_no_rules():
    assert crt._format_conventions_block(None) == ""


def test_critique_format_conventions_block_includes_glossary():
    """Critique surfaces glossary terms so the critic does NOT flag them as
    undefined jargon."""
    rules = exc.DocumentRules(
        glossary={"SRS": "Software Requirements Specification"},
    )
    block = crt._format_conventions_block(rules)
    assert "SRS" in block
    assert "Software Requirements Specification" in block
