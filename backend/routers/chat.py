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

import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    ToolMessage,
)
from pydantic import BaseModel, Field

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.deps import COOKIE_NAME, decode_access_token
from backend.models import ChatMessage, ChatSession, Project, User
from backend.agents.subagents.srs_run_holder import rearm_run
from backend.agents.tools.grouping_tools import run_grouping_review
from backend.services.agent_service import build_agent
from backend.services.container_service import ensure_container

logger = logging.getLogger(__name__)

router = APIRouter()

# Guarda de concurrencia: una sola stream activa por sesión.
# Previende doble POST que duplicaría estado en el checkpointer.
_active_streams: set[str] = set()

MAX_TOOL_OUTPUT_CHARS = 500
ASTREAM_VERSION = "v2"

# Clave con la que LangChain (>= 1.3.15) marca las llamadas internas de
# middleware (p. ej. el resumen de compactación del SummarizationMiddleware:
# los bloques "## SESSION INTENT / ## SUMMARY"). Su texto es maquinaria
# interna: el relay NUNCA lo emite al usuario ni lo persiste (bug de la
# sesión 48: el resumen interno se mostró y se concatenó con la respuesta
# real del agente). Valor: token de proceso imposible de falsear; para el
# filtro basta la presencia de la clave en la metadata del evento.
INTERNAL_CALL_METADATA_KEY = "lc_internal_call"
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


class _RelayAccumulator:
    """Acumula el texto assistant del turno con topes anti-veneno (sesión 44).

    Un delta puede ser gigante (una llamada anidada no-streaming llega como UN
    mensaje completo) y el acumulado del turno no tiene por qué crecer sin
    límite: esta clase acota las tres salidas del relay.

    - Cada frame ``token`` lleva a lo sumo ``max_delta`` caracteres: un delta
      grande se parte en varios frames chicos (protege el transporte, no
      descarta contenido).
    - Solo el techo ``max_total`` del turno descarta contenido: deja de emitir
      lo recortado y emite UN único evento custom ``relay.truncated``
      (``{"shown", "omitted", "limit"}``), con las cifras acumuladas.
    - ``result()`` devuelve el acumulado capado + marcador con la cuenta final
      de omitidos: es lo que se persiste en ChatMessage Y lo que emite el
      evento ``completed`` (mismo texto en ambos lados).

    ``tool_start``/``tool_end``/custom no pasan por acá: ya están acotados
    por MAX_TOOL_OUTPUT_CHARS / _safe_json.
    """

    def __init__(self, *, max_delta: int, max_total: int) -> None:
        self._max_delta = max_delta
        self._max_total = max_total
        self._parts: list[str] = []
        self._shown = 0
        self._omitted = 0
        self._truncated_notified = False

    @property
    def shown(self) -> int:
        """Caracteres de texto assistant realmente emitidos en el turno."""
        return self._shown

    @property
    def omitted(self) -> int:
        """Caracteres descartados por los topes (delta y turno)."""
        return self._omitted

    def _truncated_frame(self) -> str:
        return _sse(
            "relay.truncated",
            {
                "shown": self._shown,
                "omitted": self._omitted,
                "limit": self._max_total,
            },
        )

    def add(self, text: str) -> list[str]:
        """Agrega un delta y devuelve los frames SSE a emitir por él.

        El tope por delta es de TRANSPORTE (tamaño de frame): un delta
        grande se PARTE en frames chicos de ``max_delta`` (un solo frame
        gigante congela el browser). Solo el techo ``max_total`` del turno
        descarta contenido real.
        """
        if not text:
            return []
        frames: list[str] = []
        room = max(self._max_total - self._shown, 0)
        take = min(len(text), room)
        if take > 0:
            emit = text[:take]
            self._parts.append(emit)
            self._shown += len(emit)
            for i in range(0, len(emit), self._max_delta):
                frames.append(
                    _sse("token", {"delta": emit[i : i + self._max_delta]})
                )
        dropped = len(text) - take
        if dropped > 0:
            self._omitted += dropped
            if not self._truncated_notified:
                self._truncated_notified = True
                frames.append(self._truncated_frame())
        return frames

    def result(self) -> str:
        """Acumulado capado + marcador: lo persistido y lo emitido en completed."""
        text = "".join(self._parts)
        if self._omitted > 0:
            text += (
                f"\n\n[Salida truncada: se omitieron {self._omitted} caracteres "
                f"(limite {self._max_total}).]"
            )
        return text


_THINKING_TRUNCATION_MARKER = "\n[…pensamiento interno truncado por el límite del relay]"


class _ThinkingRelay:
    """Emite el thinking interno (reasoning_content de GLM) con tope por turno.

    El thinking es texto EFÍMERO de UI (evento SSE ``thinking``): no se
    acumula, no se persiste en ChatMessage y no entra al input del modelo.
    Al alcanzar el tope emite UN marcador y calla por el resto del turno
    (distinto de _RelayAccumulator: acá no hay acumulado que reportar).
    """

    def __init__(self, max_chars: int) -> None:
        self._max = max_chars
        self._shown = 0
        self._closed = False

    def add(self, delta: str) -> list[str]:
        """Devuelve los frames SSE ``thinking`` correspondientes al delta."""
        if not delta or self._closed:
            return []
        room = self._max - self._shown
        if room <= 0:
            self._closed = True
            return [_sse("thinking", {"delta": _THINKING_TRUNCATION_MARKER})]
        if len(delta) <= room:
            self._shown += len(delta)
            return [_sse("thinking", {"delta": delta})]
        take = delta[:room]
        self._shown = self._max
        self._closed = True
        return [
            _sse("thinking", {"delta": take}),
            _sse("thinking", {"delta": _THINKING_TRUNCATION_MARKER}),
        ]


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


