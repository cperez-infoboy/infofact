"""Tests de rebase_document_id (convencion unica de document_id)."""
from __future__ import annotations

from backend.config import settings
from backend.services.doc_id import rebase_document_id


def test_rebases_host_absolute_to_container_path():
    host = f"{settings.workspaces_root}/perfil_claudio/planitrack2-0/docs/spec.pdf"
    assert rebase_document_id(host) == "/workspaces/planitrack2-0/docs/spec.pdf"


def test_idempotent_on_already_rebased():
    assert (
        rebase_document_id("/workspaces/planitrack2-0/docs/spec.pdf")
        == "/workspaces/planitrack2-0/docs/spec.pdf"
    )


def test_passthrough_relative_and_foreign_paths():
    assert rebase_document_id("docs/spec.pdf") == "docs/spec.pdf"
    assert rebase_document_id("/etc/passwd") == "/etc/passwd"
    assert rebase_document_id("") == ""


def test_profile_only_host_path_unchanged():
    # Path del host que llega solo hasta el perfil (sin slug/rel): no rebasa.
    odd = f"{settings.workspaces_root}/perfil_claudio"
    assert rebase_document_id(odd) == odd
