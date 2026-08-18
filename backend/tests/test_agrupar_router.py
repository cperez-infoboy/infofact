"""Tests de la ruta directa de ``/agrupar`` (backend/routers/chat.py).

Pins:
- ``_is_pure_agrupar``: exact match (espacios alrededor sí, texto extra no).
- ``_agrupar_directive`` / ``_rewrite_command``: la rama /agrupar produce la
  directiva con steering embebido (patron INSTRUCCIONES DEL USUARIO).
- ``_summarize_grouping``: acotado a 20 lineas de grupos, mensaje <2,
  sin duplicados, y texto de error.
- ``_agrupar_direct_stream``: secuencia SSE exacta, liberacion de la guarda
  ``_active_streams``, espejo assistant en happy path y ``failed`` en excepcion.
"""
from __future__ import annotations

import json

import pytest

from backend.routers import chat
from backend.services import agent_service


# --- _is_pure_agrupar ------------------------------------------------------

def test_is_pure_agrupar_exact_match():
    assert chat._is_pure_agrupar("/agrupar")
    assert chat._is_pure_agrupar("  /agrupar ")  # espacios alrededor sí
    assert chat._is_pure_agrupar("\n/agrupar\t")


def test_is_pure_agrupar_rejects_extra_text_and_other_commands():
    assert not chat._is_pure_agrupar("/agrupar solo seguridad")
    assert not chat._is_pure_agrupar("/agrupar solo facturación")
    assert not chat._is_pure_agrupar("/captura")
    assert not chat._is_pure_agrupar("agrupar")  # sin slash: texto normal
    assert not chat._is_pure_agrupar("")
    assert not chat._is_pure_agrupar("/Agrupar")  # case-sensitive (paridad rewrite)


# --- _agrupar_directive / _rewrite_command ---------------------------------

def test_agrupar_directive_without_steering():
    d = chat._agrupar_directive("")
    assert d.startswith("[DIRECTIVE]")
    assert "requirements-capture-agent" in d
    assert "review_grouping" in d
    assert "apply_grouping_plan" in d
    assert "INSTRUCCIONES DEL USUARIO" not in d


def test_agrupar_directive_with_steering_embeds_user_text():
    d = chat._agrupar_directive("solo los de seguridad")
    # chr(34): comillas literales alrededor del steering (patron _srs_directive).
    assert 'INSTRUCCIONES DEL USUARIO: "solo los de seguridad"' in d
    assert "types" in d and "documents" in d


def test_rewrite_command_agrupar_pure_maps_to_directive():
    out = chat._rewrite_command("/agrupar")
    assert out == chat._agrupar_directive("")


def test_rewrite_command_agrupar_with_steering():
    out = chat._rewrite_command("/agrupar solo facturación")
    assert out == chat._agrupar_directive("solo facturación")
    assert "solo facturación" in out


def test_rewrite_command_other_commands_untouched():
    assert chat._rewrite_command("hola") == "hola"
    assert chat._rewrite_command("/srs etc").startswith("[DIRECTIVE]")


# --- guardrails de exploración ---------------------------------------------

def test_agrupar_directive_has_anti_exploration_guard():
    d = chat._agrupar_directive("")
    # Paridad con _captura/_srs/_analysis: prohibición explícita de explorar.
    assert "NO explores el sistema de archivos" in d
    assert "sin ls, glob, read_file ni execute" in d
    # El dato que faltaba: dónde viven los requerimientos.
    assert "base de datos del backend" in d
    assert "NO en archivos del sandbox" in d


def test_agrupar_directive_attributes_review_grouping_to_subagent():
    d = chat._agrupar_directive("")
    # review_grouping vive en el subagente: la directiva no puede ordenar
    # al orquestador invocar una tool que no tiene.
    assert "es el subagente quien invoca `review_grouping`" in d
    assert "no es una tool tuya" in d
    assert "Invoca la tool" not in d


def test_phase_prompt_states_data_location():
    prompt = agent_service.PHASE_PROMPTS["requirements"]
    assert "fuera del sandbox" in prompt
    assert ".db" in prompt and ".sqlite" in prompt
    assert "no uses execute para salir del directorio del proyecto" in prompt


# --- _summarize_grouping ---------------------------------------------------

def _group(keeper: str, members: list[str]) -> dict:
    return {
        "keeper": {"id": 1, "code": keeper, "statement": "s"},
        "members": [
            {"id": i, "code": c, "statement": "s"} for i, c in enumerate(members)
        ],
        "reason": "duplicado exacto",
        "confidence": 1.0,
        "decision": "pending",
    }


def test_summarize_grouping_truncates_to_twenty_lines():
    result = {
        "plan_id": 7,
        "group_count": 25,
        "considered": 40,
        "scope": "tipos: functional",
        "groups": [
            _group(f"REQ-{i:03d}", ["REQ-901", "REQ-902"]) for i in range(25)
        ],
    }
    out = chat._summarize_grouping(result)
    lines = out.splitlines()
    group_lines = [ln for ln in lines if ln.startswith("- ")]
    assert len(group_lines) == chat._AGRUPAR_SUMMARY_MAX_GROUPS == 20
    assert "… y 5 grupo(s) más" in out
    assert "#7" in lines[0] and "25 grupo(s)" in lines[0]
    assert "40 requerimiento(s)" in lines[0]
    assert "tipos: functional" in lines[0]
    assert "No se fusionó nada todavía" in out


