"""FastAPI entrypoint.

El backend NO conoce lógica del agente: wirea DeepAgents al DockerSandbox del
usuario y relayea su stream. Reemplazar el agente solo toca agent_service.py.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.database import engine
from backend.models import Base
from backend.routers import auth, chat, projects, workspaces
from backend.services.agent_service import build_checkpointer, close_checkpointer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Migra el esquema existente (idempotente) y luego crea tablas nuevas.
    # `migrate_projects_slug` agrega la columna `slug` a `projects` si falta y
    # la backfilla desde `name` usando slugify + sufijos -2/-3 para colisiones.
    await migrate_projects_slug()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Inicializa el checkpointer AsyncSqliteSaver (único para toda la app).
    # setup() crea las tablas que necesita; es idempotente.
    saver = await build_checkpointer()
    app.state.checkpointer = saver

    yield

    await close_checkpointer(saver)
    await engine.dispose()


async def migrate_projects_slug() -> None:
    """Agrega `projects.slug` + unique index y backfilldea desde `name`.

    Idempotente: si la columna ya existe, no hace nada. Corre antes de
    `Base.metadata.create_all` para que esta última encuentre el esquema
    actualizado y no intente recrear la tabla (SQLite no soporta ALTER para
    agregar constraints a una tabla existente; el unique index se crea acá
    mismo tras el backfill).
    """
    from sqlalchemy import inspect, text

    from backend.services.slugify import slugify

    log = logging.getLogger(__name__)

    # Usamos el async engine directamente: ALTER + UPDATE en una transacción.
    # El inspector y los executes van por `run_sync` / `await conn.execute`
    # porque el engine es async (aiosqlite). Usar `engine.sync_engine` directo
    # rompe con MissingGreenlet fuera del loop de SQLAlchemy.
    async with engine.begin() as conn:
        def _columns(sync_conn):
            inspector = inspect(sync_conn)
            if "projects" not in inspector.get_table_names():
                return None
            return {c["name"] for c in inspector.get_columns("projects")}

        columns = await conn.run_sync(_columns)
        if columns is None:
            # La tabla todavía no existe (primer boot). create_all la crea con
            # la columna incluida; nada que migrar.
            log.info("migrate_projects_slug: tabla projects no existe, skipping")
            return

        if "slug" in columns:
            log.info("migrate_projects_slug: columna slug ya presente, skipping")
            return

        log.info("migrate_projects_slug: agregando columna slug a projects")
        await conn.execute(text("ALTER TABLE projects ADD COLUMN slug VARCHAR(48)"))

        # Backfill: para cada proyecto, slug = slugify(name); si choca con otro
        # slug del mismo usuario (computado en esta misma pasada), prueba
        # {base}-2, -3, ... Cargamos los rows ordenados por id para que el
        # primer proyecto con el nombre se quede con el slug base.
        rows = (
            await conn.execute(
                text("SELECT id, user_id, name FROM projects ORDER BY id ASC")
            )
        ).all()

        # Mapa user_id -> set de slugs ya asignados en esta pasada.
        used_per_user: dict[int, set[str]] = {}
        for row in rows:
            user_id = row.user_id
            base = slugify(row.name)
            used = used_per_user.setdefault(user_id, set())
            if base not in used:
                slug = base
            else:
                n = 2
                while f"{base}-{n}" in used:
                    n += 1
                slug = f"{base}-{n}"
            used.add(slug)
            await conn.execute(
                text("UPDATE projects SET slug = :slug WHERE id = :id"),
                {"slug": slug, "id": row.id},
            )

        # Unique index (user_id, slug). Si por algún motivo ya hay duplicados
        # residuales (no debería tras el backfill), falló la creación del
        # index: lo logueamos pero no abortamos el boot — la app sigue
        # funcionando sin el constraint, solo pierde la protección a nivel DB.
        try:
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_user_slug "
                    "ON projects (user_id, slug)"
                )
            )
            log.info(
                "migrate_projects_slug: backfill + unique index OK (%d rows)",
                len(rows),
            )
        except Exception:  # noqa: BLE001 - fallar el index no debe romper el boot
            log.exception(
                "migrate_projects_slug: no se pudo crear el unique index "
                "uq_projects_user_slug; revisar duplicados residuales"
            )


app = FastAPI(title="InfoFact", lifespan=lifespan)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
# CRUD proyectos + sesiones (incluye POST sessions bajo /api/projects/{id}/sessions).
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
# Detalle de sesión (/api/chat/sessions/{id}) Registrado aparte con prefix
# /api/chat porque routers/chat.py es solo streaming SSE y no se toca.
app.include_router(projects.session_router, prefix="/api/chat", tags=["sessions"])
# Explorar / editar archivos del workspace del usuario.
# Requerimientos + planes de agrupamiento: UI y agente comparten la misma DB
# (decision por grupo + status por plan = guarda de idempotencia compartida).
from backend.routers import requirements as requirements_router
app.include_router(requirements_router.router, prefix="/api", tags=["requirements"])
# Explorar / editar archivos del workspace del usuario.
app.include_router(workspaces.router, prefix="/api/workspaces", tags=["workspaces"])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# SPA estática: serve el bundle de SvelteKit (adapter-static) mismo origen.
# build/ se copia a /app/static en el Dockerfile. Si no existe (dev sin build),
# estas rutas no se montan — el dev server de vite (:5173) hace de frontend y
# proxyea /api a este backend.
# NOTA: parent.parent porque main.py está en /app/backend/, no /app/.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

if _STATIC_DIR.is_dir():
    # Assets con hash (/_app/immutable/...) se sirven directo.
    _app_assets = _STATIC_DIR / "_app"
    if _app_assets.is_dir():
        app.mount("/_app", StaticFiles(directory=_app_assets), name="app-assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> FileResponse:
        """Catch-all SPA: sirve archivo estático si existe, si no index.html
        para que el router client-side de SvelteKit maneje la ruta."""
        candidate = _STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")
