#!/usr/bin/env python3
"""Smoke: flujo de parser_hint (Fase C) — /upload -> captura -> parse_document_cached.

PURE (stubs, sin DB/LLM/container). Verifica las dos mitades del feature:

  1. Lado upload: ``prepare_document`` persiste ``parser_hint`` y
     ``_normalize_hint`` clampa valores invalidos a ``"auto"``.
  2. Lado captura: ``ingest_documents`` lee ``parser_hint_map`` y pasa el hint
     por-documento a ``parse_document_cached`` (el threading que conecta el
     upload con el parser).

Run: .venv/bin/python scripts/smoke_parser_hint.py
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.agents.subagents.requirements_capture_agent as mod
from backend.agents.subagents import capture_run_holder as holder
from backend.services import document_service

PROJECT_ID = 77001
_FAILED = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global _FAILED
    mark = "OK  " if ok else "FAIL"
    line = f"  {mark}  {label}"
    if not ok:
        _FAILED += 1
        if detail:
            line += f" -> {detail}"
    print(line)


def test_prepare_and_normalize() -> None:
    print("1) prepare_document + _normalize_hint (lado upload)")
    doc = document_service.prepare_document(1, "spec.pdf", b"x", parser_hint="glm-ocr")
    check("prepare_document fija parser_hint=glm-ocr", doc.parser_hint == "glm-ocr", doc.parser_hint)
    doc2 = document_service.prepare_document(1, "spec.pdf", b"x", parser_hint="docling")
    check("prepare_document fija parser_hint=docling", doc2.parser_hint == "docling", doc2.parser_hint)
    doc3 = document_service.prepare_document(1, "spec.pdf", b"x")  # default
    check("prepare_document default parser_hint=auto", doc3.parser_hint == "auto", doc3.parser_hint)
    check("_normalize_hint(bogus) -> auto", document_service._normalize_hint("bogus") == "auto")
    check("_normalize_hint(glm-ocr) -> glm-ocr", document_service._normalize_hint("glm-ocr") == "glm-ocr")


async def test_capture_threading() -> None:
    print("2) ingest_documents pasa parser_hint por-documento")
    holder.clear_run(PROJECT_ID)
    doc_path = Path("/tmp/ws/docs/rotated.pdf")
    smap = types.SimpleNamespace(
        document_id="rotated", full_text="tabla rotada", name="rotated.pdf"
    )
    received_hints: list[str] = []

    mod._resolve_target = lambda ws, sub: doc_path.parent
    mod.discover_documents = lambda t: [doc_path]

    async def _parse(d, *, session=None, parser_hint="auto"):
        received_hints.append(parser_hint)
        return (["c1"], smap)

    mod.parse_document_cached = _parse

    async def _enrich(s, **kw):
        return None

    mod.enrich_structure_map = _enrich

    # parser_hint_map devuelve el hint para ESTE doc (simula que vino de /upload
    # con parser_hint=glm-ocr). Asi probamos que ingest_documents lo propaga.
    async def _hint_map(project_id, workspace_root):
        return {str(doc_path.resolve()): "glm-ocr"}

    mod.parser_hint_map = _hint_map

    async def _count(project_id):
        return {"requirements": 0, "grouping_plans": 0, "last_code": None}

    mod._count_existing = _count

    tools = mod._make_stage_tools(PROJECT_ID, Path("/tmp/ws"), "Proj", "desc")
    out = await tools[0].ainvoke({"target_subpath": ""})  # ingest_documents
    check("ingest corrio sin error", "error" not in out, out)
    check(
        "parse_document_cached recibio parser_hint=glm-ocr",
        received_hints == ["glm-ocr"],
        received_hints,
    )
    holder.clear_run(PROJECT_ID)


async def main() -> None:
    print("== parser_hint flow (Fase C) ==")
    test_prepare_and_normalize()
    await test_capture_threading()
    print("=" * 52)
    if _FAILED:
        print(f"  FAIL: {_FAILED} chequeo(s) fallaron")
        sys.exit(1)
    print("  OK: flujo de parser_hint verificado")


if __name__ == "__main__":
    asyncio.run(main())
