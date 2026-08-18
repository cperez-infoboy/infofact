"""Cache de embeddings de enunciados (requirement_embeddings).

Miss -> encode + upsert; hit -> sale del cache sin tocar el embedder. La
clave es (modelo, sha256 del enunciado): un enunciado nuevo/editado cambia
de hash y se re-codifica.
"""
from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.agents.pipelines import consolidation
from backend.models import Base  # noqa: F401 -- registra todas las tablas
from backend.models.requirement_embedding import RequirementEmbedding


def _fake_embed_factory(encoded: list):
    """Embedder falso de dim fija 8 que registra cada llamada."""

    def fake_embed(texts):
        encoded.append(list(texts))
        return np.tile(
            np.linspace(1.0, 0.1, 8, dtype="float32"), (len(texts), 1)
        )

    return fake_embed


@pytest.mark.asyncio
async def test_cache_encodes_only_missing_statements(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    encoded: list[list[str]] = []
    monkeypatch.setattr(consolidation, "embed_texts", _fake_embed_factory(encoded))

    # Primera corrida: todo es miss.
    async with maker() as s:
        out = await consolidation.embed_texts_cached(s, ["alpha", "beta"])
        assert out.shape == (2, 8)
        assert encoded == [["alpha", "beta"]]
        await s.commit()

    # Segunda corrida: alpha/beta salen del cache; solo gamma se codifica.
    async with maker() as s:
        out2 = await consolidation.embed_texts_cached(
            s, ["alpha", "beta", "gamma"]
        )
        assert out2.shape == (3, 8)
        assert encoded == [["alpha", "beta"], ["gamma"]]
        rows = (await s.scalars(select(RequirementEmbedding))).all()
        assert len(rows) == 3
        await s.commit()

    # El vector cacheado es el mismo que el fresco (roundtrip BLOB).
    assert np.allclose(out2[0], out[0])
    await engine.dispose()


@pytest.mark.asyncio
async def test_cached_rows_shared_across_statements(monkeypatch):
    """Enunciados repetidos (mismo texto) comparten fila: una sola codificacion."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    encoded: list[list[str]] = []
    monkeypatch.setattr(consolidation, "embed_texts", _fake_embed_factory(encoded))

    async with maker() as s:
        out = await consolidation.embed_texts_cached(s, ["repetido", "repetido"])
        assert out.shape == (2, 8)
        assert encoded == [["repetido"]]  # dedup por hash antes de encodear
        rows = (await s.scalars(select(RequirementEmbedding))).all()
        assert len(rows) == 1
        await s.commit()

    await engine.dispose()
