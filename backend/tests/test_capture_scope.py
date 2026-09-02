"""Enforcement mecánico del alcance pedido en la captura (lección sesión 13).

La carpeta pedida tras ``/captura`` se registra por proyecto
(``capture_run_holder``) y viaja a la directiva como ``[SCOPE]``. Las tools
``orient_documents`` e ``ingest_documents`` la verifican mecánicamente: una
llamada que omite el argumento (default = todo el proyecto) o nombra otra
carpeta recibe ``scope_violation`` con instrucción correctiva, en vez de
descubrir el workspace entero. Fija además que ``/captura <carpeta>`` separa
la carpeta del steering libre.
"""
from __future__ import annotations

import pytest

import backend.agents.subagents.requirements_capture_agent as mod
from backend.agents.subagents import capture_run_holder as holder
from backend.routers.chat import (
    _captura_agente_directive,
    _extract_captura_scope,
    _rewrite_command,
)

PROJECT_ID = 4242

CARPETA = "Docs_Entrada/Requerimientos_post_reunion_20_08_2026"


@pytest.fixture(autouse=True)
def _isolate_scope():
    holder.clear_capture_scope(PROJECT_ID)
    yield
    holder.clear_capture_scope(PROJECT_ID)


# --- normalización y registro ---------------------------------------------


def test_normalize_strips_at_quotes_and_slashes():
    assert holder.normalize_scope("@" + CARPETA + "/") == CARPETA
    assert holder.normalize_scope('"' + CARPETA + '"') == CARPETA
    assert holder.normalize_scope("//" + CARPETA) == CARPETA


def test_normalize_empty_and_dot_mean_no_scope():
    assert holder.normalize_scope("") == ""
    assert holder.normalize_scope(".") == ""
    assert holder.normalize_scope("  ") == ""


def test_normalize_refuses_traversal_and_absolute():
    with pytest.raises(ValueError):
        holder.normalize_scope("../otro")
    with pytest.raises(ValueError):
        holder.normalize_scope("/etc/passwd")


def test_set_and_get_scope_roundtrip():
    out = holder.set_capture_scope(PROJECT_ID, "@" + CARPETA)
    assert out == CARPETA
    assert holder.get_capture_scope(PROJECT_ID) == CARPETA


def test_set_empty_scope_clears_previous():
    holder.set_capture_scope(PROJECT_ID, CARPETA)
    holder.set_capture_scope(PROJECT_ID, "")
    assert holder.get_capture_scope(PROJECT_ID) == ""


def test_scope_mismatch_omitted_target_is_violation():
    holder.set_capture_scope(PROJECT_ID, CARPETA)
    out = holder.scope_mismatch(PROJECT_ID, "")
    assert out is not None
    assert out["error"] == "scope_violation"
    assert out["requested"] == CARPETA
    assert CARPETA in out["message"]


def test_scope_mismatch_other_folder_is_violation():
    holder.set_capture_scope(PROJECT_ID, CARPETA)
    assert holder.scope_mismatch(PROJECT_ID, "Observaciones") is not None


def test_scope_mismatch_accepts_decorated_variants():
    holder.set_capture_scope(PROJECT_ID, CARPETA)
    assert holder.scope_mismatch(PROJECT_ID, "@" + CARPETA + "/") is None


def test_no_scope_registered_means_no_mismatch_ever():
    # Sin alcance pedido, el comportamiento actual no cambia: "" y cualquier
    # carpeta pasan (la contención real la hace _resolve_target).
    assert holder.scope_mismatch(PROJECT_ID, "") is None
    assert holder.scope_mismatch(PROJECT_ID, "Observaciones") is None


# --- tools: verificación mecánica en orient/ingest -------------------------


def _make_orient_tool():
    return mod._make_orient_tool(
        PROJECT_ID, __import__("pathlib").Path("/tmp/ws")
    )


@pytest.mark.asyncio
async def test_orient_rejects_omitted_target_when_scoped():
    holder.set_capture_scope(PROJECT_ID, CARPETA)
    tool = _make_orient_tool()
    out = await tool.ainvoke({"target_subpath": ""})
    assert out["error"] == "scope_violation"
    assert out["requested"] == CARPETA


@pytest.mark.asyncio
async def test_orient_rejects_wrong_folder_when_scoped():
    holder.set_capture_scope(PROJECT_ID, CARPETA)
    tool = _make_orient_tool()
    out = await tool.ainvoke({"target_subpath": "Observaciones"})
    assert out["error"] == "scope_violation"


@pytest.mark.asyncio
async def test_orient_accepts_requested_folder_without_discovering(
    monkeypatch,
):
    # Caso feliz: el target correcto pasa el gate. Con _resolve_target real
    # fallaría porque /tmp/ws/<carpeta> no existe; el stub prueba SOLO el
    # enforcement (un stub que devuelve None probaría el flujo completo de la
    # tool, que ya cubren los tests de etapas).
    def fake_resolve(ws, subpath):
        return __import__("pathlib").Path("/tmp/ws") / subpath

    monkeypatch.setattr(mod, "_resolve_target", fake_resolve)
    holder.set_capture_scope(PROJECT_ID, CARPETA)
    tool = _make_orient_tool()
    out = await tool.ainvoke({"target_subpath": CARPETA})
    assert "error" not in out


