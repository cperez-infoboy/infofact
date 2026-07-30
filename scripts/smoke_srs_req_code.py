"""Smoke de regresión: resolucion req_id -> codigo opaque (REQ-XXXX).

El bug: la pestana de Calidad mostraba el id interno (``req #{req_id}``) en vez
del codigo opaque, con lo que el usuario no podia identificar el requerimiento.

Cubre:
  - ``finding_to_dict(f)`` por defecto -> ``req_code`` es None.
  - ``finding_to_dict(f, req_code=...)`` -> propaga el codigo.
  - ``findings_to_dicts`` con mapa vacio -> None (miss + hallazgos SET).
  - ``_req_code_map`` -> {id: code} del proyecto (una consulta).
  - ``findings_to_dicts`` con el mapa real -> ITEM resuelve su codigo, SET queda None.

Run: ``.venv/bin/python scripts/smoke_srs_req_code.py``
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.models  # noqa: F401 — registra todas las tablas en metadata
from backend.models.base import Base
from backend.models.requirement import Priority, ReqType, RequirementItem
from backend.models.srs import (
    FindingDimension,
    FindingScope,
    FindingSeverity,
)
from backend.services import srs_store
from backend.services.srs_store import (
    _req_code_map,
    findings_to_dicts,
    finding_to_dict,
    list_findings,
    replace_findings,
)


def _check(label: str, ok: bool, detail: str = "") -> int:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    return 0 if ok else 1


async def main() -> int:
    fails = 0
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        url = f"sqlite+aiosqlite:///{fh.name}"
    engine = create_async_engine(url, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        # Dos requerimientos con codigos opaque distintos.
        r1 = RequirementItem(
            project_id=1,
            code="REQ-AB12",
            statement="El sistema debera autenticar al usuario.",
            type=ReqType.FUNCTIONAL,
            priority=Priority.MUST,
        )
        r2 = RequirementItem(
            project_id=1,
            code="REQ-CD34",
            statement="El sistema debera responder en menos de 2s.",
            type=ReqType.PERFORMANCE,
            priority=Priority.SHOULD,
        )
        session.add_all([r1, r2])
        await session.commit()
        await session.refresh(r1)
        await session.refresh(r2)

        # Hallazgos: uno de ITEM (req r1) + uno de SET (req_id None).
        await replace_findings(
            session,
            project_id=1,
            findings=[
                {
                    "req_id": r1.id,
                    "scope": FindingScope.ITEM,
                    "dimension": FindingDimension.AMBIGUITY,
                    "rule_id": "ambiguous_term",
                    "severity": FindingSeverity.MAJOR,
                    "message": '"web" no especifica el servidor.',
                    "suggestion": "Especificar Apache/Nginx.",
                },
                {
                    "req_id": None,
                    "scope": FindingScope.SET,
                    "dimension": FindingDimension.COVERAGE_GAP,
                    "rule_id": "iso25010.portability",
                    "severity": FindingSeverity.MINOR,
                    "message": "No hay requerimientos de portabilidad.",
                    "suggestion": None,
                },
            ],
        )
        findings = await list_findings(session, 1)
        assert len(findings) == 2, f"esperaba 2 hallazgos, hay {len(findings)}"
        item_f = next(f for f in findings if f.req_id == r1.id)
        set_f = next(f for f in findings if f.req_id is None)

        print("finding_to_dict — req_code:")
        d0 = finding_to_dict(item_f)
        fails += _check(
            "req_code None por defecto", d0.get("req_code") is None,
            f"got {d0.get('req_code')!r}",
        )
        d1 = finding_to_dict(item_f, req_code="REQ-AB12")
        fails += _check(
            "req_code se propaga", d1.get("req_code") == "REQ-AB12",
            f"got {d1.get('req_code')!r}",
        )

        print("findings_to_dicts — con mapa vacio (miss + SET):")
        empty = findings_to_dicts(findings, {})
        fails += _check(
            "ITEM sin mapa -> None",
            next(d for d in empty if d["req_id"] == r1.id)["req_code"] is None,
        )
        fails += _check(
            "SET sin mapa -> None",
            next(d for d in empty if d["req_id"] is None)["req_code"] is None,
        )

        print("_req_code_map — consulta id -> code:")
        code_map = await _req_code_map(session, 1)
        fails += _check(
            "resuelve r1", code_map.get(r1.id) == "REQ-AB12",
            f"got {code_map.get(r1.id)!r}",
        )
        fails += _check(
            "resuelve r2", code_map.get(r2.id) == "REQ-CD34",
            f"got {code_map.get(r2.id)!r}",
        )

        print("findings_to_dicts — con mapa real:")
        full = findings_to_dicts(findings, code_map)
        item_d = next(d for d in full if d["req_id"] == r1.id)
        set_d = next(d for d in full if d["req_id"] is None)
        fails += _check(
            "ITEM resuelve su codigo", item_d["req_code"] == "REQ-AB12",
            f"got {item_d['req_code']!r}",
        )
        fails += _check(
            "SET queda None (no tiene req)", set_d["req_code"] is None,
            f"got {set_d['req_code']!r}",
        )

    await engine.dispose()
    Path(fh.name).unlink(missing_ok=True)
    print(f"\n{'TODO OK' if fails == 0 else str(fails) + ' FALLAS'}")
    return fails


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
