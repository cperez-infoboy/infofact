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

    # Restaurar el contenido original del usuario: el checkpointer guarda el
    # mensaje reescrito por _rewrite_command (directiva), no el texto original
    # (e.g. /captura). Superponemos el contenido de ChatMessage posicionalmente:
    # cada HumanMessage en el checkpointer viene de un POST que persistió en
    # ChatMessage antes del stream, así que el match posicional es 1:1.
    if items:
        async with AsyncSessionLocal() as db:
            user_rows = (
                await db.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.role == "user",
                    )
                    .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
                )
            ).scalars().all()
        user_idx = 0
        for item in items:
            if item.get("role") == "user" and user_idx < len(user_rows):
                item["content"] = user_rows[user_idx].content
                user_idx += 1

    # Fallback: thread inexistente (sesión nueva sin turnos del agente) o
    # fallo de reconstrucción. Conserva compatibilidad con sesiones que solo
    # tienen prompts de usuario persistidos.
    if not items:
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
                )
            ).scalars().all()
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
