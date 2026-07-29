"""Tests for Settings.workspaces_root: CWD-independent workspace resolution.

Pins bug #1 fix: a RELATIVE ``workspaces_host_root`` must anchor to the repo
root (not the volatile process CWD, which drifts in long-lived dev processes
and broke path resolution at session-15). An ABSOLUTE value must pass through
verbatim: the compose DinD bind-mount needs the host-visible path.
"""
from __future__ import annotations

from pathlib import Path

from backend.config import Settings, _REPO_ROOT


def test_relative_root_is_anchored_to_repo_root():
    s = Settings(workspaces_host_root="./data/workspaces")
    assert s.workspaces_root == (_REPO_ROOT / "data" / "workspaces").resolve()


def test_relative_root_independent_of_cwd(tmp_path, monkeypatch):
    s = Settings(workspaces_host_root="./data/workspaces")
    anchored = s.workspaces_root
    # The bug scenario: the process CWD drifts to an unrelated directory.
    # Resolution must NOT change because the path is anchored to the repo root,
    # not the CWD.
    monkeypatch.chdir(tmp_path)
    assert s.workspaces_root == anchored
    assert s.workspaces_root == (_REPO_ROOT / "data" / "workspaces").resolve()


def test_absolute_root_passes_through_verbatim():
    abs_path = "/var/lib/infofact/workspaces"
    s = Settings(workspaces_host_root=abs_path)
    assert s.workspaces_root == Path(abs_path).resolve()


def test_absolute_tmp_path_passes_through(tmp_path):
    s = Settings(workspaces_host_root=str(tmp_path))
    assert s.workspaces_root == tmp_path.resolve()
