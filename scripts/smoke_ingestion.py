"""Smoke test for ingestion.py against a real client document.

Usage:
    .venv/bin/pip install docling
    .venv/bin/python scripts/smoke_ingestion.py <doc> [<doc> ...]

Accepts any path Docling handles (pdf, docx, pptx, html, txt, md, csv, images).

Checks:
    - convert_document + chunk_document + build_structure_map run end-to-end
    - every chunk has text + document_id
    - full_text (input for verify_spans in extraction.py) is non-empty
    - prints chunk count, section/table counts, page count, first chunk sample

Exit 1 if any document yields zero chunks or incomplete chunks.
First run is slow: Docling downloads its models (DocLayNet, TableFormer) on
first use and caches them under ~/.cache/huggingface.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the repo root importable so `backend...` resolves when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agents.pipelines.ingestion import ingest_document  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: smoke_ingestion.py <doc> [<doc> ...]", file=sys.stderr)
        return 2

    rc = 0
    for raw in argv[1:]:
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            print(f"not a file: {path}", file=sys.stderr)
            rc = 1
            continue

        print(f"\n=== {path.name} ===")
        chunks, smap = ingest_document(path)
        print(f"pages={smap.page_count}  chunks={len(chunks)}  "
              f"sections={len(smap.sections)}  tables={len(smap.tables)}")

        if not chunks:
            print("  FAIL: 0 chunks extracted", file=sys.stderr)
            rc = 1
            continue

        bad = [c for c in chunks if not c.text or not c.document_id]
        if bad:
            print(f"  FAIL: {len(bad)} chunks missing text/document_id",
                  file=sys.stderr)
            rc = 1

        print(f"  full_text length: {len(smap.full_text)} chars")
        first = chunks[0]
        print(f"  first chunk ({len(first.text)} chars, "
              f"kinds={first.element_kinds}, page={first.page}):")
        preview = first.text[:200].replace("\n", " ")
        print(f"    {preview} ...")

        if smap.sections:
            print("  first 3 sections:")
            for s in smap.sections[:3]:
                print(f"    [lvl {s.level}] {s.title[:60]} (p.{s.page})")
        if smap.tables:
            t = smap.tables[0]
            print(f"  first table: {t.row_count}x{t.col_count} "
                  f"caption={(t.caption or '')[:40]!r}")

    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
