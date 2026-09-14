"""Consolidación (afinamiento) del MER: pase anti over-fragmentación.

El MER de Planitrack2.0 produjo 304 entidades para un rango sano de 30-80:
el descubrimiento corre en 41 lotes independientes y el único dedupe era por
nombre exacto. El pase 3b consolida: detección determinista de grupos
candidatos (0 LLM), decisión LLM batcheada de qué grupos fusionar, y merge
determinista (keep absorbe atributos/trazas; relaciones re-apuntadas).
Estos tests fijan:

- Normalización de nombres (singular, acentos, separadores) y detección
  de grupos por nombre equivalente y por solapamiento de trazas.
- El merge determinista: atributos dedupeados, trazas unidas, relaciones
  re-apuntadas a keep, self-loops eliminados, absorbidas fuera.
- Batcheado: N grupos en lotes de _MER_CONSOLIDATION_BATCH_SIZE.
- Degradación graciosa: un lote condenado solo pierde SUS merges.
- Kill-switch: INFOFACT_MER_CONSOLIDATE=0 deja el pase sin efecto.
- La tool generate_mer reporta consolidation en su salida.

No DB / no LLM real: ``structured_llm`` se stubbea por módulo (mismo patrón
de ``test_analysis_batching.py``).
"""
from __future__ import annotations

import pytest

import backend.agents.pipelines.mer_pipeline as mer_mod
from backend.agents.pipelines.mer_pipeline import (
    MerConsolidationGroup,
    MerConsolidationSchema,
    MerEntitySchema,
    MerRelationshipSchema,
    MerResult,
    _candidate_consolidation_groups,
    _consolidate_entities,
    _normalize_entity_name,
)

PROJECT_ID = 4493


class _LLMFactory:
    """Stub de ``structured_llm``: cuenta llamadas, decide desde el texto.

    Al no haber cola, cada llamada devuelve merges para TODOS los grupos
    del lote usando el PRIMER nombre de cada grupo como keep (espeja lo que
    el LLM real devolvería para grupos claros). ``fail_from`` hace fallar
    persistentemente las llamadas a partir de ese ordinal.
    """

    def __init__(self, fail_from: int | None = None):
        self.calls = 0
        self.fail_from = fail_from

    def __call__(self, schema, **kw):
        self._schema = schema
        return self

    async def ainvoke(self, msgs, **kw):
        self.calls += 1
        if self.fail_from is not None and self.calls >= self.fail_from:
            raise RuntimeError("persistent failure (truncated output)")
        human = msgs[1][1] if len(msgs) > 1 else ""
        groups: list[list[str]] = []
        for line in human.splitlines():
            line = line.strip()
            if line.startswith("- [") and line.endswith("]"):
                groups.append(
                    [n.strip() for n in line[3:-1].split(",")]
                )
        merges = [
            MerConsolidationGroup(
                keep=group[0],
                absorb=group[1:],
                reason="duplicado detectado",
            )
            for group in groups
        ]
        return MerConsolidationSchema(merges=merges)


def _ent(name: str, codes: list[str] | None = None, **kw) -> MerEntitySchema:
    return MerEntitySchema(
        name=name, traced_req_codes=codes or [], **kw
    )


def _rel(a: str, b: str, cardinality: str = "1:N") -> MerRelationshipSchema:
    return MerRelationshipSchema(
        from_entity=a, to_entity=b, cardinality=cardinality
    )


# --------------------------------------------------------------------------- #
# Normalización y detección determinista                                       #
# --------------------------------------------------------------------------- #


def test_normalize_entity_name_folds_case_plurals_accents():
    assert _normalize_entity_name("Clientes") == _normalize_entity_name(
        "Cliente"
    )
    assert _normalize_entity_name("Árbol de Gastos") == "arboldegasto"
    assert _normalize_entity_name("Order_Items") == "orderitem"


def test_candidate_groups_by_equivalent_name():
    entities = [
        _ent("Clientes", ["REQ-1"]),
        _ent("Cliente", ["REQ-2"]),
        _ent("Factura", ["REQ-9"]),
    ]
    groups = _candidate_consolidation_groups(entities)
    assert groups == [["Cliente", "Clientes"]]


