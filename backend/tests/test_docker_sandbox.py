"""Tests de DockerSandbox: glob anclado al workspace + timeout del lado del container.

Contexto (incidente 2026-08-14, sesión 44 de Planitrack2.0): ``BaseSandbox.glob``
de deepagents defaultea ``path or "/"`` y la tool del middleware también — un
``glob("**/...")`` sin ``path`` recorría TODO el filesystem del container como
uid 1000 (pico de CPU + permission denied). Además, al vencer el timeout el
cliente ``docker exec`` muere pero el proceso queda huérfano adentro del
container, quemando CPU hasta que alguien lo mata a mano.
"""
from __future__ import annotations

import asyncio
import base64
import re
import shlex
from types import SimpleNamespace

from backend.agents.sandboxes.docker_sandbox import DockerSandbox

CMD_PATH_RE = re.compile(r"path = base64\.b64decode\('([^']+)'\)")


def _make_sandbox() -> DockerSandbox:
    return DockerSandbox(profile="perfil_claudio", project_slug="planitrack2-0")


def _capture_exec(monkeypatch) -> list[list[str]]:
    """Monkeypatchea subprocess.run y devuelve la lista de comandos capturados."""
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(stdout=b"", stderr=b"", returncode=0)

    monkeypatch.setattr(
        "backend.agents.sandboxes.docker_sandbox.subprocess.run", fake_run
    )
    return calls


def _decoded_search_path(cmd: list[str]) -> str:
    """Extrae el ``search_path`` (b64) que el template de glob le pasa a python3."""
    # El comando viaja envuelto en ``timeout ... sh -c '<script>'``; des-quotear
    # primero para que las comillas internas de shlex.quote no rompan el regex.
    script = shlex.split(" ".join(cmd))[-1]
    m = CMD_PATH_RE.search(script)
    assert m, f"no se encontró path_b64 en: {sh_cmd[:200]}"
    return base64.b64decode(m.group(1)).decode("utf-8")


def test_execute_wraps_command_with_container_side_timeout(monkeypatch):
    calls = _capture_exec(monkeypatch)
    sbx = _make_sandbox()

    sbx.execute("ls -la")

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[:2] == ["docker", "exec"]
    # uid no-root + cwd anclado al workspace del proyecto.
    assert cmd[cmd.index("-u") + 1] == "1000:1000"
    assert cmd[cmd.index("-w") + 1] == "/workspaces/planitrack2-0"
    # El comando viaja envuelto en timeout DEL LADO DEL CONTAINER.
    inner = cmd[-1]
    assert inner.startswith("timeout -k 5 30 sh -c ")
    assert "ls -la" in inner


def test_execute_uses_configured_timeout(monkeypatch):
    calls = _capture_exec(monkeypatch)
    monkeypatch.setattr(
        "backend.agents.sandboxes.docker_sandbox.settings",
        SimpleNamespace(sandbox_exec_timeout=7),
    )
    sbx = _make_sandbox()

    sbx.execute("echo hola")

    assert calls[0][-1].startswith("timeout -k 5 7 sh -c ")


def test_glob_defaults_to_project_root_not_container_root(monkeypatch):
    """Sin ``path``, busca desde el cwd del proyecto (.) — nunca desde ``/``."""
    calls = _capture_exec(monkeypatch)
    sbx = _make_sandbox()

    sbx.glob("**/*.md")

    assert _decoded_search_path(calls[0]) == "."


def test_glob_accepts_paths_inside_workspace(monkeypatch):
    calls = _capture_exec(monkeypatch)
    sbx = _make_sandbox()

    sbx.glob("**/*.md", path="/workspaces/planitrack2-0/docs")

    assert _decoded_search_path(calls[0]) == "docs"


