"""Tests de truncate_messages + SizeGuardMiddleware (guard del input al LLM).

Casos del plan:
  (a) mensaje de 1.1MB -> content capado (head+marcador+tail), id/tipo/
      tool_calls/tool_call_id/name intactos;
  (b) lista bajo umbral -> idéntica (mismos objetos, mismo orden);
  (c) presupuesto total: los últimos PROTECTED_TAIL_MESSAGES intactos y
      ``len(messages)`` igual (NUNCA elimina mensajes);
  (d) el middleware llama ``handler(request.override(messages=truncados))``.
"""
from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from backend.agents.size_guard import (
    PER_MESSAGE_HEAD_CHARS,
    PER_MESSAGE_TAIL_CHARS,
    PROTECTED_TAIL_MESSAGES,
    SizeGuardMiddleware,
    _MARKER_TEMPLATE,
    truncate_messages,
)

# Prefix del marcador (el conteo de omitidos se asserts por caso).
MARKER_PREFIX = "[contenido truncado por limite de contexto:"

MAX_MESSAGE = 24_000
TOTAL_INPUT = 240_000


def test_giant_assistant_message_capped_metadata_intact():
    tool_calls = [
        {"name": "ingest_documents", "args": {"subpath": "docs"}, "id": "call-1"}
    ]
    big = AIMessage(id="ai-1", content="x" * 1_100_000, tool_calls=tool_calls)

    out = truncate_messages(
        [big], max_message_chars=MAX_MESSAGE, total_input_chars=TOTAL_INPUT
    )

    assert len(out) == 1
    rep = out[0]
    # (a) metadata intacta: mismo id, mismo tipo, mismos tool_calls.
    assert isinstance(rep, AIMessage)
    assert rep.id == "ai-1"
    # AIMessage normaliza tool_calls al construir (agrega "type": "tool_call");
    # lo que se preserva es exactamente lo que tenía el original.
    assert rep.tool_calls == big.tool_calls
    # Content capado: head + marcador + tail.
    content = rep.content
    assert content.startswith("x" * PER_MESSAGE_HEAD_CHARS)
    assert content.endswith("x" * PER_MESSAGE_TAIL_CHARS)
    omitted = 1_100_000 - PER_MESSAGE_HEAD_CHARS - PER_MESSAGE_TAIL_CHARS
    assert _MARKER_TEMPLATE.format(omitted=omitted) in content
    assert len(content) < 30_000  # muy lejos del original de 1.1MB


def test_giant_tool_message_keeps_tool_call_id_and_name():
    big = ToolMessage(
        id="tm-1", content="y" * 1_100_000, tool_call_id="call-1", name="t"
    )

    out = truncate_messages(
        [big], max_message_chars=MAX_MESSAGE, total_input_chars=TOTAL_INPUT
    )

    rep = out[0]
    assert isinstance(rep, ToolMessage)
    assert rep.id == "tm-1"
    assert rep.tool_call_id == "call-1"
    assert rep.name == "t"
    assert MARKER_PREFIX in rep.content
    assert len(rep.content) < 30_000


def test_under_threshold_returns_same_objects_in_order():
    msgs = [
        HumanMessage("hola", id="h-1"),
        AIMessage("chau", id="a-1"),
        ToolMessage("ok", tool_call_id="call-1", name="t", id="t-1"),
    ]

    out = truncate_messages(
        msgs, max_message_chars=MAX_MESSAGE, total_input_chars=TOTAL_INPUT
    )

    # (b) idéntica: mismos objetos (nada que copiar), mismo orden.
    assert out == msgs
    assert out[0] is msgs[0]
    assert out[1] is msgs[1]
    assert out[2] is msgs[2]


def test_total_budget_last_six_intact_and_no_message_dropped():
    # 20 mensajes de 23_000 chars (< per-message cap) = 460_000 > 240_000:
    # el paso 2 recorta de los más viejos hacia adelante.
    size, count = 23_000, 20
    msgs = [
        HumanMessage(f"{i:02d}-" + "x" * (size - 3), id=f"h-{i}")
        for i in range(count)
    ]

    out = truncate_messages(
        msgs, max_message_chars=MAX_MESSAGE, total_input_chars=TOTAL_INPUT
    )

    # (c) NUNCA elimina mensajes.
    assert len(out) == count
    # Los últimos PROTECTED_TAIL_MESSAGES quedan intactos.
    for original, capped in zip(
        msgs[-PROTECTED_TAIL_MESSAGES:], out[-PROTECTED_TAIL_MESSAGES:]
    ):
        assert capped is original
    # Los primeros 10 caben en el presupuesto (10 * 23_000 = 230_000).
    for original, capped in zip(msgs[:10], out[:10]):
        assert capped is original
    # El 11º rebalsa: head parcial (10_000) + marcador, mismo id.
    assert out[10].content == (
        msgs[10].content[:10_000]
        + "\n"
        + _MARKER_TEMPLATE.format(omitted=size - 10_000)
    )
    assert out[10].id == "h-10"
    # 12º-14º: presupuesto agotado -> solo marcador (preservan id).
    for capped in out[11 : count - PROTECTED_TAIL_MESSAGES]:
        assert capped.content == _MARKER_TEMPLATE.format(omitted=size)
        assert capped.id.startswith("h-")


class _FakeRequest:
    """Duck-typed ModelRequest: registra los overrides y devuelve un marcador."""

    def __init__(self, msgs):
        self.messages = msgs
        self.overrides: list[dict] = []

    def override(self, **kw):
        self.overrides.append(kw)
        return ("overridden", kw)


def test_wrap_model_call_passes_overridden_messages_to_handler():
    seen: dict = {}

    def handler(req):
        seen["req"] = req
        return "ok"

    fake = _FakeRequest([AIMessage("x" * 100_000, id="a-1")])
    result = SizeGuardMiddleware().wrap_model_call(fake, handler)

    # (d) el handler recibió exactamente lo que devolvió request.override.
    assert result == "ok"
    marker, kw = seen["req"]
    assert marker == "overridden"
    assert list(kw) == ["messages"]
    assert len(kw["messages"][0].content) < 100_000
    assert kw["messages"][0].id == "a-1"
    assert fake.overrides == [{"messages": kw["messages"]}]


def test_awrap_model_call_same_override():
    seen: dict = {}

    async def handler(req):
        seen["req"] = req
        return "ok-async"

    fake = _FakeRequest([HumanMessage("hola")])
    result = asyncio.run(SizeGuardMiddleware().awrap_model_call(fake, handler))

    assert result == "ok-async"
    _, kw = seen["req"]
    assert list(kw) == ["messages"]
    # Bajo umbral: mismas instancias, solo la lista es nueva.
    assert kw["messages"] == fake.messages
