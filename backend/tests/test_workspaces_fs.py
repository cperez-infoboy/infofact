"""Tests de las operaciones de filesystem del explorador de workspace.

Dos niveles:

1. Semántica de ``_FS_OP_SCRIPT``: se ejecuta con el intérprete local
   (subprocess) contra un tmpdir, igual que corre en el container contra la
   raíz del workspace. Cubre mkdir/new_file/move/copy/delete con sus códigos
   de error (is_root, already_exists, target_exists, target_inside_source,
   not_found) y el sufijo `` copia`` ante colisiones.

2. Router: mapeo de errores a HTTP (400/404/409) y guards de raíz, llamando
   los handlers directamente con file_service monkeypatcheado (mismo patrón
   unitario que test_capture_gate_router.py, sin TestClient).
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend.routers import workspaces
from backend.services import file_service


# --- 1. Semántica del script ---------------------------------------------


def run_op(tmp_path, *args: str) -> dict:
    """Corre el micro-script como lo hace _fs_op (cwd = raíz del workspace)."""
    res = subprocess.run(
        [sys.executable, "-c", file_service._FS_OP_SCRIPT, *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notas.md").write_text("hola")
    (tmp_path / "docs" / "plani.xlsx").write_bytes(b"\x00\x01")
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "logo.png").write_bytes(b"\x89PNG")
    return tmp_path


def test_mkdir_ok_and_already_exists(ws):
    assert run_op(ws, "mkdir", "nueva") == {"ok": True, "path": "nueva"}
    assert (ws / "nueva").is_dir()
    assert run_op(ws, "mkdir", "nueva") == {"ok": False, "error": "already_exists"}
    # mkdir anidado crea padres (os.makedirs)
    assert run_op(ws, "mkdir", "a/b/c") == {"ok": True, "path": "a/b/c"}


def test_new_file_ok_and_already_exists(ws):
    assert run_op(ws, "new_file", "docs/vacio.txt") == {"ok": True, "path": "docs/vacio.txt"}
    assert (ws / "docs" / "vacio.txt").read_text() == ""
    assert run_op(ws, "new_file", "docs/vacio.txt")["error"] == "already_exists"
    assert run_op(ws, "new_file", "noexiste/x.txt")["error"] == "not_found"


def test_move_renames_and_moves(ws):
    assert run_op(ws, "move", "docs/notas.md", "docs/notas2.md")["ok"] is True
    assert (ws / "docs" / "notas2.md").exists()
    # mover entre carpetas
    assert run_op(ws, "move", "docs/notas2.md", "img/notas2.md")["ok"] is True
    assert (ws / "img" / "notas2.md").exists()


def test_move_guards(ws):
    assert run_op(ws, "move", ".", "x")["error"] == "is_root"
    assert run_op(ws, "move", "docs", "img")["error"] == "target_exists"
    assert run_op(ws, "move", "noexiste", "otro")["error"] == "not_found"
    # mover una carpeta dentro de sí misma (dst == src llega como target_exists:
    # el destino existe, que es chequeado antes)
    assert run_op(ws, "move", "docs", "docs/sub")["error"] == "target_inside_source"
    assert run_op(ws, "move", "docs", "docs")["error"] == "target_exists"
    # destino con padre inexistente
    assert run_op(ws, "move", "docs/notas.md", "noexiste/x.md")["error"] == "not_found"


def test_copy_file_with_collision_suffix(ws):
    out = run_op(ws, "copy", "docs/notas.md", "docs")
    assert out == {"ok": True, "path": "docs/notas copia.md"}
    out2 = run_op(ws, "copy", "docs/notas.md", "docs")
    assert out2["path"] == "docs/notas copia 2.md"
    # sin colisión conserva el nombre
    assert run_op(ws, "copy", "docs/notas.md", "img")["path"] == "img/notas.md"
    assert (ws / "img" / "notas.md").read_text() == "hola"


def test_copy_dir_recursive(ws):
    out = run_op(ws, "copy", "docs", ".")
    assert out["path"] == "docs copia"
    assert (ws / "docs copia" / "notas.md").exists()
    assert run_op(ws, "copy", "docs", "noexiste")["error"] == "not_found"
    assert run_op(ws, "copy", ".", "docs")["error"] == "is_root"


def test_delete_file_dir_and_root_guard(ws):
    assert run_op(ws, "delete", "docs/notas.md")["ok"] is True
    assert not (ws / "docs" / "notas.md").exists()
    assert run_op(ws, "delete", "img")["ok"] is True
    assert not (ws / "img").exists()
    assert run_op(ws, "delete", ".")["error"] == "is_root"
    assert run_op(ws, "delete", "noexiste")["error"] == "not_found"


def test_unknown_op(ws):
    assert run_op(ws, "nonsense", "x")["error"] == "unknown_op"


# --- 2. Router: mapeo de errores -----------------------------------------


class _FakeUser:
    profile = "tester"
    id = 1


@pytest.fixture
def owned(monkeypatch):
    """_resolve_owned_project → proyecto stub; user fake inyectado directo."""
    fake_project = SimpleNamespace(slug="mi-proyecto", user_id=1)

    async def fake_resolve(project_id: int, user):
        return fake_project

    monkeypatch.setattr(workspaces, "_resolve_owned_project", fake_resolve)


def _raises_code(fn, *args, **kwargs) -> str:
    with pytest.raises(workspaces.HTTPException) as exc:
        asyncio.run(fn(*args, **kwargs))
    return str(exc.value.detail)


def test_fs_mkdir_maps_conflict(monkeypatch, owned):
    async def boom(*a, **kw):
        raise file_service.FsOpError("already_exists")

    monkeypatch.setattr(file_service, "create_dir", boom)
    code = _raises_code(
        workspaces.fs_mkdir,
        workspaces.FsPathBody(path="docs"),
        project_id=1,
        user=_FakeUser(),
    )
    assert code == "already_exists"


def test_fs_delete_maps_root_guard(monkeypatch, owned):
    async def boom(*a, **kw):
        raise file_service.FsOpError("is_root")

    monkeypatch.setattr(file_service, "delete_entry", boom)
    code = _raises_code(
        workspaces.fs_delete, project_id=1, path=".", user=_FakeUser()
    )
    assert code == "is_root"


def test_fs_move_maps_target_inside_source(monkeypatch, owned):
    async def boom(*a, **kw):
        raise file_service.FsOpError("target_inside_source")

    monkeypatch.setattr(file_service, "move_entry", boom)
    code = _raises_code(
        workspaces.fs_move,
        workspaces.FsMoveBody(src="docs", dst="docs/sub"),
        project_id=1,
        user=_FakeUser(),
    )
    assert code == "target_inside_source"


def test_fs_op_invalid_path_maps_400(monkeypatch, owned):
    async def boom(*a, **kw):
        raise ValueError("traversal")

    # mkdir (body) y delete (query): las dos firmas de handler del router.
    monkeypatch.setattr(file_service, "create_dir", boom)
    monkeypatch.setattr(file_service, "delete_entry", boom)

    with pytest.raises(workspaces.HTTPException) as exc:
        asyncio.run(
            workspaces.fs_mkdir(
                workspaces.FsPathBody(path="../etc"),
                project_id=1,
                user=_FakeUser(),
            )
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "invalid_path"

    with pytest.raises(workspaces.HTTPException) as exc:
        asyncio.run(
            workspaces.fs_delete(project_id=1, path="../etc", user=_FakeUser())
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "invalid_path"


def test_fs_copy_returns_final_path(monkeypatch, owned):
    captured: dict = {}

    async def fake_copy(profile, slug, *, src, dst_dir):
        captured["args"] = (profile, slug, src, dst_dir)
        return "docs/notas copia.md"

    monkeypatch.setattr(file_service, "copy_entry", fake_copy)

    out = asyncio.run(
        workspaces.fs_copy(
            workspaces.FsCopyBody(src="docs/notas.md", dst_dir="docs"),
            project_id=1,
            user=_FakeUser(),
        )
    )
    assert out.path == "docs/notas copia.md"
    assert captured["args"] == ("tester", "mi-proyecto", "docs/notas.md", "docs")


def test_download_maps_not_found(monkeypatch, owned):
    async def boom(*a, **kw):
        raise FileNotFoundError("x")

    monkeypatch.setattr(file_service, "read_bytes", boom)
    code = _raises_code(
        workspaces.download_file, project_id=1, path="docs/x.pdf", user=_FakeUser()
    )
    assert code == "file_not_found"


def test_download_response_headers(monkeypatch, owned):
    async def fake_read(profile, slug, *, path):
        return b"%PDF-1.4 fake"

    monkeypatch.setattr(file_service, "read_bytes", fake_read)

    res = asyncio.run(
        workspaces.download_file(
            project_id=1, path="docs/informé final.pdf", user=_FakeUser()
        )
    )
    assert res.media_type == "application/pdf"
    assert res.body == b"%PDF-1.4 fake"
    disp = res.headers["content-disposition"]
    assert disp.startswith("attachment;")
    assert "UTF-8''inform%C3%A9%20final.pdf" in disp
