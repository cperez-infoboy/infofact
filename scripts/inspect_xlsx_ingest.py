"""Inspect Docling ingestion of the RFP xlsx: confirm requirement descriptions
are present in the parsed text, and measure per-chunk size to detect giant-table
chunks that would cause lost-in-the-middle at extraction."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agents.pipelines.ingestion import ingest_document

path = Path("docs/testing/1. Requerimientos tecnicos- funcionales.xlsx")
chunks, smap = ingest_document(path)

print(f"chunks={len(chunks)}  full_text={len(smap.full_text)} chars  tables={len(smap.tables)}")
print("\n--- per-chunk size ---")
for c in chunks:
    print(f"  chunk[{c.index}] {len(c.text)} chars  kinds={c.element_kinds}  page={c.page}  sec={c.section_path[:40]!r}")

# probes: known requirement descriptions from the RFP (col D)
probes = [
    "Aplicación nativa en Android ejecutándose en versiones Android 13",
    "arquitectura basada en eventos",
    "datos almacenados en el dispositivo deben estar cifrados",
    "arquitectura resiliente a fallos",
]
ft = smap.full_text
print("\n--- description probes in full_text ---")
for p in probes:
    hit = p.lower() in ft.lower()
    print(f"  {'FOUND' if hit else 'MISSING'}: {p[:70]!r}")
