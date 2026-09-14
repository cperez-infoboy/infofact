"""Mantenimiento one-shot de data/checkpoints.db (checkpointer de LangGraph).

El archivo crece monótono: cada superstep persiste un snapshot completo del
estado (sesión 17 de Planitrack2.0 dejó ~58 MB solo del thread 60; el archivo
roza los 2 GB). Este script mide cuánto espacio real hay que recuperar
(freelist) y, SOLO con --aplicar, ejecuta wal_checkpoint(TRUNCATE) + VACUUM.

Uso:
    .venv/bin/python scripts/vacuum_checkpoints.py             # solo medición
    .venv/bin/python scripts/vacuum_checkpoints.py --aplicar   # compacta

El VACUUM necesita que NO haya escritores: detené el backend antes de
aplicar (sqlite3.locked se detecta y aborta sin dañar nada). Los datos de la
app viven en data/infofact.db — este script no la toca.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "checkpoints.db"


def _fmt(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="ejecuta wal_checkpoint(TRUNCATE) + VACUUM (requiere backend detenido)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB,
        help="ruta del checkpoints.db (default: data/checkpoints.db)",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"no existe {args.db}")
        return 1

    page_size = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True).execute(
        "PRAGMA page_size"
    ).fetchone()[0]
    disk = args.db.stat().st_size

    con = sqlite3.connect(str(args.db))
    try:
        pages, freelist = con.execute(
            "PRAGMA page_count"
        ).fetchone()[0], con.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        con.close()

    used = pages * page_size
    free = freelist * page_size
    print(f"archivo      : {args.db} ({_fmt(disk)})")
    print(f"paginas usadas: {_fmt(used)}  ({pages} x {page_size} B)")
    print(f"freelist     : {_fmt(free)}  ({freelist} paginas libres)")
    print(f"recuperable  : ~{_fmt(max(disk - used, 0))} restando el freelist ya contado")

    if not args.aplicar:
        if free < 64 * 1024 * 1024:
            print("\nsin --aplicar: poco/nada que recuperar (<64 MB en freelist);"
                  " el tamaño del archivo es historia viva del checkpointer.")
        else:
            print(f"\nsin --aplicar: hay ~{_fmt(free)} recuperables. "
                  "Re-run con --aplicar (backend detenido).")
        return 0

    print("\naplicando (requiere que el backend esté detenido)...")
    con = sqlite3.connect(str(args.db), timeout=5)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")
        con.commit()
    except sqlite3.OperationalError as exc:
        print(f"abortado: {exc} (¿el backend sigue corriendo?)")
        return 1
    finally:
        con.close()

    disk2 = args.db.stat().st_size
    print(f"listo: {_fmt(disk)} -> {_fmt(disk2)} (liberados {_fmt(disk - disk2)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
