"""Factory para DeepAgents agents wired to a user's DockerSandbox.

Iter 2: el agente se construye CON checkpointer AsyncSqliteSaver (compartido,
singleton creado en main.lifespan). El thread_id se pasa en tiempo de INVOKE
via config={'configurable': {'thread_id': ...}}, NO en build_agent — pero lo
dejamos en la firma para documentación / uso futuro.

El agente es de corto plazo: se construye por request, bound al container del
usuario. El estado persistente vive en (a) el filesystem del container y (b)
el SQLite checkpointer (key = ChatSession.id).

LLM coupling es intencionalmente mínimo: cualquier endpoint OpenAI-compatible
sirve. Configura vía LLM_API_KEY / LLM_BASE_URL / LLM_MODEL en .env.

Secrets NUNCA se inyectan al container: el agente corre adentro y podría
exfiltrar lo que haya en su environment.
"""
from __future__ import annotations

import logging
from typing import Any

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.agents.llm import build_llm
from backend.agents.sandboxes.docker_sandbox import DockerSandbox
from backend.agents.tools import fetch_url, web_search
from backend.agents.subagents.requirements_capture import (
    make_requirements_capture_subagent,
)
from backend.config import settings

logger = logging.getLogger(__name__)

PHASE_PROMPTS: dict[str, str] = {
    "requirements": (
        "Eres un analista de requerimientos de software. Tu directorio de "
        "trabajo actual es la raíz del workspace del proyecto. Usa las "
        "herramientas del filesystem para leer, escribir y editar documentos. "
        "Entrega un SRS (Software Requirements Specification), historias de "
        "usuario con criterios de aceptación en Gherkin y una lista de "
        "supuestos. Guarda todo como Markdown dentro de la carpeta docs/ "
        "(por ejemplo: docs/srs.md, docs/user-stories.md). Usa rutas "
        "relativas a tu directorio actual (docs/srs.md); son la forma "
        "preferida y la más robusta. NUNCA crees una carpeta llamada "
        "workspaces dentro de tu directorio actual: ya estás adentro del "
        "workspace del proyecto. Si ejecutas comandos shell (git init, "
        "mkdir, ls), hazlo directamente en tu directorio actual."
    ),
}


def _build_model() -> ChatOpenAI:
    # Thin wrapper kept for existing call sites; real construction lives in
    # backend.agents.llm so the requirements pipelines reuse it without
    # importing backend.services (service layer only relays, CLAUDE.md).
    return build_llm()


async def build_checkpointer() -> AsyncSqliteSaver:
    """Crea y configura el AsyncSqliteSaver sobre settings.checkpointer_db.

    Contrato verificado (langgraph-checkpoint-sqlite 3.1.0):
      - from_conn_string(path) es async context manager.
      - await saver.setup() crea las tablas (idempotente via flag is_setup).
    Caller debe mantener el saver vivo y cerrarlo en shutdown con
    close_checkpointer(saver). Lo guarda en app.state.checkpointer.
    """
    # Mantenemos viva la referencia al ctx para poder cerrarlo después.
    ctx = AsyncSqliteSaver.from_conn_string(settings.checkpointer_db)
    saver = await ctx.__aenter__()
    await saver.setup()
    # Stash del ctx en el saver: close_checkpointer lo usa para teardown.
    saver._infofact_ctx = ctx  # type: ignore[attr-defined]
    return saver


async def close_checkpointer(saver: AsyncSqliteSaver) -> None:
    """Cierra el AsyncSqliteSaver abierto por build_checkpointer()."""
    ctx = getattr(saver, "_infofact_ctx", None)
    if ctx is None:
        return
    await ctx.__aexit__(None, None, None)
    saver._infofact_ctx = None  # type: ignore[attr-defined]


def build_agent(
    profile: str,
    *,
    project_slug: str,
    project_id: int | None = None,
    project_name: str | None = None,
    project_description: str | None = None,
    thread_id: str | int | None = None,
    phase: str = "requirements",
    checkpointer: AsyncSqliteSaver | None = None,
) -> Any:
    """Construye un DeepAgent bound al container del usuario, acotado al
    workspace del proyecto.

    Args:
        profile: slug del usuario; identifica el container.
        project_slug: slug del proyecto; define el subdirectorio del workspace
            (`/workspaces/{slug}`) que el agente usará como CWD.
        project_name: nombre legible del proyecto; si se pasa, se menciona en
            el system prompt para dar contexto al agente.
        project_description: descripción opcional del proyecto; se anexa al
            system prompt cuando project_name y project_description están
            presentes.
        thread_id: NO se usa en el build. LangGraph lo recibe en INVOKE via
            config['configurable']['thread_id']. Está en la firma solo para
            documentación / uso futuro.
        phase: fase del agente; selecciona el system prompt.
        checkpointer: AsyncSqliteSaver compartido. Si es None, el agente corre
            sin persistencia (útil para tests / smoke tests).
    """
    del thread_id  # ver docstring: se pasa en invoke, no en build
    sandbox = DockerSandbox(profile=profile, project_slug=project_slug)
    system_prompt = PHASE_PROMPTS.get(phase, PHASE_PROMPTS["requirements"])
    if project_name:
        # Contexto del proyecto al inicio del prompt, en español neutro.
        # La descripción es opcional: algunos proyectos vienen sin ella.
        header = f'Estás trabajando en el proyecto "{project_name}".'
        if project_description:
            header += f" Descripción: {project_description}"
        system_prompt = header + "\n\n" + system_prompt
    # Requirements phase gets the capture subagent: a thin orchestrator that
    # owns the deterministic pipeline + the editing tools. project_id is closed
    # over so the model cannot address another project's rows. Built per request
    # alongside the agent (same lifecycle as the sandbox).
    subagents: list[Any] = []
    if phase == "requirements" and project_id is not None:
        subagents.append(
            make_requirements_capture_subagent(
                project_id=project_id,
                profile=profile,
                project_slug=project_slug,
                project_name=project_name or "",
                project_description=project_description or "",
            )
        )

    return create_deep_agent(
        model=_build_model(),
        system_prompt=system_prompt,
        backend=sandbox,
        tools=[web_search, fetch_url],
        subagents=subagents or None,
        checkpointer=checkpointer,
    )
