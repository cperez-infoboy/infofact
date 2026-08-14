"""Repair one-off para threads envenenados por mensajes gigantes (sesion 44).

Incidente: un mensaje assistant de 1.1 MB (el JSON crudo del anotador
``enrich_structure_map``) quedó persistido en el estado del thread del
checkpointer y en su espejo de ``chat_messages``. Cada turno re-enviaba la
historia completa como input del LLM → stall del proveedor → timeout → ciclos
de reintento de ~7 min → sesión inutilizable. Las guardas de tamaño (tag
``nostream`` en la fuente + SizeGuardMiddleware) cortan el origen para capturas
futuras; este script repara los threads YA envenenados.

Qué hace, por mensaje con contenido de texto > ``--threshold`` chars:

  1. Dump forense del original en
     ``{workspaces_root}/{profile}/{slug}/.infofact/debug/session-{id}/``
     (``.infofact/`` está excluido del discovery del agente). ``profile`` y
     ``slug`` salen de la DB (users/projects vía chat_sessions.project_id),
     el path host de settings.workspaces_root.
  2. Reemplazo en el checkpointer vía ``aupdate_state`` con el MISMO ``id``
     (sin id, ``add_messages`` appendearía una copia nueva en vez de
     reemplazar), mismo tipo, ``tool_calls`` intactos y ``content`` = head
     4.000 + marcador + tail 1.000. Prueba ``as_node=None``; si LangGraph no
     infiere el nodo escritor, reintenta con ``as_node="model"``.
  3. UPDATE del espejo oversize en ``chat_messages`` (sqlite3 directo).
  4. Verificación posterior con ``aget_state``: reporta el máximo restante.

Los checkpoints viejos NO se borran (las lecturas usan el último) ni se hace
VACUUM: el estado vigente queda limpio y el histórico intacto.

Uso (desde la raíz del repo, con el backend DETENIDO):

    .venv/bin/python scripts/repair_poisoned_thread.py --session 44 --dry-run
    .venv/bin/python scripts/repair_poisoned_thread.py --session 44
    .venv/bin/python scripts/repair_poisoned_thread.py --scan-all
    .venv/bin/python scripts/repair_poisoned_thread.py --session 44 --threshold 10000

PERMISOS: en deploy con docker, ``data/`` (ambas DBs y workspaces) suele ser
root-owned (el backend corre como root dentro del container). El dump forense
y el UPDATE espejo requieren sudo o backend detenido con permisos suficientes.
El dump es PREVIO a cualquier modificación: si no se puede escribir, el script
aborta sin tocar el thread. ``LLM_API_KEY`` debe estar en ``.env`` porque
``_get_read_graph`` construye el modelo (aunque este script nunca lo invoca).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

# Importable al correr desde la raíz del repo (mismo bootstrap que los smokes).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import BaseMessage

from backend.config import settings
from backend.services.agent_service import (
    _get_read_graph,
    build_checkpointer,
    close_checkpointer,
)

# Root del repo (padre de backend/): ancla paths de datos RELATIVOS igual que
# config.workspaces_root (el CWD del proceso no es confiable).
REPO_ROOT = Path(__file__).resolve().parents[1]

# Un mensaje con más de esto se considera veneno (los reports legítimos del
# agente observados son de 1-5K chars).
DEFAULT_THRESHOLD = 10_000
# Truncado de reparación: cabecera + marcador + cola.
REPAIR_HEAD_CHARS = 4_000
REPAIR_TAIL_CHARS = 1_000
REPAIR_MARKER_TEMPLATE = (
    "[contenido truncado por reparacion: se omitieron {omitted} caracteres "
    "(original {original})]"
)


# ---------------------------------------------------------------------------
# Funciones puras (testeables sin DB ni checkpointer)
# ---------------------------------------------------------------------------

def truncate_repair_content(
    text: str,
    *,
    head: int = REPAIR_HEAD_CHARS,
    tail: int = REPAIR_TAIL_CHARS,
) -> str:
    """Función pura: head + marcador + tail. ``text`` si no hace falta."""
    if len(text) <= head + tail:
        return text
    marker = REPAIR_MARKER_TEMPLATE.format(
        omitted=len(text) - head - tail, original=len(text)
    )
    return f"{text[:head]}\n{marker}\n{text[-tail:]}"


def build_replacement(
    message: BaseMessage, *, threshold: int = DEFAULT_THRESHOLD
) -> BaseMessage | None:
    """Copia reparada del mensaje, o None si no necesita reparación.

    - Contenido no-texto (bloques de imagen) o <= ``threshold`` -> None
      (intocado).
    - Mensaje oversize SIN id -> ValueError: ``add_messages`` appendearía una
      copia nueva en vez de reemplazar (duplicaría el veneno). El caller lo
      reporta como warning y lo saltea.
    - Si no: ``model_copy(update={"content": ...})`` — mismo ``id``, mismo
      tipo, ``tool_calls`` / ``tool_call_id`` / ``name`` intactos.
    """
    content = message.content
    if not isinstance(content, str) or len(content) <= threshold:
        return None
    if message.id is None:
        raise ValueError(
            "mensaje oversize sin id: aupdate_state lo appendearía en vez de "
            "reemplazar (se saltea)"
        )
    return message.model_copy(update={"content": truncate_repair_content(content)})


# ---------------------------------------------------------------------------
# Helpers de selección / reporte
# ---------------------------------------------------------------------------

def _text_len(message: BaseMessage) -> int:
    content = message.content
    return len(content) if isinstance(content, str) else 0


def _select_oversize(
    messages: list[BaseMessage], threshold: int
) -> list[BaseMessage]:
    return [
        m for m in messages if isinstance(m.content, str) and len(m.content) > threshold
    ]


def _describe(message: BaseMessage) -> str:
    return f"#{message.id} ({type(message).__name__}, {_text_len(message)} chars)"


# ---------------------------------------------------------------------------
# DB espejo (sqlite3 directo, sin SQLAlchemy)
# ---------------------------------------------------------------------------

def _sqlite_path(url: str) -> Path:
    """``sqlite+aiosqlite:///./data/x.db`` -> Path absoluta anclada al repo."""
    raw = url.split(":///", 1)[-1] if ":///" in url else url
    p = Path(raw)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p


