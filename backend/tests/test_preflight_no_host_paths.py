"""El preflight no debe filtrar el path absoluto del host al LLM.

`check_capture_health` corre PRIMERO en toda captura; su sonda de workspace
devolvía `"{root} escribible"` con el root del host — inyectando al contexto,
desde el arranque del turno, un path que no existe dentro del sandbox.
"""
from __future__ import annotations

from backend.agents.pipelines import preflight
from backend.config import Settings


def test_workspace_probe_ok_has_no_host_path(monkeypatch, tmp_path):
    monkeypatch.setattr(Settings, "workspaces_root", property(lambda self: tmp_path))
    probe = preflight._probe_workspace()
    assert probe.ok
    assert probe.detail == "workspace escribible"
    assert str(tmp_path) not in probe.detail
    assert str(tmp_path) not in probe.hint


def test_workspace_probe_failure_has_no_host_path(monkeypatch, tmp_path):
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("x")  # mkdir sobre un archivo => FileExistsError
    monkeypatch.setattr(Settings, "workspaces_root", property(lambda self: blocked))
    probe = preflight._probe_workspace()
    assert not probe.ok
    # Sin str(exc): los mensajes de PermissionError/FileExistsError embeben
    # el path absoluto del host.
    assert probe.detail == "FileExistsError"
    assert str(blocked) not in probe.detail
    assert str(blocked) not in probe.hint
    assert "WORKSPACES_HOST_ROOT" in probe.hint
