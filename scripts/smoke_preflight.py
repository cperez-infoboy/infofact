#!/usr/bin/env python
"""Smoke: preflight probes pass in a healthy container and flag a broken dep.

Parte 1 — entorno sano: check_capture_environment() debe dar ok=True con todos
los probes pasando (docling/opencv/tiktoken/sentence-transformers importables,
endpoint LLM alcanzable, workspace escribible).

Parte 2 — dep roto simulado: _probe_import sobre un modulo inexistente debe
fallar y traer un hint de remediacion (la clase de fallo que preflight debe
detectar ANTES de la captura, como el caso libxcb).
"""
import asyncio
import sys

from backend.agents.pipelines.preflight import (
    _probe_import,
    check_capture_environment,
)


async def main() -> int:
    # Parte 1: entorno real del contenedor (se asume sano en el image de prod).
    report = check_capture_environment()
    print(f"[parte 1] ok={report.ok}")
    for p in report.probes:
        flag = "OK" if p.ok else "FAIL"
        print(f"  [{flag}] {p.name}: {p.detail}")
        if not p.ok:
            print(f"        hint: {p.hint}")
    assert report.ok, (
        "esperaba entorno sano en el contenedor — revisar LLM_API_KEY, "
        "WORKSPACES_HOST_ROOT y las deps de la imagen"
    )

    # Parte 2: dep roto simulado -> el probe falla con hint accionable.
    broken = _probe_import("docling_broken", "no_such_module_xyz")
    print(
        f"[parte 2] broken probe ok={broken.ok}, "
        f"detail={broken.detail!r}, hint={broken.hint!r}"
    )
    assert not broken.ok, "un import inexistente debe fallar"
    assert broken.hint, "un probe fallido debe traer hint de remediacion"

    print("OK: preflight detecta entorno sano Y dep roto simulado")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
