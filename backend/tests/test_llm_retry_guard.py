"""Tests de EmptyResponseRetryMiddleware (respuestas vacías del LLM).

Casos del plan (incidente del lote B4, sesión 8):
  (a) respuesta vacía (HTTP 200, content='', sin tool_calls) -> reintento con
      thinking desactivado en extra_body;
  (b) respuesta útil -> una sola llamada, sin overrides;
  (c) reintentos agotados -> AIMessage sintético que explica el corte;
  (d) sync y async con el mismo comportamiento;
  (e) is_empty_response: thinking invisible no cuenta como contenido.
"""
from __future__ import annotations

import asyncio
import dataclasses

from langchain.agents.middleware import ModelResponse
from langchain_core.messages import AIMessage, ToolMessage

from backend.agents.llm_retry_guard import (
    _EMPTY_RETRIES_ENV,
    EmptyResponseRetryMiddleware,
    _read_max_retries,
    is_empty_response,
)

THINKING_BODY = {"thinking": {"type": "disabled"}}


def _empty_ai(**kw) -> AIMessage:
    return AIMessage(content="", **kw)


def _resp(*messages) -> ModelResponse:
    return ModelResponse(result=list(messages))


class _FakeModel:
    """Registra los bind(**kwargs) y devuelve un marcador distinto."""

    def __init__(self, name: str = "base"):
        self.name = name
        self.bound: list[dict] = []

    def bind(self, **kwargs):
        self.bound.append(kwargs)
        return _FakeModel(f"{self.name}#bound{len(self.bound)}")


class _FakeRequest:
    """Duck-typed ModelRequest: registra los overrides."""

    def __init__(self, model: _FakeModel | None = None):
        self.model = model or _FakeModel()
        self.overrides: list[dict] = []

    def override(self, **kw):
        self.overrides.append(kw)
        clone = _FakeRequest(kw.get("model", self.model))
        clone.overrides = self.overrides
        return clone


class _Handler:
    """Devuelve respuestas en cola; registra los requests recibidos."""

    def __init__(self, responses: list[ModelResponse]):
        self.responses = list(responses)
        self.requests: list = []

    def __call__(self, request):
        self.requests.append(request)
        return self.responses.pop(0)

    def as_async(self):
        inner = self

        async def handler(request):
            return inner(request)

        return handler


# ---------------------------------------------------------------------------
# is_empty_response
# ---------------------------------------------------------------------------


def test_empty_response_variants():
    assert is_empty_response(_empty_ai())
    assert is_empty_response(AIMessage(content="   \n  "))
    # Bloques de contenido sin texto (p.ej. solo thinking) no cuentan.
    assert is_empty_response(AIMessage(content=[{"type": "thinking", "thinking": "x"}]))


def test_non_empty_response_variants():
    assert not is_empty_response(AIMessage(content="hola"))
    # Un tool_call es acción útil aunque el texto venga vacío.
    assert not is_empty_response(
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1"}])
    )


def test_last_ai_message_with_trailing_tool_message():
    # El resultado de structured output deja ToolMessage después del AIMessage.
    from backend.agents.llm_retry_guard import _last_ai_message

    tool = ToolMessage("ok", tool_call_id="c1", name="t")
    resp = _resp(_empty_ai(), tool)
    assert _last_ai_message(resp) is not None
    assert isinstance(_last_ai_message(resp), AIMessage)


# ---------------------------------------------------------------------------
# wrap_model_call / awrap_model_call
# ---------------------------------------------------------------------------


def test_empty_response_retries_with_thinking_disabled():
    good = _resp(AIMessage(content="Lote procesado"))
    handler = _Handler([_resp(_empty_ai(finish_reason=None)), good])
    request = _FakeRequest()

    result = EmptyResponseRetryMiddleware(max_retries=2).wrap_model_call(
        request, handler
    )

    assert result is good
    assert len(handler.requests) == 2
    # El reintento pasó por request.override(model=...) y el modelo quedó
    # atado con el body que apaga el thinking.
    assert list(request.overrides[0]) == ["model"]
    assert request.overrides[0]["model"] is handler.requests[1].model
    # El bind quedó registrado en el modelo base con el body que apaga el
    # thinking.
    assert request.model.bound == [{"extra_body": THINKING_BODY}]


