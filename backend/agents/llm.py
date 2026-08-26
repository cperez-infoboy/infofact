"""Shared LLM construction for the agent and the requirements pipelines.

Single source of truth for the OpenAI-compatible ChatOpenAI client (default
target: Z.ai GLM via settings.llm_*). Callers tune temperature/streaming to
their role: the conversational agent streams; the deterministic extraction and
critic passes use temperature=0.

Living here (not under backend.services) lets the pipelines reuse it without
importing backend.services — the service layer only relays, it does not own
agent logic (CLAUDE.md architecture).
"""
from __future__ import annotations

import os
import re

from langchain_openai import ChatOpenAI
from langgraph.constants import TAG_NOSTREAM

from backend.config import settings


def build_llm(
    *,
    temperature: float = 0.3,
    streaming: bool = False,
    extra_body: dict | None = None,
) -> ChatOpenAI:
    """Build the OpenAI-compatible chat client from settings.

    Raises RuntimeError at call time if the API key is unset, so the backend can
    import/start without an LLM (smoke tests) but refuses to actually call one.
    """
    if not settings.llm_api_key:
        raise RuntimeError(
            "LLM_API_KEY is not set. Put it in .env (see .env.example)."
        )
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=temperature,
        streaming=streaming,
        # Incidente sesion 49: sin max_tokens explicito el proxy cortaba la
        # salida en 4096 tokens (finish_reason=length) y write_file llegaba
        # con args vacios. Env-tunable via LLM_MAX_TOKENS.
        max_tokens=settings.llm_max_tokens,
        # Z.ai (glm-5.2) sometimes stalls mid-stream: TCP stays open but chunks
        # stop arriving. langchain_openai's default 120s then aborts with
        # StreamChunkTimeoutError. 300s (5 min) gives margin for provider
        # rate-limiting/queueing. Tune via env if needed.
        stream_chunk_timeout=300,
        # SDK handles 429/5xx backoff natively; critique.py handles parse
        # failures and the last-mile persistent-429 case.
        max_retries=2,
        # Extensiones de body propietarias del proveedor (p.ej. thinking de
        # Z.ai). None mantiene el request idéntico para deployments
        # portables en otros proveedores OpenAI-compatible.
        extra_body=extra_body,
    )


def disable_thinking_body() -> dict | None:
    """Body que apaga el razonamiento interno (thinking) de GLM en Z.ai.

    ``thinking`` es una extensión propietaria de Z.ai: otros proveedores
    OpenAI-compatible pueden rechazar con 400 un campo desconocido, así que
    el parámetro solo viaja cuando el endpoint apunta a Z.ai (o se fuerza
    explícito desde el entorno).

    ``INFOFACT_JUDGE_DISABLE_THINKING``: ``1`` fuerza enviarlo, ``0`` fuerza
    no enviarlo; el default ``auto`` lo envía solo si el host de
    ``settings.llm_base_url`` contiene ``z.ai``.
    """
    choice = os.environ.get("INFOFACT_JUDGE_DISABLE_THINKING", "auto")
    choice = choice.strip().lower()
    if choice in {"0", "false", "no", "off"}:
        return None
    if choice in {"1", "true", "yes", "on"}:
        return {"thinking": {"type": "disabled"}}
    if "z.ai" in (settings.llm_base_url or "").lower():
        return {"thinking": {"type": "disabled"}}
    return None


# ---------------------------------------------------------------------------
# Llamadas dentro de tools: fuera del stream `messages`
# ---------------------------------------------------------------------------

# Incidente de la sesión 44: un StructuredRunnable invocado dentro de una tool
# hereda el árbol de callbacks del grafo, así que LangGraph emitía su AIMessage
# COMPLETO (JSON crudo del anotador, 1.1 MB) al stream `messages`, y el relay lo
# acumulaba, lo persistía y lo mandaba al navegador. Con el tag `nostream`
# (TAG_NOSTREAM de langgraph) la llamada no se registra en el stream: el
# progreso de estas pasadas ya viaja por eventos custom (`extraction.progress`).


def with_nostream(llm):
    """Devuelve el LLM con el tag `nostream` ligado (fuera del stream)."""
    return llm.with_config(tags=[TAG_NOSTREAM])


