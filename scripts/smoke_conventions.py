"""Smoke test for the CONVENTIONS stage (plan §13.B-D).

Exercises the end-to-end data flow without a real LLM key:

1. ``extract_conventions`` happy path: stubbed StructuredRunnable returns a
   ``DocumentRules`` with a priority legend; the function caches it on the
   ``smap.conventions`` slot.
2. ``extract_conventions`` graceful degradation: when the stub raises, the
   function returns an empty ``DocumentRules`` and never re-raises (the
   pipeline must not abort).
3. ``merge_conventions`` deterministic merge: legend/scope/glossary
   concatenate, and the first non-None ``priority_field_label`` wins.
4. ``extract_chunk`` surfaces ``PRIORITY_FIELD_LABEL:`` only when the rules
   carry a non-empty label.
5. ``_classify_item`` user message carries both ``PRIORITY_HINT:`` (from the
   item) and the ``DOCUMENT_CONVENTIONS:`` block (from the rules).

Run: ``.venv/bin/python scripts/smoke_conventions.py``
"""
import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agents.pipelines import classification as clf
from backend.agents.pipelines import extraction as exc
from backend.models.requirement import Priority, ReqType


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------


class _StubRunnable:
    """Minimal stand-in for the StructuredRunnable returned by
    ``_structured_llm`` / ``structured_llm``. Captures the messages it was
    called with and returns a canned response."""

    def __init__(self, response, capture=None, raises=None):
        self._response = response
        self._capture = capture
        self._raises = raises

    async def ainvoke(self, messages):
        if self._capture is not None:
            self._capture["messages"] = messages
        if self._raises is not None:
            raise self._raises
        return self._response


def _smap(full_text: str = "Leyenda: Alta = crítico, Media = normal."):
    return types.SimpleNamespace(
        document_id="doc1",
        full_text=full_text,
        name="doc1.pdf",
        conventions=None,
    )


# ---------------------------------------------------------------------------
# 1. extract_conventions happy path caches on smap
# ---------------------------------------------------------------------------


def smoke_extract_conventions_happy(monkey_ctx):
    canned = exc.DocumentRules(
        priority_legend=[
            exc.LegendEntry(label="Alta", source_span="Alta = crítico"),
            exc.LegendEntry(label="Media", source_span="Media = normal"),
        ],
        priority_field_label="Prioridad",
    )
    stub = _StubRunnable(canned)
    monkey_ctx.setattr(exc, "_structured_llm", lambda schema: stub)

    smap = _smap()
    rules = asyncio.run(exc.extract_conventions(
        smap, project_name="P", project_description=""))

    assert rules is canned, "happy path must return the LLM result verbatim"
    assert smap.conventions is canned, "result must be cached on smap.conventions"
    print("[1] extract_conventions happy path OK:",
          len(rules.priority_legend), "legend entries,",
          "field=", rules.priority_field_label)


# ---------------------------------------------------------------------------
# 2. extract_conventions failure degrades to empty DocumentRules
# ---------------------------------------------------------------------------


def smoke_extract_conventions_failure(monkey_ctx):
    stub = _StubRunnable(None, raises=RuntimeError("LLM boom"))
    monkey_ctx.setattr(exc, "_structured_llm", lambda schema: stub)

    smap = _smap()
    rules = asyncio.run(exc.extract_conventions(
        smap, project_name="P", project_description=""))

    assert isinstance(rules, exc.DocumentRules)
    assert rules.priority_legend == []
    assert rules.priority_field_label is None
    assert smap.conventions is None, "failure must not cache"
    print("[2] extract_conventions graceful degradation OK")


# ---------------------------------------------------------------------------
# 3. merge_conventions deterministic merge
# ---------------------------------------------------------------------------


