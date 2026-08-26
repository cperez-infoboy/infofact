"""Router de proyectos, sesiones y detalle de sesión.

Endpoints:
  POST   /api/projects                      crea proyecto + sesión inicial
  GET    /api/projects                      lista proyectos del usuario
  GET    /api/projects/{project_id}         detalle con sus sesiones
  POST   /api/projects/{project_id}/sessions   nueva sesión bajo un proyecto
  GET    /api/projects/{project_id}/sessions   lista sesiones del proyecto
  GET    /api/chat/sessions/{session_id}    detalle de sesión con mensajes
    (registrado aparte vía session_router prefix="/api/chat"; ver main.py)

Patrón de ownership (ver CLAUDE.md decisión 7): para no filtrar existencia, si
el recurso no existe O no pertenece al usuario actual, devolvemos 404
"not_found" idéntico en ambos casos. Usamos Depends(get_current_user) aquí
porque NINGUNO de estos endpoints hace streaming SSE (la trampa documentada
aplica solo al endpoint de stream en routers/chat.py).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.deps import get_current_user
from backend.models import ChatMessage, ChatSession, Project, User
from backend.services.agent_service import reconstruct_history
from backend.services.slugify import slugify
from backend.services.srs_builder import build_srs as _build_srs

logger = logging.getLogger(__name__)

router = APIRouter()

# Router separado para el endpoint de detalle de sesión que vive bajo /api/chat
# (no podemos tocar routers/chat.py y necesitamos ese prefijo). Registrado en
# main.py con prefix="/api/chat".
session_router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _generate_unique_slug(db, user_id: int, name: str) -> str:
    """Calcula un slug único para el proyecto `name` dentro del usuario.

    Slug base via slugify(name); si está tomado por otro proyecto del mismo
    usuario, prueba `{base}-2`, `{base}-3`, ... hasta encontrar uno libre.
    """
    base = slugify(name)
    # Carga todos los slugs existentes del usuario en un set: el número de
    # proyectos por usuario esperado es chico (decenas), alcanza una query.
    existing = (
        await db.execute(
            select(Project.slug).where(Project.user_id == user_id)
        )
    ).scalars().all()
    used = set(existing)
    if base not in used:
        return base
    n = 2
    while f"{base}-{n}" in used:
        n += 1
    return f"{base}-{n}"


def _ensure_project_workspace_dir(profile: str, slug: str) -> None:
    """Crea el directorio host del proyecto de forma best-effort.

    El agente y upload_files hacen `mkdir -p` sobre el dirname de cada
    archivo, así que el root se crearía igual en la primera escritura. Lo
    creamos acá para que el workspace aparezca inmediatamente en el árbol
    del frontend aunque el agente todavía no haya escrito nada.
    """
    try:
        target = settings.workspaces_root / profile / slug
        target.mkdir(parents=True, exist_ok=True)
        # El agente corre uid 1000 en su container; el workspace debe ser
        # escribible por ese uid. Si el backend corre como root (producción),
        # mkdir deja el dir root-owned y el agente no podría escribir.
        # Best-effort: si el backend ya corre como uid 1000 (dev host), el
        # chown al mismo uid es no-op o EPERM silencioso. Mismo patrón que
        # container_service._create_container.
        try:
            os.chown(target, 1000, 1000)
        except (PermissionError, OSError) as exc:
            logger.warning(
                "no se pudo chown %s a 1000:1000: %s", target, exc
            )
    except OSError:
        # No rompemos la creación del proyecto: el agente levantará el dir
        # en su primera escritura. Logueamos para diagnóstico.
        logger.exception(
            "no se pudo crear el directorio host del workspace profile=%s slug=%s",
            profile,
            slug,
        )


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    phase: str = Field("requirements", max_length=32)


class SessionCreate(BaseModel):
    title: str | None = Field(None, max_length=200)


class MessageOut(BaseModel):
    id: int | str
    role: str
    content: str = ""
    created_at: datetime
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_args: dict | None = None
    status: str | None = None
    # Foto de sesión: presente en filas de la línea de tiempo persistida;
    # None en la ruta legacy de reconstrucción.
    is_intermediate: bool | None = None


class SessionOut(BaseModel):
    id: int
    project_id: int
    title: str
    phase: str
    created_at: datetime


class SessionDetail(SessionOut):
    messages: list[MessageOut] = []


class ProjectOut(BaseModel):
    id: int
    name: str
    slug: str
    phase: str
    created_at: datetime
    # Sesión inicial creada en el POST; el frontend salta directo al chat.
    initial_session_id: int | None = None


class ProjectDetail(ProjectOut):
    description: str | None = None
    sessions: list[SessionOut] = []


class SrsOut(BaseModel):
    """Salida del endpoint GET /api/projects/{id}/srs.

    El markdown se genera on-demand desde RequirementItem[] (source of truth
    estructurada). `counts` permite al frontend pintar resumen sin parsear.
    """
    project_id: int
    markdown: str
    generated_at: str
    counts: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


def _project_out(project: Project, initial_session_id: int | None = None) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        slug=project.slug,
        phase=project.phase,
        created_at=project.created_at,
        initial_session_id=initial_session_id,
    )


@router.post(
    "",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    body: ProjectCreate,
    user: User = Depends(get_current_user),
) -> ProjectOut:
    """Crea un Project para el usuario + una ChatSession inicial asociada."""
    async with AsyncSessionLocal() as db:
        slug = await _generate_unique_slug(db, user.id, body.name)
        project = Project(
            user_id=user.id,
            name=body.name,
            slug=slug,
            phase=body.phase,
        )
        db.add(project)
        await db.flush()  # necesita project.id para el FK

        session = ChatSession(
            project_id=project.id,
            title=f"{project.name} — sesión 1",
            phase=project.phase,
        )
        db.add(session)
        await db.flush()
        initial_session_id = session.id

        await db.commit()
        await db.refresh(project)
        await db.refresh(session)

    # Workspace dir host: {workspaces_host_root}/{profile}/{slug}. Best-effort:
    # si falla, el agente lo crea en su primera escritura.
    _ensure_project_workspace_dir(user.profile, slug)

    return _project_out(project, initial_session_id=initial_session_id)


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    user: User = Depends(get_current_user),
) -> list[ProjectOut]:
    """Lista los proyectos del usuario (sin initial_session_id)."""
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Project)
                .where(Project.user_id == user.id)
                .order_by(Project.created_at.desc())
            )
        ).scalars().all()
    return [
        ProjectOut(
            id=p.id,
            name=p.name,
            slug=p.slug,
            phase=p.phase,
            created_at=p.created_at,
        )
        for p in rows
    ]


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(
    project_id: int,
    user: User = Depends(get_current_user),
) -> ProjectDetail:
    """Detalle de un proyecto + sus sesiones. 404 si no existe o no es propio."""
    async with AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        if project is None or project.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")

        sessions = (
            await db.execute(
                select(ChatSession)
                .where(ChatSession.project_id == project_id)
                .order_by(ChatSession.created_at.asc())
            )
        ).scalars().all()

    return ProjectDetail(
        id=project.id,
        name=project.name,
        slug=project.slug,
        phase=project.phase,
        created_at=project.created_at,
        description=project.description,
        sessions=[
            SessionOut(
                id=s.id,
                project_id=s.project_id,
                title=s.title,
                phase=s.phase,
                created_at=s.created_at,
            )
            for s in sessions
        ],
    )


@router.get("/{project_id}/srs", response_model=SrsOut)
async def get_project_srs(
    project_id: int,
    user: User = Depends(get_current_user),
) -> SrsOut:
    """Genera el SRS Markdown del proyecto desde RequirementItem[].

    El SRS es una vista generada (no persistida como archivo): la fuente de
    verdad es el store estructurado. 404 si el proyecto no existe o no es
    propio.
    """
    async with AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        if project is None or project.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        result = await _build_srs(
            db,
            project_id,
            project_name=project.name,
            project_description=project.description or "",
        )
    return SrsOut(
        project_id=project_id,
        markdown=result["markdown"],
        generated_at=result["generated_at"],
        counts=result["counts"],
    )


# ---------------------------------------------------------------------------
# Sessions under a project
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/sessions",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    project_id: int,
    body: SessionCreate,
    user: User = Depends(get_current_user),
) -> SessionOut:
    """Crea una nueva sesión bajo un proyecto. Verifica ownership."""
    async with AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        if project is None or project.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")

        # Título default cuenta sesiones existentes para que sea "sesión N".
        if body.title:
            title = body.title
        else:
            count_q = await db.execute(
                select(ChatSession.id)
                .where(ChatSession.project_id == project_id)
            )
            n = len(count_q.all())
            title = f"{project.name} — sesión {n + 1}"

        session = ChatSession(
            project_id=project_id,
            title=title,
            phase=project.phase,
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)

    return SessionOut(
        id=session.id,
        project_id=session.project_id,
        title=session.title,
        phase=session.phase,
        created_at=session.created_at,
    )


@router.get("/{project_id}/sessions", response_model=list[SessionOut])
async def list_sessions(
    project_id: int,
    user: User = Depends(get_current_user),
) -> list[SessionOut]:
    """Lista las sesiones de un proyecto. Verifica ownership del proyecto."""
    async with AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        if project is None or project.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")

        rows = (
            await db.execute(
                select(ChatSession)
                .where(ChatSession.project_id == project_id)
                .order_by(ChatSession.created_at.asc())
            )
        ).scalars().all()

    return [
        SessionOut(
            id=s.id,
            project_id=s.project_id,
            title=s.title,
            phase=s.phase,
            created_at=s.created_at,
        )
        for s in rows
    ]


# ---------------------------------------------------------------------------
# Session detail with messages
# (Vive aquí, no en chat.py, porque routers/chat.py es solo streaming SSE y no
# se toca en este iter. Misma propiedad: 404 si la sesión no existe o no es
# propia vía session.project.user_id.)
# ---------------------------------------------------------------------------


def _content_matches(item_text: str, row_text: str) -> bool:
    """True si el contenido del item y el de la fila corresponden al mismo
    mensaje (iguales, o uno prefijo del otro: recapitulado por _cap_history_text
    o whitespace final)."""
    return (
        item_text == row_text
        or item_text.startswith(row_text)
        or row_text.startswith(item_text)
    )


def _restore_user_content(items: list[dict], user_rows) -> None:
    """Superpone el contenido original del usuario sobre los items 'user'
    reconstruidos desde el checkpointer.

    Estrategia, en orden:
    1. Ligadura exacta por ``chat_row_id`` (el relay inyecta el id de la fila
       en ``additional_kwargs`` del HumanMessage; ``reconstruct_history`` lo
       propaga). Camino autoritativo para turnos nuevos.
    2. Para datos previos a la ligadura: apareo greedy por contenido (igual o
       prefijo en cualquier dirección).
    3. Si el item es una directiva reescrita ("[DIRECTIVE] …") y la fila no es
       un comando de ruta directa, corresponden al mismo turno: ligar.
    4. Una fila sin match es un turno de ruta directa (sin HumanMessage):
       queda para ``_merge_direct_route_turns``, que la reinserta en la
       timeline.

    Limitación conocida del paso 3: asume que la única ruta directa es
    ``/agrupar`` puro. Si se agregan más rutas directas, los datos viejos
    pueden quedar mal apareados (los nuevos siempre van por chat_row_id).

    Devuelve el set de ids de filas user ligadas a un item.
    """
    bound_ids: set[int] = set()
    # 1) Ligadura exacta por chat_row_id.
    by_row_id = {
        it["chat_row_id"]: it
        for it in items
        if it.get("role") == "user" and it.get("chat_row_id") is not None
    }
    for row in user_rows:
        it = by_row_id.get(row.id)
        if it is not None:
            it["content"] = row.content
            bound_ids.add(row.id)

    # 2-3) Apareo greedy por contenido para items sin ligadura.
    pool = [
        it
        for it in items
        if it.get("role") == "user" and it.get("chat_row_id") is None
    ]
    pi = 0
    for row in user_rows:
        if row.id in bound_ids or pi >= len(pool):
            continue
        item = pool[pi]
        item_text = item.get("content") or ""
        if _content_matches(item_text, row.content) or (
            item_text.startswith("[DIRECTIVE]")
            and not row.content.lstrip().startswith("/agrupar")
        ):
            # Match por contenido, o directiva reescrita cuya fila es el texto
            # original de este turno. Se estampa el row_id para que el merge de
            # rutas directas pueda posicionar inserciones también en datos
            # viejos.
            item["content"] = row.content
            item["chat_row_id"] = row.id
            bound_ids.add(row.id)
            pi += 1
        # else: fila de ruta directa (o sin item) -> queda para el merge.
    return bound_ids


def _merge_direct_route_turns(items: list[dict], all_rows, bound_ids: set[int]) -> None:
    """Reinserta en la timeline los turnos que nunca entraron al grafo (rutas
    directas del router: /agrupar puro, gate de captura con respuesta
    inmediata). Cada turno directo = una fila user sin ligar + las filas
    assistant consecutivas hasta la próxima fila user.

    Posicionamiento: antes del item 'user' ligado a la siguiente fila user
    conocida (por row id); si no hay, al final. Insertar de atrás hacia
    adelante para no invalidar índices.
    """
    runs: list[tuple[Any, list[Any]]] = []
    i = 0
    while i < len(all_rows):
        r = all_rows[i]
        if r.role == "user" and r.id not in bound_ids:
            assistants = []
            j = i + 1
            while j < len(all_rows) and all_rows[j].role == "assistant":
                assistants.append(all_rows[j])
                j += 1
            runs.append((r, assistants))
            i = j
        else:
            i += 1
    if not runs:
        return

    # row_id del item 'user' ligado -> índice en items (para posicionar).
    row_id_to_idx: dict[int, int] = {}
    for idx, it in enumerate(items):
        rid = it.get("chat_row_id")
        if it.get("role") == "user" and rid is not None:
            row_id_to_idx[rid] = idx

    placements: list[tuple[int, list[dict]]] = []
    for user_row, assistants in runs:
        synthetic: list[dict] = [
            {
                "id": f"db-{user_row.id}",
                "role": "user",
                "content": user_row.content,
                "created_at": user_row.created_at,
            }
        ]
        synthetic += [
            {
                "id": f"db-{a.id}",
                "role": "assistant",
                "content": a.content,
                "created_at": a.created_at,
            }
            for a in assistants
        ]
        later = [idx for rid, idx in row_id_to_idx.items() if rid > user_row.id]
        pos = min(later) if later else len(items)
        placements.append((pos, synthetic))

    for pos, synthetic in sorted(placements, key=lambda p: -p[0]):
        items[pos:pos] = synthetic


@session_router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session_detail(
    session_id: int,
    request: Request,
    user: User = Depends(get_current_user),
) -> SessionDetail:
    """Detalle de una sesión: metadata + mensajes.

    Los mensajes se reconstruyen desde el checkpointer del agente (fuente
    autoritativa: incluye llamadas a herramientas y sus resultados, que el
    streaming SSE nunca persiste). Si el thread no existe (sesión sin turnos del
    agente) o la reconstrucción falla, cae a ``chat_messages`` como fallback.

    Verifica ownership cruzando session -> project -> user_id. Mismo 404 si no
    existe o no es propia, sin filtrar existencia.
    """
    async with AsyncSessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        # Ownership via session -> project -> user_id. Los modelos no declaran
        # relationship todavía, así que cargamos Project a mano (1 query extra
        # barata por PK).
        project = await db.get(Project, session.project_id)
        if project is None or project.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")

    # Historial autoritativo desde el checkpointer (tools + resultados).
    # app.state.checkpointer lo crea el lifespan (mismo objeto que usa el relay
    # de chat). Si por algún motivo no existe, cae directo al fallback.
    checkpointer = getattr(request.app.state, "checkpointer", None)
    items: list[dict] = []
    if checkpointer is not None:
        try:
            items = await reconstruct_history(checkpointer, session_id)
        except Exception:
            logger.exception(
                "reconstruct_history falló para sesión %s; fallback a chat_messages",
                session_id,
            )

    # Filas persistidas del chat (user + assistant), una sola query.
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
        ).scalars().all()

    # Foto de sesión: si hay filas con kind, la línea de tiempo fue persistida
    # por el relay mientras streameaba -> redespliegue directo (1:1 con lo que
    # el usuario vio en vivo, sin reconstrucción). Filas NULL-kind = sesión
    # legacy -> ruta de reconstrucción desde el checkpointer.
    if any(r.kind for r in rows):
        return SessionDetail(
            id=session.id,
            project_id=session.project_id,
            title=session.title,
            phase=session.phase,
            created_at=session.created_at,
            messages=[
                MessageOut(
                    id=m.id,
                    role=m.role,
                    content=m.content,
                    created_at=m.created_at,
                    tool_name=m.tool_name,
                    tool_args=m.tool_args,
                    is_intermediate=m.is_intermediate,
                )
                for m in rows
            ],
        )

    if items:
        # Restaurar el contenido original del usuario: el checkpointer guarda
        # el mensaje reescrito por _rewrite_command (directiva), no el texto
        # original (e.g. /captura). El match NO puede ser posicional puro: los
        # turnos de ruta directa (p.ej. /agrupar puro, gate de captura)
        # persisten fila pero nunca crean HumanMessage. Estrategia y merge de
        # turnos directos: ver _restore_user_content / _merge_direct_route_turns.
        user_rows = [r for r in rows if r.role == "user"]
        bound_ids = _restore_user_content(items, user_rows)
        _merge_direct_route_turns(items, rows, bound_ids)

    # Fallback: thread inexistente (sesión nueva sin turnos del agente) o
    # fallo de reconstrucción. Conserva compatibilidad con sesiones que solo
    # tienen prompts de usuario persistidos.
    if not items:
        items = [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at,
            }
            for m in rows
        ]

    return SessionDetail(
        id=session.id,
        project_id=session.project_id,
        title=session.title,
        phase=session.phase,
        created_at=session.created_at,
        messages=[
            MessageOut(
                id=it["id"],
                role=it["role"],
                content=it.get("content") or "",
                created_at=it.get("created_at") or session.created_at,
                tool_name=it.get("tool_name"),
                tool_call_id=it.get("tool_call_id"),
                tool_args=it.get("tool_args"),
                status=it.get("status"),
            )
            for it in items
        ],
    )
