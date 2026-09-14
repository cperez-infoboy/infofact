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


async def migrate_requirement_document_ids() -> None:
    """Rebasea los ``source["document_id"]`` legacy (path del host) a la
    convencion del contenedor ``/workspaces/{slug}/{rel}``.

    Las filas escritas antes del rebase en ``_source_from_raw`` guardan el
    path absoluto del host; el agente los veia como su workspace y salia a
    explorarlos con el shell. Idempotente: solo hace UPDATE de las filas que
    cambian, asi que en el segundo arranque es un no-op (SELECT ~600 filas).
    """
    import json
    import logging as _logging

    from sqlalchemy import text

    from backend.services.doc_id import rebase_document_id

    log = _logging.getLogger(__name__)
    changed = 0
    async with engine.begin() as conn:
        rows = (
            await conn.execute(text("SELECT id, source FROM requirement_items"))
        ).all()
        for row_id, source in rows:
            if not source:
                continue
            try:
                data = json.loads(source)
            except (TypeError, ValueError):
                continue
            entries = data if isinstance(data, list) else [data]
            touched = False
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                doc_id = entry.get("document_id")
                if isinstance(doc_id, str) and doc_id:
                    new_id = rebase_document_id(doc_id)
                    if new_id != doc_id:
                        entry["document_id"] = new_id
                        touched = True
            if touched:
                await conn.execute(
                    text(
                        "UPDATE requirement_items SET source = :src WHERE id = :rid"
                    ),
                    {"src": json.dumps(data, ensure_ascii=False), "rid": row_id},
                )
                changed += 1
    if changed:
        log.info(
            "migrate_requirement_document_ids: %d fila(s) rebasada(s) a path "
            "de contenedor",
            changed,
        )


