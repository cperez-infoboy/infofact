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
            # Resuelve req_id -> codigo opaque (REQ-XXXX) por cada hallazgo.
            code_map = await srs_store._req_code_map(session, project_id)
        return {
            "findings": srs_store.findings_to_dicts(findings, code_map),
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
    async def goal_coverage() -> dict:
        """Devuelve la cobertura del MODELO de goals: reqs vivos con y sin link.

        ``without_goal_codes`` es la cola de trabajo: requerimientos vivos que
        no aportan a ningún goal (sin justificación de «por qué» en el
        modelo). ``per_goal`` lleva el conteo de links por goal.
        """
        async with AsyncSessionLocal() as session:
            return await srs_store.goal_coverage(session, project_id)

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
            code_map = await srs_store._req_code_map(session, project_id)
        return {
            "requirement": req_code,
            "findings": srs_store.findings_to_dicts(findings, code_map),
        }

    @tool
    async def list_srs_sections() -> dict:
        """Devuelve el árbol de secciones del SRS activo con metadatos.

        Para secciones ``authored`` indica si cada subsection tiene contenido.
        Para secciones ``projected`` indica cuántos requerimientos pertenecen.
        Útil para navecar el SRS sin cargar el documento completo.
        """
        from backend.services.srs_builder import SECTION_REQTYPE_MAP, ACTORS_SECTION_ID
        from backend.services.requirement_store import list_requirements
        from backend.services.actor_store import list_actors

        async with AsyncSessionLocal() as session:
            srs = await srs_store.get_latest_srs(session, project_id)
            if srs is None:
                return {
                    "error": "no_srs",
                    "message": "Aún no hay un SRS generado. Usa /srs.",
                }
            all_items = await list_requirements(
                session, project_id, include_deleted=False
            )
            live_items = [
                it for it in all_items
                if it.status.value in ("validated", "approved", "draft")
            ]
            actor_count = len(await list_actors(session, project_id))

        sections: list[dict[str, Any]] = []
        for section in srs.structure:
            entry: dict[str, Any] = {
                "id": section["id"],
                "title": section["title"],
                "kind": section.get("kind", "projected"),
            }
            if section.get("kind") == "authored" and section.get("subsections"):
                entry["subsections"] = [
                    {
                        "id": sub["id"],
                        "title": sub["title"],
                        "has_content": (
                            actor_count > 0
                            if sub["id"] == ACTORS_SECTION_ID
                            else bool(srs.narrative.get(sub["id"]))
                        ),
                    }
                    for sub in section["subsections"]
                ]
            elif section["id"] in SECTION_REQTYPE_MAP:
                reqtypes = SECTION_REQTYPE_MAP[section["id"]]
                entry["item_count"] = sum(
                    1 for it in live_items if it.type in reqtypes
                )
            sections.append(entry)
        return {"sections": sections}

    @tool
    async def read_srs_section(section_id: str) -> dict:
        """Devuelve el contenido de una sección específica del SRS.

        Para ``authored`` (e.g. ``intro.definitions``, ``overall.users``):
        el texto de la narrativa.
        Para ``projected`` (e.g. ``functional``, ``nfr``, ``constraints``,
        ``overall.actors``): lista tipada — RequirementItem con code,
        statement, priority, type; para ``overall.actors`` el catálogo
        de actores con code, name, channel, synonyms.
        """
        from backend.services.srs_builder import SECTION_REQTYPE_MAP, ACTORS_SECTION_ID
        from backend.services.requirement_store import list_requirements

        async with AsyncSessionLocal() as session:
            srs = await srs_store.get_latest_srs(session, project_id)
            if srs is None:
                return {
                    "error": "no_srs",
                    "message": "Aún no hay un SRS generado.",
                }

        # Determine section kind from structure.
        target_kind = "projected"
        target_title = section_id
        for section in srs.structure:
            if section["id"] == section_id:
                target_kind = section.get("kind", "projected")
                target_title = section["title"]
                break
            for sub in section.get("subsections", []):
                if sub["id"] == section_id:
                    target_kind = "authored"
                    target_title = sub["title"]
                    break

        if section_id == ACTORS_SECTION_ID:
            from backend.services.actor_store import list_actors

            async with AsyncSessionLocal() as session:
                actors = await list_actors(session, project_id)
            return {
                "section_id": section_id,
                "title": target_title,
                "kind": "projected",
                "items": [
                    {
                        "code": a.code,
                        "name": a.name,
                        "channel": a.channel,
                        "synonyms": list(a.synonyms or []),
                    }
                    for a in actors
                ],
            }

        if target_kind == "authored":
            content = srs.narrative.get(section_id, "")
            return {
                "section_id": section_id,
                "title": target_title,
                "kind": "authored",
                "content": content or None,
            }

        # Projected: return items.
        if section_id not in SECTION_REQTYPE_MAP:
            return {
                "section_id": section_id,
                "title": target_title,
                "kind": "projected",
                "items": [],
            }

        reqtypes = SECTION_REQTYPE_MAP[section_id]
        async with AsyncSessionLocal() as session:
            all_items = await list_requirements(
                session, project_id, include_deleted=False
            )

        items_data = [
            {
                "code": it.code,
                "statement": it.statement,
                "priority": it.priority.value,
                "type": it.type.value,
                "acceptance_criteria": list(it.acceptance_criteria or []),
                "derived": it.derived,
            }
            for it in all_items
            if it.type in reqtypes
            and it.status.value in ("validated", "approved", "draft")
        ]
        return {
            "section_id": section_id,
            "title": target_title,
            "kind": "projected",
            "items": items_data,
        }

    return [
        list_srs_versions,
        get_latest_srs,
        get_quality,
        get_coverage,
        list_goals,
        get_traceability,
        goal_coverage,
        get_requirement_findings,
        list_srs_sections,
        read_srs_section,
    ]
