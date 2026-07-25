"""Web tools del DeepAgent: búsqueda y fetch de URLs, acotados por timeout.

Corren HOST-SIDE (en el proceso FastAPI), NO dentro del container del agente.
Razones:

1. Los secretos (futuras API keys de search) viven en el backend; el container
   no los ve (principio del proyecto: secrets nunca en el container).
2. Cada request HTTP está acotado por `settings.web_tool_timeout`. Un fetch
   colgado devuelve error en N segundos en vez de bloquear el turno entero.
3. Web access funnelado por código observable: se puede loggear, rate-limitar
   y filtrar. `curl` libre en el sandbox no da ninguna de esas.

Provider: DuckDuckGo (paquete `ddgs`), sin API key. Si hace falta calidad de
Tavily más adelante, swap de `_ddgs_search` por la API de Tavial sin tocar la
firma de la tool.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Literal

import httpx
from ddgs import DDGS
from langchain_core.tools import tool

from backend.config import settings

logger = logging.getLogger(__name__)

# Protección del context window: la tool no devuelve salidas gigantes.
_MAX_RESULTS = 8
_FETCH_MAX_CHARS = 12_000

# HTML -> texto básico. Suficiente para que el agente lea un artículo; para
# extracción seria, cambiar a trafilatura o beautifulsoup4.
_SCRIPT_STYLE = re.compile(
    r"<(script|style)[^>]*>.*?</(script|style)>", re.DOTALL | re.IGNORECASE
)
_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_HTML_ENTITIES = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
}


def _strip_html(html: str) -> str:
    html = _SCRIPT_STYLE.sub(" ", html)
    text = _HTML_TAG.sub(" ", html)
    for entity, replacement in _HTML_ENTITIES.items():
        text = text.replace(entity, replacement)
    return _WHITESPACE.sub(" ", text).strip()


def _ddgs_search(query: str, kind: str, max_results: int) -> list:
    """Llamada sync a DDGS. Se invoca vía asyncio.to_thread desde web_search
    para no bloquear el event loop del backend."""
    ddgs = DDGS()
    try:
        if kind == "news":
            return list(ddgs.news(query, max_results=max_results))
        return list(ddgs.text(query, max_results=max_results))
    finally:
        close = getattr(ddgs, "close", None)
        if callable(close):
            close()


def _format_general(results) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(
            str(i) + ". " + r.get("title", "") + "\n"
            "   URL: " + r.get("href", "") + "\n"
            "   " + r.get("body", "")
        )
    return "\n\n".join(lines)


def _format_news(results) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        source = r.get("source", "")
        date = r.get("date", "")
        lines.append(
            str(i) + ". " + r.get("title", "") + "\n"
            "   URL: " + r.get("url", "") + "\n"
            "   Fuente: " + source + " · " + date + "\n"
            "   " + r.get("body", "")
        )
    return "\n\n".join(lines)


@tool
async def web_search(
    query: str,
    kind: Literal["general", "news"] = "general",
    max_results: int = 5,
) -> str:
    """Buscar información en la web. Usar para datos actuales, noticias,
    documentación o cualquier cosa que no se conozca de memoria.

    Args:
        query: texto de la búsqueda.
        kind: "news" para noticias recientes (devuelve fecha y fuente);
            "general" para búsqueda normal.
        max_results: cantidad de resultados (máximo 8).

    Devuelve una lista numerada con título, URL y resumen de cada resultado.
    """
    max_results = max(1, min(int(max_results), _MAX_RESULTS))
    timeout = settings.web_tool_timeout
    try:
        results = await asyncio.wait_for(
            asyncio.to_thread(_ddgs_search, query, kind, max_results),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return "[web_search: timeout tras " + str(timeout) + "s para la consulta " + repr(query) + "]"
    except Exception as exc:
        logger.warning("web_search falló para %r: %s", query, exc)
        return "[web_search: error (" + exc.__class__.__name__ + ": " + str(exc) + ")]"

    if not results:
        return "[web_search: sin resultados para " + repr(query) + "]"

    logger.info("web_search query=%r kind=%s resultados=%d", query, kind, len(results))
    return _format_news(results) if kind == "news" else _format_general(results)


@tool
async def fetch_url(url: str) -> str:
    """Descargar una URL y devolver su contenido como texto limpio.

    Usar para leer un artículo o página específica a partir de una URL conocida
    (por ejemplo, un resultado de web_search). NO usar para buscar; para eso
    está web_search.

    Args:
        url: URL absoluta de la página a leer.
    """
    timeout = settings.web_tool_timeout
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "InfoFactAgent/1.0"})
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            raw = resp.text
    except httpx.TimeoutException:
        return "[fetch_url: timeout tras " + str(timeout) + "s para " + url + "]"
    except httpx.HTTPStatusError as exc:
        return "[fetch_url: HTTP " + str(exc.response.status_code) + " para " + url + "]"
    except Exception as exc:
        logger.warning("fetch_url falló para %s: %s", url, exc)
        return "[fetch_url: error (" + exc.__class__.__name__ + ": " + str(exc) + ")]"

    text = _strip_html(raw) if "html" in content_type.lower() else raw
    if len(text) > _FETCH_MAX_CHARS:
        text = text[:_FETCH_MAX_CHARS] + (
            "\n\n[... contenido truncado a " + str(_FETCH_MAX_CHARS) + " caracteres]"
        )
    logger.info("fetch_url url=%s bytes=%d", url, len(text))
    return text or "[fetch_url: respuesta vacía]"
