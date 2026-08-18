"""Oculta las tools de filesystem/execute a un subagente.

deepagents INYECTA ``FilesystemMiddleware`` (con el backend del orquestador)
a TODOS los subagentes definidos como spec (``graph.py`` lo antepone al stack
base de cada uno), asi que un subagente de dominio que nunca pidio sandbox
termina con ``ls``/``glob``/``read_file``/``execute`` — la unica via por la que
el subagente de captura podia salir a explorar el filesystem del contenedor.

La version instalada no expone allowlist de tools para subagentes; si expone
``ModelRequest.override(tools=...)``, que este middleware usa para sacar esas
tools del menu del modelo ANTES de cada llamada. Sin las tools a la vista, el
modelo no las llama; las tools de dominio (requerimientos, agrupamiento,
stages) quedan intactas.
"""
from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware

# Los nombres que FilesystemMiddleware agrega (mas ``execute``, que aparece
# cuando el backend implementa SandboxBackendProtocol).
_BLOCKED_TOOL_NAMES = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute"}
)


def _without_fs_tools(tools):
    return [t for t in tools if getattr(t, "name", None) not in _BLOCKED_TOOL_NAMES]


class NoFilesystemToolsMiddleware(AgentMiddleware):
    """wrap_model_call que filtra las tools de filesystem del request."""

    def wrap_model_call(self, request, handler):
        return handler(request.override(tools=_without_fs_tools(request.tools)))

    async def awrap_model_call(self, request, handler):
        return await handler(
            request.override(tools=_without_fs_tools(request.tools))
        )
