"""Subagent tools for directed image inspection during requirements capture.

These complement the automatic vision pass in the ingestion pipeline: the agent
calls ``analyze_image`` when it wants a closer look at a specific diagram or
mockup (e.g. to resolve a doubt while editing requirements).

Bound to one project's workspace, like the requirements editing tools. Returns
an empty list when vision is unavailable so ``make_requirements_capture_agent_subagent``
registration becomes a no-op and the pipeline keeps running Docling-only.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from langchain_core.tools import tool

from backend.agents.vision import analyze_image as _analyze_image
from backend.config import settings


def make_vision_tools(profile: str, project_slug: str) -> list:
    """Build vision tools bound to one project's workspace.

    Returns ``[]`` when ``settings.supports_vision`` is False, so the subagent
    is unaffected on deployments without a configured vision model.
    """
    if not settings.supports_vision:
        return []

    host_workspace = settings.workspaces_root / profile / project_slug

    def _resolve(image_path: str) -> Path:
        """Resolve a workspace-relative image path, refusing traversal.

        Mirrors ``_resolve_target`` in requirements_capture_agent.py: the vision call
        reads host-side bytes, so a crafted path must not escape the project.
        """
        root = host_workspace.resolve()
        target = (root / image_path).resolve() if image_path else root
        if target != root and not str(target).startswith(str(root) + os.sep):
            raise ValueError(
                f"la ruta '{image_path}' sale del workspace del proyecto"
            )
        if not target.is_file():
            raise ValueError(f"no existe un archivo en '{image_path}'")
        return target

    @tool
    async def analyze_image(image_path: str, question: str) -> str:
        """Analizá una imagen del workspace del proyecto y respondé una pregunta.

        Usala para inspeccionar diagramas, mockups de interfaz o capturas que
        acompañan a los documentos del cliente, cuando necesitás un detalle más
        fino que la descripción automática que genera /captura.

        Args:
            image_path: ruta de la imagen relativa al workspace del proyecto
                (por ejemplo "mockups/login.png").
            question: qué querés saber de la imagen.

        Devuelve una descripción textual detallada, o un mensaje de error si la
        imagen no existe o no se puede procesar.
        """
        try:
            resolved = _resolve(image_path)
        except ValueError as exc:
            return str(exc)
        # analyze_image (vision.py) is sync and blocks on the LLM call; run it
        # off the event loop, same as Docling in requirements_service.
        return await asyncio.to_thread(_analyze_image, resolved, question)

    return [analyze_image]