async def reconcile_document_catalog() -> None:
    """Reconcilia el catálogo ``project_documents`` con las fuentes citadas.

    La ingesta del agente procesa documentos descubiertos por filesystem;
    antes del auto-registro (``ensure_document_registered``), los archivos que
    llegaron al workspace fuera de /upload y /scan sostenían requerimientos
    sin fila en el catálogo: invisibles para el conteo de fuentes y para el
    RAG del SRS (cuyo join pasa por el catálogo). Esta migración data-driven
    repara ese estado: los ``source[].document_id`` citados por requerimientos
    obtienen fila (con ``sha256`` del archivo si aún existe en el workspace
    host, o sin sha si desapareció), ``used_in_capture=1`` y ``parse_status``
    completado desde ``document_parses`` cuando hay parse de su contenido.
    También corrige el ``rel_path`` de filas existentes cuyo archivo vive en
    otra carpeta (p. ej. registrado como filename plano y citado bajo
    ``Docs_Entrada/``).

    Idempotente y barata en estado estable: solo hashea archivos citados sin
    fila que los represente; en el segundo arranque es SELECT + comparación.
    Corre tras create_all (data migration, post-rebase de document_ids).
    """
    import hashlib
    import json
    import logging as _logging
    import mimetypes
    import os as _os
    from pathlib import PurePosixPath
    from types import SimpleNamespace

    from sqlalchemy import text

    from backend.config import settings

    log = _logging.getLogger(__name__)

    def _sha256_of(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):  # chunks de 1 MiB
                h.update(chunk)
        return h.hexdigest()

    created = fixed = 0
    async with engine.begin() as conn:
        proj_rows = (
            await conn.execute(
                text(
                    "SELECT p.id, p.slug, u.profile FROM projects p "
                    "JOIN users u ON u.id = p.user_id"
                )
            )
        ).all()
        cited_projects = {
            r.project_id
            for r in (
                await conn.execute(
                    text("SELECT DISTINCT project_id FROM requirement_items")
                )
            ).all()
        }
        proj_meta = {
            pid: (slug, profile)
            for pid, slug, profile in proj_rows
            if pid in cited_projects
        }
        if not proj_meta:
            return

        # Cache global de parses keyed por sha256 (tabla acotada: un parse por
        # contenido distinto visto por el sistema).
        parses = {
            r.sha256: r
            for r in (
                await conn.execute(
                    text(
                        "SELECT sha256, parser_used, page_count, created_at "
                        "FROM document_parses"
                    )
                )
            ).all()
        }

        req_rows = (
            await conn.execute(
                text(
                    "SELECT project_id, source FROM requirement_items "
                    "WHERE source IS NOT NULL"
                )
            )
        ).all()
        cited: dict[int, set[str]] = {}
        for row in req_rows:
            try:
                data = json.loads(row.source)
            except (TypeError, ValueError):
                continue
            entries = data if isinstance(data, list) else [data]
            bucket = cited.setdefault(row.project_id, set())
            for entry in entries:
                if isinstance(entry, dict):
                    doc_id = entry.get("document_id")
                    if isinstance(doc_id, str) and doc_id:
                        bucket.add(doc_id)

        for pid, doc_ids in cited.items():
            meta = proj_meta.get(pid)
            if meta is None:
                continue
            slug, profile = meta
            host_root = (settings.workspaces_root / profile / slug).resolve()

            existing = (
                await conn.execute(
                    text(
                        "SELECT id, rel_path, sha256, used_in_capture, "
                        "parse_status, parser_used, page_count "
                        "FROM project_documents WHERE project_id = :pid"
                    ),
                    {"pid": pid},
                )
            ).all()
            by_rel = {r.rel_path: r for r in existing}
            by_sha = {r.sha256: r for r in existing if r.sha256}

            prefix = f"/workspaces/{slug}/"
            for doc_id in sorted(doc_ids):
                rel = (
                    doc_id.split(prefix, 1)[1]
                    if prefix in doc_id
                    else doc_id.lstrip("/")
                )
                if not rel or rel in (".", ".."):
                    continue
                host_path = host_root / rel
                size = 0
                synth = hashlib.sha256(f"missing:{rel}".encode()).hexdigest()
                if host_path.is_file():
                    try:
                        sha = _sha256_of(str(host_path))
                        size = host_path.stat().st_size
                    except OSError as exc:
                        log.warning(
                            "reconcile_document_catalog: fallo hashing %s: %s",
                            host_path,
                            exc,
                        )
                        sha = synth
                else:
                    # Archivo desaparecido: la columna sha256 es NOT NULL, así
                    # que se usa un sha sintético keyed por ruta — nunca
                    # colisiona con un contenido real y no joinea con
                    # document_parses (correcto: no hay parse del archivo).
                    sha = synth

                row = by_sha.get(sha) if sha else None
                if row is None:
                    row = by_rel.get(rel)
                parse = parses.get(sha) if sha else None
                name = PurePosixPath(rel).name
                ext = _os.path.splitext(name)[1].lower()

                if row is None:
                    await conn.execute(
                        text(
                            "INSERT INTO project_documents "
                            "(project_id, rel_path, filename, extension, mime, "
                            "size_bytes, sha256, page_count, parse_status, "
                            "parser_used, parser_hint, parsed_at, "
                            "used_in_capture) "
                            "VALUES (:pid, :rel, :name, :ext, :mime, :size, "
                            ":sha, :pages, :status, :parser, 'auto', "
                            ":parsed_at, 1)"
                        ),
                        {
                            "pid": pid,
                            "rel": rel,
                            "name": name,
                            "ext": ext,
                            "mime": mimetypes.guess_type(name)[0],
                            "size": size,
                            "sha": sha,
                            "pages": (
                                parse.page_count
                                if parse and parse.page_count
                                else None
                            ),
                            "status": "ready" if parse else "pending",
                            "parser": parse.parser_used if parse else None,
                            "parsed_at": parse.created_at if parse else None,
                        },
                    )
                    created += 1
                    # Dedupe intra-pasada: citados duplicados no reinsertan.
                    row = SimpleNamespace(
                        rel_path=rel,
                        sha256=sha,
                        used_in_capture=1,
                        parse_status="ready" if parse else "pending",
                        parser_used=parse.parser_used if parse else None,
                        page_count=(
                            parse.page_count if parse and parse.page_count else None
                        ),
                    )
                    by_rel[rel] = row
                    if sha:
                        by_sha[sha] = row
                    continue

                updates: dict[str, object] = {}
                if row.rel_path != rel:
                    updates["rel_path"] = rel
                if not row.used_in_capture:
                    updates["used_in_capture"] = 1
                if row.sha256 == synth and sha != synth:
                    # La fila tenía el sha sintético de "archivo desaparecido"
                    # y el archivo reapareció: refrescar al sha real.
                    updates["sha256"] = sha
                if parse is not None:
                    if row.parse_status != "ready":
                        updates["parse_status"] = "ready"
                    if row.parser_used != parse.parser_used:
                        updates["parser_used"] = parse.parser_used
                    if parse.page_count and not row.page_count:
                        updates["page_count"] = parse.page_count
                    updates["parsed_at"] = parse.created_at
                if updates:
                    sets = ", ".join(f"{k} = :{k}" for k in updates)
                    params = dict(updates)
                    params["id"] = row.id
                    await conn.execute(
                        text(
                            f"UPDATE project_documents SET {sets} WHERE id = :id"  # noqa: S608
                        ),
                        params,
                    )
                    fixed += 1
                if sha:
                    by_sha[sha] = row
                by_rel[rel] = row

    if created or fixed:
        log.info(
            "reconcile_document_catalog: %d fila(s) creada(s), %d corregida(s)",
            created,
            fixed,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Migra el esquema existente (idempotente) y luego crea tablas nuevas.
    # `migrate_projects_slug` agrega la columna `slug` a `projects` si falta y
    # la backfilla desde `name` usando slugify + sufijos -2/-3 para colisiones.
    await migrate_projects_slug()
    await migrate_project_documents_parser_hint()
    await migrate_requirement_explicit_priority()
    await migrate_project_documents_used_in_capture()
    await migrate_analysis_diagram_descriptions()
    await migrate_analysis_architecture_diagrams()
    await migrate_subproject_project_code()
    await migrate_chat_messages_timeline()
    await migrate_srs_quality_curation()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Data migration (post-create_all: la tabla ya existe aunque sea un boot
    # nuevo). Idempotente: en el segundo arranque es un no-op.
    await migrate_requirement_document_ids()
    # Repara el catálogo de documentos contra las fuentes citadas por los
    # requerimientos (filas faltantes, flags y rel_path). Depende del rebase
    # de document_ids de arriba (paths en convención de contenedor).
    await reconcile_document_catalog()

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


async def migrate_project_documents_parser_hint() -> None:
    """Agrega ``project_documents.parser_hint`` si falta (default ``'auto'``).

    Idempotente: si la columna ya existe, no hace nada. Corre antes de
    ``create_all`` para que este encuentre el esquema actualizado. SQLite
    backfilldea las filas existentes con el DEFAULT de la columna.
    """
    from sqlalchemy import inspect, text

    log = logging.getLogger(__name__)

    async with engine.begin() as conn:
        def _columns(sync_conn):
            inspector = inspect(sync_conn)
            if "project_documents" not in inspector.get_table_names():
                return None
            return {c["name"] for c in inspector.get_columns("project_documents")}

        columns = await conn.run_sync(_columns)
        if columns is None:
            return
        if "parser_hint" in columns:
            log.info(
                "migrate_project_documents_parser_hint: columna ya presente, skipping"
            )
            return
        log.info(
            "migrate_project_documents_parser_hint: agregando columna parser_hint"
        )
        await conn.execute(
            text(
                "ALTER TABLE project_documents "
                "ADD COLUMN parser_hint VARCHAR(16) DEFAULT 'auto'"
            )
        )


async def migrate_chat_messages_timeline() -> None:
    """Add the photo-of-session columns to ``chat_messages`` if missing.

    ``kind`` ('text' | 'tool'), ``tool_name``, ``tool_args`` and
    ``is_intermediate`` turn the table into the persisted display timeline
    the relay writes while streaming (docs/planeaciones/2026-08-19). Rows
    left NULL are legacy (pre-migration) and keep being served through the
    checkpointer reconstruction path.

    Idempotent: no-op when the columns exist. Runs before ``create_all``.
    """
    from sqlalchemy import inspect, text

    log = logging.getLogger(__name__)

    async with engine.begin() as conn:
        def _columns(sync_conn):
            inspector = inspect(sync_conn)
            if "chat_messages" not in inspector.get_table_names():
                return None
            return {c["name"] for c in inspector.get_columns("chat_messages")}

        columns = await conn.run_sync(_columns)
        if columns is None or "kind" in columns:
            if columns is not None:
                log.info(
                    "migrate_chat_messages_timeline: columns already present, skipping"
                )
            return
        log.info("migrate_chat_messages_timeline: adding timeline columns")
        await conn.execute(
            text(
                "ALTER TABLE chat_messages ADD COLUMN kind VARCHAR(8)"
            )
        )
        await conn.execute(
            text("ALTER TABLE chat_messages ADD COLUMN tool_name VARCHAR(128)")
        )
        await conn.execute(
            text("ALTER TABLE chat_messages ADD COLUMN tool_args JSON")
        )
        await conn.execute(
            text(
                "ALTER TABLE chat_messages ADD COLUMN is_intermediate BOOLEAN"
            )
        )


async def migrate_requirement_explicit_priority() -> None:
    """Add ``requirement_items.explicit_priority`` if missing (default false).

    Idempotent: no-op when the column exists. Runs before ``create_all``.
    Existing rows backfill to false — their priority origin (explicit vs
    verb-inferred) was not tracked historically, so they are treated as
    inferred until a fresh capture repopulates the flag.
    """
    from sqlalchemy import inspect, text

    log = logging.getLogger(__name__)

    async with engine.begin() as conn:
        def _columns(sync_conn):
            inspector = inspect(sync_conn)
            if "requirement_items" not in inspector.get_table_names():
                return None
            return {c["name"] for c in inspector.get_columns("requirement_items")}

        columns = await conn.run_sync(_columns)
        if columns is None:
            return
        if "explicit_priority" in columns:
            log.info(
                "migrate_requirement_explicit_priority: column already present, skipping"
            )
            return
        log.info(
            "migrate_requirement_explicit_priority: adding column explicit_priority"
        )
        await conn.execute(
            text(
                "ALTER TABLE requirement_items "
                "ADD COLUMN explicit_priority BOOLEAN DEFAULT 0"
            )
        )


async def migrate_project_documents_used_in_capture() -> None:
    """Add ``project_documents.used_in_capture`` if missing (default false).

    Idempotent: no-op when the column exists. Runs before ``create_all``.
    Existing rows backfill to false — only fresh captures mark documents.
    """
    from sqlalchemy import inspect, text

    log = logging.getLogger(__name__)

    async with engine.begin() as conn:
        def _columns(sync_conn):
            inspector = inspect(sync_conn)
            if "project_documents" not in inspector.get_table_names():
                return None
            return {c["name"] for c in inspector.get_columns("project_documents")}

        columns = await conn.run_sync(_columns)
        if columns is None:
            return
        if "used_in_capture" in columns:
            log.info(
                "migrate_project_documents_used_in_capture: column already present, skipping"
            )
            return
        log.info(
            "migrate_project_documents_used_in_capture: adding column used_in_capture"
        )
        await conn.execute(
            text(
                "ALTER TABLE project_documents "
                "ADD COLUMN used_in_capture BOOLEAN DEFAULT 0"
            )
        )


async def migrate_analysis_diagram_descriptions() -> None:
    """Add ``mer_diagram_description`` and ``component_diagram_description``
    to ``analysis_documents`` if missing.

    Idempotent: no-op if columns already exist. Runs before ``create_all``.
    """
    from sqlalchemy import inspect, text

    log = logging.getLogger(__name__)

    async with engine.begin() as conn:
        def _columns(sync_conn):
            inspector = inspect(sync_conn)
            if "analysis_documents" not in inspector.get_table_names():
                return None
            return {c["name"] for c in inspector.get_columns("analysis_documents")}

        columns = await conn.run_sync(_columns)
        if columns is None:
            return

        for col in ("mer_diagram_description", "component_diagram_description"):
            if col not in columns:
                log.info("migrate_analysis_diagram_descriptions: adding %s", col)
                await conn.execute(
                    text(
                        f"ALTER TABLE analysis_documents "
                        f"ADD COLUMN {col} TEXT DEFAULT ''"
                    )
                )


async def migrate_analysis_architecture_diagrams() -> None:
    """Add system architecture + infrastructure columns to analysis_documents.

    Idempotent: no-op if columns already exist. Runs before create_all.
    """
    from sqlalchemy import inspect, text

    log = logging.getLogger(__name__)

    async with engine.begin() as conn:
        def _columns(sync_conn):
            inspector = inspect(sync_conn)
            if "analysis_documents" not in inspector.get_table_names():
                return None
            return {
                c["name"]
                for c in inspector.get_columns("analysis_documents")
            }

        columns = await conn.run_sync(_columns)
        if columns is None:
            return

        for col in (
            "system_architecture_diagram",
            "system_architecture_description",
            "infrastructure_diagram",
            "infrastructure_description",
        ):
            if col not in columns:
                log.info(
                    "migrate_analysis_architecture_diagrams: adding %s", col
                )
                await conn.execute(
                    text(
                        f"ALTER TABLE analysis_documents "
                        f"ADD COLUMN {col} TEXT DEFAULT ''"
                    )
                )


async def migrate_subproject_project_code() -> None:
    """Add ``sub_projects.project_code`` if missing (nullable, for project areas).

    Idempotent: no-op when the column exists. Runs before create_all.
    """
    from sqlalchemy import inspect, text

    log = logging.getLogger(__name__)

    async with engine.begin() as conn:
        def _columns(sync_conn):
            inspector = inspect(sync_conn)
            if "sub_projects" not in inspector.get_table_names():
                return None
            return {
                c["name"]
                for c in inspector.get_columns("sub_projects")
            }

        columns = await conn.run_sync(_columns)
        if columns is None:
            return
        if "project_code" in columns:
            log.info(
                "migrate_subproject_project_code: column already present, skipping"
            )
            return
        log.info(
            "migrate_subproject_project_code: adding column project_code"
        )
        await conn.execute(
            text(
                "ALTER TABLE sub_projects "
                "ADD COLUMN project_code VARCHAR(32)"
            )
        )


async def migrate_srs_quality_curation() -> None:
    """Agrega las columnas de curación con memoria del pipeline de SRS.

    - ``requirement_items.quality_fingerprint`` + ``quality_judged_at``:
      habilitan el análisis de calidad delta (el juez LLM solo re-evalúa
      ítems nuevos o editados; el resto conserva su veredicto persistido).
    - ``requirement_findings.req_fingerprint`` + ``resolved_at/by/note``:
      el merge de hallazgos preserva el estado de curación (fixed/waived)
      cuando el enunciado no cambió, en vez de borrarlo en cada commit.

    Idempotente: no-op cuando las columnas existen. Corre antes de create_all.
    """
    from sqlalchemy import inspect, text

    log = logging.getLogger(__name__)

    wanted: dict[str, list[tuple[str, str]]] = {
        "requirement_items": [
            ("quality_fingerprint", "VARCHAR(64)"),
            ("quality_judged_at", "DATETIME"),
            ("goals_fingerprint", "VARCHAR(64)"),
            ("goals_judged_at", "DATETIME"),
        ],
        "requirement_findings": [
            ("req_fingerprint", "VARCHAR(64)"),
            ("resolved_at", "DATETIME"),
            ("resolved_by", "VARCHAR(16)"),
            ("resolution_note", "TEXT"),
        ],
    }

    async with engine.begin() as conn:
        def _columns(sync_conn, table: str):
            inspector = inspect(sync_conn)
            if table not in inspector.get_table_names():
                return None
            return {c["name"] for c in inspector.get_columns(table)}

        for table, cols in wanted.items():
            existing = await conn.run_sync(
                lambda sync_conn, t=table: _columns(sync_conn, t)
            )
            if existing is None:
                continue
            for col, ddl in cols:
                if col in existing:
                    continue
                log.info(
                    "migrate_srs_quality_curation: adding %s.%s", table, col
                )
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
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
from backend.routers import srs as srs_router
app.include_router(srs_router.router, prefix="/api", tags=["srs"])
from backend.routers import analysis as analysis_router
app.include_router(analysis_router.router, prefix="/api", tags=["analysis"])

from backend.routers import packages as packages_router
app.include_router(packages_router.router, prefix="/api", tags=["packages"])
# Harness de reglas persistentes del proyecto (captura / análisis / SRS).
from backend.routers import project_rules as project_rules_router
app.include_router(project_rules_router.router, prefix="/api", tags=["project-rules"])
# Explorar / editar archivos del workspace del usuario.
app.include_router(workspaces.router, prefix="/api/workspaces", tags=["workspaces"])
# Documentos fuente del proyecto: upload binario + scan + CRUD (Fase A ingesta).
from backend.routers import documents as documents_router
app.include_router(documents_router.router, prefix="/api/documents", tags=["documents"])


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