def test_summarize_grouping_member_codes_capped_at_two_plus_count():
    result = {
        "plan_id": 1,
        "group_count": 1,
        "considered": 5,
        "scope": "",
        "groups": [_group("REQ-001", ["REQ-002", "REQ-003", "REQ-004", "REQ-005"])],
    }
    out = chat._summarize_grouping(result)
    assert "- REQ-001 ← REQ-002, REQ-003 (+2)" in out


def test_summarize_grouping_zero_groups():
    out = chat._summarize_grouping(
        {"plan_id": 2, "group_count": 0, "considered": 10, "scope": "", "groups": []}
    )
    assert "No se detectaron duplicados (10 analizados)" in out


def test_summarize_grouping_considered_below_two():
    out = chat._summarize_grouping(
        {"plan_id": 3, "group_count": 0, "considered": 1, "scope": "", "groups": []}
    )
    assert "menos de 2" in out
    assert "1 requerimiento(s)" in out


def test_summarize_grouping_error_text():
    out = chat._summarize_grouping({"error": "boom"})
    assert out == "No se pudo revisar el agrupamiento: boom"


# --- _agrupar_direct_stream ------------------------------------------------

class _FakeSession:
    """Session falsa: captura los adds para verificar el espejo assistant."""

    instances: list["_FakeSession"] = []

    def __init__(self):
        self.added = []
        type(self).instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None


def _parse_frames(frames: list[str]) -> list[tuple[str, dict]]:
    out = []
    for frame in frames:
        event_line, data_line = frame.splitlines()[:2]
        assert event_line.startswith("event: ")
        assert data_line.startswith("data: ")
        out.append(
            (event_line[len("event: "):], json.loads(data_line[len("data: "):]))
        )
    return out


def _happy_result() -> dict:
    return {
        "plan_id": 3,
        "group_count": 2,
        "considered": 8,
        "scope": "",
        "groups": [_group("REQ-001", ["REQ-002"]), _group("REQ-003", ["REQ-004"])],
    }


@pytest.mark.asyncio
async def test_agrupar_direct_stream_happy_sequence(monkeypatch):
    async def fake_review(session, project_id, *, on_progress=None):
        assert project_id == 42
        return _happy_result()

    monkeypatch.setattr(chat, "run_grouping_review", fake_review)
    monkeypatch.setattr(chat, "AsyncSessionLocal", _FakeSession)
    _FakeSession.instances = []
    chat._active_streams.add("s1")

    frames = [f async for f in chat._agrupar_direct_stream(1, 42, "s1")]
    events = _parse_frames(frames)
    names = [name for name, _ in events]

    assert names[0] == "tool_start"
    assert events[0][1] == {"name": "review_grouping", "input": {}}
    assert names[1] == "tool_end"
    assert events[1][1]["name"] == "review_grouping"
    token_frames = [d for n, d in events if n == "token"]
    assert token_frames, "esperaba al menos un frame token con el resumen"
    ready_idx = names.index("grouping.ready")
    assert names[ready_idx:] == ["grouping.ready", "completed"]
    assert events[ready_idx][1] == {"plan_id": 3, "group_count": 2}
    # El resumen viaja en los tokens y en completed (mismo texto persistido).
    summary = "".join(d["delta"] for d in token_frames)
    assert "Plan de agrupamiento #3" in summary
    assert events[-1][1]["message"] == summary
    # Guarda liberada y espejo assistant persistido (best-effort).
    assert "s1" not in chat._active_streams
    mirrors = [s.added for s in _FakeSession.instances if s.added]
    assert len(mirrors) == 1
    mirror = mirrors[0][0]
    assert mirror.role == "assistant"
    assert mirror.session_id == 1
    assert mirror.content == summary


@pytest.mark.asyncio
async def test_agrupar_direct_stream_error_sequence(monkeypatch):
    async def boom(_session, _project_id, *, on_progress=None):
        raise RuntimeError("DB exploded")

    monkeypatch.setattr(chat, "run_grouping_review", boom)
    monkeypatch.setattr(chat, "AsyncSessionLocal", _FakeSession)
    _FakeSession.instances = []
    chat._active_streams.add("s2")

    frames = [f async for f in chat._agrupar_direct_stream(9, 42, "s2")]
    events = _parse_frames(frames)
    names = [name for name, _ in events]

    # tool_end con el error (evita chip eterno) + failed; sin espejo ni ready.
    assert names == ["tool_start", "tool_end", "failed"]
    assert "RuntimeError: DB exploded" in events[1][1]["output"]
    assert events[2][1]["error"] == "RuntimeError: DB exploded"
    assert "s2" not in chat._active_streams
    assert not [s.added for s in _FakeSession.instances if s.added]


@pytest.mark.asyncio
async def test_agrupar_direct_stream_error_result_no_ready_event(monkeypatch):
    """run_grouping_review devuelve {"error": ...} (no lanza): resumen con el
    error, completed (no failed) y SIN grouping.ready ni espejo basura... el
    espejo sí persiste (el resumen es la respuesta del turno)."""

    async def error_result(_session, _project_id, *, on_progress=None):
        return {"error": "review_grouping failed: algo"}

    monkeypatch.setattr(chat, "run_grouping_review", error_result)
    monkeypatch.setattr(chat, "AsyncSessionLocal", _FakeSession)
    _FakeSession.instances = []
    chat._active_streams.add("s3")

    frames = [f async for f in chat._agrupar_direct_stream(1, 42, "s3")]
    events = _parse_frames(frames)
    names = [name for name, _ in events]

    assert "grouping.ready" not in names
    assert names[-1] == "completed"
    token_text = "".join(d["delta"] for n, d in events if n == "token")
    assert token_text.startswith("No se pudo revisar el agrupamiento")
    assert "s3" not in chat._active_streams
