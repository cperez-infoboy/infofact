"""Read tools de SRS para el orquestador (read-only, espejo de requirements_read_tools).

El orquestador (agente top-level) recibe estas tools para responder preguntas
del usuario sobre el SRS SIN delegar al subagente srs-agent: listar versiones,
ver calidad/cobertura/goals/trazabilidad y los hallazgos de un requerimiento.
Las mutaciones (commit, edición de versión/goal) viven SOLO en el subagente
``srs-agent`` y en el router ``/api/projects/{id}/...``.
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from backend.database import AsyncSessionLocal
from backend.services import srs_store


def _summary(d: dict[str, Any]) -> dict[str, Any]:
    """Acota el markdown y los blobs grandes para no inflar el contexto del LLM."""
    out = dict(d)
    if "markdown" in out and isinstance(out["markdown"], str):
        md = out["markdown"]
        out["markdown"] = (md[:600] + "…[truncado]") if len(md) > 600 else md
    return out


def make_srs_read_tools(project_id: int) -> list:
    """Construye las read tools de SRS cerrando sobre project_id."""

    @tool
    async def list_srs_versions() -> dict:
        """Lista las versiones de SRS del proyecto (id, versión, estado, conteo)."""
        async with AsyncSessionLocal() as session:
            rows = await srs_store.list_srs_versions(session, project_id)
        return {
            "versions": [
                {
                    "id": s.id,
                    "version": s.version,
                    "status": s.status.value,
                    "requirement_count": s.requirement_count,
                    "generated_at": s.generated_at.isoformat() if s.generated_at else None,
                }
                for s in rows
            ]
        }

    @tool
    async def get_latest_srs() -> dict:
        """Devuelve la versión más reciente del SRS (estructura, resumen de \
calidad, cobertura, trazabilidad y un preview del markdown)."""
        async with AsyncSessionLocal() as session:
            srs = await srs_store.get_latest_srs(session, project_id)
        if srs is None:
            return {"error": "no_srs", "message": "Aún no hay un SRS generado. Usa /srs para generar uno."}
        return _summary(srs_store.srs_to_dict(srs, with_markdown=False))

    @tool
    async def get_quality() -> dict:
        """Devuelve el resumen de calidad y los hallazgos del proyecto \
(severidad, dimensión, regla, mensaje, sugerencia)."""
        async with AsyncSessionLocal() as session:
            findings = await srs_store.list_findings(session, project_id)
        return {
            "findings": [srs_store.finding_to_dict(f) for f in findings],
            "count": len(findings),
        }

    @tool
    async def get_coverage() -> dict:
        """Devuelve la cobertura ISO 25010 + secciones 29148 + goals de la \
última versión del SRS."""
        async with AsyncSessionLocal() as session:
            srs = await srs_store.get_latest_srs(session, project_id)
        if srs is None or not srs.coverage:
            return {"error": "no_coverage", "message": "No hay cobertura calculada. Usa /srs."}
        return {"coverage": srs.coverage}

    @tool
    async def list_goals() -> dict:
        """Lista los goals del proyecto (código, tipo, enunciado, estado)."""
        async with AsyncSessionLocal() as session:
            goals = await srs_store.list_goals(session, project_id)
        return {
            "goals": [
                {
                    "code": g.code,
                    "kind": g.kind.value,
                    "statement": g.statement,
                    "status": g.status.value,
                    "confidence": g.confidence,
                }
                for g in goals
            ]
        }

    @tool
    async def get_traceability() -> dict:
        """Devuelve la matriz de trazabilidad goal <-> requerimiento <-> fuente."""
        async with AsyncSessionLocal() as session:
            trace = await srs_store.build_traceability(session, project_id)
        return {"traceability": trace}

    @tool
    async def get_requirement_findings(req_code: str) -> dict:
        """Devuelve los hallazgos de calidad de un requerimiento por su código \
(p. ej. REQ-AB12)."""
        from backend.agents.tools.requirements_tools import _code_to_id
        async with AsyncSessionLocal() as session:
            try:
                req_id = await _code_to_id(session, project_id, req_code)
            except KeyError:
                return {"error": "not_found",
                        "message": f"No existe {req_code} en el proyecto."}
            findings = await srs_store.list_findings(
                session, project_id, req_id=req_id
            )
        return {
            "requirement": req_code,
            "findings": [srs_store.finding_to_dict(f) for f in findings],
        }

    return [
        list_srs_versions,
        get_latest_srs,
        get_quality,
        get_coverage,
        list_goals,
        get_traceability,
        get_requirement_findings,
    ]