@pytest.mark.asyncio
async def test_ingest_rejects_omitted_target_when_scoped():
    holder.set_capture_scope(PROJECT_ID, CARPETA)
    tools = mod._make_stage_tools(
        PROJECT_ID, __import__("pathlib").Path("/tmp/ws"), "Proj", "desc"
    )
    out = await tools[0].ainvoke({"target_subpath": "", "on_existing": "append"})
    assert out["error"] == "scope_violation"
    # El rechazo es PRE-pipeline: no crea run ni descubre documentos.
    assert holder.get_run(PROJECT_ID) is None


@pytest.mark.asyncio
async def test_ingest_wrong_folder_rejected_before_existing_gate():
    holder.set_capture_scope(PROJECT_ID, CARPETA)
    tools = mod._make_stage_tools(
        PROJECT_ID, __import__("pathlib").Path("/tmp/ws"), "Proj", "desc"
    )
    out = await tools[0].ainvoke({"target_subpath": "Observaciones"})
    assert out["error"] == "scope_violation"
    assert holder.get_run(PROJECT_ID) is None


@pytest.mark.asyncio
async def test_commit_clears_scope():
    holder.set_capture_scope(PROJECT_ID, CARPETA)
    holder.clear_capture_scope(PROJECT_ID)  # commit exitoso llama a clear
    assert holder.get_capture_scope(PROJECT_ID) == ""


# --- router: extracción de scope desde el comando --------------------------


def test_extract_scope_folder_plus_steering():
    scope, rest = _extract_captura_scope(CARPETA + " sin borrar lo existente")
    assert scope == CARPETA
    assert rest == "sin borrar lo existente"


def test_extract_scope_at_decorated_folder():
    scope, rest = _extract_captura_scope("@" + CARPETA)
    assert scope == CARPETA
    assert rest == ""


def test_extract_scope_empty_text_is_all_steering():
    scope, rest = _extract_captura_scope("")
    assert scope == ""
    assert rest == ""


def test_extract_scope_plain_steering_stays_free():
    # Un steering sin separadores ni extensión no es una carpeta.
    scope, rest = _extract_captura_scope("dale atencion a las restricciones")
    assert scope == ""
    assert rest == "dale atencion a las restricciones"


def test_extract_scope_quoted_folder():
    scope, rest = _extract_captura_scope('"' + CARPETA + '" enfocate en montos')
    assert scope == CARPETA
    assert rest == "enfocate en montos"


def test_rewrite_absolute_path_degrades_to_steering():
    # Ruta absoluta: set_capture_scope rechaza y el router degrada a steering
    # sin romper el envio ni registrar scope (la contencion final la hace
    # _resolve_target en las tools).
    out = _rewrite_command("/captura /etc/passwd", project_id=PROJECT_ID)
    assert "[SCOPE]" not in out
    assert holder.get_capture_scope(PROJECT_ID) == ""
    assert 'INSTRUCCIONES DEL USUARIO: "/etc/passwd"' in out


def test_extract_scope_multi_path_list_stays_steering():
    # Varias rutas: ingest_documents solo acepta UNA carpeta; una lista seria
    # un contrato imposible, asi que queda como steering libre.
    scope, rest = _extract_captura_scope("docs/a.pdf, docs/b.pdf foco en precios")
    assert scope == ""
    assert rest == "docs/a.pdf, docs/b.pdf foco en precios"


def test_rewrite_with_project_registers_scope_and_embeds_it():
    out = _rewrite_command("/captura @" + CARPETA, project_id=PROJECT_ID)
    assert "[SCOPE]" in out
    assert CARPETA in out
    assert "INSTRUCCIONES DEL USUARIO" not in out
    assert holder.get_capture_scope(PROJECT_ID) == CARPETA


def test_rewrite_with_project_keeps_steering_after_folder():
    out = _rewrite_command(
        "/captura " + CARPETA + " sin borrar lo existente",
        project_id=PROJECT_ID,
    )
    assert "[SCOPE]" in out
    assert 'INSTRUCCIONES DEL USUARIO: "sin borrar lo existente"' in out


def test_rewrite_without_project_id_does_not_touch_scope():
    # Los tests existentes llaman sin project_id: no se registra nada ni
    # aparece [SCOPE] (el registro ocurre solo en el flujo real del router).
    out = _rewrite_command("/captura docs/x")
    assert "[SCOPE]" not in out
    assert holder.get_capture_scope(PROJECT_ID) == ""
    assert 'INSTRUCCIONES DEL USUARIO: "docs/x"' in out


def test_rewrite_captura_agente_never_extracts_scope():
    out = _rewrite_command("/captura_agente " + CARPETA, project_id=PROJECT_ID)
    assert "[SCOPE]" not in out
    assert 'INSTRUCCIONES DEL USUARIO: "' + CARPETA + '"' in out


def test_directive_with_scope_carries_contract():
    out = _captura_agente_directive("some steering", CARPETA)
    assert "[SCOPE]" in out
    assert f"target_subpath='{CARPETA}'" in out
    assert "scope_violation" in out
