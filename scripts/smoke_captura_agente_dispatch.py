"""Smoke: /captura_agente dispatch -- directive text + target subagent.

Invokes _rewrite_command directly (no LLM, no container) and asserts the
directive names requirements-capture-agent, embeds user steering, and that
/captura delegates to the same agent-driven subagent.

Run: .venv/bin/python scripts/smoke_captura_agente_dispatch.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.routers.chat import _rewrite_command


_FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _FAILED
    mark = "OK  " if ok else "FAIL"
    line = f"  {mark}  {label}"
    if not ok:
        _FAILED += 1
        if detail:
            line += f" -> {detail}"
    print(line)


def main() -> None:
    print("== /captura_agente (sin texto) ==")
    out = _rewrite_command("/captura_agente")
    check(
        "nombra requirements-capture-agent",
        "requirements-capture-agent" in out,
        out[:140],
    )
    check("menciona la tool task", "`task`" in out, out[:140])
    check(
        "sin bloque INSTRUCCIONES DEL USUARIO",
        "INSTRUCCIONES DEL USUARIO" not in out,
    )

    print("== /captura_agente con steering ==")
    out = _rewrite_command("/captura_agente dale atencion a restricciones")
    check("nombra requirements-capture-agent", "requirements-capture-agent" in out)
    check(
        "embebe INSTRUCCIONES DEL USUARIO",
        'INSTRUCCIONES DEL USUARIO: "dale atencion a restricciones"' in out,
        out,
    )

    print("== /captura-agente (guion) ==")
    out = _rewrite_command("/captura-agente")
    check("reconoce variante con guion", "requirements-capture-agent" in out)

    print("== /captura rutea al agentico (mismo que /captura_agente) ==")
    out = _rewrite_command("/captura")
    check(
        "nombra requirements-capture-agent",
        "requirements-capture-agent" in out,
    )
    check(
        "NO menciona run_requirements_capture (determinista quitado)",
        "run_requirements_capture" not in out,
    )

    print("== /captura docs/x -> steering embebido ==")
    out = _rewrite_command("/captura docs/x")
    check(
        'INSTRUCCIONES DEL USUARIO: "docs/x"',
        'INSTRUCCIONES DEL USUARIO: "docs/x"' in out,
        out,
    )

    print("=" * 52)
    if _FAILED:
        print(f"  FAIL: {_FAILED} chequeo(s) fallaron")
        sys.exit(1)
    print("  OK: todos los chequeos pasaron")


if __name__ == "__main__":
    main()
