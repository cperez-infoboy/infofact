"""Tests for the MER pipeline: deterministic parts + multi-pass logic.

Pins the Mermaid erDiagram renderer (entity blocks, PK markers, relationship
cardinality notation), the cardinality normalizer, the empty-input fast path,
the deterministic validation (Pass 4), and multi-pass orchestration with
mocked LLM calls. The real LLM-dependent path is exercised via smoke scripts.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.pipelines.mer_pipeline import (
    EntityCandidate,
    EntityDiscoverySchema,
    MerAttribute,
    MerEntitySchema,
    MerRelationshipSchema,
    MerResult,
    MerDetailSchema,
    MerCritiqueSchema,
    _DETAIL_PROMPT,
    _normalize_cardinality,
    _reconcile_relationship_endpoints,
    _render_mermaid,
    _validate_mer,
    generate_mer,
    render_data_dictionary,
    resolve_entity_reference,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_entity(
    name: str = "User",
    attributes: list[MerAttribute] | None = None,
) -> MerEntitySchema:
    return MerEntitySchema(
        name=name,
        attributes=attributes
        or [MerAttribute(name="id", type="uuid", is_key=True)],
    )


def _make_relationship(
    from_entity: str = "User",
    to_entity: str = "Order",
    cardinality: str = "1:N",
    label: str = "has",
) -> MerRelationshipSchema:
    return MerRelationshipSchema(
        from_entity=from_entity,
        to_entity=to_entity,
        cardinality=cardinality,
        label=label,
    )


# --------------------------------------------------------------------------- #
# _render_mermaid                                                             #
# --------------------------------------------------------------------------- #


def test_render_mermaid_valid_erdiagram():
    entities = [
        _make_entity("User"),
        _make_entity(
            "Order",
            attributes=[
                MerAttribute(name="id", type="uuid", is_key=True),
                MerAttribute(name="total", type="float"),
            ],
        ),
    ]
    relationships = [_make_relationship(cardinality="1:N")]

    out = _render_mermaid(entities, relationships)

    lines = out.splitlines()
    assert lines[0] == "erDiagram"
    # Entity names appear uppercased.
    assert "USER {" in out
    assert "ORDER {" in out
    # PK attribute marker on the key attribute.
    assert "uuid id PK" in out
    # Non-key attribute has no PK suffix.
    assert "float total" in out
    # Relationship line with 1:N cardinality notation.
    assert 'USER ||--o{ ORDER : "has"' in out


def test_render_mermaid_empty_entities():
    out = _render_mermaid([], [])
    assert out == "erDiagram"


# --------------------------------------------------------------------------- #
# Cardinality notation                                                        #
# --------------------------------------------------------------------------- #


def test_mermaid_cardinality_notation():
    """Verify canonical cardinalities map to the right Mermaid notation."""
    entities = [_make_entity("A"), _make_entity("B")]

    one_to_one = _render_mermaid(
        entities,
        [_make_relationship("A", "B", cardinality="1:1", label="one-to-one")],
    )
    assert "||--||" in one_to_one

    one_to_many = _render_mermaid(
        entities,
        [_make_relationship("A", "B", cardinality="1:N", label="one-to-many")],
    )
    assert "||--o{" in one_to_many

    many_to_many = _render_mermaid(
        entities,
        [_make_relationship("A", "B", cardinality="N:M", label="many-to-many")],
    )
    assert "}o--o{" in many_to_many


# --------------------------------------------------------------------------- #
# _normalize_cardinality                                                      #
# --------------------------------------------------------------------------- #


def test_normalize_cardinality_canonical_forms():
    # Already-canonical forms pass through unchanged.
    assert _normalize_cardinality("1:1") == "1:1"
    assert _normalize_cardinality("1:N") == "1:N"
    assert _normalize_cardinality("N:M") == "N:M"


def test_normalize_cardinality_common_variants():
    # Hyphenated forms.
    assert _normalize_cardinality("1-1") == "1:1"
    assert _normalize_cardinality("1-N") == "1:N"
    assert _normalize_cardinality("N-M") == "N:M"
    # M:N variant maps to N:M.
    assert _normalize_cardinality("M:N") == "N:M"
    # Word forms.
    assert _normalize_cardinality("one-to-one") == "1:1"
    assert _normalize_cardinality("one-to-many") == "1:N"
    assert _normalize_cardinality("many-to-many") == "N:M"


def test_normalize_cardinality_whitespace_and_case():
    assert _normalize_cardinality("  1 : 1  ") == "1:1"
    assert _normalize_cardinality("n:m") == "N:M"


def test_normalize_cardinality_unknown_defaults_to_one_to_many():
    assert _normalize_cardinality("zero-or-one") == "1:N"
    assert _normalize_cardinality("???") == "1:N"


# --------------------------------------------------------------------------- #
# _validate_mer (Pass 4)                                                      #
# --------------------------------------------------------------------------- #


def test_validate_mer_missing_pk():
    """Entity without any PK attribute -> warning."""
    entities = [
        MerEntitySchema(
            name="Order",
            attributes=[
                MerAttribute(name="total", type="float", is_key=False),
            ],
        ),
    ]
    warnings = _validate_mer(entities, [])
    assert any("sin PK" in w for w in warnings)


def test_validate_mer_unknown_entity_in_relationship():
    """Relationship references non-existent entity -> warning."""
    entities = [_make_entity("User")]
    rels = [
        _make_relationship(
            from_entity="User", to_entity="NonExistent", cardinality="1:N"
        )
    ]
    warnings = _validate_mer(entities, rels)
    assert any("inexistente" in w and "NonExistent" in w for w in warnings)


def test_validate_mer_duplicate_entities():
    """Two entities with same name (different case) -> warning."""
    entities = [
        _make_entity("Customer"),
        _make_entity("customer"),
    ]
    warnings = _validate_mer(entities, [])
    assert any("duplicada" in w.lower() for w in warnings)


def test_validate_mer_invalid_cardinality():
    """Cardinality 'foo' -> warning."""
    entities = [_make_entity("A"), _make_entity("B")]
    rels = [
        MerRelationshipSchema(
            from_entity="A", to_entity="B", cardinality="foo", label="rel"
        )
    ]
    warnings = _validate_mer(entities, rels)
    assert any("invalida" in w.lower() and "foo" in w for w in warnings)


def test_validate_mer_clean_model():
    """Valid model with PKs, valid cardinalities, existing refs -> no warnings."""
    entities = [_make_entity("User"), _make_entity("Order")]
    rels = [_make_relationship(cardinality="1:N")]
    warnings = _validate_mer(entities, rels)
    assert warnings == []


# --------------------------------------------------------------------------- #
# _gap_pass (Pass 2) with mocked LLM                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_gap_pass_no_uncovered_codes_returns_empty():
    """When all input codes are covered by candidates, gap pass returns []."""
    # Use a mock to ensure the LLM is NOT called.
    with patch(
        "backend.agents.pipelines.mer_pipeline.structured_llm"
    ) as mock_llm_factory:
        # Build dummy items with codes.
        from backend.models.requirement import ReqType

        class _FakeItem:
            def __init__(self, code, statement, type_=ReqType.FUNCTIONAL):
                self.code = code
                self.statement = statement
                self.type = type_
                self.source = None

        items = [
            _FakeItem("REQ-001", "The system shall do X"),
            _FakeItem("REQ-002", "The system shall do Y"),
        ]
        candidates = [
            EntityCandidate(
                name="X",
                traced_req_codes=["REQ-001", "REQ-002"],
            )
        ]

        from backend.agents.pipelines.mer_pipeline import _gap_pass

        result = await _gap_pass(items, candidates, "Proj", "")
        assert result == []
        # LLM was never called.
        mock_llm_factory.assert_not_called()


@pytest.mark.asyncio
async def test_gap_pass_detection():
    """Items with codes not covered by candidates trigger gap pass with LLM."""
    from backend.agents.pipelines.mer_pipeline import _gap_pass
    from backend.models.requirement import ReqType

    class _FakeItem:
        def __init__(self, code, statement, type_=ReqType.FUNCTIONAL):
            self.code = code
            self.statement = statement
            self.type = type_
            self.source = None

    items = [
        _FakeItem("REQ-001", "The system shall do X"),
        _FakeItem("REQ-002", "The system shall manage invoices"),
    ]
    # REQ-001 is covered; REQ-002 is not.
    candidates = [
        EntityCandidate(name="X", traced_req_codes=["REQ-001"]),
    ]

    # Mock structured_llm to return an EntityDiscoverySchema with one new entity.
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=EntityDiscoverySchema(
            entities=[
                EntityCandidate(
                    name="Invoice",
                    traced_req_codes=["REQ-002"],
                )
            ]
        )
    )
    with patch(
        "backend.agents.pipelines.mer_pipeline.structured_llm",
        return_value=mock_llm,
    ):
        result = await _gap_pass(items, candidates, "Proj", "")

    assert len(result) == 1
    assert result[0].name == "Invoice"
    assert result[0].traced_req_codes == ["REQ-002"]


# --------------------------------------------------------------------------- #
# generate_mer stats (mocked multi-pass)                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_generate_mer_stats_include_validation_and_critique():
    """generate_mer stats dict has validation_warnings and critique_findings."""
    from backend.models.requirement import ReqType

    class _FakeItem:
        def __init__(self, code, statement, type_=ReqType.FUNCTIONAL):
            self.code = code
            self.statement = statement
            self.type = type_
            self.source = None

    items = [
        _FakeItem("REQ-001", "The system shall manage users"),
        _FakeItem("REQ-002", "The system shall manage orders"),
    ]

    # Mock each pass function.
    candidates = [
        EntityCandidate(name="User", traced_req_codes=["REQ-001"]),
        EntityCandidate(name="Order", traced_req_codes=["REQ-002"]),
    ]

    entities_full = [
        MerEntitySchema(
            name="User",
            attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
            traced_req_codes=["REQ-001"],
        ),
        MerEntitySchema(
            name="Order",
            attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
            traced_req_codes=["REQ-002"],
        ),
    ]
    relationships = [
        MerRelationshipSchema(
            from_entity="User", to_entity="Order", cardinality="1:N", label="has"
        )
    ]

    with patch(
        "backend.agents.pipelines.mer_pipeline._discover_entities",
        return_value=candidates,
    ), patch(
        "backend.agents.pipelines.mer_pipeline._gap_pass",
        return_value=[],
    ), patch(
        "backend.agents.pipelines.mer_pipeline._detail_entities_relationships",
        return_value=(entities_full, relationships),
    ), patch(
        "backend.agents.pipelines.mer_pipeline._critique_mer",
        return_value=[],
    ):
        result = await generate_mer(items, project_name="Proj")

    assert "validation_warnings" in result.stats
    assert "critique_findings" in result.stats
    assert "gap_pass_found" in result.stats
    assert "req_coverage" in result.stats
    assert result.stats["validation_warnings"] == []  # clean model
    assert result.stats["entities"] == 2
    assert result.stats["relationships"] == 1


# --------------------------------------------------------------------------- #
# generate_mer empty-input fast path                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_generate_mer_empty_items_returns_empty():
    result = await generate_mer([])
    assert isinstance(result, MerResult)
    assert result.entities == []
    assert result.relationships == []
    assert result.mermaid == ""
    assert result.stats["input"] == 0


# --------------------------------------------------------------------------- #
# generate_mer disable_critique skips Pass 5                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_generate_mer_disable_critique_skips_pass5():
    """When enable_critique=False, _critique_mer is never called."""
    from backend.models.requirement import ReqType

    class _FakeItem:
        def __init__(self, code, statement, type_=ReqType.FUNCTIONAL):
            self.code = code
            self.statement = statement
            self.type = type_
            self.source = None

    items = [_FakeItem("REQ-001", "The system shall manage users")]

    candidates = [EntityCandidate(name="User", traced_req_codes=["REQ-001"])]
    entities_full = [
        MerEntitySchema(
            name="User",
            attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
            traced_req_codes=["REQ-001"],
        )
    ]

    with patch(
        "backend.agents.pipelines.mer_pipeline._discover_entities",
        return_value=candidates,
    ), patch(
        "backend.agents.pipelines.mer_pipeline._gap_pass",
        return_value=[],
    ), patch(
        "backend.agents.pipelines.mer_pipeline._detail_entities_relationships",
        return_value=(entities_full, []),
    ), patch(
        "backend.agents.pipelines.mer_pipeline._critique_mer"
    ) as mock_critique:
        result = await generate_mer(items, enable_critique=False)

    mock_critique.assert_not_called()
    assert result.stats["critique_findings"] == []
    assert result.stats["critique_blockers"] == 0


# --------------------------------------------------------------------------- #
# MerAttribute.length + prompt del pass de detalle                            #
# --------------------------------------------------------------------------- #


def test_mer_attribute_length_defaults_empty():
    """El campo nuevo length es opcional (compatibilidad con versiones viejas)."""
    attr = MerAttribute(name="id", type="uuid", is_key=True)
    assert attr.length == ""
    # Un dict persistido sin length también valida (regresión).
    legacy = MerAttribute.model_validate(
        {"name": "status", "type": "string", "required": True}
    )
    assert legacy.length == ""


def test_detail_prompt_mentions_length_and_business_description():
    """El prompt del pass 3 pide largo con regla de tipos y descripción de negocio."""
    assert "length" in _DETAIL_PROMPT
    assert "'10,2'" in _DETAIL_PROMPT  # precisión,escala para float
    assert "120" in _DETAIL_PROMPT  # techo de la frase de negocio
    assert "enum" in _DETAIL_PROMPT  # valores permitidos dentro de la descripción


# --------------------------------------------------------------------------- #
# render_data_dictionary (determinista, 0 LLM)                                #
# --------------------------------------------------------------------------- #


def _dictionary_fixture():
    entities = [
        MerEntitySchema(
            name="Order",
            description="Pedido confirmado del cliente.",
            aggregate_root=True,
            bounded_context="Sales",
            attributes=[
                MerAttribute(
                    name="id",
                    type="uuid",
                    is_key=True,
                    description="Identificador del pedido.",
                ),
                MerAttribute(
                    name="customer",
                    type="Customer",
                    description="Cliente que realiza el pedido.",
                ),
                MerAttribute(
                    name="status",
                    type="string",
                    length="16",
                    description="Estado: activa, cancelada, cumplida.",
                ),
                MerAttribute(
                    name="total",
                    type="float",
                    length="10,2",
                    required=False,
                    description="Monto total en USD.",
                ),
            ],
        ),
        MerEntitySchema(
            name="Customer",
            description="Cliente registrado.",
            attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
        ),
    ]
    relationships = [
        MerRelationshipSchema(
            from_entity="Customer",
            to_entity="Order",
            cardinality="1:N",
            label="places",
            description="Un cliente realiza muchos pedidos.",
        )
    ]
    return entities, relationships


def test_render_data_dictionary_structure():
    """Índice de entidades, sección por entidad y tabla de campos de 6 columnas."""
    entities, relationships = _dictionary_fixture()
    out = render_data_dictionary(entities, relationships)

    assert out.startswith("## Diccionario de datos")
    # Catálogo-índice con las dos entidades.
    assert "| Entidad | Descripción | Campos |" in out
    assert "| Order |" in out and "| Customer |" in out
    # Encabezado de sección de entidad con badges.
    assert "### Order (aggregate root; contexto: Sales)" in out
    assert "### Customer" in out
    # Explicación de la entidad en prosa.
    assert "Pedido confirmado del cliente." in out
    # Tabla de campos con las columnas del diccionario.
    assert "| Campo | Tipo | Largo | Obligatorio | Clave | Descripción |" in out
    assert "| status | string | 16 | sí |  | Estado: activa, cancelada, cumplida. |" in out


def test_render_data_dictionary_pk_first_and_fk_reference():
    """Orden PK → FK → resto, y el FK referencia a la entidad destino."""
    entities, _ = _dictionary_fixture()
    out = render_data_dictionary(entities, [])

    order_section = out.split("### Order", 1)[1].split("### Customer", 1)[0]
    id_pos = order_section.index("| id |")
    customer_pos = order_section.index("| customer |")
    status_pos = order_section.index("| status |")
    assert id_pos < customer_pos < status_pos
    assert "| customer | Customer |  | sí | FK → Customer |" in order_section
    # total es opcional: la columna Obligatorio lo refleja.
    assert "| total | float | 10,2 | no |  |" in order_section


def test_render_data_dictionary_tolerates_legacy_attributes():
    """Atributos sin length y entidad sin atributos no rompen el render."""
    entities = [
        MerEntitySchema(
            name="Empty",
            attributes=[],
        ),
        MerEntitySchema(
            name="Legacy",
            attributes=[
                MerAttribute.model_validate(
                    {"name": "code", "type": "string", "is_key": True}
                ),
            ],
        ),
    ]
    out = render_data_dictionary(entities, [])
    assert "_Sin atributos modelados._" in out
    assert "| code | string |  | sí | PK |  |" in out


def test_render_data_dictionary_relationships_table():
    """La tabla final de relaciones con cardinalidad y verbo."""
    entities, relationships = _dictionary_fixture()
    out = render_data_dictionary(entities, relationships)
    assert "### Relaciones" in out
    assert "| Origen | Cardinalidad | Destino | Verbo | Descripción |" in out
    assert "| Customer | 1:N | Order | places | Un cliente realiza muchos pedidos. |" in out


@pytest.mark.asyncio
async def test_generate_mer_stats_include_data_dictionary():
    """stats["data_dictionary"] viaja en el resultado del pipeline."""
    from backend.models.requirement import ReqType

    class _FakeItem:
        def __init__(self, code, statement, type_=ReqType.FUNCTIONAL):
            self.code = code
            self.statement = statement
            self.type = type_
            self.source = None

    items = [_FakeItem("REQ-001", "The system shall manage users")]
    candidates = [EntityCandidate(name="User", traced_req_codes=["REQ-001"])]
    entities_full = [
        MerEntitySchema(
            name="User",
            attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
            traced_req_codes=["REQ-001"],
        )
    ]

    with patch(
        "backend.agents.pipelines.mer_pipeline._discover_entities",
        return_value=candidates,
    ), patch(
        "backend.agents.pipelines.mer_pipeline._gap_pass",
        return_value=[],
    ), patch(
        "backend.agents.pipelines.mer_pipeline._detail_entities_relationships",
        return_value=(entities_full, []),
    ), patch(
        "backend.agents.pipelines.mer_pipeline._critique_mer",
        return_value=[],
    ):
        result = await generate_mer(items, project_name="Proj")

    dd = result.stats["data_dictionary"]
    assert dd.startswith("## Diccionario de datos")
    assert "### User" in dd


# --------------------------------------------------------------------------- #
# resolve_entity_reference + Pass 3d (reconciliación de extremos)             #
# --------------------------------------------------------------------------- #


def test_resolve_entity_reference_match_levels():
    """Exacto case-insensitive > normalizado > compacto sin stopwords."""
    names = ["Configuración de Conexión", "Usuario"]
    # Nivel 1: exacto y case-insensitive.
    assert (
        resolve_entity_reference("Configuración de Conexión", names)
        == "Configuración de Conexión"
    )
    assert resolve_entity_reference("usuario", names) == "Usuario"
    # Nivel 3: compacto sin stopwords/acentos (el caso del nodo fantasma).
    assert (
        resolve_entity_reference("ConfiguracionConexion", names)
        == "Configuración de Conexión"
    )
    # Sin match y referencia vacía.
    assert resolve_entity_reference("ServidorDeCorreo", names) is None
    assert resolve_entity_reference("", names) is None


def test_resolve_entity_reference_ambiguous_is_none():
    """Un nivel con 2+ candidatos se rechaza: nunca adivinar."""
    names = ["Configuración de Conexión", "Configuración y Conexión"]
    assert resolve_entity_reference("ConfiguracionConexion", names) is None


def test_reconcile_relationship_endpoints_repunts_and_drops():
    """Pass 3d: re-apunta variantes y descarta aristas sin resolución."""
    entities = [
        _make_entity("Configuración de Conexión"),
        _make_entity("Usuario"),
    ]
    relationships = [
        _make_relationship(
            from_entity="ConfiguracionConexion",
            to_entity="Usuario",
            label="configura",
        ),
        _make_relationship(
            from_entity="Usuario",
            to_entity="Fantasma",
            label="ve",
        ),
    ]

    kept, stats, warnings = _reconcile_relationship_endpoints(
        entities, relationships
    )

    assert len(kept) == 1
    assert kept[0].from_entity == "Configuración de Conexión"
    assert kept[0].to_entity == "Usuario"
    assert stats["reconciled_count"] == 1
    assert stats["reconciled"][0] == {
        "side": "from",
        "was": "ConfiguracionConexion",
        "now": "Configuración de Conexión",
    }
    assert stats["dropped_count"] == 1
    assert any("Fantasma" in w for w in warnings)


def test_reconcile_relationship_endpoints_dedupes_converging_edges():
    """Aristas idénticas (o que convergen tras el re-apuntado) no se duplican."""
    entities = [_make_entity("Usuario"), _make_entity("Orden")]
    relationships = [
        _make_relationship("Usuario", "Orden", label="emite"),
        _make_relationship("Usuario", "Orden", label="emite"),
    ]

    kept, stats, warnings = _reconcile_relationship_endpoints(
        entities, relationships
    )

    assert len(kept) == 1
    assert stats["deduped_count"] == 1
    assert stats["reconciled_count"] == 0
    assert warnings == []


def test_render_mermaid_skips_edges_with_unknown_endpoints():
    """El renderer nunca emite una arista con extremo no declarado."""
    entities = [_make_entity("Usuario")]
    relationships = [
        _make_relationship("Usuario", "ConfiguracionConexion", label="usa"),
    ]

    out = _render_mermaid(entities, relationships)

    assert "CONFIGURACIONCONEXION" not in out
    assert "USUARIO {" in out


@pytest.mark.asyncio
async def test_generate_mer_reconciles_and_reports():
    """Pass 3d integrado: variante re-apuntada, fantasma descartado, stats."""
    from backend.models.requirement import ReqType

    class _FakeItem:
        def __init__(self, code, statement, type_=ReqType.FUNCTIONAL):
            self.code = code
            self.statement = statement
            self.type = type_
            self.source = None

    items = [
        _FakeItem("REQ-001", "The system shall manage connections"),
        _FakeItem("REQ-002", "The system shall manage users"),
    ]
    candidates = [
        EntityCandidate(name="Configuración de Conexión", traced_req_codes=["REQ-001"]),
        EntityCandidate(name="Usuario", traced_req_codes=["REQ-002"]),
    ]
    entities_full = [
        MerEntitySchema(
            name="Configuración de Conexión",
            attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
            traced_req_codes=["REQ-001"],
        ),
        MerEntitySchema(
            name="Usuario",
            attributes=[MerAttribute(name="id", type="uuid", is_key=True)],
            traced_req_codes=["REQ-002"],
        ),
    ]
    relationships = [
        # Variante ortográfica del extremo origen (el caso del fantasma).
        MerRelationshipSchema(
            from_entity="ConfiguracionConexion",
            to_entity="Usuario",
            cardinality="1:N",
            label="configura",
        ),
        # Extremo irrecuperable: se descarta con warning.
        MerRelationshipSchema(
            from_entity="Usuario",
            to_entity="Fantasma",
            cardinality="1:N",
            label="ve",
        ),
    ]

    with patch(
        "backend.agents.pipelines.mer_pipeline._discover_entities",
        return_value=candidates,
    ), patch(
        "backend.agents.pipelines.mer_pipeline._gap_pass",
        return_value=[],
    ), patch(
        "backend.agents.pipelines.mer_pipeline._detail_entities_relationships",
        return_value=(entities_full, relationships),
    ), patch(
        "backend.agents.pipelines.mer_pipeline._critique_mer",
        return_value=[],
    ):
        result = await generate_mer(items, project_name="Proj")

    # La variante quedó re-apuntada al nombre canónico.
    assert result.relationships[0].from_entity == "Configuración de Conexión"
    # La arista irrecuperable no viaja en el resultado.
    assert len(result.relationships) == 1
    # El diagrama no contiene el nodo fantasma.
    assert "CONFIGURACIONCONEXION" not in result.mermaid
    # Stats auditan la reconciliación y el descarte queda como warning.
    assert result.stats["reconciliation"]["reconciled_count"] == 1
    assert result.stats["reconciliation"]["dropped_count"] == 1
    assert any("Fantasma" in w for w in result.stats["validation_warnings"])
