"""Tests de las funciones puras del repair script (sesión 44).

Importa ``scripts/repair_poisoned_thread.py`` por path con importlib (scripts/
no es un paquete). Solo ejerce la lógica pura: preserva id/tool_calls,
trunca head 4.000 + marcador + tail 1.000, ignora los chicos, aborta los
sin-id. No toca DB ni checkpointer.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "repair_poisoned_thread.py"
_spec = importlib.util.spec_from_file_location("repair_poisoned_thread", _SCRIPT)
repair = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("repair_poisoned_thread", repair)
_spec.loader.exec_module(repair)

THRESHOLD = repair.DEFAULT_THRESHOLD  # 10_000


def test_replaces_giant_assistant_preserving_metadata():
    tool_calls = [{"name": "task", "args": {}, "id": "call-9"}]
    original = AIMessage(id="ai-44", content="z" * 1_100_000, tool_calls=tool_calls)

    rep = repair.build_replacement(original, threshold=THRESHOLD)

    assert rep is not None
    # Mismo id, mismo tipo, mismos tool_calls: add_messages hace upsert.
    assert isinstance(rep, AIMessage)
    assert rep.id == original.id
    # AIMessage normaliza tool_calls al construir (agrega "type": "tool_call");
    # se preserva exactamente lo que tenía el original (normalizado).
    assert rep.tool_calls == original.tool_calls
    # Content truncado: head + marcador + tail.
    assert rep.content.startswith("z" * repair.REPAIR_HEAD_CHARS)
    assert rep.content.endswith("z" * repair.REPAIR_TAIL_CHARS)
    omitted = 1_100_000 - repair.REPAIR_HEAD_CHARS - repair.REPAIR_TAIL_CHARS
    assert f"se omitieron {omitted} caracteres" in rep.content
    assert f"(original 1100000)" in rep.content
    assert len(rep.content) < 10_000
    # El original NO se muta.
    assert len(original.content) == 1_100_000


def test_replaces_giant_tool_message_preserving_tool_call_id():
    original = ToolMessage(
        id="tm-44", content="w" * 50_000, tool_call_id="call-9", name="task"
    )

    rep = repair.build_replacement(original, threshold=THRESHOLD)

    assert rep is not None
    assert isinstance(rep, ToolMessage)
    assert rep.id == "tm-44"
    assert rep.tool_call_id == "call-9"
    assert rep.name == "task"
    assert "reparacion" in rep.content
    assert len(rep.content) < 10_000


def test_small_messages_are_ignored():
    # Bajo el umbral...
    assert repair.build_replacement(HumanMessage("hola", id="h-1"), threshold=THRESHOLD) is None
    # ...y justo EN el umbral (oversize es estrictamente mayor).
    edge = AIMessage(id="a-edge", content="x" * THRESHOLD)
    assert repair.build_replacement(edge, threshold=THRESHOLD) is None


def test_non_text_content_is_ignored():
    # Bloques de imagen: no son str, quedan intactos (acotados de origen por
    # los settings de vision).
    msg = AIMessage(
        id="a-img",
        content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}],
    )
    assert repair.build_replacement(msg, threshold=THRESHOLD) is None


def test_giant_message_without_id_raises():
    no_id = AIMessage(content="q" * 100_000)
    assert no_id.id is None

    # Sin id, aupdate_state appendearía una copia en vez de reemplazar:
    # el item se aborta con error explícito (el caller lo saltea con warning).
    with pytest.raises(ValueError, match="sin id"):
        repair.build_replacement(no_id, threshold=THRESHOLD)


def test_truncate_repair_content_exact_shape():
    text = "a" * 100_000
    marker = (
        "[contenido truncado por reparacion: se omitieron 95000 caracteres "
        "(original 100000)]"
    )
    assert repair.truncate_repair_content(text) == (
        "a" * repair.REPAIR_HEAD_CHARS
        + "\n"
        + marker
        + "\n"
        + "a" * repair.REPAIR_TAIL_CHARS
    )
    # Texto corto pasa tal cual.
    assert repair.truncate_repair_content("corto") == "corto"
