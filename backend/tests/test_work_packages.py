"""Paquetes de trabajo: pipeline (gates, batching, fallback) y wiring.

Los tests de LLM usan stubs (patrón test_analysis_batching); los de gates y
renders son puros (0 LLM, 0 DB). El routing de /paquetes se prueba con la
función de reescritura del chat router.
"""
from __future__ import annotations

import pytest

import backend.agents.pipelines.workpackage_pipeline as wp_mod
import backend.routers.chat as chat_mod
from backend.agents.pipelines.workpackage_pipeline import (
    CoherenceGate,
    PackageContext,
    PackageTask,
    TaskBatchSchema,
    TaskSchema,
    _decompose_package_tasks,
    render_assembly_master,
    render_package_markdown,
    verify_packages_coherence,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _ctx(code: str = "SUB-001", name: str = "ventas-api", **kw) -> PackageContext:
    return PackageContext(
        sub_project_code=code,
        sub_project_name=name,
        project_code="PROJ-001",
        project_name="Ventas",
        mission=f"Misión de {name}",
        stack={"language": "Python"},
        entities=kw.get("entities", [{"code": "ENT-AAAA", "name": "Cliente"}]),
        requirements=kw.get(
            "requirements",
            [
                {
                    "code": "REQ-0001",
                    "statement": "El sistema debe registrar clientes",
                    "type": "functional",
                    "priority": "must",
                    "acceptance": ["Given un cliente nuevo When se registra Then persiste"],
                }
            ],
        ),
        contracts_exposed=kw.get("contracts_exposed", []),
        contracts_consumed=kw.get("contracts_consumed", []),
        tasks=kw.get("tasks", []),
    )


def _all_inputs(contexts: list[PackageContext]):
    entities = [e for c in contexts for e in c.entities]
    reqs = [
        {"code": r["code"], "is_nfr": r["type"] != "functional"}
        for c in contexts
        for r in c.requirements
    ]
    contracts = [
        {"from": x["from"], "to": x["to"], "contract_type": x["contract_type"],
         "name": x["name"], "spec": x.get("spec", "")}
        for c in contexts
        for x in c.contracts_exposed + c.contracts_consumed
    ]
    return entities, reqs, contracts


# --------------------------------------------------------------------------- #
# Gate 1: cierre de cobertura                                                  #
# --------------------------------------------------------------------------- #


def test_gate_coverage_passes_when_all_reqs_have_tasks():
    ctx = _ctx()
    ctx.tasks = [PackageTask(title="T1", req_codes=["REQ-0001"])]
    entities, reqs, contracts = _all_inputs([ctx])
    gates = verify_packages_coherence(
        [ctx], all_entities=entities, all_requirements=reqs,
        all_contracts=contracts, all_sequence_diagrams=[],
    )
    g1 = next(g for g in gates if g.gate == "cierre_cobertura")
    assert g1.status == "pass"
    assert not any(g.blocking and g.status == "fail" for g in gates)


def test_gate_coverage_fails_when_req_without_task():
    ctx = _ctx()
    ctx.tasks = [PackageTask(title="T1", req_codes=["REQ-0001"])]
    entities, reqs, contracts = _all_inputs([ctx])
    reqs.append({"code": "REQ-9999", "is_nfr": False})
    gates = verify_packages_coherence(
        [ctx], all_entities=entities, all_requirements=reqs,
        all_contracts=contracts, all_sequence_diagrams=[],
    )
    g1 = next(g for g in gates if g.gate == "cierre_cobertura")
    assert g1.status == "fail" and g1.blocking
    assert any("REQ-9999" in d for d in g1.details)


def test_gate_coverage_fails_when_package_has_no_tasks():
    ctx = _ctx()  # sin tasks
    entities, reqs, contracts = _all_inputs([ctx])
    gates = verify_packages_coherence(
        [ctx], all_entities=entities, all_requirements=reqs,
        all_contracts=contracts, all_sequence_diagrams=[],
    )
    g1 = next(g for g in gates if g.gate == "cierre_cobertura")
    assert g1.status == "fail" and g1.blocking


# --------------------------------------------------------------------------- #
# Gate 2: frontera con contrato / Gate 3: simetría + ciclos                    #
# --------------------------------------------------------------------------- #


def test_gate_border_requires_contract():
    a = _ctx("SUB-A", "a", entities=[{"code": "ENT-A", "name": "Venta"}])
    b = _ctx("SUB-B", "b", entities=[{"code": "ENT-B", "name": "Stock"}])
    a.border_relationships = [
        {"from": "ENT-A", "to": "ENT-B", "from_name": "Venta",
         "to_name": "Stock", "cardinality": "1:N", "label": ""}
    ]
    a.tasks = [PackageTask(title="T", req_codes=["REQ-0001"])]
    b.tasks = [PackageTask(title="T", req_codes=["REQ-0001"])]
    entities, reqs, contracts = _all_inputs([a, b])  # SIN contrato
    gates = verify_packages_coherence(
        [a, b], all_entities=entities, all_requirements=reqs,
        all_contracts=contracts, all_sequence_diagrams=[],
    )
    g2 = next(g for g in gates if g.gate == "frontera_con_contrato")
    assert g2.status == "fail" and g2.blocking

    # Con contrato entre ambos, pasa.
    a.contracts_exposed = [
        {"from": "SUB-A", "to": "SUB-B", "contract_type": "openapi",
         "name": "GET /ventas", "spec": ""}
    ]
    entities, reqs, contracts = _all_inputs([a, b])
    gates = verify_packages_coherence(
        [a, b], all_entities=entities, all_requirements=reqs,
        all_contracts=contracts, all_sequence_diagrams=[],
    )
    g2 = next(g for g in gates if g.gate == "frontera_con_contrato")
    assert g2.status == "pass"


def test_gate_symmetry_blocks_self_contracts():
    ctx = _ctx()
    ctx.tasks = [PackageTask(title="T", req_codes=["REQ-0001"])]
    ctx.contracts_exposed = [
        {"from": "SUB-001", "to": "SUB-001", "contract_type": "openapi",
         "name": "POST /interno", "spec": ""}
    ]
    entities, reqs, contracts = _all_inputs([ctx])
    gates = verify_packages_coherence(
        [ctx], all_entities=entities, all_requirements=reqs,
        all_contracts=contracts, all_sequence_diagrams=[],
    )
    g3 = next(g for g in gates if g.gate == "contratos_simetricos")
    assert g3.status == "fail" and g3.blocking


def test_gate_cycle_detection():
    a = _ctx("SUB-A", "a")
    b = _ctx("SUB-B", "b")
    a.tasks = [PackageTask(title="T", req_codes=["REQ-0001"])]
    b.tasks = [PackageTask(title="T", req_codes=["REQ-0001"])]
    a.contracts_consumed = [
        {"from": "SUB-B", "to": "SUB-A", "contract_type": "openapi",
         "name": "GET /b", "spec": ""}
    ]
    b.contracts_consumed = [
        {"from": "SUB-A", "to": "SUB-B", "contract_type": "openapi",
         "name": "GET /a", "spec": ""}
    ]
    entities, reqs, contracts = _all_inputs([a, b])
    gates = verify_packages_coherence(
        [a, b], all_entities=entities, all_requirements=reqs,
        all_contracts=contracts, all_sequence_diagrams=[],
    )
    g = next(g for g in gates if g.gate == "grafo_aciclico")
    assert g.status == "fail" and g.blocking


def test_gate_acceptance_warns_without_gherkin():
    ctx = _ctx(requirements=[{
        "code": "REQ-0001", "statement": "s", "type": "functional",
        "priority": "must", "acceptance": [],
    }])
    ctx.tasks = [PackageTask(title="T", req_codes=["REQ-0001"])]
    entities, reqs, contracts = _all_inputs([ctx])
    gates = verify_packages_coherence(
        [ctx], all_entities=entities, all_requirements=reqs,
        all_contracts=contracts, all_sequence_diagrams=[],
    )
    g6 = next(g for g in gates if g.gate == "aceptacion_presente")
    assert g6.status == "warn" and not g6.blocking


# --------------------------------------------------------------------------- #
# Pass 2: descomposición LLM batcheada + fallback                              #
# --------------------------------------------------------------------------- #


class _StubFactory:
    """Stub de structured_llm: cubre los codes de cada lote en 1 tarea/lote."""

    def __init__(self, fail_from: int | None = None):
        self.calls = 0
        self.fail_from = fail_from

    def __call__(self, schema, **kw):
        return self

    async def ainvoke(self, msgs, **kw):
        self.calls += 1
        if self.fail_from is not None and self.calls >= self.fail_from:
            raise RuntimeError("persistent failure")
        human = msgs[1][1]
        codes = [
            line.split()[1]
            for line in human.splitlines()
            if line.startswith("- REQ-")
        ]
        return TaskBatchSchema(
            tasks=[
                TaskSchema(
                    title=f"Tarea {codes[0]}",
                    description="d",
                    req_codes=codes,
                )
            ]
        )


def _make_ctx_n(n_reqs: int) -> PackageContext:
    reqs = [
        {
            "code": f"REQ-{i:04d}",
            "statement": f"Requerimiento {i}",
            "type": "functional",
            "priority": "must",
            "acceptance": [f"Given r{i} Then ok"],
        }
        for i in range(n_reqs)
    ]
    return _ctx(requirements=reqs)


@pytest.mark.asyncio
async def test_decompose_is_batched(monkeypatch):
    factory = _StubFactory()
    monkeypatch.setattr(wp_mod, "structured_llm", factory)
    monkeypatch.setattr(wp_mod, "_TASK_BATCH_SIZE", 5)

    tasks = await _decompose_package_tasks(_make_ctx_n(12))
    assert factory.calls == 3  # 12 reqs / 5 por lote
    covered = {c for t in tasks for c in t.req_codes}
    assert covered == {f"REQ-{i:04d}" for i in range(12)}


@pytest.mark.asyncio
async def test_decompose_fallback_covers_failed_batch(monkeypatch):
    factory = _StubFactory(fail_from=3)  # el lote 3 falla
    monkeypatch.setattr(wp_mod, "structured_llm", factory)
    monkeypatch.setattr(wp_mod, "_TASK_BATCH_SIZE", 5)

    tasks = await _decompose_package_tasks(_make_ctx_n(12))
    covered = {c for t in tasks for c in t.req_codes}
    assert covered == {f"REQ-{i:04d}" for i in range(12)}
    # Los del lote fallido caen como tareas fallback 1:1 (con Gherkin).
    fallback = [t for t in tasks if len(t.req_codes) == 1]
    assert fallback, "el lote fallido debe degradar a tareas 1:1"
    assert all(
        t.acceptance for t in fallback
    ), "las tareas fallback llevan su Gherkin"


# --------------------------------------------------------------------------- #
# Renders                                                                      #
# --------------------------------------------------------------------------- #


def test_render_package_markdown_contains_all_sections():
    ctx = _ctx()
    ctx.tasks = [
        PackageTask(
            code="TASK-001",
            title="Registrar clientes",
            description="d",
            req_codes=["REQ-0001"],
            acceptance=["Given un cliente nuevo Then persiste"],
        )
    ]
    md = render_package_markdown(ctx)
    for frag in (
        "# Paquete SUB-001",
        "## 0. Tu misión",
        "## 1. Tu mundo",
        "## 2. Tus contratos",
        "## 3. Backlog",
        "## 4. Definition of Done",
        "REQ-0001",
        "Given un cliente nuevo When se registra Then persiste",
    ):
        assert frag in md


def test_render_master_orders_levels_and_reports_gates():
    a = _ctx("SUB-A", "a")
    b = _ctx("SUB-B", "b")
    a.contracts_consumed = [
        {"from": "SUB-B", "to": "SUB-A", "contract_type": "openapi",
         "name": "GET /b", "spec": ""}
    ]
    b.contracts_exposed = [
        {"from": "SUB-B", "to": "SUB-A", "contract_type": "openapi",
         "name": "GET /b", "spec": ""}
    ]
    entities, reqs, contracts = _all_inputs([a, b])
    gates = verify_packages_coherence(
        [a, b], all_entities=entities, all_requirements=reqs,
        all_contracts=contracts, all_sequence_diagrams=[],
    )
    md = render_assembly_master(
        [a, b], gates, [], project_name="P", analysis_version=2
    )
    assert "## Orden de construcción sugerido" in md
    # SUB-B (sin dependencias) debe aparecer en el nivel 1.
    first_level = md.split("1. ")[1].splitlines()[0]
    assert "SUB-B" in first_level and "SUB-A" not in first_level
    assert "## Informe de coherencia" in md
    assert "✅" in md or "❌" in md


# --------------------------------------------------------------------------- #
# Routing del comando /paquetes                                                #
# --------------------------------------------------------------------------- #


def test_chat_routes_paquetes_command():
    rewritten = chat_mod._rewrite_command("/paquetes")
    assert "packages-agent" in rewritten
    assert "generate_work_packages" in rewritten
    assert "commit_packages" in rewritten


def test_chat_routes_paquetes_with_steering():
    rewritten = chat_mod._rewrite_command(
        "/paquetes prioriza el sub-proyecto de integraciones"
    )
    assert "prioriza el sub-proyecto de integraciones" in rewritten
    assert "packages-agent" in rewritten


def test_chat_does_not_route_other_commands_as_paquetes():
    rewritten = chat_mod._rewrite_command("/analisis")
    assert "packages-agent" not in rewritten
