"""StructuredRunnable inyecta el tag ``nostream`` en el config del LLM interno.

Incidente de la sesión 44: una llamada estructurada dentro de una tool hereda
el árbol de callbacks del grafo y LangGraph emitía su AIMessage COMPLETO al
stream ``messages``. Con TAG_NOSTREAM en los tags, la llamada no se registra
en el stream: el JSON crudo nunca llega al relay ni al navegador.

Stub pattern: registramos el config con el que el LLM interno es invocado
(mismo approach que test_stage_tools_call_internals).
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from backend.agents.llm import (
    StructuredOutputTruncatedError,
    StructuredRunnable,
)
from langgraph.constants import TAG_NOSTREAM


class _Out(BaseModel):
    answer: str


class _Resp:
    """Duck-typed respuesta del LLM: solo se lee ``.content``."""

    def __init__(self, content: str):
        self.content = content


class _StubLLM:
    """Registra el config con el que fue invocado; responde JSON válido."""

    def __init__(self, content: str = '{"answer": "ok"}'):
        self._content = content
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, messages, config=None, **kwargs):
        self.calls.append({"config": config, "kwargs": kwargs})
        return _Resp(self._content)

    def invoke(self, messages, config=None, **kwargs):
        self.calls.append({"config": config, "kwargs": kwargs})
        return _Resp(self._content)


def _run(sr, *args, **kwargs):
    return asyncio.run(sr.ainvoke(*args, **kwargs))


def test_ainvoke_adds_nostream_tag():
    llm = _StubLLM()
    sr = StructuredRunnable(llm, _Out)

    out = _run(sr, [("user", "hi")])

    # El parseo sigue funcionando con el stub.
    assert isinstance(out, _Out)
    assert out.answer == "ok"
    # Y el LLM interno fue invocado con el tag nostream.
    tags = llm.calls[0]["config"]["tags"]
    assert TAG_NOSTREAM in tags
    assert tags == [TAG_NOSTREAM]


def test_ainvoke_preserves_caller_tags_and_does_not_mutate_config():
    llm = _StubLLM()
    sr = StructuredRunnable(llm, _Out)
    config = {"tags": ["custom-tag"], "metadata": {"k": "v"}}

    _run(sr, [("user", "hi")], config=config)

    seen = llm.calls[0]["config"]
    # Los tags del caller se conservan y nostream se AGREGA al final.
    assert seen["tags"] == ["custom-tag", TAG_NOSTREAM]
    # El resto del config pasa intacto.
    assert seen["metadata"] == {"k": "v"}
    # El dict del caller NO se muta.
    assert config["tags"] == ["custom-tag"]


def test_ainvoke_with_none_config_creates_tags():
    llm = _StubLLM()
    sr = StructuredRunnable(llm, _Out)

    _run(sr, [("user", "hi")], config=None)

    assert llm.calls[0]["config"]["tags"] == [TAG_NOSTREAM]


def test_invoke_sync_also_nostream():
    llm = _StubLLM()
    sr = StructuredRunnable(llm, _Out)

    out = sr.invoke([("user", "hi")], config={"tags": ["t"]})

    assert out.answer == "ok"
    assert llm.calls[0]["config"]["tags"] == ["t", TAG_NOSTREAM]


# ---------------------------------------------------------------------------
# Detección de truncado (finish_reason=length) -> StructuredOutputTruncatedError
# ---------------------------------------------------------------------------


class _MetaResp:
    """Respuesta con metadata del proveedor (finish_reason)."""

    def __init__(self, content: str, finish_reason: str = "stop"):
        self.content = content
        self.response_metadata = {"finish_reason": finish_reason}


class _MetaStubLLM(_StubLLM):
    def __init__(self, content: str, finish_reason: str = "stop"):
        super().__init__()
        self._resp = _MetaResp(content, finish_reason)

    async def ainvoke(self, messages, config=None, **kwargs):
        self.calls.append({"config": config, "kwargs": kwargs})
        return self._resp

    def invoke(self, messages, config=None, **kwargs):
        self.calls.append({"config": config, "kwargs": kwargs})
        return self._resp


def test_truncated_json_raises_typed_error():
    """JSON roto + finish_reason=length -> error tipado de truncado."""
    llm = _MetaStubLLM('{"answer": "ok"', finish_reason="length")
    sr = StructuredRunnable(llm, _Out)

    with pytest.raises(StructuredOutputTruncatedError):
        asyncio.run(sr.ainvoke([("user", "hi")]))


def test_truncated_flag_with_valid_json_still_parses():
    """finish_reason=length con JSON completo y válido: se parsea igual.

    El flag solo cambia el diagnóstico cuando el JSON NO parsea; un proveedor
    que reporte length de más no rompe caminos que hoy funcionan.
    """
    llm = _MetaStubLLM('{"answer": "ok"}', finish_reason="length")
    sr = StructuredRunnable(llm, _Out)

    out = asyncio.run(sr.ainvoke([("user", "hi")]))
    assert out.answer == "ok"


def test_broken_json_without_truncation_raises_generic():
    """JSON roto con finish_reason=stop: error genérico, NO el tipado."""
    llm = _MetaStubLLM("no es json", finish_reason="stop")
    sr = StructuredRunnable(llm, _Out)

    with pytest.raises(Exception) as excinfo:
        asyncio.run(sr.ainvoke([("user", "hi")]))
    assert not isinstance(excinfo.value, StructuredOutputTruncatedError)
