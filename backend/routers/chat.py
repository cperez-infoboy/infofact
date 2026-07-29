"""Router de chat: relay SSE del stream del DeepAgent al navegador.

TRAMPA CONOCIDA (CLAUDE.md decisión 7): combinar Depends(get_current_user)
con el patrón de streaming (que necesita AsyncSessionLocal directo) genera
conflicto. Workaround: el endpoint de streaming resuelve el usuario leyendo
el cookie manualmente con decode_access_token + AsyncSessionLocal, NO vía
Depends. Los endpoints no-streaming sí pueden usar Depends(get_current_user).

Mapping SSE -> streams LangGraph (multi-mode v2 con subgraphs=True):
  messages[AIMessageChunk.content]          -> token
  messages[AIMessageChunk.tool_call_chunks] -> tool_start
  messages[ToolMessage]                     -> tool_end
  custom (get_stream_writer en tools)       -> eventos del pipeline (extraction.progress …)
Cierre limpio -> completed; excepción -> failed.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from pydantic import BaseModel, Field

from backend.database import AsyncSessionLocal
from backend.deps import COOKIE_NAME, decode_access_token
from backend.models import ChatMessage, ChatSession, Project, User
from backend.services.agent_service import build_agent
from backend.services.container_service import ensure_container

logger = logging.getLogger(__name__)

router = APIRouter()

# Guarda de concurrencia: una sola stream activa por sesión.
# Previende doble POST que duplicaría estado en el checkpointer.
_active_streams: set[str] = set()

MAX_TOOL_OUTPUT_CHARS = 500
ASTREAM_VERSION = "v2"
# Techo de recursión del graph (steps). LangGraph default = 25, muy chico para
# DeepAgents que loopea tool-calls. 100 ≈ 50 rondas model+tool, suficiente para
# la fase de requerimientos sin abrir la puerta a runaway costoso.
# DeepAgents documenta default 10_000 para setups multi-expert con subagents;
# acá somos single-expert, 100 alcanza y protege.
RECURSION_LIMIT = 100


class MessageBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=32_000)


async def _user_from_cookie(request: Request) -> User:
    """Resume el usuario desde el cookie SIN Depends.

    Duplica get_current_user del deps.py pero abre AsyncSessionLocal directo,
    para evitar el conflicto con Depends en el endpoint de streaming.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no_session")
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_session")
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_session")
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user_not_found")
    return user


def _sse(event_type: str, data: dict[str, Any]) -> str:
    """Formatea un frame SSE: 'event: <type>\\ndata: <json>\\n\\n'."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _stringify(obj: Any) -> str:
    """Convierte output de tool (str, ToolMessage, etc.) a texto."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    content = getattr(obj, "content", None)
    if isinstance(content, str):
        return content
    try:
        return json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - serialización best-effort
        return str(obj)


