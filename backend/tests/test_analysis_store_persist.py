"""Persistencia del análisis: resolución de extremos de relaciones.

El bug que motiva estos tests (entidad fantasma CONFIGURACIONCONEXION en
Planitrack2.0): ``create_analysis`` persistía el nombre crudo como código
cuando un extremo no resolvía a una entidad (``get(name, name)``), y el
erDiagram renderizaba un nodo fantasma que el diccionario de datos (que solo
itera entidades) jamás mostraba. Ahora los extremos se resuelven con el
mismo resolver del pipeline MER: las variantes ortográficas se re-apuntan al
código correcto y las aristas sin resolución se descartan.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import backend.models  # noqa: F401 — registra todas las tablas en Base.metadata
from backend.models import Base, Project
from backend.services import analysis_store


async def _fresh_db():
    tmp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return sm, tmp


async def _seed(sm) -> int:
    async with sm() as session:
        proj = Project(user_id=1, name="g", slug="g", description="t")
        session.add(proj)
        await session.flush()
        return proj.id


@pytest.mark.asyncio
async def test_create_analysis_resolves_variants_and_skips_ghosts():
    sm, _tmp = await _fresh_db()
    pid = await _seed(sm)
    payload = {
        "entities": [
            {
                "name": "Configuración de Conexión",
                "attributes": [{"name": "id", "type": "uuid", "is_key": True}],
            },
            {
                "name": "Usuario",
                "attributes": [{"name": "id", "type": "uuid", "is_key": True}],
            },
        ],
        "relationships": [
            # Válida directa.
            {
                "from_entity": "Usuario",
                "to_entity": "Configuración de Conexión",
                "cardinality": "1:N",
                "label": "usa",
            },
            # El caso del fantasma: la variante se RE-APUNTA, no se pierde.
            {
                "from_entity": "ConfiguracionConexion",
                "to_entity": "Usuario",
                "cardinality": "1:N",
                "label": "configura",
            },
            # Extremo irrecuperable: se descarta, nunca código = nombre crudo.
            {
                "from_entity": "Usuario",
                "to_entity": "ServidorDeCorreo",
                "cardinality": "1:1",
                "label": "ignora",
            },
        ],
    }

    async with sm() as session:
        doc = await analysis_store.create_analysis(session, pid, payload)

    async with sm() as session:
        entities = await analysis_store.list_domain_entities(session, doc.id)
        rels = await analysis_store.list_domain_relationships(session, doc.id)

    codes = {e.name: e.code for e in entities}
    assert len(entities) == 2
    assert len(rels) == 2  # la arista irrecuperable no se persiste
    # Toda arista apunta a un código ENT-XXXX de este análisis.
    assert all(r.from_entity_code in codes.values() for r in rels)
    assert all(r.to_entity_code in codes.values() for r in rels)
    # La variante quedó apuntada al código de la entidad canónica.
    by_label = {r.label: r for r in rels}
    assert (
        by_label["configura"].from_entity_code
        == codes["Configuración de Conexión"]
    )