def test_useful_response_means_single_call():
    good = _resp(AIMessage(content="todo bien"))
    handler = _Handler([good])
    request = _FakeRequest()

    result = EmptyResponseRetryMiddleware(max_retries=3).wrap_model_call(
        request, handler
    )

    assert result is good
    assert len(handler.requests) == 1
    assert request.overrides == []
    assert request.model.bound == []


def test_retries_exhausted_returns_synthetic_message():
    handler = _Handler([_resp(_empty_ai()) for _ in range(3)])  # 1 + 2 reintentos
    request = _FakeRequest()

    result = EmptyResponseRetryMiddleware(max_retries=2).wrap_model_call(
        request, handler
    )

    assert len(handler.requests) == 3
    assert isinstance(result, ModelResponse)
    assert len(result.result) == 1
    synthetic = result.result[0]
    assert isinstance(synthetic, AIMessage)
    assert not synthetic.tool_calls
    assert "3 intentos" in synthetic.content
    assert synthetic.response_metadata["empty_response_retries"] == 3


def test_synthetic_replaces_only_result_not_structured_response():
    empty = _resp(_empty_ai())
    empty.structured_response = {"ok": True}
    again = dataclasses.replace(empty, result=[_empty_ai()])
    handler = _Handler([empty, again, again])
    request = _FakeRequest()

    result = EmptyResponseRetryMiddleware(max_retries=2).wrap_model_call(
        request, handler
    )

    # dataclasses.replace preserva los demás campos de la respuesta.
    assert result.structured_response == {"ok": True}
    assert isinstance(result.result[0], AIMessage)


def test_async_retry_and_synthetic_match_sync():
    # (d) mismo comportamiento en la variante async: reintento + sintético.
    handler = _Handler([_resp(_empty_ai()), _resp(AIMessage(content="recuperado"))])
    request = _FakeRequest()

    result = asyncio.run(
        EmptyResponseRetryMiddleware(max_retries=1).awrap_model_call(
            request, handler.as_async()
        )
    )
    assert len(handler.requests) == 2
    assert request.model.bound == [{"extra_body": THINKING_BODY}]
    assert result.result[0].content == "recuperado"

    handler = _Handler([_resp(_empty_ai()), _resp(_empty_ai())])
    request = _FakeRequest()
    result = asyncio.run(
        EmptyResponseRetryMiddleware(max_retries=1).awrap_model_call(
            request, handler.as_async()
        )
    )
    assert len(handler.requests) == 2
    assert "2 intentos" in result.result[0].content


def test_reasoning_metadata_does_not_block_retry():
    # El caso B4: finish_reason='length' con usage cargado; el middleware no
    # mira esos campos para decidir, solo texto útil y tool_calls.
    message = _empty_ai()
    message.response_metadata = {"finish_reason": "length"}
    message.usage_metadata = {"input_tokens": 33_835, "output_tokens": 32_768}
    handler = _Handler([_resp(message), _resp(AIMessage(content="ok"))])
    result = EmptyResponseRetryMiddleware(max_retries=1).wrap_model_call(
        _FakeRequest(), handler
    )
    assert result.result[0].content == "ok"


# ---------------------------------------------------------------------------
# Config por entorno
# ---------------------------------------------------------------------------


def test_read_max_retries_from_env(monkeypatch):
    monkeypatch.delenv(_EMPTY_RETRIES_ENV, raising=False)
    assert _read_max_retries() == 2  # default
    monkeypatch.setenv(_EMPTY_RETRIES_ENV, "5")
    assert _read_max_retries() == 5
    monkeypatch.setenv(_EMPTY_RETRIES_ENV, "basura")
    assert _read_max_retries() == 2  # inválido cae al default


def test_constructor_env_default(monkeypatch):
    monkeypatch.setenv(_EMPTY_RETRIES_ENV, "4")
    assert EmptyResponseRetryMiddleware().max_retries == 4
    assert EmptyResponseRetryMiddleware(max_retries=0).max_retries == 0