def smoke_merge_conventions():
    a = exc.DocumentRules(
        priority_legend=[exc.LegendEntry(label="Alta", source_span="Alta")],
        priority_field_label=None,
        scope_markers=["fase 2"],
        glossary={"API": "Application Programming Interface"},
    )
    b = exc.DocumentRules(
        priority_legend=[exc.LegendEntry(label="Baja", source_span="Baja")],
        priority_field_label="Prioridad",
        scope_markers=["futuro"],
        glossary={"UI": "User Interface"},
    )
    merged = exc.merge_conventions([a, b])

    assert [e.label for e in merged.priority_legend] == ["Alta", "Baja"]
    assert merged.priority_field_label == "Prioridad", \
        "first non-None priority_field_label wins"
    assert merged.scope_markers == ["fase 2", "futuro"]
    assert merged.glossary == {
        "API": "Application Programming Interface",
        "UI": "User Interface",
    }
    print("[3] merge_conventions deterministic merge OK:",
          len(merged.priority_legend), "legend,",
          len(merged.scope_markers), "scope,",
          len(merged.glossary), "glossary terms")


# ---------------------------------------------------------------------------
# 4. extract_chunk surfaces PRIORITY_FIELD_LABEL
# ---------------------------------------------------------------------------


def smoke_extract_chunk_label(monkey_ctx):
    captured: dict = {}
    stub = _StubRunnable(types.SimpleNamespace(items=[]), capture=captured)
    monkey_ctx.setattr(exc, "_structured_llm", lambda schema: stub)

    chunk = types.SimpleNamespace(
        document_id="doc1",
        text="El sistema debe registrar auditoría.",
        page=1,
        section_path="Auditoría",
    )
    rules = exc.DocumentRules(priority_field_label="Prioridad del requerimiento")
    asyncio.run(exc.extract_chunk(
        chunk, project_name="P", project_description="", rules=rules))

    user_text = captured["messages"][1][1]
    assert "PRIORITY_FIELD_LABEL: Prioridad del requerimiento" in user_text, \
        "extract_chunk must surface the labeled field name"
    print("[4] extract_chunk PRIORITY_FIELD_LABEL surfacing OK")


# ---------------------------------------------------------------------------
# 5. _classify_item user message threads PRIORITY_HINT + DOCUMENT_CONVENTIONS
# ---------------------------------------------------------------------------


def smoke_classify_item_threading(monkey_ctx):
    captured: dict = {}
    decision = clf.ClassificationDecision(
        item_id="r1",
        type=ReqType.FUNCTIONAL,
        priority=Priority.MUST,
        rationale="Alta + legend -> MUST",
        decomposition_needed=False,
    )
    stub = _StubRunnable(decision, capture=captured)
    monkey_ctx.setattr(clf, "structured_llm", lambda schema: stub)

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
    asyncio.run(clf._classify_item(item, rules=rules))

    user_text = captured["messages"][1][1]
    assert "PRIORITY_HINT: Alta" in user_text, \
        "user message must surface the item priority_hint"
    assert "DOCUMENT_CONVENTIONS:" in user_text, \
        "user message must include the conventions block"
    assert "'Alta'" in user_text and "'fuera de alcance'" in user_text, \
        "legend labels + scope markers must be in the block"
    print("[5] _classify_item PRIORITY_HINT + DOCUMENT_CONVENTIONS threading OK")


# ---------------------------------------------------------------------------
# Minimal monkeypatch shim (we don't pull pytest in the smoke path)
# ---------------------------------------------------------------------------


class _MonkeyCtx:
    """Bare-bones setattr/restore context (no pytest dependency)."""

    def __init__(self):
        self._original = []

    def setattr(self, obj, name, value):
        self._original.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def restore(self):
        for obj, name, value in reversed(self._original):
            setattr(obj, name, value)
        self._original.clear()


def main():
    failures = 0
    # Each smoke gets its own monkeypatch ctx so stubs don't leak.
    for fn in (
        smoke_extract_conventions_happy,
        smoke_extract_conventions_failure,
        smoke_extract_chunk_label,
        smoke_classify_item_threading,
    ):
        ctx = _MonkeyCtx()
        try:
            fn(ctx)
        except Exception as exc_:  # noqa: BLE001
            failures += 1
            print(f"[FAIL] {fn.__name__}: {exc_}")
        finally:
            ctx.restore()

    # Pure-data smoke (no stubs needed).
    try:
        smoke_merge_conventions()
    except Exception as exc_:  # noqa: BLE001
        failures += 1
        print(f"[FAIL] smoke_merge_conventions: {exc_}")

    if failures:
        print(f"\n{failures} smoke(s) failed.")
        sys.exit(1)
    print("\nConventions smoke OK.")


if __name__ == "__main__":
    main()