def _is_internal_call(metadata: Any) -> bool:
    """True si el chunk viene de una llamada interna de middleware.

    Los eventos `messages` de astream llevan (chunk, metadata); la metadata de
    las llamadas internas (compactación) incluye INTERNAL_CALL_METADATA_KEY.
    """
    return isinstance(metadata, dict) and INTERNAL_CALL_METADATA_KEY in metadata


def _sanitize_error(exc: Exception) -> str:
    """Sin traceback; solo tipo + mensaje, para no filtrar internals."""
    name = type(exc).__name__
    msg = str(exc).strip()
    return f"{name}: {msg}" if msg else name


# Recuperación de overflow de ventana (sesión 17 de Planitrack2.0): un turno
# que consulta el SRS completo inunda el thread de tool_results y el upstream
# rechaza CUALQUIER llamada siguiente (400 prompt is too long). Sin
# intervención, cada reintento recarga el mismo estado inflado y la sesión
# queda clavada para siempre: la compactación es lo que la recupera.
_OVERFLOW_MARKERS = (
    "ContextWindowExceededError",
    "ContextOverflowError",
    "prompt is too long",
    "context_length_exceeded",
    "context length exceeded",
)


def _is_context_overflow(exc: Exception) -> bool:
    """El proveedor rechazó el prompt por exceder la ventana del modelo.

    El texto llega envuelto en capas (litellm envuelve el 400 del proveedor;
    langchain_openai lo re-empaqueta como OpenAIContextOverflowError), así que
    se matchea el mensaje completo y no solo la clase de excepción.
    """
    text = f"{type(exc).__name__}: {exc}"
    return any(marker in text for marker in _OVERFLOW_MARKERS)


async def _compact_overflowed_thread(agent: Any, thread_id: str) -> int | None:
    """Reescribe el thread con la historia recortada tras un overflow.

    Conserva TODOS los mensajes (los pares tool_call/ToolMessage deben llegar
    completos al proveedor) pero acota cada contenido con el mismo guard del
    SizeGuard: los megabytes históricos pasan a marcadores de truncado y el
    reintento del usuario encuentra una sesión usable. Devuelve la cantidad
    de mensajes que quedaron (None si no había thread/estado).
    """
    from langchain_core.messages import RemoveMessage
    from langgraph.graph.message import REMOVE_ALL_MESSAGES

    from backend.agents.size_guard import truncate_messages

    checkpointer = getattr(agent, "checkpointer", None)
    if checkpointer is None:
        return None
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await checkpointer.aget_tuple(config)
    messages = None
    if snapshot is not None and snapshot.checkpoint:
        messages = (snapshot.checkpoint.get("channel_values") or {}).get(
            "messages"
        )
    if not messages:
        return None
    compacted = truncate_messages(
        list(messages),
        # Contenidos históricos a un resumen de 4K: suficiente para que el
        # agente sepa qué pasó, imposible que sature una ventana.
        max_message_chars=4_000,
        total_input_chars=settings.llm_total_input_chars,
    )
    # RemoveMessage(REMOVE_ALL_MESSAGES) borra la historia previa del estado
    # y los mensajes recortados la reemplazan (patrón del summarizer de
    # langchain): mismos ids de tool_call, sin huérfanos.
    await agent.aupdate_state(
        config,
        {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *compacted]},
    )
    return len(compacted)


# Comando /captura: el router reescribe el slash command como directiva al
# subagente agentico requirements-capture-agent. Sin arg = proyecto completo;
# con arg = steering del usuario (subpath, foco).
_CAPTURA_PREFIX = "/captura"
_AGRUPAR_PREFIX = "/agrupar"
# /captura_agente MUST be matched before /captura: "/captura_agente"
# startswith("/captura"), so the generic /captura branch would otherwise
# swallow it and parse "_agente ..." as a target_subpath. Accepts _ and -.
_CAPTURA_AGENTE_PREFIXES = ("/captura_agente", "/captura-agente")
# /srs (síntesis de SRS) -> subagente srs-agent. Sin conflicto de prefijo
# con /captura ni /agrupar (no comparten raíz).
_SRS_PREFIX = "/srs"
# /analisis (Fase 2: analisis y diseno arquitectonico) -> subagente
# analysis-agent. Sin conflicto de prefijo con los demás comandos.
_ANALYSIS_PREFIX = "/analisis"
_PACKAGES_PREFIX = "/paquetes"

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


