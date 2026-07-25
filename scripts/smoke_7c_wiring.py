"""Smoke for paso 7c pure pieces (no LLM, no agent, no container).

- _rewrite_command: /captura → directiva al subagente (estrategia A del plan §9.3).
- _make_progress_emitter: silent fuera de contexto LangGraph (get_stream_writer
  raises → caught → pipeline no se rompe en smoke / invocación directa).

The astream multi-mode chunk parsing inside event_stream is exercised end-to-end
by a real agent run (paso 8 / manual e2e), not here.

Run: .venv/bin/python scripts/smoke_7c_wiring.py
"""
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from backend.routers.chat import _rewrite_command
from backend.agents.subagents.requirements_capture import _make_progress_emitter

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def main() -> None:
    print("== _rewrite_command (/captura estrategia A) ==")
    r1 = _rewrite_command("/captura")
    check("sin arg → directiva al subagente",
          "requirements-capture" in r1 and "run_requirements_capture" in r1, r1[:120])
    check("sin arg → target_subpath vacío (proyecto completo)",
          'target_subpath=""' in r1, r1[:160])

    r2 = _rewrite_command("/captura docs/rfps")
    check("con subpath → target_subpath citado",
          'target_subpath="docs/rfps"' in r2, r2[:160])

    r3 = _rewrite_command("hola, ¿cómo andás?")
    check("texto plano → sin reescritura", r3 == "hola, ¿cómo andás?", repr(r3))

    r4 = _rewrite_command("   /captura   ")
    check("con espacios → igualmente reescrito",
          "requirements-capture" in r4 and 'target_subpath=""' in r4, r4[:120])

    r5 = _rewrite_command("/captura   docs/minutas")
    check("subpath con espacios extra → normalizado",
          'target_subpath="docs/minutas"' in r5, r5[:160])

    r6 = _rewrite_command("/captura/docs/rfps")
    check("forma /captura/path (sin espacio) → reescrito igual",
          'target_subpath="docs/rfps"' in r6, r6[:160])

    # No es comando: no reescribe aunque contenga la palabra.
    r7 = _rewrite_command("¿qué hace la captura de requerimientos?")
    check("no comienza con /captura → intacto", r7.startswith("¿qué hace"), repr(r7))

    print("\n== _make_progress_emitter (silent sin contexto LangGraph) ==")
    emit = _make_progress_emitter()
    try:
        asyncio.run(emit("ingest", "discover"))
        check("emit fuera de contexto → no levanta", True)
    except Exception as exc:  # noqa: BLE001
        check("emit fuera de contexto → no levanta", False, f"{type(exc).__name__}: {exc}")

    print(f"\n{'='*40}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
