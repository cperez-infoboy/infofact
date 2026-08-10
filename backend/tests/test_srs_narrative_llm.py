"""Tests del draft narrativo asistido por LLM (draft_narrative_llm).

Cubre:
- Validación del schema SrsNarrativeDraft (8 campos).
- LLM sobrescribe placeholders preservando deterministicos (overview, features).
- Fallback graceful cuando la llamada LLM falla.
- Reconstrucción de claves legacy (intro, overall).

Mockea ``structured_llm`` y ``retrieval.search``: no llama a la API real.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.requirement import Priority, ReqStatus, ReqType
from backend.services.srs_assembler import (
    SrsNarrativeDraft,
    _draft_narrative,
    draft_narrative_llm,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_item(
    code: str = "REQ-AB12",
    statement: str = "El sistema debe registrar usuarios.",
    rtype: ReqType = ReqType.FUNCTIONAL,
    priority: Priority = Priority.MUST,
) -> SimpleNamespace:
    return SimpleNamespace(
        code=code,
        statement=statement,
        type=rtype,
        priority=priority,
        status=ReqStatus.VALIDATED,
    )


def _det_narrative() -> dict[str, str]:
    """Narrativa determinista base para los tests."""
    return _draft_narrative(
        "Mi Proyecto",
        "Descripción de prueba",
        {"total_findings": 2, "blockers": 0},
        {"totals": {"live": 5, "functional": 3, "nfr": 2}},
        {"goals": 4, "softgoals": 2, "obstacles": 1},
        5,
        live_items=[_mock_item()],
    )


def _llm_draft() -> SrsNarrativeDraft:
    """Draft LLM simulado con las 8 secciones authored."""
    return SrsNarrativeDraft(
        purpose="El propósito de este SRS es especificar Mi Proyecto.",
        scope="El producto incluye módulos de gestión. Quedan fuera los reportes BI.",
        definitions="- **SRS**: Especificación de Requerimientos de Software.\n- **OAuth**: Open Authorization.",
        references="- ISO/IEC/IEEE 29148:2018\n- ISO/IEC 25010:2011",
        perspective="El sistema se integra con Google OAuth para autenticación.",
        users="- **Administrador**: acceso total, uso diario.\n- **Operador**: acceso limitado, uso semanal.",
        environment="Plataforma web con Docker, FastAPI y SvelteKit.",
        assumptions="Se asume disponibilidad del proveedor OAuth.",
    )


def _patch_retrieval_no_hits(monkeypatch):
    """Parcha retrieval.search para que devuelva [] (sin RAG en tests)."""
    async_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "backend.services.srs_assembler.retrieval.search", async_mock
    )
    return async_mock


# ---------------------------------------------------------------------------
# SrsNarrativeDraft schema
# ---------------------------------------------------------------------------


class TestSrsNarrativeDraftSchema:
    def test_schema_validates_all_8_fields(self):
        draft = _llm_draft()
        assert isinstance(draft, SrsNarrativeDraft)
        fields = SrsNarrativeDraft.model_fields
        expected = {
            "purpose",
            "scope",
            "definitions",
            "references",
            "perspective",
            "users",
            "environment",
            "assumptions",
        }
        assert expected.issubset(set(fields.keys()))

    def test_schema_requires_all_fields(self):
        with pytest.raises(Exception):
            SrsNarrativeDraft(purpose="Solo propósito")

    def test_schema_accepts_long_text(self):
        long_text = "x" * 5000
        draft = SrsNarrativeDraft(
            purpose=long_text,
            scope=long_text,
            definitions=long_text,
            references=long_text,
            perspective=long_text,
            users=long_text,
            environment=long_text,
            assumptions=long_text,
        )
        assert len(draft.purpose) == 5000


# ---------------------------------------------------------------------------
# draft_narrative_llm: LLM sobrescribe placeholders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_narrative_llm_overwrites_placeholders(monkeypatch):
    """El LLM sobrescribe los 8 placeholders 'Editor: completar...'."""
    _patch_retrieval_no_hits(monkeypatch)

    mock_runner = MagicMock()
    mock_runner.ainvoke = AsyncMock(return_value=_llm_draft())
    mock_factory = MagicMock(return_value=mock_runner)
    monkeypatch.setattr(
        "backend.services.srs_assembler.structured_llm", mock_factory
    )

    narrative = _det_narrative()
    result = await draft_narrative_llm(
        narrative,
        project_id=1,
        project_name="Mi Proyecto",
        project_description="Descripción de prueba",
        live_items=[_mock_item()],
        quality_summary={"total_findings": 2, "blockers": 0},
        coverage={"totals": {"live": 5, "functional": 3, "nfr": 2}},
        goals_summary={"goals": 4, "softgoals": 2, "obstacles": 1},
    )

    # Las 8 secciones authored ya no tienen placeholder.
    authored_keys = [
        "intro.purpose",
        "intro.scope",
        "intro.definitions",
        "intro.references",
        "overall.perspective",
        "overall.users",
        "overall.environment",
        "overall.assumptions",
    ]
    for key in authored_keys:
        assert "Editor:" not in result[key], f"{key} aún tiene placeholder"
        assert len(result[key]) > 20, f"{key} está vacío"

    # Contenido específico del mock.
    assert "Mi Proyecto" in result["intro.purpose"]
    assert "Google OAuth" in result["overall.perspective"]
    assert "Administrador" in result["overall.users"]


@pytest.mark.asyncio
async def test_draft_narrative_llm_preserves_deterministics(monkeypatch):
    """Las secciones deterministas (overview, features) se preservan."""
    _patch_retrieval_no_hits(monkeypatch)

    mock_runner = MagicMock()
    mock_runner.ainvoke = AsyncMock(return_value=_llm_draft())
    monkeypatch.setattr(
        "backend.services.srs_assembler.structured_llm",
        MagicMock(return_value=mock_runner),
    )

    narrative = _det_narrative()
    original_overview = narrative["intro.overview"]
    original_features = narrative["overall.features"]

    result = await draft_narrative_llm(
        narrative,
        project_id=1,
        project_name="Mi Proyecto",
        project_description="desc",
        live_items=[_mock_item()],
        quality_summary={},
        coverage={},
        goals_summary={},
    )

    assert result["intro.overview"] == original_overview
    assert result["overall.features"] == original_features
    assert "Sección 1" in result["intro.overview"]
    assert "REQ-AB12" in result["overall.features"]


@pytest.mark.asyncio
async def test_draft_narrative_llm_appends_counts_block(monkeypatch):
    """El bloque de conteos del determinista se reapende a perspective."""
    _patch_retrieval_no_hits(monkeypatch)

    mock_runner = MagicMock()
    mock_runner.ainvoke = AsyncMock(return_value=_llm_draft())
    monkeypatch.setattr(
        "backend.services.srs_assembler.structured_llm",
        MagicMock(return_value=mock_runner),
    )

    result = await draft_narrative_llm(
        _det_narrative(),
        project_id=1,
        project_name="Mi Proyecto",
        project_description="desc",
        live_items=[_mock_item()],
        quality_summary={"total_findings": 2, "blockers": 0},
        coverage={"totals": {"live": 5, "functional": 3, "nfr": 2}},
        goals_summary={"goals": 4, "softgoals": 2, "obstacles": 1},
    )

    perspective = result["overall.perspective"]
    # Texto del LLM primero.
    assert "Google OAuth" in perspective
    # Bloque de conteos después.
    assert "Resumen del alcance especificado" in perspective
    assert "**5**" in perspective  # total live


# ---------------------------------------------------------------------------
# draft_narrative_llm: Fallback on exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_narrative_llm_fallback_on_llm_exception(monkeypatch):
    """Si structured_llm falla, devuelve la narrativa determinista sin cambios."""
    _patch_retrieval_no_hits(monkeypatch)

    monkeypatch.setattr(
        "backend.services.srs_assembler.structured_llm",
        MagicMock(side_effect=RuntimeError("API down")),
    )

    narrative = _det_narrative()
    result = await draft_narrative_llm(
        narrative,
        project_id=1,
        project_name="Mi Proyecto",
        project_description="desc",
        live_items=[],
        quality_summary={},
        coverage={},
        goals_summary={},
    )

    # Debe devolver el mismo dict (o equivalente) con placeholders intactos.
    assert result == narrative
    assert "Editor:" in result["intro.purpose"]
    assert "Editor:" in result["overall.users"]


@pytest.mark.asyncio
async def test_draft_narrative_llm_fallback_on_ainvoke_exception(monkeypatch):
    """Si ainvoke falla, devuelve la narrativa determinista sin cambios."""
    _patch_retrieval_no_hits(monkeypatch)

    mock_runner = MagicMock()
    mock_runner.ainvoke = AsyncMock(side_effect=RuntimeError("network error"))
    monkeypatch.setattr(
        "backend.services.srs_assembler.structured_llm",
        MagicMock(return_value=mock_runner),
    )

    narrative = _det_narrative()
    result = await draft_narrative_llm(
        narrative,
        project_id=1,
        project_name="Mi Proyecto",
        project_description="desc",
        live_items=[],
        quality_summary={},
        coverage={},
        goals_summary={},
    )

    assert result == narrative
    assert "Editor:" in result["intro.scope"]


# ---------------------------------------------------------------------------
# draft_narrative_llm: Reconstrucción de claves legacy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_narrative_llm_rebuilds_legacy_keys(monkeypatch):
    """Las claves legacy (intro, overall) se reconstruyen con el texto del LLM."""
    _patch_retrieval_no_hits(monkeypatch)

    mock_runner = MagicMock()
    mock_runner.ainvoke = AsyncMock(return_value=_llm_draft())
    monkeypatch.setattr(
        "backend.services.srs_assembler.structured_llm",
        MagicMock(return_value=mock_runner),
    )

    result = await draft_narrative_llm(
        _det_narrative(),
        project_id=1,
        project_name="Mi Proyecto",
        project_description="desc",
        live_items=[_mock_item()],
        quality_summary={"total_findings": 0, "blockers": 0},
        coverage={"totals": {"live": 1, "functional": 1, "nfr": 0}},
        goals_summary={"goals": 0, "softgoals": 0, "obstacles": 0},
    )

    # Legacy intro contiene las 5 subsecciones.
    assert "propósito" in result["intro"].lower() or "Propósito" in result["intro"]
    assert "módulos de gestión" in result["intro"]  # scope
    assert "SRS" in result["intro"]  # definitions
    assert "ISO" in result["intro"]  # references
    assert "Sección 1" in result["intro"]  # overview (determinista)

    # Legacy overall contiene las 5 subsecciones.
    assert "Google OAuth" in result["overall"]  # perspective
    assert "REQ-AB12" in result["overall"]  # features (determinista)
    assert "Administrador" in result["overall"]  # users
    assert "Docker" in result["overall"]  # environment
    assert "disponibilidad" in result["overall"]  # assumptions


@pytest.mark.asyncio
async def test_draft_narrative_llm_does_not_mutate_input(monkeypatch):
    """La función no muta el dict de entrada."""
    _patch_retrieval_no_hits(monkeypatch)

    mock_runner = MagicMock()
    mock_runner.ainvoke = AsyncMock(return_value=_llm_draft())
    monkeypatch.setattr(
        "backend.services.srs_assembler.structured_llm",
        MagicMock(return_value=mock_runner),
    )

    narrative = _det_narrative()
    original_purpose = narrative["intro.purpose"]
    original_intro = narrative["intro"]

    await draft_narrative_llm(
        narrative,
        project_id=1,
        project_name="Mi Proyecto",
        project_description="desc",
        live_items=[],
        quality_summary={},
        coverage={},
        goals_summary={},
    )

    assert narrative["intro.purpose"] == original_purpose
    assert narrative["intro"] == original_intro
