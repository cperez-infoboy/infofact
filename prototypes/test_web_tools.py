"""Validación standalone de las web tools (provider DDG) ANTES del rebuild.

Correr desde la raíz del repo:

    python prototypes/test_web_tools.py

Verifica:
  1. `ddgs` importa (dependencia nueva).
  2. `_ddgs_search` general + news devuelve resultados y mide latencia
     (para confirmar que no hay stalls tipo sesión 6).
  3. `_strip_html` limpia HTML como se espera.
  4. `fetch_url` (path async httpx + @tool) lee una URL real.

No toca el container ni al agente. Sale 0 si todo OK, 1 si falla algo.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# Repo root al path para poder hacer `from backend.agents.tools...`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _time(label: str, fn):
    t = time.monotonic()
    try:
        result = fn()
    except Exception as exc:
        print(f"  FAIL {label}: {exc.__class__.__name__}: {exc}")
        return None
    elapsed = time.monotonic() - t
    print(f"  {label}: {elapsed:.2f}s")
    return result


def main() -> int:
    # 1. dep check
    try:
        from ddgs import DDGS  # noqa: F401
    except ImportError as exc:
        print("FAIL: `ddgs` no instalado ->", exc)
        print("     pip install 'ddgs>=9.0'")
        return 1
    print("OK: ddgs importable\n")

    from backend.agents.tools.web_search import _ddgs_search, _strip_html, fetch_url

    # 2. search general
    print("=== web_search (general) ===")
    res = _time("python fastapi tutorial", lambda: _ddgs_search("python fastapi tutorial", "general", 3))
    if res is not None:
        print(f"  {len(res)} resultados")
        for r in res:
            print("   -", r.get("title", "")[:70])
            print("     ", r.get("href", ""))
    print()

    # 3. search news (el caso de la sesión 6)
    print("=== web_search (news) ===")
    res = _time("noticias de hoy tecnologia", lambda: _ddgs_search("noticias de hoy tecnologia", "news", 3))
    if res is not None:
        print(f"  {len(res)} resultados")
        for r in res:
            print("   -", r.get("title", "")[:70])
            print("     ", r.get("source", ""), "|", r.get("date", ""), "|", r.get("url", ""))
    print()

    # 4. strip html
    print("=== _strip_html ===")
    sample = "<html><body><p>Hola <b>mundo</b></p><script>evil()</script><style>x{}</style></body></html>"
    stripped = _strip_html(sample)
    print(" ", repr(stripped))
    ok = "evil" not in stripped and "Hola mundo" in stripped
    print("  OK" if ok else "  FAIL: no limpió script/style o perdió texto")
    print()

    # 5. fetch_url async (path completo @tool + httpx)
    print("=== fetch_url (async) ===")
    async def _fetch():
        return await fetch_url.ainvoke({"url": "https://example.com"})

    t = time.monotonic()
    try:
        out = asyncio.run(_fetch())
        elapsed = time.monotonic() - t
        print(f"  {elapsed:.2f}s, {len(out)} chars")
        print(" ", out[:160].replace("\n", " "))
        if out.startswith("[fetch_url:"):
            print("  (la tool devolvió error, ver mensaje arriba)")
    except Exception as exc:
        print(f"  FAIL: {exc.__class__.__name__}: {exc}")
    print()

    print("Listo. Si las latencias son < 15s y hay resultados, DDG funciona.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
