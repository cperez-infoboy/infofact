#!/usr/bin/env python3
"""Smoke del módulo compartido de reglas de calidad (shift-left de captura).

Alimenta enunciados representativos y muestra, por capa:
- Capa 0 (detección): ``programmatic_findings_for_text`` (con y sin ``req_type``).
- Capa 1 (prevención): ``PREVENTION_RULES`` interpolado en los prompts.

Patrón de los smoke scripts existentes (``smoke_srs_quality.py``). Sin DB, sin LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from backend.agents.pipelines._quality_rules import (
    PREVENTION_RULES,
    programmatic_findings_for_text,
)


# (etiqueta, enunciado, req_type) — req_type=None simula la crítica pre-clasificación.
CASES = [
    ("vago", "El sistema debe ser rápido y eficiente.", None),
    ("combinador", "Debe exportar a PDF y/o DOCX.", None),
    ("negación", "El sistema no debe permitir accesos anónimos.", None),
    ("modal faltante", "Autenticación de usuarios vía OAuth.", None),
    ("absoluto", "El sistema debe estar disponible el 100% del tiempo.", None),
    ("NFR sin target", "El sistema debe procesar conciliaciones bancarias.", "performance"),
    ("NFR con target", "Debe responder el login en menos de 200 ms.", "performance"),
    ("EARS limpio", "Cuando el usuario presiona guardar, el sistema debe persistir el formulario.", None),
]


def main() -> None:
    print("== Capa 0: detección determinista (programmatic_findings_for_text) ==\n")
    total = 0
    for label, stmt, rtype in CASES:
        flags = programmatic_findings_for_text(stmt, req_type=rtype)
        ids = ", ".join(f.rule_id for f in flags) or "(sin hallazgos)"
        total += len(flags)
        print(f"[{label}] {stmt}")
        print(f"    req_type={rtype!r} -> {ids}\n")
    print(f"Total de hallazgos: {total}\n")

    print("== Capa 1: prevención (PREVENTION_RULES, interpolado en prompts) ==\n")
    print(PREVENTION_RULES)
    print(f"(longitud: {len(PREVENTION_RULES)} caracteres)\n")

    print("quality_rules smoke: OK")


if __name__ == "__main__":
    main()