def _safe_json(obj: Any) -> Any:
    """Conversión best-effort a algo serializable en JSON."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _safe_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_json(v) for v in obj]
    content = getattr(obj, "content", None)
    if isinstance(content, str):
        return content
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:  # noqa: BLE001 - serialización best-effort
        return str(obj)


def _sanitize_error(exc: Exception) -> str:
    """Sin traceback; solo tipo + mensaje, para no filtrar internals."""
    name = type(exc).__name__
    msg = str(exc).strip()
    return f"{name}: {msg}" if msg else name


# Comando /captura: estrategiaA del plan §9.3 — el router reescribe el slash
# command como directiva fuerte al subagente requirements-capture. Sin arg =
# proyecto completo; con arg = subpath relativo al workspace.
_CAPTURA_PREFIX = "/captura"
_AGRUPAR_PREFIX = "/agrupar"
# /captura_agente MUST be matched before /captura: "/captura_agente"
# startswith("/captura"), so the generic /captura branch would otherwise
# swallow it and parse "_agente ..." as a target_subpath. Accepts _ and -.
_CAPTURA_AGENTE_PREFIXES = ("/captura_agente", "/captura-agente")

# Marcadores de decision del usuario sobre los requerimientos existentes. La
# guardia router-level solo deja pasar la captura cuando el texto los contiene
# de forma inequivoca; un texto ambiguo (p. ej. "agregar detalle a X") no cuenta
# como decision append, para que la guardia siga consultando en lugar de dejar
# pasar la captura y pisar datos sin confirmacion.
_RESET_MARKERS = (
    "resetear", "resetea", "reset", "de cero", "empezar de cero",
    "borrar todo", "eliminar todo",
)
_APPEND_MARKERS = (
    "append", "agregar a los existentes", "sumar a los existentes",
    "mantener los existentes", "conservar los existentes",
    "agregarlos", "sumarlos", "mantenerlos", "conservarlos",
)


def _user_existing_decision(content: str) -> str | None:
    """Devuelve 'reset' o 'append' si el texto expresa una decision explicita
    sobre los requerimientos existentes; si no, None (debe consultarse).

    Conservadora: solo frases inequivocas. Es el pilar que cierra el bypass por
    el cual el subagente agent-driven elegia ``on_existing`` por si solo.
    """
    low = content.lower()
    if any(m in low for m in _RESET_MARKERS):
        return "reset"
    if any(m in low for m in _APPEND_MARKERS):
        return "append"
    return None


def _is_capture_command(content: str) -> bool:
    stripped = content.strip().lower()
    return stripped.startswith(_CAPTURA_AGENTE_PREFIXES) or stripped.startswith(
        _CAPTURA_PREFIX
    )


def _extract_decision_and_subpath(rest: str) -> tuple[str, str | None]:
    """Separa la decision reset/append del subpath en el argumento de /captura.

    Quita el marcador de decision del texto para no tratarlo como un path (p.
    ej. "/captura resetear" -> subpath="", decision="reset").
    """
    low = rest.lower()
    for marker in _RESET_MARKERS:
        idx = low.find(marker)
        if idx >= 0:
            sub = (rest[:idx] + rest[idx + len(marker) :]).strip().lstrip("/").strip()
            return sub, "reset"
    for marker in _APPEND_MARKERS:
        idx = low.find(marker)
        if idx >= 0:
            sub = (rest[:idx] + rest[idx + len(marker) :]).strip().lstrip("/").strip()
            return sub, "append"
    return rest.strip().lstrip("/").strip(), None


async def _capture_gate_message(project_id: int, content: str) -> str | None:
    """Guardia router-level (inviolable) para captura sobre datos existentes.

    Antes de despachar cualquier comando de captura al agente, cuenta los
    requerimientos existentes. Si hay datos previos y el usuario no expreso una
    decision explicita (reset/append), devuelve un mensaje de confirmacion para
    mostrar en lugar de ejecutar el agente. None => procede con la captura.
    """
    if not _is_capture_command(content):
        return None
    if _user_existing_decision(content) is not None:
        return None
    from backend.agents.subagents.requirements_capture import _count_existing

    existing = await _count_existing(project_id)
    n = existing.get("requirements", 0)
    if n <= 0:
        return None
    last = existing.get("last_code")
    suffix = f" (ultimo codigo: {last})" if last else ""
    return (
        f"Ya existen {n} requerimiento(s) en este proyecto{suffix}. Antes de "
        "capturar, decide que hacer con ellos:\n\n"
        "- Reenvia el comando con **resetear** para borrar todos los "
        "requerimientos y agrupamientos y empezar de cero (irreversible).\n"
        "- Reenvia el comando con **agregar a los existentes** para conservarlos "
        "y sumar los nuevos.\n\n"
        "Ejemplo: `/captura_agente resetear` o "
        "`/captura_agente agregar a los existentes`. Puedes repetir tus "
        "instrucciones de captura junto con la decision."
    )


def _captura_agente_directive(user_instructions: str) -> str:
    """Construye la directiva para el subagente agent-driven de captura.

    El orquestador delega a ``requirements-capture-agent`` via la tool ``task``.
    El texto libre despues del comando se reenvia como steering que el agente
    incorpora en orientar/planificar. Si el texto expresa una decision sobre los
    datos existentes (reset/append), se propaga explicitamente para que el
    agente la aplique en ``ingest_documents`` sin volver a consultar.
    """
    directive = (
        "[DIRECTIVE] Delega INMEDIATAMENTE al subagente "
        "`requirements-capture-agent` (usando la tool `task`) para capturar y "
        "validar requerimientos del proyecto. NO explores el sistema de "
        "archivos antes de delegar (sin ls, glob, read_file ni execute): el "
        "subagente ubica los documentos del proyecto via orient_documents. "
        "Este subagente RAZONA la captura etapa por etapa: orienta los "
        "documentos, planifica y ejecuta las seis etapas del pipeline "
        "(ingest_documents con Docling -> extract_requirements -> "
        "consolidate_requirements -> critique_requirements -> "
        "classify_requirements -> commit_capture) y refina antes de reportar. "
        "La ingesta de texto es UNICAMENTE via ingest_documents; no extraer el "
        "documento a mano."
    )
    decision = _user_existing_decision(user_instructions)
    if decision == "reset":
        directive += (
            ' [DECISION] El usuario decidio empezar de cero: invoca '
            'ingest_documents con on_existing="reset".'
        )
    elif decision == "append":
        directive += (
            ' [DECISION] El usuario decidio conservar los existentes: invoca '
            'ingest_documents con on_existing="append".'
        )
    if user_instructions:
        # chr(34) is the ASCII double quote; used here to keep literal quotes
        # around the user text without f-string escape sequences.
        directive += " INSTRUCCIONES DEL USUARIO: "
        directive += chr(34) + user_instructions + chr(34)
        directive += (
            " (incorporalas en la orientacion/planificacion; dirigen el "
            "razonamiento, no parametros internos del pipeline)."
        )
    return directive


def _rewrite_command(content: str) -> str:
    """Reescribe los slash commands en directivas al subagente.

    - ``/captura [subpath]`` -> captura y validación de requerimientos.
    - ``/agrupar`` -> revisión de duplicados (plan de agrupamiento editable).

    El mensaje crudo del usuario (el comando literal) ya se persistió en
    ChatMessage antes del stream; esta reescritura solo cambia lo que recibe el
    agente, para que delegue de forma casi determinista.
    """
    stripped = content.strip()

    if stripped.startswith(_AGRUPAR_PREFIX):
        # /agrupar no toma argumentos: review_grouping lee todo el store vivo.
        return (
            "[DIRECTIVE] Delega al subagente `requirements-capture` para revisar "
            "el agrupamiento de duplicados del store de requerimientos. Invoca la "
            "tool `review_grouping` para generar un plan de agrupamiento editable "
            "(lo escribe bajo .infofact/grouping-plans/ y lo devuelve para mostrar "
            "al usuario). NO apliques el plan todavía: muéstralo y espera a que el "
            "usuario lo edite o lo apruebe explícitamente antes de llamar "
            "`apply_grouping_plan`."
        )

    # /captura_agente (and /captura-agente) -> agent-driven subagent. Checked
    # BEFORE the /captura branch because "/captura_agente" startswith
    # "/captura". Text after the command is user steering, not a subpath.
    for _prefix in _CAPTURA_AGENTE_PREFIXES:
        if stripped.startswith(_prefix):
            _user_instructions = (
                stripped[len(_prefix):].strip().lstrip("/").strip()
            )
            return _captura_agente_directive(_user_instructions)

    if not stripped.startswith(_CAPTURA_PREFIX):
        return content
    # Acepta "/captura docs/x" (formato del plan §9.4) y "/captura/docs/x"
    # (sin espacio); normaliza barra inicial. El subpath es siempre relativo
    # al workspace, nunca absoluto. Si el texto trae una decision sobre los
    # datos existentes (reset/append), se propaga como on_existing; si no,
    # on_existing="ask" (solo frena si hay datos, pero la guardia router-level
    # ya impidio llegar aca con datos previos sin decision explicita).
    rest = stripped[len(_CAPTURA_PREFIX):].strip().lstrip("/")
    subpath, decision = _extract_decision_and_subpath(rest)
    if subpath:
        target_clause = f' target_subpath="{subpath}"'
    else:
        target_clause = ' target_subpath="" (proyecto completo)'
    on_existing = decision or "ask"
    return (
        "[DIRECTIVE] Delega al subagente `requirements-capture` para ejecutar la "
        "captura y validación de requerimientos del proyecto. Invoca la tool "
        f"`run_requirements_capture` con{target_clause} y "
        f'on_existing="{on_existing}". Cuando termine, reporta al usuario: '
        "documentos procesados, total extraído, duplicados propuestos, "
        "contradicciones detectadas, items marcados para revisión y items "
        "rechazados por alucinación. Luego ofrece ayudar a editar, fusionar o "
        "aprobar los requerimientos."
    )


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: int,
    body: MessageBody,
    request: Request,
) -> StreamingResponse:
    user = await _user_from_cookie(request)

    # Resolver sesión + pertenencia ANTES de abrir el stream.
    async with AsyncSessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        # 404 para 'no existe' y 'no propio' indistintamente: no filtrar existencia.
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "session_not_found")
        project = await db.get(Project, session.project_id)
        if project is None or project.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "session_not_found")
        session_phase = session.phase

        # Guarda de concurrencia: una stream por sesión a la vez.
        key = str(session_id)
        if key in _active_streams:
            raise HTTPException(status.HTTP_409_CONFLICT, "stream_active")
        _active_streams.add(key)

        # Persistir mensaje de usuario antes del stream para que sobreviva aun
        # si el agente falla a mitad de camino.
        db.add(ChatMessage(session_id=session_id, role="user", content=body.content))
        await db.commit()

    # Guardia router-level (inviolable) para captura sobre datos existentes: si
    # el comando es de captura y hay requerimientos previos sin una decision
    # explicita, NO despachamos al agente; mostramos una confirmacion. Cierra el
    # bypass del subagente agent-driven que elegia on_existing por si solo.
    gate_msg = await _capture_gate_message(project.id, body.content)
    if gate_msg is not None:
        async with AsyncSessionLocal() as db:
            db.add(
                ChatMessage(
                    session_id=session_id, role="assistant", content=gate_msg
                )
            )
            await db.commit()
        async def _gate_stream() -> AsyncGenerator[str, None]:
            try:
                yield _sse("token", {"delta": gate_msg})
                yield _sse("completed", {"message": gate_msg})
            finally:
                _active_streams.discard(key)
        return StreamingResponse(
            _gate_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Arranca el container del usuario si no está corriendo.
    await ensure_container(user.profile)

    agent = build_agent(
        profile=user.profile,
        project_slug=project.slug,
        project_name=project.name,
        project_description=project.description,
        project_id=project.id,
        thread_id=str(session_id),
        phase=session_phase,
        checkpointer=getattr(request.app.state, "checkpointer", None),
    )

    # thread_id va en config, NO en build(): así lo persiste el checkpointer.
    # recursion_limit es top-level en RunnableConfig (sibling de configurable).
    config: dict[str, Any] = {
        "configurable": {"thread_id": str(session_id)},
        "recursion_limit": RECURSION_LIMIT,
    }
    content = _rewrite_command(body.content)

    async def event_stream() -> AsyncGenerator[str, None]:
        accumulated: list[str] = []
        seen_tool_calls: set[str] = set()
        try:
            async for chunk in agent.astream(
                {"messages": [{"role": "user", "content": content}]},
                stream_mode=["messages", "custom"],
                subgraphs=True,
                version=ASTREAM_VERSION,
                config=config,
            ):
                ctype = chunk.get("type")
                cdata = chunk.get("data")
                if ctype == "messages":
                    # messages mode v2: data = (message_chunk, metadata)
                    if not isinstance(cdata, tuple) or len(cdata) != 2:
                        continue
                    token, _metadata = cdata
                    # astream messages mode v2 con este modelo entrega objetos
                    # AIMessage COMPLETOS (content + tool_calls), no deltas
                    # AIMessageChunk. Se aceptan ambas formas: los deltas traen
                    # tool_call_chunks; los mensajes completos, tool_calls.
                    # Dedup por id de tool call en cualquier caso.
                    if isinstance(token, (AIMessage, AIMessageChunk)):
                        tcc = getattr(token, "tool_call_chunks", None)
                        tc = getattr(token, "tool_calls", None)
                        calls = tcc or tc
                        text = getattr(token, "content", "")
                        # Delta de texto (ignorar mensajes que traen tool calls).
                        if isinstance(text, str) and text and not calls:
                            accumulated.append(text)
                            yield _sse("token", {"delta": text})
                        # Inicio de tool call: emitir tool_start una vez por id.
                        if calls:
                            for c in calls:
                                if not isinstance(c, dict):
                                    continue
                                c_name = c.get("name")
                                c_id = c.get("id") or c_name
                                if c_name and c_id not in seen_tool_calls:
                                    seen_tool_calls.add(c_id)
                                    yield _sse(
                                        "tool_start",
                                        {
                                            "name": c_name,
                                            "input": _safe_json(c.get("args")),
                                        },
                                    )
                    elif isinstance(token, ToolMessage):
                        yield _sse(
                            "tool_end",
                            {
                                "name": getattr(token, "name", "") or "tool",
                                "output": _truncate(
                                    _stringify(getattr(token, "content", ""))
                                ),
                            },
                        )
                elif ctype == "custom":
                    # Evento del pipeline via get_stream_writer dentro de una tool.
                    # cdata = {"event": "extraction.progress", "data": {...}}
                    if isinstance(cdata, dict):
                        ev_name = cdata.get("event") or "custom"
                        ev_data = cdata.get("data", {})
                        yield _sse(ev_name, _safe_json(ev_data))

            assistant_text = "".join(accumulated)
            try:
                async with AsyncSessionLocal() as db:
                    db.add(
                        ChatMessage(
                            session_id=session_id,
                            role="assistant",
                            content=assistant_text,
                        )
                    )
                    await db.commit()
            except Exception:  # noqa: BLE001 - la persistencia no rompe el stream
                logger.exception(
                    "no se pudo persistir mensaje del asistente session_id=%s",
                    session_id,
                )

            yield _sse("completed", {"message": assistant_text})
        except Exception as exc:  # noqa: BLE001 - saneamos y emitimos 'failed'
            logger.exception("agent stream failed session_id=%s", session_id)
            yield _sse("failed", {"error": _sanitize_error(exc)})
        finally:
            _active_streams.discard(key)

    # X-Accel-Buffering: no => desactiva buffer de proxies (nginx).
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
