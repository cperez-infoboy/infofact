"""Smoke test del motor de cobertura del SRS (ISO/IEC 25010 + 29148 + goals).

Sin LLM: ``compute_coverage`` es 100% programático. Verifica que:
- cuenta requerimientos por característica 25010,
- detecta los gaps (características sin ningún req),
- emite hallazgos COVERAGE_GAP por cada gap y MISSING_REQ si no hay funcionales,
- reporta los totales y la presencia de secciones 29148.

Run: .venv/bin/python scripts/smoke_srs_coverage.py
"""
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.models  # noqa: F401  (registra todas las tablas en Base.metadata)
from backend.models.base import Base
from backend.models.project import Project
from backend.models.requirement import ReqType
from backend.services import requirement_store as store
from backend.services.srs_coverage import ISO_25010, compute_coverage

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


async def main() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        proj = Project(
            user_id=1, name="Smoke SRS", slug="smoke-srs",
            description="coverage smoke", phase="requirements",
        )
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        pid = proj.id

        # Poblamos: 2 funcionales, 1 performance (con número), 1 seguridad.
        # Dejamos vacías: compatibility, usability, reliability, maintainability,
        # portability -> serán gaps.
        await store.add_requirement(
            session, pid, statement="El sistema debe autenticar usuarios.",
            type=ReqType.FUNCTIONAL,
        )
        await store.add_requirement(
            session, pid, statement="El sistema debe listar facturas.",
            type=ReqType.FUNCTIONAL,
        )
        await store.add_requirement(
            session, pid,
            statement="El login debe responder en menos de 200 ms.",
            type=ReqType.PERFORMANCE,
        )
        await store.add_requirement(
            session, pid,
            statement="Las contraseñas deben almacenarse con bcrypt.",
            type=ReqType.SECURITY,
        )

        coverage, findings = await compute_coverage(session, pid)

        print("\n== cobertura 25010 ==")
        for key, info in coverage["iso_25010"].items():
            print(f"  {key:22s} {info['count']:2d}  {info['label']}")

        gaps = coverage["gaps_25010"]
        print("\ngaps:", gaps)
        check("functional cuenta 2",
              coverage["iso_25010"]["functional_suitability"]["count"] == 2)
        check("performance cuenta 1",
              coverage["iso_25010"]["performance_efficiency"]["count"] == 1)
        check("security cuenta 1",
              coverage["iso_25010"]["security"]["count"] == 1)
        check("compatibility es gap", "compatibility" in gaps)
        check("portability es gap", "portability" in gaps)
        check("usability es gap", "usability" in gaps)

        print("\n== hallazgos ==")
        cov_findings = [f for f in findings
                        if f["dimension"].value == "coverage_gap"]
        missing = [f for f in findings
                   if f["dimension"].value == "missing_req"]
        for f in cov_findings:
            print(f"  {f['rule_id']:28s} {f['severity'].value}")
        check("hay >=5 hallazgos de gap (uno por char vacía)",
              len(cov_findings) >= 5, f"got {len(cov_findings)}")
        check("NO hay MISSING_REQ (hay funcionales)",
              len(missing) == 0, f"got {len(missing)}")

        print("\n== secciones 29148 + totales ==")
        secs = coverage["iso_29148_sections"]
        print("  secciones:", secs)
        print("  totales:", coverage["totals"])
        check("specific_requirements presente", secs["specific_requirements"])
        check("functional presente", secs["functional"])
        check("nonfunctional presente", secs["nonfunctional"])
        check("totals.live == 4", coverage["totals"]["live"] == 4)
        check("totals.functional == 2", coverage["totals"]["functional"] == 2)
        check("totals.nfr == 2 (perf+sec)",
              coverage["totals"]["nfr"] == 2)

        # Escenario BLOCKER: sin funcionales -> MISSING_REQ.
        proj2 = Project(
            user_id=1, name="Smoke sin func", slug="smoke-no-func",
            description="coverage smoke", phase="requirements",
        )
        session.add(proj2)
        await session.commit()
        await session.refresh(proj2)
        await store.add_requirement(
            session, proj2.id,
            statement="Debe responder en 100 ms.",
            type=ReqType.PERFORMANCE,
        )
        cov2, find2 = await compute_coverage(session, proj2.id)
        blocker = [f for f in find2
                   if f["dimension"].value == "missing_req"
                   and f["severity"].value == "blocker"]
        check("sin funcionales -> MISSING_REQ blocker",
              len(blocker) == 1, f"got {len(blocker)}")

    print(f"\n{'='*40}\ncoverage smoke: {PASS} pass, {FAIL} fail")
    return FAIL


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) == 0 else 1)
