"""Tests del Pass 3c: higiene de contextos acotados del MER.

Cubre la normalización ortográfica determinista (0 LLM), el mapeo LLM de
consolidación (stubbeado) con resolución de cadenas, y la degradación
grácil cuando el mapeo falla o está desactivado.
"""

import pytest

from backend.agents.pipelines import mer_pipeline as mer_mod
from backend.agents.pipelines.mer_pipeline import (
    MerContextConsolidationSchema,
    MerContextMapping,
    MerEntitySchema,
    consolidate_bounded_contexts,
    consolidate_context_labels,
    normalize_context_key,
)


def _ent(name: str, bc: str = "") -> MerEntitySchema:
    return MerEntitySchema(name=name, bounded_context=bc)


# --- normalización ----------------------------------------------------------


def test_normalize_context_key_collapses_variants():
    assert normalize_context_key("Gestión de Terreno") == "gestionterreno"
    assert normalize_context_key("Gestion de Terreno") == "gestionterreno"
    assert normalize_context_key("GestionTerreno") == "gestionterreno"
    assert normalize_context_key("Venta en Campo") == "ventacampo"
    assert normalize_context_key("VentaEnCampo") == "ventacampo"


def test_normalize_context_key_empty():
    assert normalize_context_key("") == ""
    assert normalize_context_key(None) == ""
    assert normalize_context_key("   ") == ""


def test_normalize_context_key_keeps_distinct_domains():
    # Dominios de negocio distintos NO deben colisionar.
    assert normalize_context_key("Logística Inversa") != normalize_context_key(
        "Logística"
    )
    assert normalize_context_key("Ruteo") != normalize_context_key("Navegación")


# --- normalización aplicada a entidades --------------------------------------


def test_consolidate_labels_merges_orthographic_variants():
    ents = [
        _ent("A", "Gestión de Terreno"),
        _ent("B", "Gestion de Terreno"),
        _ent("C", "GestionTerreno"),
        _ent("D", "Facturacion"),
    ]
    consolidate_context_labels(ents)
    labels = {e.name: e.bounded_context for e in ents}
    # Empate 1-1-1 en frecuencia y espacios: desempata el orden alfabético
    # (determinista) -> la variante sin acento.
    assert labels["A"] == labels["B"] == labels["C"] == "Gestion de Terreno"
    assert labels["D"] == "Facturacion"


def test_consolidate_labels_prefers_most_frequent():
    # Variantes que SÍ colapsan ortográficamente (solo difieren en
    # mayusculas/espacios): gana la etiqueta más frecuente.
    ents = [
        _ent("A", "Comunicaciones"),
        _ent("B", "Comunicaciones"),
        _ent("C", "comunicaciones"),
    ]
    consolidate_context_labels(ents)
    # 2 votos contra 1: la variante más frecuente gana sin ambigüedad.
    assert all(e.bounded_context == "Comunicaciones" for e in ents)


def test_consolidate_labels_keeps_empty_context():
    ents = [_ent("A", ""), _ent("B", "   ")]
    consolidate_context_labels(ents)
    assert [e.bounded_context for e in ents] == ["", ""]


# --- pass completo (normalización + mapeo LLM stubbeado) ----------------------


@pytest.mark.asyncio
async def test_contexts_below_target_skips_llm(monkeypatch):
    async def _boom(*args, **kwargs):
        raise AssertionError("no debe llamar al LLM por debajo del techo")

    monkeypatch.setattr(mer_mod, "invoke_structured_resilient", _boom)
    ents = [_ent("A", "Ventas"), _ent("B", "Compras")]
    _, stats = await consolidate_bounded_contexts(ents)
    assert stats == {
        "raw": 2,
        "normalized": 2,
        "llm_mapped": False,
        "final": 2,
    }


@pytest.mark.asyncio
async def test_contexts_kill_switch_skips_llm(monkeypatch):
    async def _boom(*args, **kwargs):
        raise AssertionError("kill-switch activo: no debe llamar al LLM")

    monkeypatch.setattr(mer_mod, "invoke_structured_resilient", _boom)
    monkeypatch.setattr(mer_mod, "_MER_CONTEXT_CONSOLIDATE_ENABLED", False)
    monkeypatch.setattr(mer_mod, "_MER_CONTEXT_TARGET_MAX", 1)
    ents = [_ent("A", "Ventas"), _ent("B", "Compras")]
    _, stats = await consolidate_bounded_contexts(ents)
    assert stats["llm_mapped"] is False
    assert stats["final"] == 2


@pytest.mark.asyncio
async def test_contexts_applies_llm_mapping(monkeypatch):
    async def _fake_invoke(fn, msgs, **kwargs):
        return MerContextConsolidationSchema(
            mappings=[
                MerContextMapping(raw="Billing", canonical="Facturación"),
            ]
        )

    monkeypatch.setattr(mer_mod, "invoke_structured_resilient", _fake_invoke)
    monkeypatch.setattr(mer_mod, "_MER_CONTEXT_TARGET_MAX", 2)
    ents = [
        _ent("A", "Ventas"),
        _ent("B", "Facturación"),
        _ent("C", "Billing"),
    ]
    _, stats = await consolidate_bounded_contexts(ents)
    assert stats["llm_mapped"] is True
    assert stats["final"] == 2
    # El canónico debe existir en el modelo; «Billing» cae a «Facturación».
    labels = {e.bounded_context for e in ents}
    assert labels == {"Ventas", "Facturación"}


@pytest.mark.asyncio
async def test_contexts_resolves_mapping_chains(monkeypatch):
    # A->B y B->C deben resolver a C (fixpoint), no dejar A en B.
    async def _fake_invoke(fn, msgs, **kwargs):
        return MerContextConsolidationSchema(
            mappings=[
                MerContextMapping(raw="Aa", canonical="Bb"),
                MerContextMapping(raw="Bb", canonical="Cc"),
            ]
        )

    monkeypatch.setattr(mer_mod, "invoke_structured_resilient", _fake_invoke)
    monkeypatch.setattr(mer_mod, "_MER_CONTEXT_TARGET_MAX", 2)
    ents = [_ent("E1", "Aa"), _ent("E2", "Bb"), _ent("E3", "Cc")]
    _, stats = await consolidate_bounded_contexts(ents)
    assert stats["final"] == 1
    assert all(e.bounded_context == "Cc" for e in ents)


@pytest.mark.asyncio
async def test_contexts_degrades_on_llm_failure(monkeypatch):
    async def _fail(*args, **kwargs):
        raise RuntimeError("upstream caido")

    monkeypatch.setattr(mer_mod, "invoke_structured_resilient", _fail)
    monkeypatch.setattr(mer_mod, "_MER_CONTEXT_TARGET_MAX", 2)
    ents = [
        _ent("A", "Gestión de Terreno"),
        _ent("B", "GestionTerreno"),
        _ent("C", "Ruteo"),
    ]
    _, stats = await consolidate_bounded_contexts(ents)
    # Degradación: quedan las etiquetas ya normalizadas ortográficamente
    # (las dos variantes de Terreno colapsan en una; Ruteo sigue aparte).
    assert stats["llm_mapped"] is False
    assert stats["normalized"] == 2
    labels = {e.bounded_context for e in ents}
    assert labels == {"Gestión de Terreno", "Ruteo"}
