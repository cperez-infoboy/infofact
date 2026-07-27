"""Smoke: reconstruye el historial de una sesión desde el checkpointer.

Valida que ``reconstruct_history`` devuelva items ``role:'tool'`` (con
``tool_name`` y output poblados) además de ``user``/``assistant``, reproduciendo
lo que el usuario vio en vivo (las llamadas a herramientas que el streaming SSE
nunca persiste en ``chat_messages``).

Uso:
    python scripts/smoke_reconstruct_history.py [session_id]

Por defecto ``session_id=8`` (sesión "test1 — sesión 7", con ~14 tool_calls en
el checkpointer).
"""
import asyncio
import sys
from pathlib import Path

# Asegurar que el paquete backend sea importable al correr desde la raíz.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.agent_service import (
    build_checkpointer,
    close_checkpointer,
    reconstruct_history,
)


async def main() -> int:
    session_id = int(sys.argv[1]) if len(sys.argv) > 1 else 8

    checkpointer = await build_checkpointer()
    try:
        items = await reconstruct_history(checkpointer, session_id)
    finally:
        await close_checkpointer(checkpointer)

    if not items:
        print(f"Session {session_id}: SIN items (thread inexistente o vacío)")
        return 1

    print(f"Session {session_id}: {len(items)} items reconstruidos")

    by_role: dict[str, int] = {}
    for it in items:
        by_role[it["role"]] = by_role.get(it["role"], 0) + 1
    print("Por role:", by_role)

    tools = [it for it in items if it["role"] == "tool"]
    print(f"Tools: {len(tools)}")
    for t in tools:
        name = t.get("tool_name") or ""
        out_len = len((t.get("content") or ""))
        print(f"  - {name} (tool_call_id={t.get('tool_call_id')}) output={out_len}b")

    users = [it for it in items if it["role"] == "user"]
    assistants = [it for it in items if it["role"] == "assistant"]
    print(f"User: {len(users)}  Assistant: {len(assistants)}")

    # Aserciones: hay tools con nombre + output, y mensajes user/assistant.
    assert tools, "Esperaba al menos un item role:'tool'"
    assert all(t.get("tool_name") for t in tools), "Algún tool sin tool_name"
    assert any(t.get("content") for t in tools), "Ningún tool con output"
    assert users, "Esperaba al menos un item role:'user'"
    assert assistants, "Esperaba al menos un item role:'assistant'"

    print("\nOK: historial reconstruido con tools + user + assistant")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
