"""Smoke test for the SRS builder.

Validates that `build_srs` produces Markdown from a RequirementItem store with
mixed statuses, types, priorities, soft-deletes and an open relation.

Run: .venv/bin/python scripts/smoke_srs.py
"""
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.models.project
import backend.models.requirement
from backend.models.base import Base
from backend.models.project import Project
from backend.models.requirement import (
    Priority,
    ReqStatus,
    ReqType,
    RequirementItem,
    RequirementRelation,
    RelationKind,
    RelationStatus,
)
from backend.services.srs_builder import build_srs

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
            user_id=1, name="Proyecto Demo", slug="demo",
            description="SRS smoke", phase="requirements",
        )
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        pid = proj.id

        # Sembramos 6 ítems con diversidad de tipo/prioridad/estado.
        items = [
            RequirementItem(
                project_id=pid, code="REQ-001",
                statement="El sistema debe autenticar usuarios vía OAuth2.",
                type=ReqType.SECURITY, priority=Priority.MUST,
                status=ReqStatus.APPROVED,
                source={"quote": "autenticación obligatoria", "section": "3.1", "page": 4},
                span_verified=True,
                acceptance_criteria=["Given un usuario válido When intenta login Then obtiene un token JWT"],
            ),
            RequirementItem(
                project_id=pid, code="REQ-002",
                statement="El sistema debe responder en p95 < 200ms.",
                type=ReqType.PERFORMANCE, priority=Priority.SHOULD,
                status=ReqStatus.VALIDATED,
                source={"quote": "latencia p95 bajo 200ms", "section": "4.2", "page": 7},
                span_verified=True,
            ),
            RequirementItem(
                project_id=pid, code="REQ-003",
                statement="El sistema debería permitir exportar a PDF.",
                type=ReqType.FUNCTIONAL, priority=Priority.COULD,
                status=ReqStatus.DRAFT,
                source={"quote": "exportación PDF", "section": "5"},
                span_verified=False,
            ),
            RequirementItem(
                project_id=pid, code="REQ-004",
                statement="Cumplir GDPR para datos personales.",
                type=ReqType.COMPLIANCE, priority=Priority.MUST,
                status=ReqStatus.APPROVED,
                source={"quote": "cumplimiento GDPR", "section": "6"},
                span_verified=True,
            ),
            RequirementItem(
                project_id=pid, code="REQ-005",
                statement="Req derivado de seguridad.",
                type=ReqType.SECURITY, priority=Priority.MUST,
                status=ReqStatus.VALIDATED,
                source={"quote": "auditoría", "section": "3.2"},
                span_verified=True,
                derived=True, explicit=False,
            ),
            RequirementItem(
                project_id=pid, code="REQ-006",
                statement="Item rechazado por alucinación.",
                type=ReqType.FUNCTIONAL, priority=Priority.WONT,
                status=ReqStatus.REJECTED,
            ),
        ]
        session.add_all(items)
        await session.commit()

        # Relación abierta (contradicts, PROPOSED).
        session.add(RequirementRelation(
            from_id=items[0].id, to_id=items[1].id,
            kind=RelationKind.CONTRADICTS, status=RelationStatus.PROPOSED,
            note="posible tensión entre seguridad y performance",
        ))
        await session.commit()

        result = await build_srs(
            session, pid,
            project_name=proj.name,
            project_description=proj.description or "",
        )

        md = result["markdown"]
        counts = result["counts"]

        print("\n== COUNTS ==")
        check("counts.live == 5 (excluye REJECTED)",
              counts["live"] == 5, f"live={counts['live']}")
        check("counts.soft_deleted == 1",
              counts["soft_deleted"] == 1, f"soft_deleted={counts['soft_deleted']}")
        check("counts.open_relations == 1",
              counts["open_relations"] == 1, f"open_relations={counts['open_relations']}")
        check("by_priority.must == 3",
              counts["by_priority"].get("must") == 3,
              f"by_priority={counts['by_priority']}")
        check("by_type.security == 2",
              counts["by_type"].get("security") == 2,
              f"by_type={counts['by_type']}")

        print("\n== MARKDOWN CONTENT ==")
        check("header tiene título del proyecto", "# Proyecto Demo" in md)
        check("header tiene generated_at + totales",
              "Generado:" in md and "Total en SRS: 5" in md)
        check("tabla de resumen incluye Derivados",
              "| Derivados | 1 |" in md)
        check("tabla Sin cita verificada",
              "| Sin cita verificada | 1 |" in md)
        check("sección Detalle por tipo",
              "## Detalle por tipo" in md)
        check("requerimiento functional en cuerpo",
              "`REQ-003`" in md)
        check("cita literal del REQ-001",
              "> \"autenticación obligatoria\" (3.1, p.4)" in md)
        check("criterio Gherkin aparece",
              "Given un usuario válido" in md)
        check("flag derivado en REQ-005",
              "_derivado, implícito_" in md)
        check("sección de conflictos abiertos",
              "## Conflictos y dependencias abiertos" in md and
              "`REQ-001` y `REQ-002`" in md)
        check("sección auditoría lista REQ-006",
              "## Auditoría de cambios" in md and
              "`REQ-006`" in md and "Rechazado" in md)
        check("REQ-006 REJECTED no aparece en cuerpo principal",
              md.index("`REQ-006`") > md.index("## Auditoría de cambios"))

        print("\n== PREVIEW (primeros 800 chars) ==")
        print(md[:800])

    print(f"\n{'='*40}\nRESULT: {PASS} passed, {FAIL} failed")
    await engine.dispose()
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