def test_candidate_groups_by_req_overlap():
    entities = [
        _ent("PedidoItem", ["REQ-1", "REQ-2", "REQ-3"]),
        _ent("LineaPedido", ["REQ-1", "REQ-2", "REQ-4"]),
        _ent("Cliente", ["REQ-8"]),
    ]
    groups = _candidate_consolidation_groups(entities)
    assert [["LineaPedido", "PedidoItem"]] == groups


def test_candidate_groups_empty_when_no_duplicates():
    entities = [
        _ent("Cliente", ["REQ-1", "REQ-2"]),
        _ent("Factura", ["REQ-3", "REQ-4"]),
    ]
    assert _candidate_consolidation_groups(entities) == []


# --------------------------------------------------------------------------- #
# Merge determinista                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_consolidation_merges_attributes_and_relationships(monkeypatch):
    factory = _LLMFactory()
    monkeypatch.setattr(mer_mod, "structured_llm", factory)

    entities = [
        _ent("Clientes", ["REQ-1"], aggregate_root=True),
        _ent("Cliente", ["REQ-2"]),
        _ent("Factura", ["REQ-9"]),
    ]
    relationships = [
        _rel("Clientes", "Factura"),
        _rel("Factura", "Cliente"),
    ]

    keepers, new_rels, merges = await _consolidate_entities(
        entities, relationships
    )
    names = {e.name for e in keepers}
    # El grupo ordenado es ['Cliente', 'Clientes'] => keep=Cliente.
    assert names == {"Cliente", "Factura"}
    assert len(merges) == 1
    assert merges[0]["keep"] == "Cliente"

    # Trazas unidas en el keeper.
    keep = next(e for e in keepers if e.name == "Cliente")
    assert set(keep.traced_req_codes) == {"REQ-1", "REQ-2"}

    # Relaciones re-apuntadas: ambas son Clientes<->Factura pero en
    # direcciones distintas, ambas sobreviven; ninguna cita absorbidas.
    for rel in new_rels:
        assert rel.from_entity in names and rel.to_entity in names


@pytest.mark.asyncio
async def test_consolidation_drops_self_loops(monkeypatch):
    factory = _LLMFactory()
    monkeypatch.setattr(mer_mod, "structured_llm", factory)

    entities = [
        _ent("Order", ["REQ-1"]),
        _ent("Orders", ["REQ-2"]),
    ]
    relationships = [
        _rel("Order", "Orders"),
        _rel("Order", "Order"),
    ]
    keepers, new_rels, merges = await _consolidate_entities(
        entities, relationships
    )
    assert {e.name for e in keepers} == {"Order"}
    assert len(merges) == 1
    # La relación Order->Orders se volvió self-loop y se descartó; la
    # self-loop original también.
    assert new_rels == []


# --------------------------------------------------------------------------- #
# Batcheado y degradación graciosa                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_consolidation_is_batched(monkeypatch):
    factory = _LLMFactory()
    monkeypatch.setattr(mer_mod, "structured_llm", factory)

    # 8 grupos (un par TablaN/TablaNs por grupo) con batch 2 => 4 llamadas.
    entities = []
    for i in range(8):
        entities.append(_ent(f"Tabla{i}", [f"REQ-{i}"]))
        entities.append(_ent(f"Tabla{i}s", [f"REQ-{i}"]))
    monkeypatch.setattr(mer_mod, "_MER_CONSOLIDATION_BATCH_SIZE", 2)

    await _consolidate_entities(entities, [])
    assert factory.calls == 4


@pytest.mark.asyncio
async def test_consolidation_failed_batch_degrades(monkeypatch):
    factory = _LLMFactory(fail_from=2)  # lotes 2+ fallan persistentes
    monkeypatch.setattr(mer_mod, "structured_llm", factory)

    entities = []
    for i in range(8):
        entities.append(_ent(f"Tabla{i}", [f"REQ-{i}"]))
        entities.append(_ent(f"Tabla{i}s", [f"REQ-{i}"]))
    monkeypatch.setattr(mer_mod, "_MER_CONSOLIDATION_BATCH_SIZE", 2)

    keepers, _rels, merges = await _consolidate_entities(entities, [])
    # Solo sobreviven los merges del lote 1 (2 grupos => 2 entidades menos).
    assert len(merges) == 2
    assert len(keepers) == len(entities) - 2


