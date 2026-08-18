"""NoFilesystemToolsMiddleware: saca las tools de filesystem/execute del menu.

deepagents inyecta FilesystemMiddleware a todos los subagentes spec; este
middleware filtra esos nombres del request para que el modelo ni los vea.
"""
from __future__ import annotations

import pytest

from backend.agents.no_fs_tools import NoFilesystemToolsMiddleware


class _Tool:
    def __init__(self, name):
        self.name = name


class _Request:
    def __init__(self, tools):
        self.tools = tools

    def override(self, **kw):
        return _Request(kw.get("tools", self.tools))


def test_filters_fs_and_execute_keeps_domain_tools():
    req = _Request([
        _Tool(n)
        for n in (
            "ls", "glob", "execute", "read_file",
            "write_file", "edit_file", "grep",
            "review_grouping", "list_requirements",
        )
    ])
    seen = {}

    def handler(r):
        seen["tools"] = [t.name for t in r.tools]
        return "ok"

    assert NoFilesystemToolsMiddleware().wrap_model_call(req, handler) == "ok"
    assert seen["tools"] == ["review_grouping", "list_requirements"]


@pytest.mark.asyncio
async def test_async_variant_filters_the_same():
    req = _Request([_Tool("ls"), _Tool("capture_status")])
    seen = {}

    async def handler(r):
        seen["tools"] = [t.name for t in r.tools]
        return "ok"

    out = await NoFilesystemToolsMiddleware().awrap_model_call(req, handler)
    assert out == "ok"
    assert seen["tools"] == ["capture_status"]


def test_request_without_fs_tools_passes_through():
    req = _Request([_Tool("task")])
    seen = {}

    def handler(r):
        seen["count"] = len(r.tools)
        return "ok"

    NoFilesystemToolsMiddleware().wrap_model_call(req, handler)
    assert seen["count"] == 1
