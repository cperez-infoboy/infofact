"""Tests del módulo compartido de reglas de calidad (shift-left de captura).

Cubre:
- ``programmatic_findings_for_text``: disparo de cada regla determinista y la
  compuerta de ``req_type`` para ``incose.unmeasurable_nfr``.
- ``_apply_quality_flags`` (critique): el pre-check programático se fusiona de
  forma autoritativa con el veredicto del crítico (atomic/verifiable/reasons).
- Paridad: ``srs_quality.programmatic_findings`` (wrapper) produce los mismos
  ``rule_id`` que el motor puro para los enunciados del smoke.
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.agents.pipelines import _quality_rules
from backend.agents.pipelines._quality_rules import (
    PREVENTION_RULES,
    ProgrammaticFinding,
    programmatic_findings_for_text,
)
from backend.agents.pipelines.critique import CritiqueVerdict, _apply_quality_flags
from backend.models.requirement import ReqType


def _ids(findings) -> set[str]:
    return {f.rule_id for f in findings}


# --- programmatic_findings_for_text: disparo por regla -----------------------

def test_vague_term_fires():
    ids = _ids(programmatic_findings_for_text("El sistema debe ser rápido al cargar."))
    assert "smell.vague_term" in ids


def test_negation_fires():
    ids = _ids(programmatic_findings_for_text("El sistema no debe permitir accesos anónimos."))
    assert "smell.negation" in ids


def test_combinator_y_o_fires():
    ids = _ids(programmatic_findings_for_text("El sistema debe exportar a PDF y/o DOCX."))
    assert "smell.combinator" in ids


def test_combinator_slash_fires():
    ids = _ids(programmatic_findings_for_text("El sistema debe soportar roles admin/usuario."))
    assert "smell.combinator" in ids


def test_absolute_fires():
    ids = _ids(programmatic_findings_for_text("El sistema debe estar disponible el 100% del tiempo."))
    assert "smell.absolute" in ids


def test_too_long_fires():
    stmt = (
        "El sistema debe permitir al administrador gestionar de manera completa "
        "todos los aspectos relacionados con la facturación electrónica incluyendo "
        "la creación, edición, eliminación, envío al ente recaudador, descarga en "
        "formato PDF firmado digitalmente, reenvío por correo y auditoría de cada "
        "operación realizada sobre el comprobante durante todo su ciclo de vida."
    )
    assert len(stmt) > 220
    ids = _ids(programmatic_findings_for_text(stmt))
    assert "incose.too_long" in ids


def test_modal_missing_fires():
    ids = _ids(programmatic_findings_for_text("Autenticación de usuarios vía Google OAuth."))
    assert "incose.modal_missing" in ids


def test_unmeasurable_nfr_fires_with_req_type():
    ids = _ids(programmatic_findings_for_text(
        "El sistema debe procesar conciliaciones bancarias.", req_type="performance"))
    assert "incose.unmeasurable_nfr" in ids


def test_unmeasurable_nfr_skipped_without_req_type():
    # En captura (pre-clasificación) el tipo aún no existe -> se omite la regla.
    ids = _ids(programmatic_findings_for_text(
        "El sistema debe procesar conciliaciones bancarias."))
    assert "incose.unmeasurable_nfr" not in ids


def test_unmeasurable_nfr_skipped_for_non_measurable_type():
    ids = _ids(programmatic_findings_for_text(
        "El sistema debe procesar conciliaciones bancarias.", req_type="functional"))
    assert "incose.unmeasurable_nfr" not in ids


def test_measurable_nfr_not_flagged():
    ids = _ids(programmatic_findings_for_text(
        "El sistema debe responder el inicio de sesión en menos de 200 ms.", req_type="performance"))
    assert "incose.unmeasurable_nfr" not in ids


def test_clean_ears_yields_no_findings():
    stmt = (
        "Cuando el usuario presiona el botón de guardar, el sistema "
        "debe persistir el formulario validado."
    )
    assert programmatic_findings_for_text(stmt) == []


def test_ears_pattern_detection():
    assert (
        _quality_rules.detect_ears_pattern(
            "Cuando el usuario guarda, el sistema debe persistir los datos."
        )
        == "event_driven"
    )
    assert (
        _quality_rules.detect_ears_pattern(
            "Mientras la sesión esté activa, el sistema debe mantener el contexto."
        )
        == "state_driven"
    )
    assert _quality_rules.detect_ears_pattern("El sistema debe persistir los datos.") is None


# --- _apply_quality_flags: fusión autoritativa en la crítica -----------------

def _clean_verdict(item_id: str = "raw-0") -> CritiqueVerdict:
    return CritiqueVerdict(
        item_id=item_id,
        nature="requirement",
        fidelity="pass",
        atomic="pass",
        verifiable="pass",
        reasons={},
        suggested_rewrite=None,
    )


def test_apply_quality_flags_combinator_forces_atomic_fix():
    flags = [
        ProgrammaticFinding(
            rule_id="smell.combinator",
            dimension="requirement_smell",
            severity="major",
            message="combina alternativas",
        )
    ]
    v = _apply_quality_flags(_clean_verdict(), flags)
    assert v.atomic == "fix"
    assert "quality" in v.reasons


def test_apply_quality_flags_vague_forces_verifiable_flag():
    flags = [
        ProgrammaticFinding(
            rule_id="smell.vague_term",
            dimension="requirement_smell",
            severity="major",
            message="término vago",
        )
    ]
    v = _apply_quality_flags(_clean_verdict(), flags)
    assert v.verifiable == "flag"
    assert "quality" in v.reasons


def test_apply_quality_flags_informational_keeps_dimensions():
    flags = [
        ProgrammaticFinding(
            rule_id="incose.modal_missing",
            dimension="incose_rule",
            severity="minor",
            message="falta modal",
        )
    ]
    v = _apply_quality_flags(_clean_verdict(), flags)
    assert v.atomic == "pass"
    assert v.verifiable == "pass"
    assert "quality" in v.reasons


def test_apply_quality_flags_empty_leaves_verdict_unchanged():
    base = _clean_verdict()
    v = _apply_quality_flags(base, [])
    assert v.atomic == base.atomic
    assert v.verifiable == base.verifiable
    assert v.reasons == base.reasons


# --- Paridad: srs_quality wrapper == motor puro -----------------------------

def test_srs_quality_wrapper_parity():
    """``srs_quality.programmatic_findings`` (wrapper) == motor puro en rule_id."""
    from backend.services.srs_quality import programmatic_findings as wrapper

    cases = [
        ("El sistema debe ser rápido al responder.", ReqType.FUNCTIONAL),
        ("El sistema debe exportar a PDF y/o DOCX.", ReqType.FUNCTIONAL),
        ("El sistema no debe permitir accesos anónimos.", ReqType.FUNCTIONAL),
        ("El sistema debe procesar conciliaciones bancarias.", ReqType.PERFORMANCE),
        ("Cuando el usuario guarda, el sistema debe persistir el formulario.", ReqType.FUNCTIONAL),
    ]
    for stmt, rtype in cases:
        item = SimpleNamespace(id=7, statement=stmt, type=rtype)
        wrapper_ids = {f["rule_id"] for f in wrapper(item)}
        pure_ids = _ids(programmatic_findings_for_text(stmt, req_type=rtype.value))
        assert wrapper_ids == pure_ids, (stmt, wrapper_ids, pure_ids)


# --- PREVENTION_RULES: prevención y detección comparten vocabulario ---------

def test_prevention_rules_built_from_vague_terms():
    """La guía de prevención (Capa 1) reusa la blocklist de detección."""
    assert "EARS" in PREVENTION_RULES
    assert "ATOMIC" in PREVENTION_RULES
    # Algún término vago conocido debe quedar interpolado.
    assert "rápido" in PREVENTION_RULES
    # La blocklist interpolada está normalizada: ningún término con espacios líder.
    blocklist = _quality_rules._vague_blocklist()
    terms = blocklist.split(", ")
    assert terms
    assert all(not t.startswith(" ") and not t.endswith(" ") for t in terms)
    # El término defectuoso " scalable" queda normalizado a "scalable".
    assert "scalable" in terms


# --- Actores y enunciados paraguas (sesiones 7-9 de Planitrack) --------------

def test_actor_missing_fires_for_functional_without_role():
    ids = _ids(programmatic_findings_for_text(
        "El sistema debe exportar el reporte mensual a PDF.",
        req_type="functional"))
    assert "smell.actor_missing" in ids


def test_actor_missing_skipped_when_role_named():
    ids = _ids(programmatic_findings_for_text(
        "El administrador del tenant debe exportar el reporte mensual a PDF.",
        req_type="functional"))
    assert "smell.actor_missing" not in ids


def test_actor_missing_skipped_for_non_functional():
    ids = _ids(programmatic_findings_for_text(
        "El sistema debe responder el inicio de sesión en menos de 200 ms.",
        req_type="performance"))
    assert "smell.actor_missing" not in ids


def test_actor_missing_skipped_without_req_type():
    # En captura (pre-clasificación) el tipo aún no existe: no se marca. Allí
    # corre la prevención (PREVENTION_RULES), no la detección.
    ids = _ids(programmatic_findings_for_text(
        "El sistema debe exportar el reporte mensual a PDF."))
    assert "smell.actor_missing" not in ids


def test_actor_missing_accepts_project_role_terms():
    stmt = "El despachador debe asignar la ruta del día a cada móvil."
    # Sin catálogo: "despachador" no está en el léxico base -> dispara.
    assert "smell.actor_missing" in _ids(
        programmatic_findings_for_text(stmt, req_type="functional"))
    # Con el catálogo del proyecto: rol reconocido -> no dispara.
    assert "smell.actor_missing" not in _ids(
        programmatic_findings_for_text(
            stmt, req_type="functional", role_terms=["despachador"]))


def test_generic_user_fires_when_no_named_role():
    ids = _ids(programmatic_findings_for_text(
        "El usuario debe poder filtrar la grilla por fecha.",
        req_type="functional"))
    assert "actor.generic_user" in ids
    # Un rol específico nombrado gana aunque también se mencione «usuario».
    ids2 = _ids(programmatic_findings_for_text(
        "El supervisor debe poder asignar la tarea a un usuario.",
        req_type="functional"))
    assert "actor.generic_user" not in ids2


def test_umbrella_fires_on_verb_enumeration():
    ids = _ids(programmatic_findings_for_text(
        "El administrador debe crear, editar, eliminar, exportar e importar "
        "los contratos.",
        req_type="functional"))
    assert "smell.umbrella" in ids


def test_umbrella_fires_on_grouping_marker():
    # Caso real de Planitrack: «gestionar de manera completa todos los
    # aspectos... incluyendo la creación, edición, ...».
    stmt = (
        "El sistema debe permitir al administrador gestionar de manera completa "
        "todos los aspectos relacionados con la facturación electrónica."
    )
    ids = _ids(programmatic_findings_for_text(stmt, req_type="functional"))
    assert "smell.umbrella" in ids


def test_umbrella_skipped_for_atomic_statement():
    ids = _ids(programmatic_findings_for_text(
        "El administrador debe crear un contrato nuevo.",
        req_type="functional"))
    assert "smell.umbrella" not in ids


def test_considerar_and_contemplar_are_vague():
    for term in ("considerar", "contemplar"):
        ids = _ids(programmatic_findings_for_text(
            f"El sistema debe {term} el huso horario del tenant.",
            req_type="functional"))
        assert "smell.vague_term" in ids


def test_prevention_rules_cover_actor_and_umbrella():
    assert "ROL-PENDIENTE" in PREVENTION_RULES
    assert "umbrella" in PREVENTION_RULES


def test_apply_quality_flags_umbrella_forces_atomic_fix():
    flags = [
        ProgrammaticFinding(
            rule_id="smell.umbrella",
            dimension="requirement_smell",
            severity="major",
            message="enunciado paraguas",
        )
    ]
    v = _apply_quality_flags(_clean_verdict(), flags)
    assert v.atomic == "fix"


def test_apply_quality_flags_actor_rules_are_informational():
    flags = [
        ProgrammaticFinding(
            rule_id="smell.actor_missing",
            dimension="requirement_smell",
            severity="major",
            message="falta rol",
        ),
        ProgrammaticFinding(
            rule_id="actor.generic_user",
            dimension="requirement_smell",
            severity="minor",
            message="usuario genérico",
        ),
    ]
    v = _apply_quality_flags(_clean_verdict(), flags)
    assert v.atomic == "pass"
    assert v.verifiable == "pass"
    assert "quality" in v.reasons
