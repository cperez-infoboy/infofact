"""Filtro del relay SSE contra llamadas internas de middleware.

El SummarizationMiddleware de LangChain hace una llamada interna al LLM para
generar el resumen de compactación (bloques "## SESSION INTENT / ## SUMMARY").
Esa llamada viaja con la clave `lc_internal_call` en la metadata del evento
`messages`; el relay NO debe emitir su texto como tokens al usuario (bug de la
sesión 48: el resumen interno se mostró y se concatenó con la respuesta real).
"""

from backend.routers.chat import INTERNAL_CALL_METADATA_KEY, _is_internal_call


def test_metadata_with_internal_call_key_is_internal():
    # El valor es un token de proceso imposible de falsear; para el filtro del
    # relay basta la presencia de la clave (el usuario no puede inyectar
    # metadata de config en las llamadas al modelo).
    assert _is_internal_call({INTERNAL_CALL_METADATA_KEY: "deadbeef"}) is True


def test_plain_metadata_is_not_internal():
    assert _is_internal_call({"langgraph_step": 3, "tags": []}) is False


def test_empty_or_none_metadata_is_not_internal():
    assert _is_internal_call({}) is False
    assert _is_internal_call(None) is False
    assert _is_internal_call("not-a-dict") is False
