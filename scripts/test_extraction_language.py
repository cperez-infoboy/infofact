"""Quick check: extractor must preserve the source document's language.

A Spanish chunk yields a Spanish statement, NOT an English translation.
Bug context: the extractor's system prompt was in English and never told the
LLM to preserve the source language, so Z.ai GLM defaulted to translating.
This script exercises one Spanish chunk end-to-end through ``extract_chunk``
and asserts the statement comes back in Spanish.

Run: ``.venv/bin/python scripts/test_extraction_language.py``
Exits 0 on success, 1 on failure (statement translated or no items returned).
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agents.pipelines.extraction import extract_chunk
from backend.agents.pipelines.ingestion import Chunk

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

# Same pattern as the REQ-1086 case from the e2e (was coming out in English).
SPANISH_CHUNK_TEXT = (
    "La aplicación debe estar disponible en la Play Store y notificar a los "
    "usuarios de la aplicación sobre la recolección de datos."
)

# English tokens that signal unwanted translation. Lowercase, substring match.
_EN_TOKENS = [
    "must", "shall", "the application", "the system", "should be",
    "notify application users", "data collection",
]
# Spanish tokens expected in a faithful Spanish statement.
_ES_TOKENS = [
    "aplicación", "debe", "notificar", "usuarios", "recolección",
    "datos",
]


async def main() -> None:
    chunk = Chunk(
        text=SPANISH_CHUNK_TEXT,
        document_id="test-language-doc",
        section_path="Funcional",
    )
    result = await extract_chunk(
        chunk,
        project_name="TestLang",
        project_description="Proyecto de prueba para preservacion de idioma",
    )
    if not result.items:
        print("FAIL: el extractor devolvio 0 items para un chunk con req claro.")
        sys.exit(1)

    stmt = result.items[0].statement
    print(f"statement: {stmt}")
    print(f"source_span: {result.items[0].source_span}")

    stmt_lower = stmt.lower()
    en_hits = [t for t in _EN_TOKENS if t in stmt_lower]
    es_hits = [t for t in _ES_TOKENS if t in stmt_lower]
    print(f"EN tokens hallados: {en_hits}")
    print(f"ES tokens hallados: {es_hits}")

    # Pass: Spanish tokens present AND no English template tokens.
    ok = bool(es_hits) and not en_hits
    print("OK — idioma preservado" if ok else "FAIL — tradujo al ingles")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
