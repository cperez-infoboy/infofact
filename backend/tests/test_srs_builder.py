"""Tests del builder de SRS: estructura 29148, narrative, renderers.

Cubre las funciones puras de rendering (_render_authored, _render_goals,
_render_requirements, _render_single_item) y _draft_narrative (subsections),
sin necesidad de DB. Para build_srs end-to-end se necesita una sesión async
con RequirementItem reales.
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.models.project_actor import ActorStatus
from backend.models.requirement import Priority, ReqStatus, ReqType
from backend.services.srs_assembler import _draft_narrative
from backend.services.srs_builder import (
    SRS_STRUCTURE,
    _fmt_criteria,
    _fmt_source,
    _flags_label,
    _render_actors_table,
    _render_authored,
    _render_goals,
    _render_requirements,
    _render_single_item,
)


# ---------------------------------------------------------------------------
# _draft_narrative
# ---------------------------------------------------------------------------


def test_draft_narrative_returns_subsection_keys():
    n = _draft_narrative("Mi App", "desc", {}, {}, {}, 0, live_items=[])
    expected_keys = {
        "intro.purpose",
        "intro.scope",
        "intro.definitions",
        "intro.references",
        "intro.overview",
        "overall.perspective",
        "overall.features",
        "overall.users",
        "overall.environment",
        "overall.assumptions",
    }
    assert expected_keys.issubset(n.keys())


def test_draft_narrative_returns_legacy_keys():
    n = _draft_narrative("Mi App", "desc", {}, {}, {}, 0, live_items=[])
    assert "intro" in n
    assert "overall" in n
    assert "Propósito" in n["intro"] or "propósito" in n["intro"].lower()
    assert "Perspectiva" in n["overall"] or "perspectiva" in n["overall"].lower()


def test_draft_narrative_overview_is_deterministic():
    n = _draft_narrative("Test", "", {}, {}, {}, 0, live_items=[])
    overview = n["intro.overview"]
    assert "Sección 1" in overview
    assert "Sección 8" in overview
    assert "Anexos" in overview


def test_draft_narrative_features_empty_when_no_items():
    n = _draft_narrative("Test", "", {}, {}, {}, 0, live_items=[])
    assert "Sin requerimientos funcionales" in n["overall.features"]


def test_draft_narrative_features_from_live_items():
    mock_item = SimpleNamespace(
        code="REQ-AB12",
        statement="El sistema debe permitir registrar usuarios vía Google OAuth.",
        type=SimpleNamespace(value="functional"),
        status=ReqStatus.VALIDATED,
    )
    n = _draft_narrative("Test", "", {}, {}, {}, 1, live_items=[mock_item])
    features = n["overall.features"]
    assert "REQ-AB12" in features
    assert "registrar usuarios" in features


def test_draft_narrative_features_truncates_long_statements():
    long_stmt = "El sistema debe " + "x" * 200
    mock_item = SimpleNamespace(
        code="REQ-CD34",
        statement=long_stmt,
        type=SimpleNamespace(value="functional"),
        status=ReqStatus.VALIDATED,
    )
    n = _draft_narrative("Test", "", {}, {}, {}, 1, live_items=[mock_item])
    # Each bullet line should be truncated to ~120 chars + ellipsis.
    for line in n["overall.features"].split("\n"):
        if "REQ-CD34" in line:
            assert "…" in line
            # statement portion after "— " should be short.
            stmt_part = line.split("— ", 1)[1] if "— " in line else ""
            assert len(stmt_part) <= 125


def test_draft_narrative_features_grouped_by_goal():
    mock_item = SimpleNamespace(
        code="REQ-AB12",
        statement="El sistema debe permitir registrar usuarios vía Google OAuth.",
        type=SimpleNamespace(value="functional"),
        status=ReqStatus.VALIDATED,
    )
    goal_groups = [("GOAL-F1", "Autenticación del sistema", [mock_item])]
    n = _draft_narrative(
        "Test", "", {}, {}, {}, 1,
        live_items=[mock_item],
        goal_groups=goal_groups,
    )
    features = n["overall.features"]
    assert "**`GOAL-F1`** — Autenticación del sistema" in features
    assert "REQ-AB12" in features
    assert "Sin goal asociado" not in features


def test_draft_narrative_features_unlinked_bucket():
    mock_item = SimpleNamespace(
        code="REQ-AB12",
        statement="El sistema debe permitir registrar usuarios.",
        type=SimpleNamespace(value="functional"),
        status=ReqStatus.VALIDATED,
    )
    n = _draft_narrative(
        "Test", "", {}, {}, {}, 1,
        live_items=[mock_item],
        goal_groups=[],
    )
    assert "Sin goal asociado" in n["overall.features"]
    assert "REQ-AB12" in n["overall.features"]


def test_draft_narrative_features_shows_moscow_and_type():
    mock_item = SimpleNamespace(
        code="REQ-AB12",
        statement="El sistema debe hacer X.",
        type=SimpleNamespace(value="functional"),
        priority=SimpleNamespace(value="must"),
        status=ReqStatus.VALIDATED,
    )
    n = _draft_narrative("Test", "", {}, {}, {}, 1, live_items=[mock_item])
    features = n["overall.features"]
    assert "(MUST · Requerimientos funcionales)" in features


def test_draft_narrative_users_with_actors_block():
    """Con catálogo, el fallback determinista lista los actores en users."""
    block = "\n".join([
        "PROJECT_ACTORS (canonical actors):",
        "- R1 Coordinador de terreno (humano)",
        "- R2 Sistema meteorológico (sistema_externo) [aka: API clima]",
    ])
    n = _draft_narrative("Test", "", {}, {}, {}, 0, actors_block_text=block)
    users = n["overall.users"]
    assert "- R1 Coordinador de terreno (humano)" in users
    assert "- R2 Sistema meteorológico" in users
    assert "Actores definidos durante la captura:" in users
    assert "_Editor:" in users  # privilegios/frecuencia siguen pendientes


def test_draft_narrative_users_without_actors_block_is_placeholder():
    n = _draft_narrative("Test", "", {}, {}, {}, 0)
    assert "clases de usuario" in n["overall.users"]
    assert "R1" not in n["overall.users"]


# ---------------------------------------------------------------------------
# _render_authored
# ---------------------------------------------------------------------------


def test_render_authored_with_subsection_narrative():
    section = {
        "id": "intro",
        "title": "1. Introducción",
        "kind": "authored",
        "subsections": [
            {"id": "intro.purpose", "title": "1.1 Propósito"},
            {"id": "intro.scope", "title": "1.2 Alcance"},
        ],
    }
    narrative = {
        "intro.purpose": "Propósito del producto.",
        "intro.scope": "Alcance del producto.",
    }
    lines = _render_authored(section, narrative)
    md = "\n".join(lines)
    assert "## 1. Introducción" in md
    assert "### 1.1 Propósito" in md
    assert "Propósito del producto." in md
    assert "### 1.2 Alcance" in md
    assert "Alcance del producto." in md


def test_render_authored_legacy_section_key():
    """When narrative has section-level key but no subsection keys."""
    section = {
        "id": "intro",
        "title": "1. Introducción",
        "kind": "authored",
        "subsections": [
            {"id": "intro.purpose", "title": "1.1 Propósito"},
        ],
    }
    narrative = {"intro": "Texto legacy del bloque completo."}
    lines = _render_authored(section, narrative)
    md = "\n".join(lines)
    assert "Texto legacy del bloque completo." in md


def test_render_authored_empty_narrative_shows_placeholders():
    section = {
        "id": "intro",
        "title": "1. Introducción",
        "kind": "authored",
        "subsections": [
            {"id": "intro.purpose", "title": "1.1 Propósito"},
        ],
    }
    lines = _render_authored(section, None)
    md = "\n".join(lines)
    assert "### 1.1 Propósito" in md
    assert "Sin contenido" in md


def test_render_authored_no_subsections():
    section = {"id": "custom", "title": "Custom Section", "kind": "authored"}
    lines = _render_authored(section, {"custom": "Custom content."})
    md = "\n".join(lines)
    assert "## Custom Section" in md
    assert "Custom content." in md


# ---------------------------------------------------------------------------
# _render_goals
# ---------------------------------------------------------------------------


def _mock_goal(
    code: str = "GOAL-AB12",
    statement: str = "Goal statement",
    kind: str = "functional_goal",
    rationale: str | None = None,
    gid: int = 1,
):
    return SimpleNamespace(
        id=gid,
        code=code,
        statement=statement,
        kind=SimpleNamespace(value=kind),
        rationale=rationale,
    )


def test_render_goals_empty():
    lines = _render_goals([], {}, {})
    md = "\n".join(lines)
    assert "Sin goals inferidos" in md


def test_render_goals_grouped_by_kind():
    goals = [
        _mock_goal("GOAL-F1", "Funcional 1", "functional_goal", gid=1),
        _mock_goal("GOAL-S1", "Softgoal 1", "softgoal", gid=2),
        _mock_goal("GOAL-O1", "Obstáculo 1", "obstacle", gid=3),
    ]
    lines = _render_goals(goals, [], {})
    md = "\n".join(lines)
    assert "### Goals funcionales" in md
    assert "### Softgoals" in md
    assert "### Obstáculos" in md
    # Canonical order: functional_goal before softgoal before obstacle.
    pos_func = md.index("Goals funcionales")
    pos_soft = md.index("Softgoals")
    pos_obs = md.index("Obstáculos")
    assert pos_func < pos_soft < pos_obs


def test_render_goals_with_links():
    goals = [_mock_goal("GOAL-F1", "Funcional", "functional_goal", gid=10)]
    links = [
        SimpleNamespace(
            goal_id=10,
            req_id=99,
            relation=SimpleNamespace(value="realizes"),
        ),
    ]
    id_to_code = {99: "REQ-AB12"}
    lines = _render_goals(goals, links, id_to_code)
    md = "\n".join(lines)
    assert "Realiza:" in md
    assert "REQ-AB12" in md


# ---------------------------------------------------------------------------
# _render_requirements
# ---------------------------------------------------------------------------


def _mock_item(
    code: str = "REQ-AB12",
    statement: str = "El sistema debe hacer algo.",
    rtype: ReqType = ReqType.FUNCTIONAL,
    priority: Priority = Priority.MUST,
    derived: bool = False,
    span_verified: bool = True,
    explicit: bool = True,
    acceptance_criteria: list | None = None,
    source=None,
):
    return SimpleNamespace(
        code=code,
        statement=statement,
        type=rtype,
        priority=priority,
        derived=derived,
        span_verified=span_verified,
        explicit=explicit,
        acceptance_criteria=acceptance_criteria or [],
        source=source,
    )


def test_render_requirements_empty():
    lines = _render_requirements("4. Funcional", "functional", [])
    md = "\n".join(lines)
    assert "Sin requerimientos en esta sección" in md


def test_render_requirements_groups_by_priority():
    items = [
        _mock_item("REQ-01", "Must item", priority=Priority.MUST),
        _mock_item("REQ-02", "Should item", priority=Priority.SHOULD),
    ]
    lines = _render_requirements("4. Funcional", "functional", items)
    md = "\n".join(lines)
    assert "### Must (obligatorio)" in md
    assert "### Should (deseable)" in md
    assert "REQ-01" in md
    assert "REQ-02" in md


def test_render_requirements_nfr_groups_by_subtype():
    items = [
        _mock_item("REQ-P1", "Perf req", rtype=ReqType.PERFORMANCE),
        _mock_item("REQ-S1", "Sec req", rtype=ReqType.SECURITY),
    ]
    lines = _render_requirements("6. NFR", "nfr", items)
    md = "\n".join(lines)
    assert "### Rendimiento" in md
    assert "### Seguridad" in md
    assert "REQ-P1" in md
    assert "REQ-S1" in md


def test_render_requirements_nfr_skips_empty_subtypes():
    items = [
        _mock_item("REQ-P1", "Perf req", rtype=ReqType.PERFORMANCE),
    ]
    lines = _render_requirements("6. NFR", "nfr", items)
    md = "\n".join(lines)
    assert "### Rendimiento" in md
    # Should NOT have empty Usabilidad/Seguridad/etc headers.
    assert "### Seguridad" not in md
    assert "### Usabilidad" not in md


# ---------------------------------------------------------------------------
# _render_single_item
# ---------------------------------------------------------------------------


def test_render_single_item_basic():
    it = _mock_item("REQ-01", "El sistema debe hacer X.")
    lines = _render_single_item(it)
    md = "\n".join(lines)
    assert "#### `REQ-01`" in md
    assert "El sistema debe hacer X." in md


def test_render_single_item_shows_priority_and_type():
    it = _mock_item(
        "REQ-01",
        "El sistema debe hacer X.",
        rtype=ReqType.SECURITY,
        priority=Priority.SHOULD,
    )
    md = "\n".join(_render_single_item(it))
    assert "**Prioridad:** Should (deseable)" in md
    assert "**Tipo:** Seguridad" in md


def test_render_single_item_with_acceptance_criteria():
    it = _mock_item(
        "REQ-01",
        "El sistema debe hacer X.",
        acceptance_criteria=["Dado un usuario, cuando hace click, entonces se guarda."],
    )
    lines = _render_single_item(it)
    md = "\n".join(lines)
    assert "Criterios de aceptación" in md
    assert "Dado un usuario" in md


def test_render_single_item_with_flags():
    it = _mock_item(
        "REQ-01",
        "Stmt",
        derived=True,
        span_verified=False,
        explicit=False,
    )
    lines = _render_single_item(it)
    md = "\n".join(lines)
    assert "derivado" in md
    assert "sin cita verificada" in md
    assert "implícito" in md


# ---------------------------------------------------------------------------
# SRS_STRUCTURE invariants
# ---------------------------------------------------------------------------


def test_srs_structure_has_11_sections():
    assert len(SRS_STRUCTURE) == 11


def test_srs_structure_has_business_rules():
    ids = [s["id"] for s in SRS_STRUCTURE]
    assert "business_rules" in ids


def test_srs_structure_authored_has_subsections():
    for section in SRS_STRUCTURE:
        if section["kind"] == "authored":
            assert "subsections" in section
            assert len(section["subsections"]) > 0


def test_srs_structure_intro_has_5_subsections():
    intro = next(s for s in SRS_STRUCTURE if s["id"] == "intro")
    assert len(intro["subsections"]) == 5
    sub_ids = [s["id"] for s in intro["subsections"]]
    assert "intro.definitions" in sub_ids
    assert "intro.references" in sub_ids
    assert "intro.overview" in sub_ids


def test_srs_structure_overall_has_6_subsections():
    overall = next(s for s in SRS_STRUCTURE if s["id"] == "overall")
    assert len(overall["subsections"]) == 6
    sub_ids = [s["id"] for s in overall["subsections"]]
    assert "overall.actors" in sub_ids
    assert "overall.features" in sub_ids
    assert "overall.users" in sub_ids
    assert "overall.environment" in sub_ids
    assert "overall.assumptions" in sub_ids
    # La de actores es proyectada y va antes de las authored.
    actors_sub = next(s for s in overall["subsections"] if s["id"] == "overall.actors")
    assert actors_sub["kind"] == "projected"
    assert sub_ids.index("overall.actors") < sub_ids.index("overall.users")


# --------------------------------------------------------------------------- #
# _render_actors_table / subsection projected                                  #
# --------------------------------------------------------------------------- #


def _mock_actor(
    code: str = "R1",
    name: str = "Coordinador de terreno",
    channel: str | None = "humano",
    status: ActorStatus = ActorStatus.ACTIVE,
):
    return SimpleNamespace(
        code=code, name=name, channel=channel, status=status
    )


def test_render_actors_table_with_actors():
    lines = _render_actors_table([_mock_actor(), _mock_actor("R2", "Auditor", None)])
    md = "\n".join(lines)
    assert "| Código | Actor | Canal |" in md
    assert "`R1` | Coordinador de terreno | humano" in md
    # Canal ausente se renderiza con guion, no con None.
    assert "`R2` | Auditor | —" in md


def test_render_actors_table_skips_retired_and_empty():
    retired = _mock_actor("R3", "Consultor", status=ActorStatus.RETIRED)
    assert "R3" not in "\n".join(_render_actors_table([retired]))
    assert "Sin actores definidos" in "\n".join(_render_actors_table([]))


def test_render_authored_projects_actors_subsection():
    """La subsección proyectada overall.actors renderiza la tabla aunque no
    haya narrative keys (el contenido no es editable)."""
    section = next(s for s in SRS_STRUCTURE if s["id"] == "overall")
    lines = _render_authored(
        section,
        {"overall.users": "Prosa de usuarios."},
        actors=[_mock_actor()],
    )
    md = "\n".join(lines)
    assert "### 2.3 Actores del sistema" in md
    assert "`R1` | Coordinador de terreno" in md
    assert "### 2.4 Clases y características de usuarios" in md
    assert "Prosa de usuarios." in md


def test_render_authored_actors_table_without_narrative_keys():
    """Legacy (sin subsection keys en narrative): la tabla igual aparece."""
    section = next(s for s in SRS_STRUCTURE if s["id"] == "overall")
    lines = _render_authored(section, {}, actors=[_mock_actor()])
    md = "\n".join(lines)
    assert "### 2.3 Actores del sistema" in md
    assert "`R1`" in md
