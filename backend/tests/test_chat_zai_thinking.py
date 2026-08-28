"""Tests de la captura del thinking interno (reasoning_content de GLM).

langchain-openai 1.3.5 descarta los campos no estándar de proveedores
third-party (docstring de ChatOpenAI: "reasoning_content ... are not
extracted or preserved"). Z.ai manda la cadena de pensamiento por
``delta.reasoning_content`` en el stream; ``ChatZai`` la copia a
``additional_kwargs`` del chunk para que el relay la reenvíe como evento
SSE ``thinking`` sin tocar ``content``. ``_ThinkingRelay`` aplica el tope
por turno.

Nota: el path NO-streaming usa la función module-level
``_convert_dict_to_message`` (no overridable por subclase), así que con el
kill-switch ``LLM_AGENT_STREAMING=false`` el thinking simplemente no llega:
comportamiento previo, sin regresión.
"""
from __future__ import annotations

from langchain_core.messages import AIMessageChunk

from backend.agents.llm import ChatZai, build_llm
from backend.config import settings
from backend.routers.chat import _ThinkingRelay, _sse


def _chunk(delta: dict) -> dict:
    """Chunk de streaming OpenAI-compatible como llega a la conversión."""
    return {
        "id": "chatcmpl-test",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        "usage": None,
    }


def _client() -> ChatZai:
    return ChatZai(model="glm-5.2", api_key="test-key")


# --- ChatZai: captura de reasoning_content ---------------------------------


def test_captures_reasoning_content_from_delta():
    client = _client()
    gen = client._convert_chunk_to_generation_chunk(
        _chunk({"role": "assistant", "content": "", "reasoning_content": "paso 1"}),
        AIMessageChunk,
        None,
    )
    assert gen is not None
    msg = gen.message
    assert isinstance(msg, AIMessageChunk)
    # El thinking viaja en additional_kwargs, NO contamina content.
    assert msg.additional_kwargs.get("reasoning_content") == "paso 1"
    assert msg.content == ""


def test_without_reasoning_content_no_key():
    client = _client()
    gen = client._convert_chunk_to_generation_chunk(
        _chunk({"role": "assistant", "content": "hola"}),
        AIMessageChunk,
        None,
    )
    assert gen is not None
    assert "reasoning_content" not in gen.message.additional_kwargs
    assert gen.message.content == "hola"


def test_empty_choices_returns_default_without_key():
    client = _client()
    gen = client._convert_chunk_to_generation_chunk(
        {"choices": [], "usage": None},
        AIMessageChunk,
        None,
    )
    assert gen is not None
    assert "reasoning_content" not in gen.message.additional_kwargs


def test_captures_openrouter_reasoning_field():
    """OpenRouter normaliza el thinking a ``delta.reasoning`` (otro nombre).

    La captura también debe aceptarlo, guardándolo bajo la clave canónica
    ``reasoning_content`` para que el relay y el frontend no cambien.
    """
    client = _client()
    gen = client._convert_chunk_to_generation_chunk(
        _chunk({"role": "assistant", "content": "", "reasoning": "paso OR"}),
        AIMessageChunk,
        None,
    )
    assert gen is not None
    msg = gen.message
    assert isinstance(msg, AIMessageChunk)
    assert msg.additional_kwargs.get("reasoning_content") == "paso OR"
    assert msg.content == ""


def test_reasoning_content_tiene_prioridad_sobre_reasoning():
    """Si un proveedor manda ambos campos, gana la convención canónica."""
    client = _client()
    gen = client._convert_chunk_to_generation_chunk(
        _chunk({"reasoning_content": "canonico", "reasoning": "openrouter"}),
        AIMessageChunk,
        None,
    )
    assert gen is not None
    assert gen.message.additional_kwargs.get("reasoning_content") == "canonico"


def test_reasoning_no_string_se_descarta():
    """``reasoning`` estructurado (lista/dict de detalles) no es thinking de
    texto: se descarta sin romper (el bloque simplemente no llega)."""
    client = _client()
    gen = client._convert_chunk_to_generation_chunk(
        _chunk({"reasoning": [{"type": "text", "text": "x"}]}),
        AIMessageChunk,
        None,
    )
    assert gen is not None
    assert "reasoning_content" not in gen.message.additional_kwargs


def test_reasoning_vacio_no_crea_key():
    client = _client()
    gen = client._convert_chunk_to_generation_chunk(
        _chunk({"reasoning": ""}),
        AIMessageChunk,
        None,
    )
    assert gen is not None
    assert "reasoning_content" not in gen.message.additional_kwargs


def test_build_llm_uses_chat_zai(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    assert isinstance(build_llm(), ChatZai)


# --- _ThinkingRelay: tope por turno ----------------------------------------


def test_relay_passthrough_bajo_el_tope():
    relay = _ThinkingRelay(max_chars=100)
    assert relay.add("hola") == [_sse("thinking", {"delta": "hola"})]
    assert relay.add(" mundo") == [_sse("thinking", {"delta": " mundo"})]


def test_relay_trunca_con_marcador_y_cierra():
    relay = _ThinkingRelay(max_chars=10)
    frames = relay.add("1234567890ABCDEF")
    # 10 chars pasan + UN marcador de truncado.
    assert len(frames) == 2
    assert frames[0] == _sse("thinking", {"delta": "1234567890"})
    assert "truncado" in frames[1]
    # Cerrado: los deltas siguientes no emiten nada.
    assert relay.add("más texto") == []


def test_relay_marcador_al_alcanzar_el_tope_despues():
    relay = _ThinkingRelay(max_chars=5)
    assert relay.add("12345") == [_sse("thinking", {"delta": "12345"})]
    # Tope agotado: primer delta siguiente dispara el marcador (una vez).
    frames = relay.add("X")
    assert len(frames) == 1
    assert "truncado" in frames[0]
    assert relay.add("Y") == []


def test_relay_delta_vacio_no_emite():
    relay = _ThinkingRelay(max_chars=10)
    assert relay.add("") == []
