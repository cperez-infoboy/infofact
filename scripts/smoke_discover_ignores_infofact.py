#!/usr/bin/env python
"""Smoke: discover_documents ignores .infofact/ (y copias sueltas de
grouping-plans/) so plans are never parsed as client requirements.

Sin el fix (agregar '.infofact' y 'grouping-plans' a _IGNORED_DIRS en
ingestion.py), el pipeline intentaria parsear
.infofact/grouping-plans/<ts>/plan.md como un documento cliente y lo meteria
como requisito. La copia suelta en la raiz del proyecto cubre el caso real de
la sesion 44 (Planitrack2.0, 2026-08-14): la directiva pre-DB le decia al
agente que guardara en .infofact/, el dir estaba root:root, y el agente creo
grouping-plans/ FUERA de .infofact. Este smoke clava ambos comportamientos.
"""
import asyncio
import sys
import tempfile
from pathlib import Path

from backend.agents.pipelines.ingestion import discover_documents


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Un doc de cliente legitimo + un plan interno bajo .infofact/ + una
        # copia suelta en la raiz (el workaround del agente).
        (root / "spec.md").write_text(
            "# Requerimientos\nEl sistema debe autenticar usuarios.\n",
            encoding="utf-8",
        )
        plan_dir = root / ".infofact" / "grouping-plans" / "2026-01-01T000000"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan.md").write_text(
            "---\nstatus: proposed\n---\n\n## Grupo 1\n"
            "- **Mantener:** REQ-001\n- **Fusionar:** REQ-002\n",
            encoding="utf-8",
        )
        stray_dir = root / "grouping-plans"
        stray_dir.mkdir()
        (stray_dir / "plan.md").write_text(
            "---\nstatus: proposed\n---\n\n## Grupo 1\n- **Mantener:** REQ-001\n",
            encoding="utf-8",
        )

        found = discover_documents(root)
        names = sorted(p.name for p in found)
        print(f"discovered: {names}")

        assert names == ["spec.md"], (
            f"debe descubrir SOLO el doc de cliente; encontro: {names}"
        )
        leaked = [
            p for p in found if ".infofact" in p.parts or "grouping-plans" in p.parts
        ]
        assert not leaked, (
            f".infofact y grouping-plans deben ser ignorados; se filtraron: {leaked} "
            "(plan.md seria parseado como requisito)"
        )
        print("OK: .infofact y grouping-plans suelto ignorados, doc de cliente descubierto")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
