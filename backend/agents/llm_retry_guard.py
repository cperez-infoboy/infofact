"""Reintento ante respuestas vacías del LLM (incidente del lote B4, sesión 8).

El modelo puede devolver un HTTP 200 válido con ``content=''`` y ningún
tool_call: consumió TODO el presupuesto de salida en razonamiento interno
(thinking) y no le quedó presupuesto ni para texto ni para herramientas. El
grafo interpreta "sin tool_calls" como fin de turno y el subagente muere en
silencio; el SDK no reintenta porque no hubo error de transporte.

Este middleware envuelve cada llamada al modelo: si la respuesta final es un
AIMessage sin texto útil y sin tool_calls, reintenta con el thinking
desactivado — la causa medida fue un presupuesto de salida agotado por el
razonamiento, así que reintentar idéntico reproduciría el bucle. Agotados los
reintentos (``INFOFACT_LLM_EMPTY_RETRIES``, default 2), sustituye la respuesta
vacía por un AIMessage sintético que lo explica: el turno termina con un
mensaje visible en vez de un texto en blanco.

Mismo patrón y cableado que SizeGuardMiddleware: deepagents NO propaga
middleware a los subagentes, cada spec necesita el suyo. Transitorio: solo
cambia lo que el modelo ve en esa llamada; el estado queda intacto.
"""
from __future__ import annotations

import dataclasses
import logging
import os

from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain_core.messages import AIMessage

from backend.agents.llm import _content_to_text

logger = logging.getLogger(__name__)

# Body que apaga el razonamiento interno del proveedor. Viaja SIEMPRE en el
# reintento, sin importar el endpoint: el caso B4 midió finish_reason='length'
# con 32.768 tokens de salida y contenido vacío. Si un proveedor rechaza el
# campo, el reintento falla con un error visible (no en silencio).
_THINKING_DISABLED_BODY = {"thinking": {"type": "disabled"}}

_DEFAULT_EMPTY_RETRIES = 2
_EMPTY_RETRIES_ENV = "INFOFACT_LLM_EMPTY_RETRIES"

# Texto del mensaje sintético cuando se agotan los reintentos. Termina el
# turno en el hilo (el usuario lo ve en el chat) sin fingir una respuesta.
_SYNTHETIC_TEMPLATE = (
    "(La llamada al modelo devolvió una respuesta vacía en los {total} intentos "
    "realizados y el turno se canceló. Vuelve a enviar el mensaje para reintentar; "
    "si el problema persiste, revisa la configuración de LLM_MAX_TOKENS o el "
    "proveedor del modelo.)"
)


def _read_max_retries() -> int:
    raw = os.environ.get(_EMPTY_RETRIES_ENV, "").strip()
    if not raw:
        return _DEFAULT_EMPTY_RETRIES
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_EMPTY_RETRIES


def is_empty_response(message: AIMessage) -> bool:
    """True si la respuesta no aporta nada al grafo.

    Sin texto útil y sin tool_calls. El razonamiento interno invisible
    (thinking / reasoning_content) no cuenta: el turno muere igual.
    """
    if getattr(message, "tool_calls", None):
        return False
    return not _content_to_text(message.content).strip()


def _last_ai_message(response) -> AIMessage | None:
    result = getattr(response, "result", None) or []
    for message in reversed(result):
        if isinstance(message, AIMessage):
            return message
    return None


def _describe(message: AIMessage) -> str:
    meta = getattr(message, "response_metadata", None) or {}
    usage = getattr(message, "usage_metadata", None) or {}
    return (
        f"finish_reason={meta.get('finish_reason')!r} "
        f"in={usage.get('input_tokens')} out={usage.get('output_tokens')}"
    )


def _synthetic_response(response, total_attempts: int):
    """Copia la respuesta con un AIMessage sintético en lugar del vacío."""
    synthetic = AIMessage(
        content=_SYNTHETIC_TEMPLATE.format(total=total_attempts),
        response_metadata={"empty_response_retries": total_attempts},
    )
    return dataclasses.replace(response, result=[synthetic])


class EmptyResponseRetryMiddleware(AgentMiddleware):
    """wrap_model_call que reintenta respuestas vacías con thinking apagado.

    Se conecta al orquestador (build_agent) y a los specs de los subagentes
    (requirements-capture, srs, analysis). ``max_retries`` es el número de
    REINTENTOS: la llamada total es 1 + max_retries.
    """

    def __init__(self, max_retries: int | None = None):
        self.max_retries = (
            _read_max_retries() if max_retries is None else max_retries
        )

    def wrap_model_call(self, request, handler):
        response = handler(request)
        for attempt in range(1, self.max_retries + 1):
            message = _last_ai_message(response)
            if message is None or not is_empty_response(message):
                return response
            logger.warning(
                "Respuesta vacía del LLM (intento %s/%s): %s; reintento con "
                "thinking desactivado",
                attempt,
                self.max_retries,
                _describe(message),
            )
            response = handler(
                request.override(
                    model=request.model.bind(extra_body=_THINKING_DISABLED_BODY)
                )
            )
        message = _last_ai_message(response)
        if message is not None and is_empty_response(message):
            logger.error(
                "Respuesta vacía del LLM tras %s reintentos: %s",
                self.max_retries,
                _describe(message),
            )
            return _synthetic_response(response, self.max_retries + 1)
        return response

    async def awrap_model_call(self, request, handler):
        response = await handler(request)
        for attempt in range(1, self.max_retries + 1):
            message = _last_ai_message(response)
            if message is None or not is_empty_response(message):
                return response
            logger.warning(
                "Respuesta vacía del LLM (intento %s/%s): %s; reintento con "
                "thinking desactivado",
                attempt,
                self.max_retries,
                _describe(message),
            )
            response = await handler(
                request.override(
                    model=request.model.bind(extra_body=_THINKING_DISABLED_BODY)
                )
            )
        message = _last_ai_message(response)
        if message is not None and is_empty_response(message):
            logger.error(
                "Respuesta vacía del LLM tras %s reintentos: %s",
                self.max_retries,
                _describe(message),
            )
            return _synthetic_response(response, self.max_retries + 1)
        return response