@pytest.mark.asyncio
async def test_consolidation_noop_when_no_groups():
    entities = [_ent("Cliente", ["REQ-1"]), _ent("Factura", ["REQ-2"])]
    keepers, rels, merges = await _consolidate_entities(entities, [_rel("Cliente", "Factura")])
    assert len(keepers) == 2
    assert merges == []
    assert len(rels) == 1


# --------------------------------------------------------------------------- #
# Kill-switch y stats                                                          #
# --------------------------------------------------------------------------- #


def test_kill_switch_disabled(monkeypatch):
    monkeypatch.setattr(mer_mod, "_MER_CONSOLIDATE_ENABLED", False)
    assert mer_mod._MER_CONSOLIDATE_ENABLED is False


@pytest.mark.asyncio
async def test_generate_mer_stats_include_consolidation(monkeypatch):
    """generate_mer puebla stats['consolidation'] con before/after/merges."""
    factory = _LLMFactory()
    monkeypatch.setattr(mer_mod, "structured_llm", factory)

    class _Item:
        def __init__(self, code: str):
            self.code = code
            self.statement = f"El sistema debe gestionar {code}"
            self.type = type("T", (), {"value": "functional"})()
            self.status = type("S", (), {"value": "validated"})()
            self.source = None
            self.acceptance_criteria = []

    items = [_Item(f"REQ-{i:04d}") for i in range(4)]

    # Passthrough: los pases devuelven entidades duplicadas por nombre
    # normalizado; la consolidación las fusiona y el stat queda poblado.
    class _Router:
        def __init__(self):
            self.n = 0

        def __call__(self, schema, **kw):
            self._schema = schema
            return self

        async def ainvoke(self, msgs, **kw):
            self.n += 1
            schema = self._schema
            if schema is mer_mod.EntityDiscoverySchema:
                return mer_mod.EntityDiscoverySchema(
                    entities=[
                        mer_mod.EntityCandidate(
                            name="Cliente", traced_req_codes=["REQ-0000"]
                        ),
                        mer_mod.EntityCandidate(
                            name="Clientes", traced_req_codes=["REQ-0001"]
                        ),
                    ]
                )
            if schema is mer_mod.MerDetailSchema:
                return mer_mod.MerDetailSchema(
                    entities=[
                        mer_mod.MerEntityDetail(
                            name="Cliente",
                            attributes=[
                                mer_mod.MerAttribute(
                                    name="clienteId", type="uuid", is_key=True
                                )
                            ],
                        ),
                        mer_mod.MerEntityDetail(
                            name="Clientes",
                            attributes=[
                                mer_mod.MerAttribute(
                                    name="nombre", type="string"
                                )
                            ],
                        ),
                    ],
                    relationships=[],
                )
            if schema is mer_mod.MerCritiqueSchema:
                return mer_mod.MerCritiqueSchema(findings=[])
            if schema is mer_mod.MerConsolidationSchema:
                return MerConsolidationSchema(
                    merges=[
                        MerConsolidationGroup(
                            keep="Cliente", absorb=["Clientes"], reason="dup"
                        )
                    ]
                )
            return mer_mod.MerDescriptionSchema(description="d")

    monkeypatch.setattr(mer_mod, "structured_llm", _Router())
    # Bajar el gate de escala para que la consolidación corra con 2 entidades.
    monkeypatch.setattr(mer_mod, "_MER_BATCH_SIZE", 1)

    result: MerResult = await mer_mod.generate_mer(
        items, enable_critique=False
    )
    assert {e.name for e in result.entities} == {"Cliente"}
    cons = result.stats.get("consolidation") or {}
    assert cons.get("before") == 2
    assert cons.get("after") == 1
    assert len(cons.get("merges") or []) == 1
