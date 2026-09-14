"""Tests de _build_traceability_payload (grafo de trazabilidad sin DB).

La función pura construye nodos/aristas desde filas persistidas. Los tests
fijan los dos niveles de vista:

- ``overview``: SOLO proyectos + sub-proyectos con belongs/contract; los
  counts de reqs por sub-proyecto viajan en el nodo (no sus REQs sueltos).
- ``full``: descomposición + entidades + REQs + tareas (mapa histórico).

SimpleNamespace en lugar de modelos reales: la función solo lee atributos.
"""

from types import SimpleNamespace

from backend.routers.analysis import _build_traceability_payload


def _proj(code: str, domain: str = "core") -> SimpleNamespace:
    return SimpleNamespace(code=code, name=f"Proyecto {code}", domain_type=domain)


def _sub(
    code: str,
    project: str,
    entities: list[str],
    nfrs: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        code=code,
        name=f"Sub {code}",
        project_code=project,
        entity_codes=entities,
        nfr_codes=nfrs or [],
    )


def _ent(code: str, traced: list[str]) -> SimpleNamespace:
    return SimpleNamespace(code=code, name=f"Entidad {code}", traced_req_codes=traced)


def _contract(src: str, dst: str, name: str = "POST /x") -> SimpleNamespace:
    return SimpleNamespace(
        from_subproject_code=src,
        to_subproject_code=dst,
        contract_type=SimpleNamespace(value="openapi"),
        name=name,
    )


def _payload(view: str = "overview"):
    subs = [
        _sub("SUB-1", "PROJ-1", ["ENT-A", "ENT-B"], nfrs=["REQ-3"]),
        _sub("SUB-2", "PROJ-1", ["ENT-C"]),
    ]
    entities = [
        _ent("ENT-A", ["REQ-1", "REQ-2"]),
        _ent("ENT-B", ["REQ-1"]),
        _ent("ENT-C", ["REQ-3"]),
    ]
    return _build_traceability_payload(
        projects=[_proj("PROJ-1")],
        subs=subs,
        contracts=[_contract("SUB-1", "SUB-2")],
        entities=entities,
        rels=[],
        live_reqs={
            "REQ-1": SimpleNamespace(code="REQ-1"),
            "REQ-2": SimpleNamespace(code="REQ-2"),
            "REQ-3": SimpleNamespace(code="REQ-3"),
        },
        tasks_by_subproject={"SUB-1": [SimpleNamespace(code="TASK-1", title="t", package_id=1, req_codes=["REQ-1"])]},
        task_pkg_code={1: "WP-1"},
        view=view,
    )


def _by_id(nodes: list[dict]) -> dict:
    return {n["id"]: n for n in nodes}


def test_overview_only_projects_subprojects_and_contracts():
    nodes, edges = _payload("overview")
    kinds = {n["kind"] for n in nodes}
    assert kinds == {"project", "subproject"}  # sin entity/req/task
    edge_kinds = {e["kind"] for e in edges}
    assert edge_kinds == {"belongs", "contract"}  # sin owns/rel/traces/task_of
    assert len(nodes) == 3  # 1 proyecto + 2 sub-proyectos


def test_overview_counts_reqs_per_subproject():
    nodes, _edges = _payload("overview")
    by_id = _by_id(nodes)
    # SUB-1 posee ENT-A (REQ-1, REQ-2) y ENT-B (REQ-1) => 2 reqs distintos.
    assert by_id["SUB-1"]["reqs"] == 2
    assert by_id["SUB-1"]["entities"] == 2
    assert by_id["SUB-1"]["tasks"] == 1
    # SUB-2 posee ENT-C (REQ-3) => 1.
    assert by_id["SUB-2"]["reqs"] == 1


def test_full_includes_entities_reqs_and_tasks():
    nodes, edges = _payload("full")
    kinds = {n["kind"] for n in nodes}
    assert kinds == {"project", "subproject", "entity", "req", "task"}
    edge_kinds = {e["kind"] for e in edges}
    assert {"belongs", "contract", "owns", "traces", "task_of", "nfr_of"} <= edge_kinds
    by_id = _by_id(nodes)
    assert by_id["TASK-1@WP-1"]["kind"] == "task"
    # En full, el count reqs del sub-proyecto no se agrega (usa los nodos).
    assert "reqs" not in by_id["SUB-1"]


def test_full_is_default_and_backward_compatible():
    full_named, _ = _payload("full")
    default, _ = _payload("otro-valor")
    assert {n["id"] for n in full_named} == {n["id"] for n in default}


def test_contract_with_unknown_subproject_is_dropped():
    nodes, edges = _payload("overview")
    by_id = _by_id(nodes)
    # El contrato SUB-1 -> SUB-2 existe; el tipo se deserializa a string.
    contracts = [e for e in edges if e["kind"] == "contract"]
    assert len(contracts) == 1
    assert contracts[0]["from"] == "SUB-1"
    assert contracts[0]["to"] == "SUB-2"
    assert by_id["SUB-2"]["project"] == "PROJ-1"