def _is_pure_agrupar(content: str) -> bool:
    """True para el comando ``/agrupar`` EXACTO (con espacios alrededor sí).

    El comando puro es determinista (review_grouping + persistir + resumen),
    así que send_message lo responde por la ruta directa sin tocar el modelo.
    Cualquier texto extra ("/agrupar solo seguridad") es steering y va por la
    ruta agéntica con directiva; no hay DSL de parsing en el router.
    """
    return content.strip() == "/agrupar"


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
    from backend.agents.subagents.requirements_capture_agent import _count_existing

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
        "Ejemplo: `/captura resetear` o "
        "`/captura agregar a los existentes`. Puedes repetir tus "
        "instrucciones de captura junto con la decision."
    )


def _captura_agente_directive(
    user_instructions: str, capture_scope: str = ""
) -> str:
    """Construye la directiva para el subagente agent-driven de captura.

    El orquestador delega a ``requirements-capture-agent`` via la tool ``task``.
    El texto libre despues del comando se reenvia como steering que el agente
    incorpora en orientar/planificar. Si el texto expresa una decision sobre los
    datos existentes (reset/append), se propaga explicitamente para que el
    agente la aplique en ``ingest_documents`` sin volver a consultar. Cuando el
    comando nombra una carpeta, capture_scope viaja como [SCOPE]: un contrato
    que las tools verifican mecanicamente (scope_violation), no steering.
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
    if capture_scope:
        directive += (
            f" [SCOPE] El usuario pidio capturar UNICAMENTE la carpeta "
            f"'{capture_scope}'. Pasa target_subpath='{capture_scope}' en "
            "orient_documents e ingest_documents, EXACTAMENTE ese valor en "
            "cada llamada: si lo omites, la tool rechaza la llamada con "
            "scope_violation. Ningun documento fuera de esa carpeta entra en "
            "esta captura; si detectas material relevante fuera del alcance, "
            "informalo en tu reporte final sin procesarlo."
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


def _srs_directive(user_instructions: str) -> str:
    """Directiva para el comando ``/srs`` (espejo de ``_captura_agente_directive``).

    Delega al subagente ``srs-agent`` la generación del SRS a partir de los
    requerimientos YA capturados. Sin gate router-level propio: cada invocación
    crea una versión CANDIDATE nueva (versionado, no destructivo sobre los
    requerimientos ni sobre versiones previas). El subagente gestiona el caso
    "no hay requerimientos vivos" informándolo al usuario.
    """
    directive = (
        "[DIRECTIVE] Delega INMEDIATAMENTE al subagente `srs-agent` (usando la "
        "tool `task`) para generar el SRS del proyecto a partir de los "
        "requerimientos YA capturados. NO explores el sistema de archivos antes "
        "de delegar (sin ls, glob, read_file ni execute): el subagente lee los "
        "requerimientos vivos del store. Este subagente RAZONA la generación "
        "etapa por etapa en este orden: analyze_quality (calidad INCOSE + "
        "smells + EARS + ambigüedad LLM) -> infer_goals (modelo de goals GORE, "
        "los persiste) -> check_coverage (ISO 25010 + secciones 29148 + "
        "cobertura de goals) -> commit_srs (persiste un SrsDocument CANDIDATE). "
        "La cobertura DEBE ir después de goals (depende de ellos). Si no hay "
        "requerimientos vivos, el subagente lo informará: el usuario debe "
        "capturar primero con /captura. Cuando termine, reporta al "
        "usuario: versión generada, requerimientos incluidos, hallazgos por "
        "severidad, gaps de cobertura y goals inferidos."
    )
    if user_instructions:
        # chr(34) is the ASCII double quote; keeps literal quotes around the
        # user text without f-string escape sequences (mismo truco que captura).
        directive += " INSTRUCCIONES DEL USUARIO: "
        directive += chr(34) + user_instructions + chr(34)
        directive += (
            " (incorporalas en el razonamiento entre etapas; dirigen el "
            "razonamiento, no parámetros internos del pipeline)."
        )
    return directive


def _analysis_directive(user_instructions: str) -> str:
    """Directiva para el comando ``/analisis`` (subagente analysis-agent).

    Delega al subagente ``analysis-agent`` la generación de artefactos
    arquitectonicos (MER, diagramas de proceso, NFR, ADRs, sub-proyectos) a
    partir de los requerimientos YA capturados. Sin gate router-level propio:
    cada invocacion crea una versión CANDIDATE nueva del AnalysisDocument
    (versionado, no destructivo). El subagente gestiona el caso "no hay
    requerimientos vivos" informándolo al usuario.
    """
    directive = (
        "[DIRECTIVE] Delega INMEDIATAMENTE al subagente `analysis-agent` "
        "(usando la tool `task`) para ejecutar el analisis y diseno "
        "arquitectonico del proyecto a partir de los requerimientos YA "
        "capturados. NO explores el sistema de archivos antes de delegar (sin "
        "ls, glob, read_file ni execute): el subagente lee los requerimientos "
        "vivos del store. Este subagente RAZONA la generación etapa por etapa "
        "en este orden: generate_mer (Modelo Entidad-Relacion) -> "
        "analyze_nfrs (decisiones arquitectonicas + stack) -> "
        "generate_processes (maquinas de estados + secuencias) -> "
        "generate_adrs (Architecture Decision Records) -> "
        "propose_subprojects (descomposicion + contratos) -> "
        "commit_analysis (persiste un AnalysisDocument CANDIDATE). Respeta "
        "las dependencias: procesos necesita el MER, ADRs necesita el "
        "analisis NFR, sub-proyectos necesita MER + ADRs. Si no hay "
        "requerimientos vivos, el subagente lo informará: el usuario debe "
        "capturar primero con /captura. Cuando termine, reporta al usuario: "
        "entidades, relaciones, ADRs, sub-proyectos y diagramas generados."
    )
    if user_instructions:
        directive += " INSTRUCCIONES DEL USUARIO: "
        directive += chr(34) + user_instructions + chr(34)
        directive += (
            " (incorporalas en el razonamiento entre etapas; dirigen el "
            "razonamiento, no parámetros internos del pipeline)."
        )
    return directive


def _packages_directive(user_instructions: str) -> str:
    """Directiva para el comando ``/paquetes`` (subagente packages-agent).

    Delega al subagente ``packages-agent`` la generacion de paquetes de
    trabajo entregables (uno por sub-proyecto + maestro de ensamblaje) a
    partir del ultimo AnalysisDocument comprometido. La puerta de coherencia
    del pipeline puede BLOQUEAR el commit: el subagente reporta los gates
    fallidos en vez de persistir.
    """
    directive = (
        "[DIRECTIVE] Delega INMEDIATAMENTE al subagente `packages-agent` "
        "(usando la tool `task`) para generar los paquetes de trabajo del "
        "proyecto a partir del ultimo analisis comprometido. NO explores el "
        "sistema de archivos antes de delegar. El subagente ejecuta: "
        "generate_work_packages (ensambla paquetes + corre la puerta de "
        "coherencia) y, si el informe lo permite, commit_packages (persiste "
        "la version CANDIDATE). Si un gate bloqueante falla, NO commitea: "
        "reporta al usuario los gates fallidos y sugiere refinar el "
        "analisis. Cuando termine, reporta: cantidad de paquetes y tareas, "
        "orden de construccion sugerido, deudas visibles (gates en warn y "
        "hallazgos de la critica cruzada) y donde verlos/descargarlos."
    )
    if user_instructions:
        directive += " INSTRUCCIONES DEL USUARIO: "
        directive += chr(34) + user_instructions + chr(34)
        directive += (
            " (incorporalas en el razonamiento; dirigen el reporte, no "
            "parámetros internos del pipeline)."
        )
    return directive


def _agrupar_directive(user_instructions: str) -> str:
    """Directiva para ``/agrupar`` con steering (espejo de ``_srs_directive``).

    El comando puro ya no llega acá en producción (send_message lo responde
    por la ruta directa); esta directiva cubre el caso con steering, donde el
    modelo traduce el alcance pedido por el usuario a los argumentos
    ``types``/``documents`` de ``review_grouping``.
    """
    directive = (
        "[DIRECTIVE] Delega INMEDIATAMENTE al subagente "
        "`requirements-capture-agent` (usando la tool `task`) para revisar el "
        "agrupamiento de duplicados del store de requerimientos: es el "
        "subagente quien invoca `review_grouping` (no es una tool tuya) para "
        "generar y persistir un plan de agrupamiento editable en la DB (NO "
        "lo escribas a mano en el workspace). NO explores el sistema de "
        "archivos antes de delegar (sin ls, glob, read_file ni execute): "
        "los requerimientos viven en la base de datos del backend, NO en "
        "archivos del sandbox — el subagente los lee del store. Si el "
        "usuario acotó el alcance de la revisión (tipos de "
        "requerimiento y/o documentos específicos), pásalo como los "
        "argumentos `types` y/o `documents` de `review_grouping`; si el "
        "alcance no mapea a esos argumentos, revisa todo el store vivo. NO "
        "apliques el plan todavía: muéstralo y espera a que el usuario lo "
        "edite o lo apruebe explícitamente antes de llamar "
        "`apply_grouping_plan` (las fusiones son destructivas). Cuando "
        "termine, reporta al usuario: plan_id, cantidad de grupos y alcance "
        "aplicado."
    )
    if user_instructions:
        # chr(34) is the ASCII double quote; keeps literal quotes around the
        # user text without f-string escape sequences (mismo truco que captura).
        directive += " INSTRUCCIONES DEL USUARIO: "
        directive += chr(34) + user_instructions + chr(34)
        directive += (
            " (incorpóralas al alcance de review_grouping vía types/documents; "
            "dirigen el razonamiento, no parámetros internos del pipeline)."
        )
    return directive


# Extensiones de documento que la UI puede mencionar como alcance de captura.
# Un token suelto sin separador de carpeta solo cuenta como alcance si termina
# en una de estas (p. ej. "acta.pdf"); "atencion" nunca matchea.
_CAPTURA_DOC_EXTENSIONS = re.compile(
    r"\.(?:md|txt|csv|docx?|pdf|xlsx?|pptx?)$", re.IGNORECASE
)


def _looks_like_scope_token(token: str) -> bool:
    """True si el token es plausiblemente una carpeta o documento del workspace.

    Un token citado (comillas) es alcance por definición. Uno suelto lo es si
    contiene un separador de carpeta o termina en extensión de documento; el
    steering libre ("enfatiza las restricciones") no matchea nunca.
    """
    bare = token.strip().strip(",").strip()
    if bare != token.strip().strip(","):
        return False
    if bare.startswith('"') or bare.startswith("'"):
        return True
    bare = bare.lstrip("@")
    return bool(bare) and ("/" in bare or "\\" in bare or bool(
        _CAPTURA_DOC_EXTENSIONS.search(bare)
    ))


def _extract_captura_scope(text: str) -> tuple[str, str]:
    """Separa ``texto`` en (alcance pedido, steering restante).

    Consume los primeros tokens con forma de ruta (soporta una lista separada
    por comas, decorada con @ y comillas) y se frena en la primera palabra
    libre. Devuelve ("", text) si el primer token no es una ruta: todo queda
    como steering libre del usuario.
    """
    text = text.strip()
    if not text:
        return "", ""
    quoted = re.match(r'^(["\'])(.+?)\1\s*(.*)$', text)
    if quoted is not None:
        return quoted.group(2).strip(), quoted.group(3).strip()
    scope_tokens: list[str] = []
    rest = text
    while rest:
        head, _, tail = rest.partition(" ")
        if not _looks_like_scope_token(head):
            break
        scope_tokens.append(head.strip().strip(","))
        rest = tail.strip()
    if not scope_tokens:
        return "", text
    if len(scope_tokens) > 1:
        # Varias rutas sueltas: el pipeline solo acepta UNA carpeta por
        # captura (target_subpath), asi que un scope multi-ruta seria un
        # contrato imposible. Queda como steering libre (comportamiento
        # previo) hasta que exista soporte de listas en ingest_documents.
        return "", text
    scope = scope_tokens[0].lstrip("@")
    return normalize_scope_text(scope), rest


def normalize_scope_text(subpath: str) -> str:
    """Decoraciones fuera (/, @, comillas) para comparar contra el registro."""
    from backend.agents.subagents.capture_run_holder import normalize_scope

    try:
        return normalize_scope(subpath)
    except ValueError:
        return subpath


def _rewrite_command(content: str, project_id: int | None = None) -> str:
    """Reescribe los slash commands en directivas al subagente.

    - ``/captura [subpath] [steering]`` -> captura y validación de
      requerimientos. Si el primer token tras el comando es una carpeta o
      documento del workspace, se registra como alcance pedido (contract
      scope, verificado por las tools) y viaja además como [SCOPE] en la
      directiva; el resto del texto queda como steering libre.
    - ``/agrupar [steering]`` -> revisión de duplicados. El comando puro lo
      responde la rama directa de ``send_message``; con texto extra, esta
      directiva delega con el alcance del usuario (types/documents).
    - ``/srs [steering]`` -> generación del SRS (calidad + goals + cobertura + commit).

    El mensaje crudo del usuario (el comando literal) ya se persistió en
    ChatMessage antes del stream; esta reescritura solo cambia lo que recibe el
    agente, para que delegue de forma casi determinista.
    """
    stripped = content.strip()

    if stripped.startswith(_AGRUPAR_PREFIX):
        # /agrupar puro no llega acá en producción (rama directa de
        # send_message); queda para tests/smokes y el caso con steering. El
        # plan vive en la DB (grouping_plans), no en archivos del workspace.
        _user_instructions = (
            stripped[len(_AGRUPAR_PREFIX):].strip().lstrip("/").strip()
        )
        return _agrupar_directive(_user_instructions)

    # /srs -> generación de SRS (subagente srs-agent). Texto tras el comando
    # es steering del usuario, no un subpath. Se chequea antes del fallback.
    if stripped.startswith(_SRS_PREFIX):
        _user_instructions = (
            stripped[len(_SRS_PREFIX):].strip().lstrip("/").strip()
        )
        return _srs_directive(_user_instructions)

    # /analisis -> Fase 2: analisis y diseno arquitectonico (subagente
    # analysis-agent). Texto tras el comando es steering del usuario.
    if stripped.startswith(_ANALYSIS_PREFIX):
        _user_instructions = (
            stripped[len(_ANALYSIS_PREFIX):].strip().lstrip("/").strip()
        )
        return _analysis_directive(_user_instructions)

    # /paquetes -> entrega a desarrolladores (subagente packages-agent).
    # Texto tras el comando es steering del usuario.
    if stripped.startswith(_PACKAGES_PREFIX):
        _user_instructions = (
            stripped[len(_PACKAGES_PREFIX):].strip().lstrip("/").strip()
        )
        return _packages_directive(_user_instructions)

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
    # /captura rutea al mismo subagente agentico que /captura_agente. La
    # extraccion de alcance corre sobre el texto CRUDO tras el comando: el
    # lstrip("/") historico (separador del comando) va DESPUES, porque si no
    # "/captura /etc/passwd" llega al registro como scope "etc/passwd" (un
    # absoluto disfrazado de relativo) en vez de degradar a steering.
    _after_command = stripped[len(_CAPTURA_PREFIX):].strip()
    _user_instructions = _after_command.lstrip("/").strip()
    _capture_scope = ""
    if project_id is not None:
        _capture_scope, _remaining = _extract_captura_scope(_after_command)
        if _capture_scope:
            from backend.agents.subagents.capture_run_holder import (
                set_capture_scope,
            )
            try:
                _capture_scope = set_capture_scope(project_id, _capture_scope)
                _user_instructions = _remaining
            except ValueError:
                # Ruta invalida (absoluta, traversal): no se registra scope ni
                # se rompe el envio. El steering se restaura LITERAL (el
                # lstrip historico no aplica: no sabemos que era decoracion) y
                # la contencion final la hace _resolve_target en las tools.
                _capture_scope = ""
                _user_instructions = _after_command
    return _captura_agente_directive(_user_instructions, _capture_scope)


# Máximo de líneas de grupos en el resumen del chat de /agrupar (directo).
_AGRUPAR_SUMMARY_MAX_GROUPS = 20


def _summarize_grouping(result: dict) -> str:
    """Resumen en español (tuteo neutral) del resultado de run_grouping_review.

    Acotado por diseño: cabecera con plan_id/grupos/alcance, como máximo
    ``_AGRUPAR_SUMMARY_MAX_GROUPS`` líneas de grupos (keeper ← miembros) y un
    cierre que remarca que NO se fusionó nada (el apply es una acción aparte).
    """
    if "error" in result:
        return f"No se pudo revisar el agrupamiento: {result['error']}"
    considered = int(result.get("considered") or 0)
    scope = str(result.get("scope") or "")
    scope_frag = f" · alcance: {scope}" if scope else ""
    if considered < 2:
        return (
            f"Se analizaron {considered} requerimiento(s){scope_frag}. Con "
            "menos de 2 requerimientos en el alcance no hay duplicados que "
            "agrupar, así que no se detectaron grupos."
        )
    group_count = int(result.get("group_count") or 0)
    groups = result.get("groups") or []
    if group_count == 0:
        return (
            f"No se detectaron duplicados ({considered} analizados"
            f"{scope_frag})."
        )
    lines = [
        f"Plan de agrupamiento #{result.get('plan_id')}: {group_count} "
        f"grupo(s) de duplicados sobre {considered} requerimiento(s)"
        f"{scope_frag}."
    ]
    for g in groups[:_AGRUPAR_SUMMARY_MAX_GROUPS]:
        keeper = (g.get("keeper") or {}).get("code") or "?"
        members = [m.get("code") for m in (g.get("members") or []) if m.get("code")]
        shown = ", ".join(members[:2])
        if len(members) > 2:
            shown += f" (+{len(members) - 2})"
        lines.append(f"- {keeper} ← {shown}")
    if group_count > _AGRUPAR_SUMMARY_MAX_GROUPS:
        lines.append(
            f"… y {group_count - _AGRUPAR_SUMMARY_MAX_GROUPS} grupo(s) más"
        )
    lines.append(
        "Revisa los grupos en la pestaña «Agrupamiento» para aceptar o "
        "rechazar cada fusión. No se fusionó nada todavía: los cambios se "
        "aplican solo cuando lo confirmes."
    )
    return "\n".join(lines)


def _agrupar_direct_stream(session_id: int, project_id: int, key: str):
    """Stream SSE directo para ``/agrupar`` puro (sin rondas del orquestador).

    Secuencia (D2): tool_start -> grouping.progress* (etapas/lotes via una
    queue mientras corre run_grouping_review en un task) -> tool_end (output
    truncado) -> token(s) con el resumen (frames acotados por el acumulador)
    -> grouping.ready -> completed. En excepción: tool_end con el error (para
    no dejar el chip de tool vivo en el chat) + failed, sin espejo. El
    mensaje assistant se espeja en ChatMessage best-effort (precedente del
    gate); el thread del agente no se toca (D3).
    """
    async def _stream() -> AsyncGenerator[str, None]:
        accumulator = _RelayAccumulator(
            max_delta=settings.relay_max_delta_chars,
            max_total=settings.relay_max_assistant_chars,
        )
        tool_end_sent = False
        try:
            yield _sse("tool_start", {"name": "review_grouping", "input": {}})
            queue: asyncio.Queue[dict] = asyncio.Queue()

            async def _on_progress(evt: dict) -> None:
                await queue.put(_safe_json(evt))

            async with AsyncSessionLocal() as session:
                task = asyncio.create_task(
                    run_grouping_review(
                        session, project_id, on_progress=_on_progress
                    )
                )
                # Bomba de progreso: mientras corre el review en el task, cada
                # evento que cae en la queue sale como frame SSE inmediato (el
                # banner del frontend vive de esto; antes la ruta era muda).
                while True:
                    getter = asyncio.ensure_future(queue.get())
                    done, _ = await asyncio.wait(
                        {task, getter}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if getter in done:
                        yield _sse("grouping.progress", getter.result())
                    if task in done:
                        if not getter.done():
                            getter.cancel()
                        break
            # Drenar eventos que quedaron en la cola al terminar el task.
            while not queue.empty():
                yield _sse("grouping.progress", queue.get_nowait())
            result = task.result()
            tool_end_sent = True
            yield _sse(
                "tool_end",
                {
                    "name": "review_grouping",
                    "output": _truncate(
                        json.dumps(result, default=str, ensure_ascii=False)
                    ),
                },
            )
            summary = _summarize_grouping(result)
            for frame in accumulator.add(summary):
                yield frame
            if "error" not in result:
                yield _sse(
                    "grouping.ready",
                    {
                        "plan_id": result.get("plan_id"),
                        "group_count": int(result.get("group_count") or 0),
                    },
                )
            assistant_text = accumulator.result()
            try:
                async with AsyncSessionLocal() as db:
                    db.add(
                        ChatMessage(
                            session_id=session_id,
                            role="assistant",
                            kind="text",
                            is_intermediate=False,
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
            logger.exception(
                "agrupar direct stream failed session_id=%s", session_id
            )
            if not tool_end_sent:
                yield _sse(
                    "tool_end",
                    {"name": "review_grouping", "output": _truncate(_sanitize_error(exc))},
                )
            yield _sse("failed", {"error": _sanitize_error(exc)})
        finally:
            _active_streams.discard(key)

    return _stream()


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
        # si el agente falla a mitad de camino. Se guarda el id para ligarlo al
        # HumanMessage del checkpointer (additional_kwargs['chat_row_id']): así
        # get_session_detail restaura el contenido original sin adivinar
        # posiciones cuando hay turnos de ruta directa (p.ej. /agrupar puro)
        # que persisten fila pero nunca crean HumanMessage.
        user_msg = ChatMessage(
            session_id=session_id, role="user", kind="text", content=body.content
        )
        db.add(user_msg)
        await db.commit()
        user_msg_id = user_msg.id

    # Guardia router-level (inviolable) para captura sobre datos existentes: si
    # el comando es de captura y hay requerimientos previos sin una decision
    # explicita, NO despachamos al agente; mostramos una confirmacion. Cierra el
    # bypass del subagente agent-driven que elegia on_existing por si solo.
    gate_msg = await _capture_gate_message(project.id, body.content)

    # /srs: rearmar los contadores de loop del run activo (si lo hay) al
    # arrancar un episodio nuevo. Router-owned (mismo principio que la guardia
    # de arriba): el subagente no puede rearmarse por su cuenta. Preserva las
    # salidas de etapas ya completadas para que el run continue donde quedo.
    if body.content.strip().startswith(_SRS_PREFIX):
        rearm_run(project.id)
    if gate_msg is not None:
        async with AsyncSessionLocal() as db:
            db.add(
                ChatMessage(
                    session_id=session_id,
                    role="assistant",
                    kind="text",
                    content=gate_msg,
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

    # /agrupar puro responde por la ruta directa (D1): el comando es
    # determinista (review + persistir + resumen), no justifica las ~2 rondas
    # de modelo del orquestador (~42 min en sesiones maduras). Salta
    # ensure_container: solo toca DB + embeddings locales. El mensaje de
    # usuario ya quedó persistido arriba y la guarda _active_streams ya está
    # tomada; el generador la libera en finally.
    if _is_pure_agrupar(body.content):
        return StreamingResponse(
            _agrupar_direct_stream(session_id, project.id, key),
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
    content = _rewrite_command(body.content, project_id=project.id)

    async def event_stream() -> AsyncGenerator[str, None]:
        accumulator = _RelayAccumulator(
            max_delta=settings.relay_max_delta_chars,
            max_total=settings.relay_max_assistant_chars,
        )
        # Thinking interno (reasoning_content de GLM): efímero, con tope
        # propio por turno (ver _ThinkingRelay).
        thinking_relay = _ThinkingRelay(max_chars=settings.relay_max_thinking_chars)
        seen_tool_calls: set[str] = set()
        # Foto de sesión: segmento assistant en curso (se persiste al cerrarse)
        # y tools con su name/args vistos en tool_start, para persistir la fila
        # completa al tool_end.
        segment_parts: list[str] = []
        pending_tools: dict[str, dict] = {}

        async def _close_segment(intermediate: bool) -> None:
            """Persiste el segmento assistant actual como fila de la línea de
            tiempo. Cerrado por tool_start -> razonamiento intermedio; por
            completed/failed -> respuesta final. Fallo de persistencia no
            rompe el stream."""
            nonlocal segment_parts
            seg = "".join(segment_parts).strip()
            segment_parts = []
            if not seg:
                return
            try:
                async with AsyncSessionLocal() as db:
                    db.add(
                        ChatMessage(
                            session_id=session_id,
                            role="assistant",
                            kind="text",
                            is_intermediate=intermediate,
                            content=seg[: settings.relay_max_assistant_chars],
                        )
                    )
                    await db.commit()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "no se pudo persistir segmento session_id=%s", session_id
                )

        try:
            async for chunk in agent.astream(
                {
                    "messages": [
                        HumanMessage(
                            content=content,
                            additional_kwargs={"chat_row_id": user_msg_id},
                        )
                    ]
                },
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
                    # Llamada interna de middleware (resumen de compactación):
                    # su texto no se muestra ni se persiste.
                    if _is_internal_call(_metadata):
                        continue
                    # astream messages mode v2 con este modelo entrega objetos
                    # AIMessage COMPLETOS (content + tool_calls), no deltas
                    # AIMessageChunk. Se aceptan ambas formas: los deltas traen
                    # tool_call_chunks; los mensajes completos, tool_calls.
                    # Dedup por id de tool call en cualquier caso.
                    if isinstance(token, (AIMessage, AIMessageChunk)):
                        # Thinking interno del modelo (reasoning_content,
                        # capturado por ChatZai en agents/llm.py): evento SSE
                        # efímero — NO se acumula en segment_parts ni se
                        # persiste; llega por delta y el frontend lo muestra
                        # en el bloque colapsable "Pensamiento interno".
                        reasoning_delta = (
                            getattr(token, "additional_kwargs", None) or {}
                        ).get("reasoning_content")
                        if isinstance(reasoning_delta, str) and reasoning_delta:
                            for frame in thinking_relay.add(reasoning_delta):
                                yield frame
                        tcc = getattr(token, "tool_call_chunks", None)
                        tc = getattr(token, "tool_calls", None)
                        calls = tcc or tc
                        text = getattr(token, "content", "")
                        # Delta de texto (ignorar mensajes que traen tool calls).
                        if isinstance(text, str) and text and not calls:
                            segment_parts.append(text)
                            for frame in accumulator.add(text):
                                yield frame
                        # Inicio de tool call: emitir tool_start una vez por id.
                        if calls:
                            for c in calls:
                                if not isinstance(c, dict):
                                    continue
                                c_name = c.get("name")
                                c_id = c.get("id") or c_name
                                if c_name and c_id not in seen_tool_calls:
                                    seen_tool_calls.add(c_id)
                                    # El tool cierra el segmento actual:
                                    # razonamiento intermedio.
                                    await _close_segment(intermediate=True)
                                    pending_tools[c_id] = {
                                        "name": c_name,
                                        "args": c.get("args")
                                        if isinstance(c.get("args"), dict)
                                        else {},
                                    }
                                    yield _sse(
                                        "tool_start",
                                        {
                                            "name": c_name,
                                            "input": _safe_json(c.get("args")),
                                        },
                                    )
                    elif isinstance(token, ToolMessage):
                        output = _truncate(
                            _stringify(getattr(token, "content", ""))
                        )
                        pending = pending_tools.pop(
                            getattr(token, "tool_call_id", None) or "", None
                        )
                        yield _sse(
                            "tool_end",
                            {
                                "name": getattr(token, "name", "") or "tool",
                                "output": output,
                            },
                        )
                        try:
                            async with AsyncSessionLocal() as db:
                                db.add(
                                    ChatMessage(
                                        session_id=session_id,
                                        role="tool",
                                        kind="tool",
                                        tool_name=(pending or {}).get("name")
                                        or getattr(token, "name", "")
                                        or "tool",
                                        tool_args=(pending or {}).get("args") or {},
                                        content=output,
                                    )
                                )
                                await db.commit()
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "no se pudo persistir tool row session_id=%s",
                                session_id,
                            )
                elif ctype == "custom":
                    # Evento del pipeline via get_stream_writer dentro de una tool.
                    # cdata = {"event": "extraction.progress", "data": {...}}
                    if isinstance(cdata, dict):
                        ev_name = cdata.get("event") or "custom"
                        ev_data = cdata.get("data", {})
                        yield _sse(ev_name, _safe_json(ev_data))

            assistant_text = accumulator.result()
            # Foto de sesión: la respuesta final es el ÚLTIMO segmento (los
            # intermedios ya quedaron persistidos por _close_segment). El
            # evento completed sigue llevando el acumulado completo del turno
            # (compatibilidad con el frontend en vivo).
            await _close_segment(intermediate=False)
            yield _sse("completed", {"message": assistant_text})
        except Exception as exc:  # noqa: BLE001 - saneamos y emitimos 'failed'
            logger.exception("agent stream failed session_id=%s", session_id)
            # Turno abortado: persistir el segmento parcial como intermedio
            # para que la recarga muestre fielmente el corte.
            await _close_segment(intermediate=True)
            if _is_context_overflow(exc):
                # Recuperación: compactar el thread envenenado para que el
                # reintento no recargue los megabytes que reventaron la
                # ventana (sesión 17). Si la compactación falla, la sesión
                # queda como estaba y el error viaja igual.
                try:
                    kept = await _compact_overflowed_thread(
                        agent, str(session_id)
                    )
                    if kept is not None:
                        logger.info(
                            "thread compactado tras overflow session_id=%s "
                            "(%d mensajes)",
                            session_id,
                            kept,
                        )
                        yield _sse("failed", {
                            "error": (
                                "La conversación excedió la ventana de "
                                "contexto del modelo y fue compactada. "
                                "Volvé a intentar el pedido."
                            ),
                            "recovered": "context_compacted",
                        })
                        return
                except Exception:  # noqa: BLE001 - best-effort
                    logger.exception(
                        "compactación del thread falló session_id=%s",
                        session_id,
                    )
            yield _sse("failed", {"error": _sanitize_error(exc)})
        finally:
            _active_streams.discard(key)

    # X-Accel-Buffering: no => desactiva buffer de proxies (nginx).
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
