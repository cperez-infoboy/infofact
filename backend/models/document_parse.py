"""DocumentParse: cache del parseo deterministico, keyed por sha256.

Un documento parseado una vez (Docling o plaintext + chunking + structure_map +
vision) se persiste aqui keyed por su ``sha256`` (clave GLOBAL, no por proyecto).
Cualquier proyecto que referencie el mismo contenido reutiliza el mismo parseo,
sin volver a correr Docling ni el modelo de vision.

Solo cachea la parte DETERMINISTICA del parseo (depende unicamente del archivo).
Las etapas dependientes del proyecto (``enrich_structure_map`` rellena summary /
req_likelihood / glossary; ``extract_conventions`` rellena conventions) se
re-ejecutan siempre sobre el ``StructureMap`` devuelto por el cache, que es
siempre fresco: la frontera de serializacion (JSON en SQLite) reconstruye
objetos Python nuevos en cada HIT, asi que no hace falta deepcopy para evitar
contaminacion entre proyectos.

``parser_version`` es la clave de invalidacion: combina el parser usado, la
version de Docling y un schema version del codigo de parseo. Si cualquiera
cambia (upgrade de Docling, refactor del chunking), el HIT se trata como MISS y
se re-parsea, evitando servir cache obsoleto.

Relacionado con ProjectDocument (modelo de Fase A): este modelo cachea el
parseo del archivo al que ProjectDocument le da identidad + sha256.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class DocumentParse(Base):
    __tablename__ = "document_parses"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Clave de cache global: mismo contenido (sha256) = mismo parseo, sin
    # importar el proyecto. Unique -> dedupe natural entre proyectos.
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # "docling" | "plaintext" (Fase C anadira "glm_ocr").
    parser_used: Mapped[str] = mapped_column(String(32))
    # Clave de invalidacion compuesta: parser + version de Docling + schema del
    # codigo. Si no calza con la version actual, se re-parsea.
    parser_version: Mapped[str] = mapped_column(String(127))
    # markdown export (StructureMap.full_text): entrada de verify_spans y, en
    # Fase D, base de get_passage.
    markdown: Mapped[str] = mapped_column(Text, default="")
    # JSON: lista de chunks serializada (incluye los image_description).
    chunks_json: Mapped[str] = mapped_column(Text, default="[]")
    # JSON: lista de SectionNode (solo campos del parser; los enriquecidos van
    # en sus defaults y los rellena enrich_structure_map tras el HIT).
    sections_json: Mapped[str] = mapped_column(Text, default="[]")
    tables_json: Mapped[str] = mapped_column(Text, default="[]")
    # Siempre {} al momento de cachear (lo rellena enrich_structure_map despues).
    glossary_json: Mapped[str] = mapped_column(Text, default="{}")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
