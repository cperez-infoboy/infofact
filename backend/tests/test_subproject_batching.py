"""Batched detailing del pipeline de sub-proyectos a escala Planitrack2.0.

La corrida del 2026-09-10 contra ~304 entidades terminó con 0 sub-proyectos y
0 contratos: el Pass 3 era UNA llamada estructurada que pedía todo el
desglose (sub-proyectos + contratos con specs completos + diagrama) y el
presupuesto de 32K tokens truncaba el JSON siempre (``finish_reason=length``);
los 3 reintentos truncaban igual y la etapa quedaba en ``detailing_failed``.
Estos tests fijan el comportamiento nuevo:

- Pass 3 batcheado por proyecto: un lote LLM por área descubierta (los
  sub-proyectos sin proyecto van a un lote de reservas), N lotes => N
  llamadas cuando hay varios proyectos.
- Contratos livianos: los endpoints/eventos viajan sin spec completa en el
  output estructurado (la spec fat no cabe; era la causa del truncado).
- Degradación graciosa: un lote que falla persistentemente (truncado real)
  no aborta la pasada — los otros lotes aportan sus sub-proyectos.
- El passthrough de skeletons cuando TODOS los lotes fallan (el fallback
  determinista mantiene la etapa con contenido, nunca vacía).
- ``on_progress`` reporta avances por lote (misma forma que process/MER).
- ``refine_analysis`` siembra el holder con resultados tipados del documento
  previo (espejo de ``seed_from_last_srs``), para que re-ejecutar SOLO
  propose_subprojects reciba mer/nfr/adr/projects y no un holder vacío.

No DB / no LLM real: ``structured_llm`` se stubbea por módulo con factories
que graban cada llamada y devuelven schemas encolados (mismo patrón de
``test_analysis_batching.py``).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import backend.agents.pipelines.subproject_pipeline as sp_mod
import backend.agents.subagents.analysis_agent as agent_mod
from backend.agents.pipelines.adr_pipeline import AdrSchema
from backend.agents.pipelines.mer_pipeline import (
    MerAttribute,
    MerEntitySchema,
    MerRelationshipSchema,
)
from backend.agents.pipelines.project_pipeline import ProjectSkeleton
from backend.agents.pipelines.subproject_pipeline import (
    ContractSchema,
    SubProjectBatchDetailSchema,
    SubProjectSchema,
    SubProjectSkeleton,
    SubProjectSkeletonSchema,
)
from backend.agents.subagents import analysis_run_holder as holder
from backend.agents.subagents.analysis_run_holder import STAGE_MER

PROJECT_ID = 4492


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


class _LLMFactory:
    """Stand-in for ``structured_llm`` (espejo de test_analysis_batching).

    ``fail_after=N`` deja de servir después de la N-ésima llamada de forma
    persistente — la forma real de un lote condenado por truncado (nunca
    parsea en reintento). Cuando la cola se agota, devuelve instancias
    válidas vacías del schema pedido.
    """

    def __init__(self, queue=None, fail_after: int | None = None):
        self.queue = list(queue or [])
        self.fail_after = fail_after
        self.calls: int = 0
        self._schema = None

    def __call__(self, schema, **kw):
        self._schema = schema
        return self

    async def ainvoke(self, msgs, **kw):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("persistent failure (truncated output)")
        if self.queue:
            result = self.queue.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return _empty_schema(self._schema, msgs)


def _empty_schema(schema, msgs=None):
    if schema is SubProjectSkeletonSchema:
        return SubProjectSkeletonSchema(sub_projects=[])
    if schema is SubProjectBatchDetailSchema and msgs:
        # Detalle determinista desde el texto del lote: un SubProjectSchema
        # por linea "- <name> — ..." del mensaje humano, con el area viajada
        # en "(area: <key>)". Espeja lo que el LLM real devolveria. SOLO la
        # seccion a detallar: el inventario completo de vecinos tambien
        # lista nombres y el stub no debe fabricar sub-proyectos desde ahi.
        human = msgs[1][1] if len(msgs) > 1 else ""
        human = human.split("SUB-PROYECTOS A DETALLAR")[-1]
        area = ""
        for token in human.split("area:"):
            if token != human:
                area = token.strip().rstrip(")\n ").split("\n")[0]
                break
        subs = []
        for line in human.splitlines():
            stripped = line.strip()
            if stripped.startswith("- ") and " — " in stripped:
                name = stripped[2:].split(" — ")[0].strip()
                subs.append(
                    SubProjectSchema(
                        project_name=area,
                        name=name,
                        responsibility=f"Responsabilidad de {name}",
                    )
                )
        return SubProjectBatchDetailSchema(sub_projects=subs)
    return SubProjectBatchDetailSchema()


def _skeleton(
    name: str, entities: list[str], project: str = ""
) -> SubProjectSkeleton:
    return SubProjectSkeleton(
        project_name=project,
        name=name,
        responsibility=f"Responsabilidad de {name}",
        entity_codes=entities,
        bounded_contexts=[],
    )


def _make_mer(n_entities: int):
    entities = [
        MerEntitySchema(
            name=f"Ent{i}",
            attributes=[
                MerAttribute(name=f"ent{i}Id", type="uuid", is_key=True)
            ],
        )
        for i in range(n_entities)
    ]
    rels = (
        [
            MerRelationshipSchema(
                from_entity="Ent0", to_entity="Ent1", cardinality="1:N"
            )
        ]
        if n_entities >= 2
        else []
    )
    return SimpleNamespace(entities=entities, relationships=rels)


def _projects_ns(*names: str) -> SimpleNamespace:
    return SimpleNamespace(
        projects=[
            ProjectSkeleton(name=n, description="d", domain_type="core")
            for n in names
        ]
    )


@pytest.fixture(autouse=True)
def _isolate_holder():
    holder.clear_run(PROJECT_ID)
    yield
    holder.clear_run(PROJECT_ID)


# --------------------------------------------------------------------------- #
# Pass 3: batched detailing                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_detail_is_batched_per_project(monkeypatch):
    """Varios proyectos => varios lotes LLM en el Pass 3 (no una llamada grasa)."""
    factory = _LLMFactory()
    monkeypatch.setattr(sp_mod, "structured_llm", factory)

    skeletons = [
        _skeleton("ventas-api", ["Ent0", "Ent1"], project="Ventas"),
        _skeleton("billing-api", ["Ent2"], project="Facturacion"),
        _skeleton("identity", ["Ent3"], project="Ventas"),
    ]

    out = await sp_mod._detail_subprojects(
        _make_mer(4), skeletons, None, None,
        project_result=_projects_ns("Ventas", "Facturacion"),
    )
    assert out is not None
    # 1 lote por proyecto distinto (Ventas, Facturacion) => 2 llamadas.
    assert factory.calls == 2
    assert {sp.name for sp in out.sub_projects} >= {
        "ventas-api",
        "billing-api",
        "identity",
    }


@pytest.mark.asyncio
async def test_detail_single_call_when_no_projects(monkeypatch):
    """Sin proyectos descubiertos, los skeletons van en un solo lote."""
    factory = _LLMFactory()
    monkeypatch.setattr(sp_mod, "structured_llm", factory)

    skeletons = [
        _skeleton("a", ["Ent0"]),
        _skeleton("b", ["Ent1"]),
    ]
    out = await sp_mod._detail_subprojects(_make_mer(2), skeletons)
    assert out is not None
    assert factory.calls == 1


@pytest.mark.asyncio
async def test_detail_failed_batch_degrades_gracefully(monkeypatch):
    """Un lote condenado (truncado persistente) no borra los demás lotes."""
    factory = _LLMFactory(fail_after=1)
    monkeypatch.setattr(sp_mod, "structured_llm", factory)

    skeletons = [
        _skeleton("ventas-api", ["Ent0"], project="Ventas"),
        _skeleton("billing-api", ["Ent1"], project="Facturacion"),
    ]
    out = await sp_mod._detail_subprojects(
        _make_mer(2), skeletons, None, None,
        project_result=_projects_ns("Ventas", "Facturacion"),
    )
    assert out is not None
    # El lote condenado consume reintentos de parse (2 llamadas extra); el
    # lote sano hace exactamente 1. Lo que importa: degradacion correcta.
    assert factory.calls >= 3
    # Solo sobrevive el sub-proyecto del lote que no falló.
    assert {sp.name for sp in out.sub_projects} == {"ventas-api"}
    assert out.failed_batches >= 1


@pytest.mark.asyncio
async def test_propose_subprojects_passthrough_when_all_batches_fail(
    monkeypatch,
):
    """Si TODOS los lotes fallan, los skeletons pasan como fallback."""
    factory = _LLMFactory(fail_after=1)
    monkeypatch.setattr(sp_mod, "structured_llm", factory)

    # Pass 1 (llamada 1) devuelve skeletons validos; lo demas condenado.
    pass1 = SubProjectSkeletonSchema(
        sub_projects=[_skeleton("ventas-api", ["Ent0"])]
    )
    factory.queue = [pass1]

    result = await sp_mod.propose_subprojects(
        _make_mer(1), None, None, enable_critique=False
    )
    assert len(result.sub_projects) == 1
    assert result.sub_projects[0].name == "ventas-api"
    assert result.stats.get("detailing_fallback") is True


@pytest.mark.asyncio
async def test_detail_reports_progress_per_batch(monkeypatch):
    """``on_progress`` recibe un mensaje por lote completado."""
    factory = _LLMFactory()
    monkeypatch.setattr(sp_mod, "structured_llm", factory)

    progress: list[str] = []
    skeletons = [
        _skeleton("ventas-api", ["Ent0"], project="Ventas"),
        _skeleton("billing-api", ["Ent1"], project="Facturacion"),
    ]
    await sp_mod._detail_subprojects(
        _make_mer(2),
        skeletons,
        None,
        None,
        project_result=_projects_ns("Ventas", "Facturacion"),
        on_progress=progress.append,
    )
    assert len(progress) == 2
    assert all("lote" in p for p in progress)


# --------------------------------------------------------------------------- #
# Guardas deterministas (auto-contratos y huerfanos)                            #
# --------------------------------------------------------------------------- #


def _contract(a: str, b: str, name: str = "POST /x"):
    from backend.agents.pipelines.subproject_pipeline import ContractSchema

    return ContractSchema(
        from_subproject=a,
        to_subproject=b,
        contract_type="openapi",
        name=name,
    )


def test_drop_self_contracts_filters_and_counts():
    contracts = [
        _contract("SUB-001", "SUB-002", "GET /rutas"),
        _contract("SUB-007", "SUB-007", "POST /webhooks"),
        _contract("SUB-001", "SUB-008", "gestion.reasignada"),
        _contract("SUB-003", "SUB-003", "GET /reportes"),
    ]
    kept, dropped = sp_mod._drop_self_contracts(contracts)
    assert dropped == 2
    assert all(c.from_subproject != c.to_subproject for c in kept)
    assert len(kept) == 2


def test_drop_self_contracts_keeps_all_when_clean():
    contracts = [_contract("SUB-001", "SUB-002")]
    kept, dropped = sp_mod._drop_self_contracts(contracts)
    assert dropped == 0 and len(kept) == 1


def test_reassign_orphans_when_single_project():
    sub_projects = [
        _skeleton("a", ["Ent0"], project="Ventas"),
        _skeleton("huérfano", ["Ent1"], project=""),
    ]
    # project_result con UN solo proyecto: el huerfano hereda esa area.
    projects = _projects_ns("Ventas")
    out, n = sp_mod._reassign_orphan_subprojects(
        [
            sp_mod.SubProjectSchema(
                project_name=sp.project_name,
                name=sp.name,
                responsibility=sp.responsibility,
                entity_codes=sp.entity_codes,
            )
            for sp in sub_projects
        ],
        projects,
    )
    assert n == 1
    assert out[1].project_name == "Ventas"
    assert out[0].project_name == "Ventas"


def test_reassign_orphans_noop_with_multiple_projects():
    """Con varias areas, la re-asignacion es ambigua y no se hace."""
    sub_projects = [
        sp_mod.SubProjectSchema(
            name="huerfano", responsibility="r", entity_codes=["Ent1"]
        )
    ]
    out, n = sp_mod._reassign_orphan_subprojects(
        sub_projects, _projects_ns("Ventas", "Facturacion")
    )
    assert n == 0
    assert out[0].project_name == ""


@pytest.mark.asyncio
async def test_propose_subprojects_applies_guards(monkeypatch):
    """El pipeline completo aplica ambas guardas y reporta en stats."""
    # El stub por defecto parsea TODO el contexto y mete ruido; encolamos
    # explicitamente el Pass 1 y el detailing de cada lote.
    factory = _LLMFactory()
    monkeypatch.setattr(sp_mod, "structured_llm", factory)

    # Pass 1: dos skeletons, uno huerfano (sin area).
    pass1 = SubProjectSkeletonSchema(
        sub_projects=[
            _skeleton("ventas-api", ["Ent0"], project="Ventas"),
            _skeleton("huerfano", ["Ent1"], project=""),
        ]
    )
    # Pass 3: lote del area Ventas (un skeleton) y lote de huerfanos (otro):
    # 2 lotes => 2 resultados de detailing con contratos: uno normal y uno
    # auto-contrato (SUB interno) para ejercitar _drop_self_contracts.
    factory.queue = [
        pass1,
        sp_mod.SubProjectBatchDetailSchema(
            sub_projects=[
                sp_mod.SubProjectSchema(
                    project_name="Ventas",
                    name="ventas-api",
                    responsibility="r",
                    entity_codes=["Ent0"],
                )
            ],
            contracts=[
                sp_mod.ContractData(
                    from_subproject="ventas-api",
                    to_subproject="ventas-api",
                    contract_type="openapi",
                    name="POST /interno",
                ),
            ],
        ),
        sp_mod.SubProjectBatchDetailSchema(
            sub_projects=[
                sp_mod.SubProjectSchema(
                    project_name="",
                    name="huerfano",
                    responsibility="r",
                    entity_codes=["Ent1"],
                )
            ],
            contracts=[
                sp_mod.ContractData(
                    from_subproject="ventas-api",
                    to_subproject="huerfano",
                    contract_type="openapi",
                    name="GET /asignaciones",
                ),
            ],
        ),
    ]

    result = await sp_mod.propose_subprojects(
        _make_mer(2), None, None,
        project_result=_projects_ns("Ventas"),
        enable_critique=False,
    )
    # El auto-contrato (ventas-api -> ventas-api) fue descartado.
    assert result.stats.get("self_contracts_dropped") == 1
    assert len(result.contracts) == 1
    assert result.contracts[0].to_subproject == "huerfano"
    # El huerfano hereda la unica area descrita.
    assert result.stats.get("orphans_reassigned") == 1
    by_name = {sp.name: sp for sp in result.sub_projects}
    assert by_name["huerfano"].project_name == "Ventas"


# --------------------------------------------------------------------------- #
# Contratos cruzados: el inventario de vecinos en cada lote                     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_detail_prompt_lists_all_skeleton_names(monkeypatch):
    """Cada lote recibe el inventario COMPLETO de sub-proyectos del modelo.

    La corrida v4 de Planitrack2.0 generó 8 contratos, todos intra-área
    (PROJ-027): cada lote LLM solo veía SUS skeletons y un contrato hacia
    un sub-proyecto de OTRO lote era estructuralmente imposible.
    """
    factory = _LLMFactory()
    seen_prompts: list[str] = []

    class _CapturingFactory(_LLMFactory):
        async def ainvoke(self, msgs, **kw):
            # Sistema (reglas) + humano (contexto/inventario): la regla de
            # contratos cruzados viaja en el system prompt.
            seen_prompts.append(msgs[0][1] + "\n" + msgs[1][1])
            return await super().ainvoke(msgs, **kw)

    factory = _CapturingFactory()
    monkeypatch.setattr(sp_mod, "structured_llm", factory)

    skeletons = [
        _skeleton("ventas-api", ["Ent0"], project="Ventas"),
        _skeleton("billing-api", ["Ent1"], project="Facturacion"),
        _skeleton("identity", ["Ent2"], project="Ventas"),
    ]
    await sp_mod._detail_subprojects(
        _make_mer(3), skeletons, None, None,
        project_result=_projects_ns("Ventas", "Facturacion"),
    )
    assert factory.calls == 2
    for prompt in seen_prompts:
        # El inventario de vecinos incluye a los del propio lote y a los
        # de los demás (asi el LLM PUEDE emitir contratos cruzados).
        assert "ventas-api" in prompt
        assert "billing-api" in prompt
        assert "identity" in prompt
        assert "inventario completo" in prompt.lower()
        # La regla del prompt declara validos los contratos hacia vecinos
        # de otra area ("es valido y necesario").
        assert "valido y necesario" in prompt.lower()


@pytest.mark.asyncio
async def test_detail_cross_area_contract_survives_pipeline(monkeypatch):
    """Un contrato que el lote emite hacia un sub-proyecto de OTRO lote
    llega al resultado final (v4 de Planitrack2.0 los perdia todos)."""
    factory = _LLMFactory()
    monkeypatch.setattr(sp_mod, "structured_llm", factory)

    # Pass 1: dos areas con un skeleton cada una.
    pass1 = SubProjectSkeletonSchema(
        sub_projects=[
            _skeleton("ventas-api", ["Ent0"], project="Ventas"),
            _skeleton("facturacion-api", ["Ent1"], project="Facturacion"),
        ]
    )
    factory.queue = [
        pass1,
        # Lote Ventas: emite un contrato CRUZADO hacia facturacion-api,
        # que NO pertenece a este lote.
        sp_mod.SubProjectBatchDetailSchema(
            sub_projects=[
                sp_mod.SubProjectSchema(
                    project_name="Ventas",
                    name="ventas-api",
                    responsibility="r",
                    entity_codes=["Ent0"],
                )
            ],
            contracts=[
                sp_mod.ContractData(
                    from_subproject="ventas-api",
                    to_subproject="facturacion-api",
                    contract_type="openapi",
                    name="POST /facturas",
                ),
            ],
        ),
        # Lote Facturacion: sin contratos.
        sp_mod.SubProjectBatchDetailSchema(
            sub_projects=[
                sp_mod.SubProjectSchema(
                    project_name="Facturacion",
                    name="facturacion-api",
                    responsibility="r",
                    entity_codes=["Ent1"],
                )
            ],
            contracts=[],
        ),
    ]

    result = await sp_mod.propose_subprojects(
        _make_mer(2), None, None,
        project_result=_projects_ns("Ventas", "Facturacion"),
        enable_critique=False,
    )
    assert result.stats.get("detailing_fallback") is False
    names = {(c.from_subproject, c.to_subproject) for c in result.contracts}
    assert ("ventas-api", "facturacion-api") in names


# --------------------------------------------------------------------------- #
# refine_analysis: siembra del holder                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_refine_analysis_seeds_holder_from_previous(monkeypatch):
    """Tras refine_analysis, el holder tiene mer/nfr/adr/projects tipados."""
    payload = {
        "id": 7,
        "version": 1,
        "entities": [
            {
                "name": "Orden",
                "description": "",
                "attributes": [
                    {
                        "name": "ordenId",
                        "type": "uuid",
                        "is_key": True,
                        "required": True,
                    }
                ],
                "aggregate_root": True,
                "bounded_context": "Ventas",
                "traced_req_codes": ["REQ-0001"],
            }
        ],
        "relationships": [
            {
                "from_entity": "Orden",
                "to_entity": "Cliente",
                "cardinality": "1:N",
                "label": "",
                "description": "",
                "traced_req_codes": ["REQ-0001"],
            }
        ],
        "mer_diagram": "erDiagram ORDEN {}",
        "mer_diagram_description": "d",
        "nfr_analysis": {
            "decisions": [
                {
                    "req_code": "REQ-0002",
                    "category": "performance",
                    "decision": "Cache Redis",
                    "stack_component": "Redis",
                    "rationale": "latencia p95 < 100 ms",
                }
            ],
            "stack": [
                {
                    "layer": "database",
                    "technology": "PostgreSQL",
                    "rationale": "r",
                }
            ],
            "data_consistency": "saga",
            "patterns": "CQRS",
        },
        "adrs": [
            {
                "title": "Usar PostgreSQL",
                "context": "c",
                "decision": "d",
                "alternatives": [],
                "rationale": "r",
                "nfr_codes": ["REQ-0002"],
            }
        ],
        "projects": [
            {
                "name": "Ventas",
                "description": "d",
                "domain_type": "core",
                "bounded_contexts": ["Ventas"],
                "entity_names": ["Orden"],
                "traced_req_codes": ["REQ-0001"],
            }
        ],
        "sub_projects": [],
        "contracts": [],
        "component_diagram": "",
        "component_diagram_description": "",
        "system_architecture_diagram": "",
        "system_architecture_description": "",
        "infrastructure_diagram": "",
        "infrastructure_description": "",
        "process_diagrams": [],
    }

    run = holder.get_or_create_run(
        PROJECT_ID, project_name="P", project_description="D"
    )

    async def _fake_doc(pid):
        return payload

    async def _fake_latest(session, pid):
        return SimpleNamespace(id=7, version=1)

    import backend.services.analysis_store as store_mod

    monkeypatch.setattr(agent_mod, "_latest_analysis_document", _fake_doc)
    monkeypatch.setattr(store_mod, "get_latest_analysis", _fake_latest)
    monkeypatch.setattr(agent_mod, "AsyncSessionLocal", _FakeSession)

    tools = agent_mod._make_stage_tools(
        PROJECT_ID, project_name="P", project_description="D"
    )
    # _make_stage_tools devuelve una lista ordenada por etapa; refine_analysis
    # va después de las 8 de etapa (ver _make_stage_tools).
    tool_map = {t.name: t for t in tools}
    out = await tool_map["refine_analysis"].ainvoke({"feedback": "mejora X"})
    assert out["status"] == "loaded"
    assert run.mer_result is not None
    assert [e.name for e in run.mer_result.entities] == ["Orden"]
    assert run.nfr_result is not None
    assert run.nfr_result.decisions[0].req_code == "REQ-0002"
    assert run.nfr_result.stack[0].technology == "PostgreSQL"
    assert run.adr_result is not None
    assert isinstance(run.adr_result.adrs[0], AdrSchema)
    assert run.project_result is not None
    assert isinstance(run.project_result.projects[0], ProjectSkeleton)
    assert run.stages_done  # todas las etapas marcadas como done


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False
