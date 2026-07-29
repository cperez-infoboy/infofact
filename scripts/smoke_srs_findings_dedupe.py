"""Smoke de regresión: dedupe de hallazgos en ``srs_store.replace_findings``.

Reproduce el bug del proyecto "test 1" sesión 14: el motor LLM de calidad
emitía dos hallazgos ``ambiguous_term`` (AMBIGUITY) para el mismo req_id, y
``replace_findings`` los insertaba ambos -> ``UNIQUE constraint failed:
requirement_findings.req_id, requirement_findings.rule_id`` -> rollback de
toda la tanda -> el SRS quedaba sin hallazgos.

Dos partes:
  1. ``_dedupe_findings`` (función pura): colapsa por (req_id, rule_id),
     conserva el más severo y une mensajes. Los SET (req_id None) se preservan.
  2. ``replace_findings`` end-to-end contra una SQLite temporal: con duplicados
     reales, confirma que NO hay IntegrityError y queda una fila por clave.

Incluye además el caso espejo en ``replace_goals``: links duplicados por
``(goal_id, req_id, relation)`` contra ``UniqueConstraint("goal_id","req_id","relation")``.

Run: ``.venv/bin/python scripts/smoke_srs_findings_dedupe.py``
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.models  # noqa: F401 — registra todas las tablas en metadata
from backend.models.base import Base
from backend.models.srs import FindingDimension, FindingScope, FindingSeverity
from backend.services.srs_store import _dedupe_findings, replace_findings, replace_goals


def _check(label: str, ok: bool, detail: str = "") -> int:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    return 0 if ok else 1


def test_dedupe_pure() -> int:
    """Cubre: merge por (req_id, rule_id), severidad máxima, mensajes unidos,
    preservación de SET, inmutabilidad de singletones."""
    fails = 0
    print("Pure function — _dedupe_findings:")

    # Escenario del bug: dos ambiguous_term para req 22 (uno MAJOR, uno MINOR).
    findings = [
        {
            "req_id": 22,
            "scope": FindingScope.ITEM,
            "dimension": FindingDimension.AMBIGUITY,
            "rule_id": "ambiguous_term",
            "severity": FindingSeverity.MAJOR,
            "message": '"web" no es un tipo de servidor.',
            "suggestion": "Especificar Apache/Nginx.",
        },
        {
            "req_id": 22,
            "scope": FindingScope.ITEM,
            "dimension": FindingDimension.AMBIGUITY,
            "rule_id": "ambiguous_term",
            "severity": FindingSeverity.MINOR,
            "message": '"rápido" es un término vago.',
            "suggestion": "Cuantificar (ms).",
        },
        # Distinto rule_id para el mismo req -> se conserva (no se colapsa).
        {
            "req_id": 22,
            "scope": FindingScope.ITEM,
            "dimension": FindingDimension.EARS_VIOLATION,
            "rule_id": "ears.missing_condition",
            "severity": FindingSeverity.BLOCKER,
            "message": "Sin condición EARS.",
        },
        # Hallazgo de conjunto (req_id None) -> se preserva siempre.
        {
            "req_id": None,
            "scope": FindingScope.SET,
            "dimension": FindingDimension.COVERAGE_GAP,
            "rule_id": "gap.compatibility",
            "severity": FindingSeverity.MAJOR,
            "message": "Nada cubre compatibility.",
        },
    ]

    out = _dedupe_findings(findings)

    # 4 entradas -> 3 (dos ambiguous_term colapsadas en una).
    fails += _check(
        "cuenta tras dedupe = 3", len(out) == 3, f"got {len(out)}"
    )

    item_amb = [f for f in out if f.get("req_id") == 22 and f.get("rule_id") == "ambiguous_term"]
    fails += _check(
        "una sola fila (22, ambiguous_term)", len(item_amb) == 1, f"got {len(item_amb)}"
    )
    # Se conserva la severidad más alta (MAJOR > MINOR).
    sev = item_amb[0]["severity"]
    sev_val = sev.value if hasattr(sev, "value") else str(sev)
    fails += _check(
        "severidad conservada = major", sev_val == "major", f"got {sev_val}"
    )
    # Mensajes unidos con ' · ' (distintos, sin duplicados).
    msg = item_amb[0]["message"]
    fails += _check(
        "mensajes unidos",
        '"web" no es un tipo de servidor.' in msg and '"rápido" es un término vago.' in msg,
        f"got {msg!r}",
    )

    # Singleton (22, ears.missing_condition) sin tocar.
    ears = [f for f in out if f.get("rule_id") == "ears.missing_condition"]
    fails += _check(
        "singleton ears preservado", len(ears) == 1 and ears[0]["message"] == "Sin condición EARS."
    )

    # SET preservado.
    sets = [f for f in out if f.get("req_id") is None]
    fails += _check("hallazgo SET preservado", len(sets) == 1)

    # Severidad como string (no enum) también debe funcionar.
    str_out = _dedupe_findings([
        {"req_id": 5, "scope": "item", "dimension": "ambiguity",
         "rule_id": "r", "severity": "minor", "message": "a"},
        {"req_id": 5, "scope": "item", "dimension": "ambiguity",
         "rule_id": "r", "severity": "blocker", "message": "b"},
    ])
    fails += _check(
        "severity como string -> blocker gana",
        len(str_out) == 1 and "blocker" in str(str_out[0]["severity"]),
    )

    return fails


async def test_replace_findings_e2e() -> int:
    """End-to-end: replace_findings con duplicados NO levanta IntegrityError."""
    fails = 0
    print("E2E — replace_findings contra SQLite temporal:")

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        url = f"sqlite+aiosqlite:///{path}"
        engine = create_async_engine(url, future=True)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)

        findings = [
            {"req_id": 22, "scope": FindingScope.ITEM,
             "dimension": FindingDimension.AMBIGUITY, "rule_id": "ambiguous_term",
             "severity": FindingSeverity.MAJOR, "message": "m1"},
            # Duplicado exacto del bug: mismo (22, ambiguous_term).
            {"req_id": 22, "scope": FindingScope.ITEM,
             "dimension": FindingDimension.AMBIGUITY, "rule_id": "ambiguous_term",
             "severity": FindingSeverity.MINOR, "message": "m2"},
            {"req_id": 22, "scope": FindingScope.ITEM,
             "dimension": FindingDimension.EARS_VIOLATION, "rule_id": "ears.missing_condition",
             "severity": FindingSeverity.BLOCKER, "message": "e1"},
        ]
        async with Session() as session:
            try:
                rows = await replace_findings(session, project_id=1, findings=findings)
                ok = True
                detail = f"{len(rows)} filas"
            except Exception as exc:  # noqa: BLE001 — el bug lanzaba IntegrityError
                ok = False
                detail = f"{type(exc).__name__}: {exc}"
        fails += _check(
            "replace_findings no levanta IntegrityError", ok, detail
        )

        # Verificación directa: una fila por (req_id, rule_id).
        from sqlalchemy import select
        from backend.models.srs import RequirementFinding

        async with Session() as session:
            result = await session.execute(
                select(RequirementFinding.req_id, RequirementFinding.rule_id).where(
                    RequirementFinding.project_id == 1
                )
            )
            keys = result.all()
        fails += _check(
            "una fila por (req_id, rule_id)",
            len(keys) == 2 and len(set(keys)) == 2,
            f"keys={keys}",
        )

        # Re-run (replace, no append): debe reemplazar sin error y mantener 2.
        async with Session() as session:
            await replace_findings(session, project_id=1, findings=findings[:2])
        async with Session() as session:
            result = await session.execute(
                select(RequirementFinding.req_id, RequirementFinding.rule_id).where(
                    RequirementFinding.project_id == 1
                )
            )
            keys2 = result.all()
        fails += _check(
            "reemplazo (no append) tras segunda corrida",
            len(keys2) == 1,
            f"keys={keys2}",
        )

        await engine.dispose()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return fails


async def test_replace_goals_dedupe_links() -> int:
    """replace_goals con links duplicados (mismo goal+req+relation) -> NO
    IntegrityError, se colapsan a una arista."""
    fails = 0
    print("E2E — replace_goals dedupe de links:")

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}", future=True)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)

        # 1 goal estable (code "G1") referenciado por los links. req_by_code
        # arbitrario (FKs off -> el id no necesita existir).
        goals = [{"code": "G1", "statement": "Permitir X", "kind": "functional_goal"}]
        links = [
            {"goal_code": "G1", "req_code": "REQ-AB12", "relation": "realizes", "rationale": "r1"},
            # Duplicado exacto (mismo goal+req+relation) -> debe colapsar.
            {"goal_code": "G1", "req_code": "REQ-AB12", "relation": "realizes", "rationale": "r2"},
            # Distinta relation sobre el mismo par -> se conserva.
            {"goal_code": "G1", "req_code": "REQ-AB12", "relation": "contributes"},
        ]
        async with Session() as session:
            try:
                result = await replace_goals(
                    session, project_id=1, goals=goals, links=links,
                    req_by_code={"REQ-AB12": 99},
                )
                ok = True
                detail = f"goals={result['goals']} links={result['links']}"
            except Exception as exc:  # noqa: BLE001
                ok = False
                detail = f"{type(exc).__name__}: {exc}"
        fails += _check("replace_goals no levanta IntegrityError", ok, detail)
        fails += _check(
            "links colapsados a 2 (realizes x2 -> 1, contributes -> 1)",
            ok and result["links"] == 2,
            f"links={result['links']}" if ok else "",
        )

        await engine.dispose()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return fails


async def main() -> int:
    fails = test_dedupe_pure()
    fails += await test_replace_findings_e2e()
    fails += await test_replace_goals_dedupe_links()
    print(f"\n{'='*40}")
    print(f"{'TODO OK' if fails == 0 else f'{fails} FALLA(S)'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