def _session_owner(session_id: int) -> tuple[str, str]:
    """(profile, slug) del proyecto dueño de la sesión, desde la DB de la app."""
    db = _sqlite_path(settings.database_url)
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise SystemExit(f"[!] No se pudo abrir {db} en modo lectura: {exc}") from exc
    try:
        row = con.execute(
            "SELECT u.profile, p.slug FROM chat_sessions s "
            "JOIN projects p ON p.id = s.project_id "
            "JOIN users u ON u.id = p.user_id WHERE s.id = ?",
            (session_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise SystemExit(f"[!] La sesión {session_id} no existe en {db}")
    return row[0], row[1]


def dump_forensics(session_id: int, oversize: list[BaseMessage]) -> Path:
    """Dump del original ANTES de tocar el thread. Aborta si no se puede escribir."""
    profile, slug = _session_owner(session_id)
    out_dir = (
        settings.workspaces_root
        / profile
        / slug
        / ".infofact"
        / "debug"
        / f"session-{session_id}"
    )
    manifest: list[dict[str, Any]] = []
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, message in enumerate(oversize):
            fname = f"msg-{i:02d}-{str(message.id).replace('/', '_')}.txt"
            (out_dir / fname).write_text(message.content, encoding="utf-8")
            manifest.append(
                {
                    "index": i,
                    "id": message.id,
                    "type": type(message).__name__,
                    "chars": len(message.content),
                    "file": fname,
                }
            )
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        raise SystemExit(
            f"[!] No se pudo escribir el dump forense en {out_dir}: {exc}\n"
            "    Corré con permisos suficientes (sudo si data/ es root-owned) "
            "y el backend detenido. No se modificó nada."
        ) from exc
    return out_dir


def update_chat_messages_mirror(session_id: int, *, threshold: int) -> int:
    """Trunca el espejo oversize en ``chat_messages`` (mismo head/marcador/tail).

    Devuelve las filas actualizadas, o -1 si la DB no es escribible por el
    usuario actual (root-owned del container): el thread del checkpointer ya
    quedó reparado; el espejo es cosmético para el fallback de GET /sessions.
    """
    db = _sqlite_path(settings.database_url)
    try:
        con = sqlite3.connect(db)
    except sqlite3.OperationalError as exc:
        print(f"[!] No se pudo abrir {db} para escritura: {exc}")
        return -1
    try:
        rows = con.execute(
            "SELECT id, content FROM chat_messages "
            "WHERE session_id = ? AND LENGTH(content) > ?",
            (session_id, threshold),
        ).fetchall()
        for row_id, content in rows:
            con.execute(
                "UPDATE chat_messages SET content = ? WHERE id = ?",
                (truncate_repair_content(content), row_id),
            )
        con.commit()
    except sqlite3.OperationalError as exc:
        print(
            f"[!] UPDATE espejo falló ({exc}); el thread del checkpointer ya "
            "quedó reparado. Reintentá con sudo o el backend detenido."
        )
        return -1
    finally:
        con.close()
    return len(rows)


# ---------------------------------------------------------------------------
# Flujo principal
# ---------------------------------------------------------------------------

async def repair_session(session_id: int, *, threshold: int, dry_run: bool) -> int:
    """Repara el thread ``session_id``. Devuelve 0 ok, 1 nada que hacer, 2 parcial."""
    checkpointer = await build_checkpointer()
    try:
        graph = _get_read_graph(checkpointer)
        config = {"configurable": {"thread_id": str(session_id)}}
        snapshot = await graph.aget_state(config)
        if snapshot is None or not snapshot.values:
            print(f"[!] Thread {session_id}: inexistente o vacío en el checkpointer.")
            return 1

        messages = list(snapshot.values.get("messages") or [])
        oversize = _select_oversize(messages, threshold)
        print(
            f"Session {session_id}: {len(messages)} mensajes, "
            f"{len(oversize)} oversize (> {threshold} chars), "
            f"max len {max((_text_len(m) for m in messages), default=0)}."
        )
        for m in oversize:
            print(f"  - {_describe(m)}")
        if not oversize:
            print("Nada que reparar.")
            return 0

        if dry_run:
            print(
                f"[dry-run] Se reemplazarían {len(oversize)} mensaje(s) por "
                f"head {REPAIR_HEAD_CHARS} + marcador + tail {REPAIR_TAIL_CHARS}, "
                "con dump forense previo y UPDATE del espejo en chat_messages."
            )
            return 0

        replacements: list[BaseMessage] = []
        skipped = 0
        for m in oversize:
            try:
                rep = build_replacement(m, threshold=threshold)
            except ValueError as exc:
                print(f"[!] WARNING, salteado: {exc}")
                skipped += 1
                continue
            if rep is not None:
                replacements.append(rep)
        if not replacements:
            print("[!] Nada reparable (todos los oversize vienen sin id). No se modificó nada.")
            return 1

        dump_dir = dump_forensics(session_id, oversize)
        print(f"Dump forense: {dump_dir}")

        try:
            await graph.aupdate_state(config, {"messages": replacements})
            print("aupdate_state: ok (as_node=None)")
        except Exception as exc:  # noqa: BLE001 - reintento con escritor explícito
            print(
                f"aupdate_state con as_node=None falló ({exc}); "
                "reintentando con as_node='model'…"
            )
            await graph.aupdate_state(config, {"messages": replacements}, as_node="model")
            print("aupdate_state: ok (as_node='model')")

        # Verificación: releer el estado y reportar el máximo restante.
        snap2 = await graph.aget_state(config)
        msgs2 = list((snap2.values if snap2 else {}).get("messages") or [])
        remaining = _select_oversize(msgs2, threshold)
        max_len = max((_text_len(m) for m in msgs2), default=0)
        print(
            f"Verificación: {len(msgs2)} mensajes, max len {max_len}, "
            f"oversize restantes {len(remaining)}"
        )
        for m in remaining:
            print(f"  - restante {_describe(m)}")
        if skipped:
            print(f"[i] {skipped} mensaje(s) salteado(s) por falta de id.")

        updated = update_chat_messages_mirror(session_id, threshold=threshold)
        if updated >= 0:
            print(f"Espejo chat_messages: {updated} fila(s) truncadas.")

        return 0 if not remaining else 2
    finally:
        await close_checkpointer(checkpointer)


async def scan_all_threads(*, threshold: int) -> int:
    """Reporta (SIN tocar) todos los threads con mensajes oversize."""
    checkpointer = await build_checkpointer()
    try:
        graph = _get_read_graph(checkpointer)
        seen: set[str] = set()
        async for tup in checkpointer.alist(None):
            tid = str(tup.config.get("configurable", {}).get("thread_id", ""))
            if tid:
                seen.add(tid)
        print(f"Threads en el checkpointer: {len(seen)}")

        poisoned: list[str] = []
        for tid in sorted(seen, key=lambda t: (0, int(t)) if t.isdigit() else (1, 0)):
            snapshot = await graph.aget_state({"configurable": {"thread_id": tid}})
            values = snapshot.values if snapshot is not None else {}
            msgs = list((values or {}).get("messages") or [])
            oversize = _select_oversize(msgs, threshold)
            if oversize:
                poisoned.append(tid)
                print(
                    f"  [VENENO] thread {tid}: {len(oversize)} oversize, "
                    f"max {max(_text_len(m) for m in oversize)} chars"
                )
        if not poisoned:
            print("Sin threads envenenados.")
        else:
            print(f"{len(poisoned)} thread(s) con veneno: {', '.join(poisoned)}")
            print("Repará cada uno con: --session <id>")
        return 0
    finally:
        await close_checkpointer(checkpointer)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repara threads envenenados por mensajes gigantes (sesión 44)."
    )
    parser.add_argument("--session", type=int, help="ID de la sesión a reparar.")
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        help=f"Tamaño en chars sobre el cual un mensaje es veneno "
        f"(default: {DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo reporta qué tocaría; no modifica nada.",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Reporta (sin tocar) todos los threads con mensajes oversize.",
    )
    args = parser.parse_args(argv)
    if args.threshold <= REPAIR_HEAD_CHARS + REPAIR_TAIL_CHARS:
        parser.error(
            f"--threshold debe ser > {REPAIR_HEAD_CHARS + REPAIR_TAIL_CHARS} "
            "(head + tail de la reparación)."
        )
    if not args.scan_all and args.session is None:
        parser.error("se requiere --session (o --scan-all)")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.scan_all:
        return asyncio.run(scan_all_threads(threshold=args.threshold))
    return asyncio.run(
        repair_session(args.session, threshold=args.threshold, dry_run=args.dry_run)
    )


if __name__ == "__main__":
    raise SystemExit(main())
