"""Recuperación de overflow de ventana en el relay del chat (sesión 17).

Un turno que consulta el SRS completo deja megabytes de tool_results en el
thread del checkpointer; la siguiente llamada al modelo recibe 400
«prompt is too long» y SIN intervención cada reintento recarga el mismo
estado inflado: la sesión queda clavada. El router detecta el overflow,
compacta el thread (misma lista de mensajes, contenidos acotados, pares
tool_call/ToolMessage intactos) y le avisa al frontend con `recovered`.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from backend.routers.chat import _compact_overflowed_thread, _is_context_overflow

# --- detector -----------------------------------------------------------------


def test_detects_overflow_wrapped_in_provider_layers():
    exc = RuntimeError(
        "Error code: 400 - litellm.ContextWindowExceededError: "
        "litellm.BadRequestError: AnthropicError - {\"type\":\"error\","
        "\"error\":{\"type\":\"invalid_request_error\",\"code\":\"1261\","
        "\"message\":\"[1261][prompt is too long]\"}}"
    )
    assert _is_context_overflow(exc) is True


def test_does_not_flag_other_errors():
    assert _is_context_overflow(RuntimeError("connection reset by peer")) is False
    assert _is_context_overflow(ValueError("429 rate limited")) is False


# --- compactación -------------------------------------------------------------


class _FakeCheckpoint(dict):
    """Contrato real: checkpoint['channel_values'] se accede por suscripción."""

    def __init__(self, channel_values):
        super().__init__(channel_values=channel_values)


class _FakeTuple:
    def __init__(self, checkpoint):
        self.checkpoint = checkpoint


class _FakeCheckpointer:
    def __init__(self, messages):
        self._messages = messages
        self.written = None

    async def aget_tuple(self, config):
        if self._messages is None:
            return None
        return _FakeTuple(_FakeCheckpoint({"messages": list(self._messages)}))


class _FakeAgent:
    """Lo que _compact_overflowed_thread necesita del grafo compilado."""

    def __init__(self, checkpointer):
        self.checkpointer = checkpointer

    async def aupdate_state(self, config, values, as_node=None):
        self.written = values
        return config


def _poisoned_history() -> list:
    big = AIMessage(content="x" * 3_000_000)
    big.tool_calls = [
        {"name": "get_latest_srs", "args": {}, "id": "call_1", "type": "tool_call"}
    ]
    return [
        HumanMessage(content="generá el srs"),
        AIMessage(
            content="voy a consultarlo",
            tool_calls=[
                {
                    "name": "get_latest_srs",
                    "args": {},
                    "id": "call_0",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="y" * 2_500_000, tool_call_id="call_0"),
        big,
        AIMessage(content="informe corto"),
    ]


@pytest.mark.asyncio
async def test_compact_thread_trims_contents_and_keeps_pairs():
    history = _poisoned_history()
    agent = _FakeAgent(_FakeCheckpointer(history))

    kept = await _compact_overflowed_thread(agent, "60")

    assert kept == len(history)
    assert agent.written is not None
    updates = agent.written["messages"]
    assert isinstance(updates[0], RemoveMessage)
    assert updates[0].id == REMOVE_ALL_MESSAGES
    compacted = updates[1:]
    # Misma cantidad de mensajes (sin huérfanos)...
    assert len(compacted) == len(history)
    # ...pares tool_call/ToolMessage intactos...
    assert compacted[1].tool_calls[0]["id"] == "call_0"
    assert compacted[2].tool_call_id == "call_0"
    # ...y contenidos acotados (head 20K + marcador + tail 2K, semántica
    # existente del SizeGuard: max_chars decide SI trunca, no el tamaño).
    assert len(compacted[2].content) < 25_000
    assert "limite de contexto" in compacted[2].content
    assert len(compacted[1].content) < 500  # no fue truncado


@pytest.mark.asyncio
async def test_compact_thread_without_checkpointer_returns_none():
    agent = _FakeAgent(None)
    assert await _compact_overflowed_thread(agent, "60") is None


@pytest.mark.asyncio
async def test_compact_thread_with_empty_state_returns_none():
    agent = _FakeAgent(_FakeCheckpointer(None))
    assert await _compact_overflowed_thread(agent, "60") is None
