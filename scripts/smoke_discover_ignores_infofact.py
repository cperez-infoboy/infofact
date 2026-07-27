#!/usr/bin/env python
"""Smoke: discover_documents ignores .infofact/ so plans are never parsed as
client requirements.

Sin el fix (agregar '.infofact' a _IGNORED_DIRS en ingestion.py), el pipeline
intentaria parsear .infofact/grouping-plans/<ts>/plan.md como un documento
cliente y lo meteria como requisito. Este smoke clava ese comportamiento.
"""
import asyncio
import sys
import tempfile
from pathlib import Path

from backend.agents.pipelines.ingestion import discover_documents


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Un doc de cliente legitimo + un plan interno bajo .infofact/.
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

        found = discover_documents(root)
        names = sorted(p.name for p in found)
        print(f"discovered: {names}")

        assert "spec.md" in names, "el doc de cliente debe ser descubierto"
        leaked = [p for p in found if ".infofact" in p.parts]
        assert not leaked, (
            f".infofact debe ser ignorado; se filtraron: {leaked} "
            "(plan.md seria parseado como requisito)"
        )
        print("OK: .infofact ignorado, doc de cliente descubierto")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