def build_pipeline_llm(*, temperature: float = 0.3, streaming: bool = False):
    """LLM para pasadas estructuradas dentro de tools, fuera del stream.

    Drop-in de build_llm() para TODO LLM que corra dentro de una tool del
    agente (pipeline de captura, reparación de diagramas, vision): mismo
    cliente, pero LangGraph no emite su respuesta al stream `messages`, así
    nunca llega al relay ni al navegador. El modelo conversacional NO usa
    esta variante: sus tokens sí deben streamearse.
    """
    return with_nostream(build_llm(temperature=temperature, streaming=streaming))


# ---------------------------------------------------------------------------
# Structured output, tolerant of markdown code fences
# ---------------------------------------------------------------------------

# Some OpenAI-compatible providers (Z.ai GLM, DeepSeek, Qwen) wrap JSON in
# markdown code fences ("```json ... ```") even when asked for plain JSON, and/or
# lack native tool calling — so ChatOpenAI.with_structured_output (which falls
# back to strict json_schema parsing) raises pydantic ValidationError on the
# fences. This runnable strips fences before pydantic validation and works as a
# drop-in replacement: `structured_llm(Schema)` then `await it.ainvoke([...])`.

_FENCE_OPEN_RE = re.compile(r"^\s*```[a-zA-Z0-9]*\s*\n?", re.MULTILINE)
_FENCE_CLOSE_RE = re.compile(r"\n?```\s*$")
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # multimodal / content blocks
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return str(content)


def _strip_code_fences(text: str) -> str:
    stripped = _FENCE_OPEN_RE.sub("", text.strip())
    stripped = _FENCE_CLOSE_RE.sub("", stripped).strip()
    return stripped


def _format_instructions(schema) -> str:
    """Inject the exact JSON schema so models without native tool-calling (Z.ai
    GLM) emit the right fields instead of improvising their own (e.g. `category`
    instead of `section`)."""
    import json
    js = schema.model_json_schema()
    return (
        "Respond with ONLY a single JSON object that validates against this JSON "
        "schema. Do NOT wrap it in markdown code fences. Do NOT add any prose. "
        "Every field shown as required in the schema MUST be present with the "
        "correct key name.\n"
        f"JSON SCHEMA:\n{json.dumps(js, ensure_ascii=False)}"
    )


class StructuredRunnable:
    """LLM -> pydantic schema, fence-tolerant. Duck-typed ainvoke/invoke."""

    def __init__(self, llm: ChatOpenAI, schema):
        self._llm = llm
        self._schema = schema

    def _parse(self, text: str):
        text = _strip_code_fences(_content_to_text(text))
        try:
            return self._schema.model_validate_json(text)
        except Exception:
            # last resort: isolate the first {...} object (model may have added
            # prose around the JSON)
            match = _JSON_OBJECT_RE.search(text)
            if match:
                return self._schema.model_validate_json(match.group(0))
            raise

    @staticmethod
    def _nostream_config(config):
        """Fusiona el tag `nostream` en el config, respetando tags del caller.

        LangGraph no registra en el stream `messages` las llamadas con este
        tag, así el JSON crudo de las pasadas estructuradas nunca llega al
        relay ni al navegador (incidente de la sesión 44). No pisa los tags
        que el caller ya haya mandado: solo agrega el que falta.
        """
        config = dict(config or {})
        tags = list(config.get("tags") or [])
        if TAG_NOSTREAM not in tags:
            tags.append(TAG_NOSTREAM)
        config["tags"] = tags
        return config

    async def ainvoke(self, messages, config=None, **kwargs):
        msgs = list(messages) + [("system", _format_instructions(self._schema))]
        resp = await self._llm.ainvoke(
            msgs, config=self._nostream_config(config), **kwargs
        )
        return self._parse(resp.content)

    def invoke(self, messages, config=None, **kwargs):
        msgs = list(messages) + [("system", _format_instructions(self._schema))]
        resp = self._llm.invoke(
            msgs, config=self._nostream_config(config), **kwargs
        )
        return self._parse(resp.content)


def structured_llm(
    schema, *, temperature: float = 0.0, extra_body: dict | None = None
) -> StructuredRunnable:
    """Return a fence-tolerant LLM->schema runnable.

    Drop-in for build_llm(temperature=t).with_structured_output(schema) on
    providers that wrap JSON in code fences or lack native tool calling.
    ``extra_body`` (opcional) reenvía extensiones propietarias del request.
    """
    return StructuredRunnable(
        build_llm(temperature=temperature, extra_body=extra_body), schema
    )
