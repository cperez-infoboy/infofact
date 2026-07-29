"""Contract tests for the vision description prompts.

The diagram prompt must disambiguate database / entity-relationship schemas
from architecture diagrams, so a data model embedded in a client document is
described as tables + keys + cardinality — not reframed as component flows.
"""
from backend.agents.vision import _DIAGRAM_PROMPT, _PROMPT_BY_KIND


def test_diagram_prompt_distinguishes_data_schemas():
    p = _DIAGRAM_PROMPT.lower()
    # Must explicitly call out database / entity-relationship schemas.
    assert "base de datos" in p or "entidad-relación" in p
    # Must instruct to identify the diagram type first (disambiguation step).
    assert "identifique" in p or "determine" in p
    # Must capture data-schema specifics: tables/entities, fields, keys, cardinality.
    assert "tabla" in p or "entidad" in p
    assert "clave" in p                       # PK / FK
    assert "cardinalidad" in p or "1:n" in p or "1 a muchos" in p
    # Must explicitly warn against reframing entities as components / data flows.
    assert "componentes" in p and "flujos de datos" in p


def test_diagram_prompt_routed_for_diagram_kind():
    assert _PROMPT_BY_KIND["diagram"] is _DIAGRAM_PROMPT
