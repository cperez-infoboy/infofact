"""Contratos del subsistema de parsers (Fase C).

``ParsedDoc`` es el retorno uniforme de cualquier parser (chunks + StructureMap
+ el nombre del parser que lo produjo). El router despacha; la capa de cache lo
usa para distinguir parses del mismo sha256 bajo parsers distintos.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.agents.pipelines.ingestion import Chunk, StructureMap


@dataclass
class ParsedDoc:
    """Salida uniforme de un parser."""

    chunks: "list[Chunk]"
    smap: "StructureMap"
    parser_used: str  # "docling" | "plaintext" | "glm-ocr"