def test_glob_rejects_escape_paths_without_executing(monkeypatch):
    calls = _capture_exec(monkeypatch)
    sbx = _make_sandbox()

    for escape in ("/etc", "/workspaces/otro-proyecto/x", "../fuera"):
        res = sbx.glob("**/*.md", path=escape)
        assert res.error, f"esperaba error para {escape!r}"

    # Ningún path escapado debe llegar a ejecutar nada en el container.
    assert calls == []


def test_aglob_same_workspace_bound(monkeypatch):
    calls = _capture_exec(monkeypatch)
    sbx = _make_sandbox()

    res = asyncio.run(sbx.aglob("**/*.pdf"))
    assert res.error is None
    assert _decoded_search_path(calls[0]) == "."

    res_bad = asyncio.run(sbx.aglob("**/*.pdf", path="/"))
    assert res_bad.error
    assert len(calls) == 1


def test_ls_rejects_escape_paths_without_executing(monkeypatch):
    """`BaseSandbox.ls` no pasa por _safe_path: un path absoluto fuera del
    workspace listaba proyectos hermanos. Misma guardia que glob."""
    calls = _capture_exec(monkeypatch)
    sbx = _make_sandbox()

    for escape in ("/workspaces", "/workspaces/otro-proyecto", "/etc"):
        res = sbx.ls(escape)
        assert res.error, f"esperaba error para {escape!r}"
        # El error nombra el root valido para que el modelo se corrija.
        assert "/workspaces/planitrack2-0" in res.error

    # Ningún path escapado debe llegar a ejecutar nada en el container.
    assert calls == []


def test_ls_accepts_workspace_relative_path(monkeypatch):
    calls = _capture_exec(monkeypatch)
    sbx = _make_sandbox()

    res = sbx.ls("docs")

    assert res.error is None
    assert _decoded_search_path(calls[0]) == "docs"


def test_bound_error_names_valid_root(monkeypatch):
    """El mensaje de rechazo incluye el root valido (autocorreccion en un
    turno, no iteracion a ciegas)."""
    _capture_exec(monkeypatch)
    sbx = _make_sandbox()

    res = sbx.glob("**/*.md", path="/etc")

    assert "path escapes project workspace" in res.error
    assert "/workspaces/planitrack2-0" in res.error


def test_upload_allows_conversation_history_offload_path(monkeypatch):
    """El offload del resumen (SummarizationMiddleware de deepagents) escribe
    el respaldo en /conversation_history/<thread>.md con path absoluto FIJO:
    el guard debe aceptarlo (si no, cada resumen falla con invalid_path y el
    historial previo queda sin respaldo)."""
    calls = _capture_exec(monkeypatch)
    sbx = _make_sandbox()

    res = sbx.write("/conversation_history/session_abc.md", "contenido")

    assert res.error is None
    assert len(calls) == 1
    script = " ".join(calls[0])
    assert "mkdir -p /conversation_history" in script
    assert "cat > /conversation_history/session_abc.md" in script


def test_download_allows_conversation_history_offload_path(monkeypatch):
    """La re-lectura del contenido offloaded (recuperabilidad del historial)
    usa el mismo path absoluto: debe pasar el guard."""
    calls = _capture_exec(monkeypatch)
    sbx = _make_sandbox()

    res = sbx.download_files(["/conversation_history/session_abc.md"])

    assert res[0].error is None
    assert calls[0][-2:] == ["cat", "/conversation_history/session_abc.md"]


def test_upload_still_rejects_other_absolute_escape_paths(monkeypatch):
    """La excepción es SOLO /conversation_history: /etc y proyectos hermanos
    siguen rechazados sin ejecutar nada."""
    calls = _capture_exec(monkeypatch)
    sbx = _make_sandbox()

    for escape in ("/etc/passwd", "/workspaces/otro-proyecto/x.md"):
        res = sbx.write(escape, "x")
        # El mensaje con el formato que emite write() (el mismo que quedó en
        # los logs del incidente del offload).
        assert res.error == f"Failed to write file '{escape}': invalid_path", (
            f"esperaba invalid_path para {escape!r}"
        )

    assert calls == []
