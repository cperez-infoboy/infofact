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
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
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

    # Read-only access to the requirements store so the orchestrator can answer
    # "list / describe / show the decomposition tree of REQ-NNN" directly,
    # without delegating to the capture subagent. Bound to project_id in the
    # requirements phase only; mutation tools stay exclusive to the subagent.
    orchestrator_tools: list[Any] = [web_search, fetch_url]
    if phase == "requirements" and project_id is not None:
        from backend.agents.tools.requirements_tools import (
            make_requirements_read_tools,
        )
        orchestrator_tools.extend(make_requirements_read_tools(project_id))

    return create_deep_agent(
        model=_build_model(),
        system_prompt=system_prompt,
        backend=sandbox,
        tools=orchestrator_tools,
        subagents=subagents or None,
        checkpointer=checkpointer,
    )


# ---------------------------------------------------------------------------
# Read-only history reconstruction from the checkpointer
# ---------------------------------------------------------------------------
#
# The checkpointer (AsyncSqliteSaver, settings.checkpointer_db) is the
# authoritative record of what the agent actually did on a session: it stores
# the full message list per thread, INCLUDING AIMessage.tool_calls and the
# ToolMessage results, which the SSE layer emits as ephemeral events and never
# persists to chat_messages. reconstruct_history reads that state so the GET
# /sessions/{id} endpoint can replay a session faithfully (tools + final
# answers), not just the lossy assistant accumulation.
#
# The boundary stays clean: this lives in agent_service (the agent wrapper), so
# when phase 3 swaps in a container-per-user transport only THIS function
# changes (HTTP to the container instead of the local saver) — the router and
# the schema are untouched. CLAUDE.md Decision 1.

_read_graph: Any = None


def _get_read_graph(checkpointer: AsyncSqliteSaver) -> Any:
    """DeepAgent compiled ONLY to call aget_state: same state schema
    (messages + files channels), same checkpointer, but a dummy backend and
    no tools/subagents. aget_state only reads the checkpointer (SQLite) — it
    never invokes the backend nor the LLM — so the dummy backend is never
    exercised. Compiled once (module-level cache); thread_id distinguishes
    sessions, the schema is project-agnostic.

    Assumes a single shared checkpointer for the app's lifetime (true today:
    the saver is built once in the lifespan). If the saver ever changes per
    request, key this cache by id(checkpointer).
    """
    global _read_graph
    if _read_graph is None:
        _read_graph = create_deep_agent(
            model=_build_model(),
            system_prompt="read-only-history",
            backend=DockerSandbox(profile="__read__", project_slug="__read__"),
            tools=[],
            subagents=None,
            checkpointer=checkpointer,
        )
    return _read_graph


def _coerce_text(content: Any) -> str:
    """Extract plain text from a BaseMessage content (str or content-block list)."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


async def reconstruct_history(
    checkpointer: AsyncSqliteSaver, session_id: int
) -> list[dict]:
    """Neutral chat history for a session reconstructed from the checkpointer.

    Each dict is plain (no LangGraph types leak to the router):
        {id, role, content, created_at,
         tool_name?, tool_call_id?, tool_args?, status?}
    role in {'user', 'assistant', 'tool'}.

    Mapping (chronological, as stored by the checkpointer):
      - HumanMessage  -> {role:'user', content}
      - AIMessage     -> {role:'assistant', content} (if non-empty text), plus
                         one {role:'tool', tool_name, tool_call_id, tool_args}
                         per tool_call (output filled later by its ToolMessage).
                         Skipped entirely if empty and tool-less (streaming
                         artefact that would render an invisible bubble).
      - ToolMessage   -> back-fills content (output) on the matching
                         role:'tool' item by tool_call_id.
      - SystemMessage -> skipped.

    Returns [] if the thread does not exist (the router then falls back to
    chat_messages). created_at is NOT set per item: LangGraph carries no
    reliable per-message timestamp; the router assigns session.created_at.
    """
    graph = _get_read_graph(checkpointer)
    config = {"configurable": {"thread_id": str(session_id)}}
    try:
        snapshot = await graph.aget_state(config)
    except Exception:
        logger.exception(
            "reconstruct_history: aget_state failed for session %s", session_id
        )
        return []
    if snapshot is None or not snapshot.values:
        return []

    messages = snapshot.values.get("messages") or []
    out: list[dict] = []
    # Map tool_call_id -> index in `out` of the role:'tool' item waiting for its
    # ToolMessage output. Lets a ToolMessage back-fill the right item even when
    # several tool calls are in flight.
    pending: dict[str, int] = {}

    for msg in messages:
        if isinstance(msg, HumanMessage):
            out.append({
                "id": f"u-{len(out)}",
                "role": "user",
                "content": _coerce_text(msg.content),
                "created_at": None,
            })
        elif isinstance(msg, AIMessage):
            text = _coerce_text(msg.content)
            if text:
                out.append({
                    "id": f"a-{len(out)}",
                    "role": "assistant",
                    "content": text,
                    "created_at": None,
                })
            for tc in msg.tool_calls or []:
                tc_id = tc.get("id") or f"tc-{len(out)}"
                item = {
                    "id": f"t-{len(out)}",
                    "role": "tool",
                    "content": "",
                    "created_at": None,
                    "tool_name": tc.get("name") or "",
                    "tool_call_id": tc_id,
                    "tool_args": tc.get("args") or {},
                    "status": "done",
                }
                pending[tc_id] = len(out)
                out.append(item)
        elif isinstance(msg, ToolMessage):
            tc_id = msg.tool_call_id
            idx = pending.get(tc_id)
            if idx is not None:
                out[idx]["content"] = _coerce_text(msg.content)
                if not out[idx].get("tool_name") and getattr(msg, "name", None):
                    out[idx]["tool_name"] = msg.name
            else:
                # Orphan ToolMessage (no preceding tool_call seen in state):
                # emit it directly so the result is not lost.
                out.append({
                    "id": f"t-{len(out)}",
                    "role": "tool",
                    "content": _coerce_text(msg.content),
                    "created_at": None,
                    "tool_name": getattr(msg, "name", None) or "",
                    "tool_call_id": tc_id,
                    "tool_args": {},
                    "status": "done",
                })
        elif isinstance(msg, SystemMessage):
            continue

    return out
