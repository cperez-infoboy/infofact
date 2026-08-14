"""Guarda de tamaño del input que ve el modelo (incidente de la sesión 44).

Un mensaje assistant de 1.1 MB persistido en el thread del checkpointer se
re-enviaba como input en cada turno: el proveedor stalleaba, el timeout mataba
la llamada y los reintentos hacían la sesión inutilizable. Este middleware
trunca la lista de mensajes ANTES de cada llamada al modelo.

Es TRANSITORIO: solo cambia lo que el modelo VE en esa llamada; el estado del
checkpointer queda intacto. Ese es el punto — repara threads ya envenenados
sin tocar datos (la escritura del thread solo la hace el agente mismo).
"""
from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage

from backend.config import settings

# Truncado por mensaje: cabecera + marcador + cola.
PER_MESSAGE_HEAD_CHARS = 20_000
PER_MESSAGE_TAIL_CHARS = 2_000
# Presupuesto total: los últimos N mensajes del prompt quedan intactos.
PROTECTED_TAIL_MESSAGES = 6

_MARKER_TEMPLATE = (
    "[contenido truncado por limite de contexto: {omitted} chars omitidos]"
)


def _text_size(message: BaseMessage) -> int:
    """Tamaño del contenido de texto del mensaje (los bloques no-texto no cuentan)."""
    content = message.content
    if isinstance(content, str):
        return len(content)
    return 0


def _replace_content(message: BaseMessage, new_content: str) -> BaseMessage:
    """Copia el mensaje con nuevo contenido, preservando id, tipo, tool_calls,
    tool_call_id y name (model_copy NO reescribe los demás campos)."""
    return message.model_copy(update={"content": new_content})


def _cap_message(message: BaseMessage, *, max_chars: int) -> BaseMessage:
    """Tope por mensaje: head + marcador + tail cuando supera ``max_chars``."""
    content = message.content
    if not isinstance(content, str) or len(content) <= max_chars:
        return message
    omitted = len(content) - PER_MESSAGE_HEAD_CHARS - PER_MESSAGE_TAIL_CHARS
    marker = _MARKER_TEMPLATE.format(omitted=omitted)
    return _replace_content(
        message,
        f"{content[:PER_MESSAGE_HEAD_CHARS]}\n{marker}\n{content[-PER_MESSAGE_TAIL_CHARS:]}",
    )


def truncate_messages(
    messages: list[BaseMessage],
    *,
    max_message_chars: int | None = None,
    total_input_chars: int | None = None,
) -> list[BaseMessage]:
    """Aplica los dos topes de tamaño. Función pura: no muta los mensajes.

    1. Por mensaje: si ``len(content)`` supera ``max_message_chars``
       (default: settings.llm_max_message_chars), el contenido pasa a ser
       head + marcador + tail (PER_MESSAGE_HEAD_CHARS / PER_MESSAGE_TAIL_CHARS).
    2. Presupuesto total: si la suma de los contenidos supera
       ``total_input_chars`` (default: settings.llm_total_input_chars), se
       recorta el CONTENIDO de los mensajes más viejos primero, dejando
       intactos los últimos ``PROTECTED_TAIL_MESSAGES``.

    NUNCA elimina mensajes: un AIMessage con tool_calls eliminado orfanaría
    sus ToolMessages y rompería el request (los pares llamada/resultado deben
    llegar completos, aunque acortados). Los contenidos no-texto (bloques de
    imagen) pasan tal cual: están acotados de origen por los settings de vision.
    """
    if max_message_chars is None:
        max_message_chars = settings.llm_max_message_chars
    if total_input_chars is None:
        total_input_chars = settings.llm_total_input_chars

    # Paso 1: tope por mensaje.
    out = [_cap_message(m, max_chars=max_message_chars) for m in messages]

    # Paso 2: presupuesto total, de los más viejos hacia adelante.
    total = sum(_text_size(m) for m in out)
    if total <= total_input_chars:
        return out

    budget = total_input_chars
    protected = PROTECTED_TAIL_MESSAGES
    for i, message in enumerate(out):
        if len(out) - i <= protected:
            break  # los últimos N mensajes quedan intactos
        size = _text_size(message)
        if size == 0:
            continue
        if size <= budget:
            budget -= size
            continue
        # Este mensaje no cabe en el presupuesto restante: recortar la
        # cabecera a lo que quede (o solo el marcador si no queda nada).
        keep = max(budget, 0)
        omitted = size - keep
        marker = _MARKER_TEMPLATE.format(omitted=omitted)
        if keep > 0:
            new_content = f"{message.content[:keep]}\n{marker}"
        else:
            new_content = marker
        out[i] = _replace_content(message, new_content)
        budget = 0
    return out


class SizeGuardMiddleware(AgentMiddleware):
    """wrap_model_call que aplica truncate_messages al input del modelo.

    Se conecta tanto al agente orquestador (build_agent) como a los specs de
    los subagentes: deepagents NO propaga middleware a los subagentes, cada
    spec necesita el suyo. Transitorio por diseño: solo lo que el modelo ve.
    """

    def wrap_model_call(self, request, handler):
        return handler(
            request.override(messages=truncate_messages(request.messages))
        )

    async def awrap_model_call(self, request, handler):
        return await handler(
            request.override(messages=truncate_messages(request.messages))
        )
