"""Ensamblador del SRS: orquesta los motores y arma el payload de SrsDocument.

Punto único que coordina la generación de un SRS candidato:
  analyze_quality -> infer_goals -> compute_coverage -> build_traceability
y combina sus salidas en el payload que ``srs_store.create_srs`` persiste:
estructura 29148/Volere (secciones ``projected`` vs ``authored``), narrativa
editable (borrador por subsection), markdown snapshot, resumen de calidad,
cobertura, matriz de trazabilidad y los códigos de requerimiento.

Vive aparte de ``srs_builder`` (que es la proyección pura a Markdown) para no
acoplar la síntesis del entregable con la proyección; el builder se invoca aquí
para el cuerpo de requerimientos.
"""
from __future__ import annotations

import logging
import os
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.llm import structured_llm
from backend.agents.retrieval import store as retrieval
from backend.services.goals_engine import infer_goals
from sqlalchemy import select

from backend.models.project_document import ProjectDocument
from backend.models.srs import Goal, GoalLink
from backend.services.requirement_store import list_requirements
from backend.services.srs_builder import (
    _TYPE_LABELS,
    SRS_STRUCTURE,
    build_srs,
)
from backend.services.srs_coverage import compute_coverage
from backend.services.srs_quality import analyze_quality
from backend.services.srs_store import build_traceability, replace_findings

# Estados vivos (consistente con srs_builder).
from backend.models.requirement import Priority, ReqStatus

logger = logging.getLogger(__name__)

_LIVE_STATUSES = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)

# Orden de prioridad para la muestra de requerimientos (MUST primero).
_PRIORITY_RANK = {
    Priority.MUST: 0,
    Priority.SHOULD: 1,
    Priority.COULD: 2,
    Priority.WONT: 3,
}


# ---------------------------------------------------------------------------
# Schema para el draft narrativo asistido por LLM (8 secciones authored).
# ---------------------------------------------------------------------------


class SrsNarrativeDraft(BaseModel):
    """LLM-generated prose for the 7 authored SRS subsections.

    §1.4 Referencias NO está acá: es una proyección determinista del catálogo
    de fuentes de captura (``_projected_references``) — una prosa LLM congelada
    por el carry-forward desfasaba el conteo de documentos tras capturas append.
    """

    purpose: str = Field(
        description=(
            "Sección 1.1 Propósito: 2-3 párrafos sobre el propósito del "
            "producto y del documento SRS."
        )
    )
    scope: str = Field(
        description="Sección 1.2 Alcance: qué incluye el producto y qué queda fuera."
    )
    definitions: str = Field(
        description=(
            "Sección 1.3 Definiciones: términos y acrónimos del dominio en "
            "formato Markdown con viñetas."
        )
    )
    perspective: str = Field(
        description=(
            "Sección 2.1 Perspectiva: descripción del producto, dependencias "
            "y contexto. NO incluir conteos."
        )
    )
    users: str = Field(
        description=(
            "Sección 2.4 Usuarios: los actores del catálogo provisto, con "
            "privilegios y frecuencia estimada SOLO si hay respaldo en los "
            "requerimientos o fragmentos."
        )
    )
    environment: str = Field(
        description=(
            "Sección 2.4 Entorno operativo: plataforma, tecnologías e "
            "integraciones inferidas."
        )
    )
    assumptions: str = Field(
        description="Sección 2.5 Supuestos y dependencias del proyecto."
    )


_NARRATIVE_SYSTEM = (
    "Eres un analista de requerimientos de software que redacta secciones "
    "de una Especificación de Requerimientos de Software (SRS) conforme a "
    "ISO/IEC/IEEE 29148:2018. Recibirás el nombre del proyecto, su "
    "descripción, una muestra de requerimientos, fragmentos de documentos "
    "fuente y, cuando exista, el catálogo de actores del sistema definido "
    "durante la captura.\n"
    "Reglas:\n"
    "- IDIOMA: español neutro y profesional. Sin regionalismos.\n"
    "- TONO: objetivo, técnico, impersonal (tercera persona).\n"
    "- No inventes funcionalidades que no estén respaldadas por los "
    "requerimientos o fragmentos proporcionados.\n"
    "- Si el contexto incluye el bloque PROJECT_ACTORS, la sección de "
    "usuarios debe describir EXACTAMENTE esos actores (usando su rol "
    "canónico) y NINGÚN otro; los sinónimos solo para referenciar la misma "
    "clase. Privilegios y frecuencia estimada SOLO si los requerimientos o "
    "fragmentos los respaldan.\n"
    "- Para definitions: extrae términos técnicos y acrónimos del dominio "
    "que aparezcan en los requerimientos o fragmentos.\n"
    "- Cada sección debe ser prosa coherente, excepto definitions que puede "
    "usar viñetas Markdown.\n"
)

# Marcador del bloque de conteos de §2.1: lo arma el determinista y se
# reapende tras la prosa para que el resumen de alcance nunca quede desfasado.
_COUNTS_MARKER = "\n**Resumen del alcance especificado:**"

# Subsecciones authored cuyo TEXTO carry-forward preserva de la versión
# previa (la perspectiva va aparte: hay que separarle el bloque de conteos).
# ``intro.references`` NO está acá: §1.4 es proyección determinista del
# catálogo de fuentes — una prosa congelada desfasaba el conteo de documentos
# tras capturas append (sesión 16 de Planitrack2.0: «4 fuentes» eternas).
_AUTHORED_PROSE_KEYS = (
    "intro.purpose",
    "intro.scope",
    "intro.definitions",
    "overall.users",
    "overall.environment",
    "overall.assumptions",
)

_LEGACY_KEYS = {
    "intro": (
        "intro.purpose",
        "intro.scope",
        "intro.definitions",
        "intro.references",
        "intro.overview",
    ),
    "overall": (
        "overall.perspective",
        "overall.features",
        "overall.users",
        "overall.environment",
        "overall.assumptions",
    ),
}


def _split_counts_block(perspective: str) -> tuple[str, str]:
    """Separa una §2.1 en (prosa, bloque de conteos). El bloque puede ser ''."""
    if _COUNTS_MARKER in perspective:
        idx = perspective.index(_COUNTS_MARKER)
        return perspective[:idx], perspective[idx:]
    return perspective, ""


def _carryover_narrative(
    previous: dict[str, str], det: dict[str, str]
) -> dict[str, str]:
    """Combina la prosa authored de la versión previa con el determinista fresco.

    El texto authored (6 subsecciones + perspectiva sin su bloque de conteos)
    se conserva VERBATIM de ``previous``; los deterministas (``intro.overview``,
    ``intro.references``, ``overall.features``, bloque de conteos) salen de
    ``det`` y reflejan el
    store vivo. Así «refrescá el SRS tras una curación» preserva el texto
    curado sin redactar de nuevo: sin este canal el redactor re-draftaba de
    cero y marcaba el documento con notas provisionales (sesión 53 v11).
    Tolera narrativas previas parciales (versiones viejas sin alguna clave).
    """
    carried = dict(det)
    for key in _AUTHORED_PROSE_KEYS:
        text = previous.get(key)
        if isinstance(text, str) and text.strip():
            carried[key] = text
    prev_prose, _ = _split_counts_block(
        previous.get("overall.perspective") or ""
    )
    _, fresh_block = _split_counts_block(det.get("overall.perspective", ""))
    carried["overall.perspective"] = (
        prev_prose.rstrip() + "\n\n" + fresh_block.lstrip("\n")
        if fresh_block
        else prev_prose.rstrip()
    )
    for legacy, parts in _LEGACY_KEYS.items():
        carried[legacy] = "\n\n".join(
            text for text in (carried.get(k, "") for k in parts) if text
        )
    return carried


def _feature_line(it: Any) -> str:
    """Línea de requerimiento para la sección 2.2: MoSCoW + tipo + resumen."""
    stmt = it.statement or ""
    # Truncate to ~120 chars at word boundary (overview, no catálogo completo).
    if len(stmt) > 120:
        stmt = stmt[:117].rsplit(" ", 1)[0] + "…"
    prio = getattr(getattr(it, "priority", None), "value", "?").upper()
    type_val = getattr(getattr(it, "type", None), "value", "?")
    type_label = _TYPE_LABELS.get(type_val, type_val)
    return f"- `{it.code}` ({prio} · {type_label}) — {stmt}"


# Media embebida extraída del parseo (imágenes OCR): se agrupa como anexo en
# la proyección de fuentes en vez de listar archivo por archivo.
_MEDIA_DIR_MARKER = "/.infofact-media/"

_STANDARDS_BLOCK = (
    "**Normas aplicadas:** ISO/IEC/IEEE 29148:2018 (Ingeniería de "
    "requerimientos), ISO/IEC 25010:2011 (Calidad del producto software)."
)


async def _capture_sources(
    session: AsyncSession, project_id: int
) -> dict[str, Any]:
    """Resumen vivo de las fuentes usadas en captura, para §1.4 y §2.1.

    Cruza el catálogo (``ProjectDocument`` con ``used_in_capture``) con los
    ``source[].document_id`` citados por requerimientos vivos: cada fuente
    con su conteo de requerimientos respaldados. Los archivos de media
    embebida (``.infofact-media/``) se cuentan aparte como anexo OCR — la
    proyección los agrupa en una sola línea. Citas sin fila en el catálogo
    (registro histórico perdido) entran marcadas ``unregistered`` para que
    la tabla refleje siempre el respaldo real.
    """
    from sqlalchemy import text as sa_text

    slug = await session.scalar(
        sa_text("SELECT slug FROM projects WHERE id = :pid"), {"pid": project_id}
    )
    docs = list(
        await session.scalars(
            select(ProjectDocument).where(
                ProjectDocument.project_id == project_id,
                ProjectDocument.used_in_capture.is_(True),
            )
        )
    )
    items = await list_requirements(session, project_id, include_deleted=True)
    live = [it for it in items if it.status in _LIVE_STATUSES]

    prefix = f"/workspaces/{slug}/" if slug else None
    cited: dict[str, int] = {}
    for it in live:
        src = it.source
        if not src:
            continue
        entries = src if isinstance(src, list) else [src]
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            doc_id = entry.get("document_id")
            if not (isinstance(doc_id, str) and doc_id) or doc_id in seen:
                continue
            seen.add(doc_id)
            if prefix and prefix in doc_id:
                rel = doc_id.split(prefix, 1)[1]
            else:
                rel = doc_id.lstrip("/")
            if rel:
                cited[rel] = cited.get(rel, 0) + 1

    def _entry(rel: str, reqs: int, unregistered: bool) -> dict[str, Any]:
        name = PurePosixPath(rel).name
        return {
            "rel_path": rel,
            "filename": name,
            "extension": os.path.splitext(name)[1].lower().lstrip("."),
            "reqs": reqs,
            "unregistered": unregistered,
        }

    documents: list[dict[str, Any]] = []
    media_files = 0
    media_reqs = 0
    covered_rels: set[str] = set()
    for d in sorted(docs, key=lambda x: x.rel_path):
        reqs = cited.get(d.rel_path, 0)
        if _MEDIA_DIR_MARKER in f"/{d.rel_path}":
            media_files += 1
            media_reqs += reqs
        else:
            documents.append(_entry(d.rel_path, reqs, False))
        covered_rels.add(d.rel_path)

    for rel in sorted(cited):
        if rel in covered_rels:
            continue
        if _MEDIA_DIR_MARKER in f"/{rel}":
            media_files += 1
            media_reqs += cited[rel]
        else:
            documents.append(_entry(rel, cited[rel], True))

    documents.sort(key=lambda e: (-e["reqs"], e["filename"]))
    return {
        "documents": documents,
        "media_files": media_files,
        "media_reqs": media_reqs,
        "total_files": len(documents) + media_files,
    }


def _projected_references(sources: dict[str, Any] | None) -> str:
    """Tabla Markdown de §1.4: fuentes de captura vivas + anexo OCR + normas.

    Proyección pura (sin LLM): siempre coincide con el catálogo y con los
    requerimientos vivos. Filenames duplicados se muestran con su ruta
    relativa para desambiguar.
    """
    if not sources or not sources.get("total_files"):
        return (
            "_Sin documentos fuente registrados en captura todavía._\n\n"
            + _STANDARDS_BLOCK
        )
    lines = [
        "| # | Fuente | Tipo | Reqs respaldados |",
        "|---|--------|------|------------------|",
    ]
    name_counts: dict[str, int] = {}
    for e in sources["documents"]:
        name_counts[e["filename"]] = name_counts.get(e["filename"], 0) + 1
    for i, e in enumerate(sources["documents"], start=1):
        label = e["rel_path"] if name_counts[e["filename"]] > 1 else e["filename"]
        flag = " (sin registro)" if e.get("unregistered") else ""
        ext = (e["extension"] or "?").upper()
        lines.append(f"| {i} | {label}{flag} | {ext} | {e['reqs']} |")
    if sources.get("media_files"):
        lines.append(
            f"| — | Anexo de imágenes (OCR, {sources['media_files']} archivo(s)) "
            f"| IMG | {sources.get('media_reqs', 0)} |"
        )
    return "\n".join(lines) + "\n\n" + _STANDARDS_BLOCK


def _draft_narrative(
    project_name: str,
    project_description: str,
    quality_summary: dict[str, Any],
    coverage: dict[str, Any],
    goals_summary: dict[str, Any],
    live_count: int,
    live_items: list | None = None,
    goal_groups: list[tuple[str, str, list]] | None = None,
    actors_block_text: str | None = None,
    sources_summary: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Borrador de la prosa editable por subsection.

    Determinista (sin LLM): el usuario lo pule vía PATCH. Devuelve un dict con
    claves por subsection ID (e.g. ``"intro.purpose"``) más claves legacy
    (``"intro"``, ``"overall"``) que concatenan las subsections para
    retrocompatibilidad con versiones anteriores. ``intro.references`` es una
    proyección de ``sources_summary`` (ver ``_capture_sources``): refleja el
    catálogo de fuentes vivo, no prosa redactada.
    """
    title = project_name or "Especificación de Requerimientos de Software"
    desc_line = project_description.strip() if project_description else ""

    total = coverage.get("totals", {}).get("live", live_count)
    nfr_count = coverage.get("totals", {}).get("nfr", 0)
    func_count = coverage.get("totals", {}).get("functional", 0)
    blockers = quality_summary.get("blockers", 0)
    findings = quality_summary.get("total_findings", 0)

    # --- Subsections: intro ------------------------------------------------
    intro_purpose = (
        f"Este documento especifica los requerimientos de **{title}**. "
        f"_Editor: completar el propósito del producto._"
    )
    intro_scope = "_Editor: describir el alcance del producto y qué queda fuera._"
    intro_definitions = (
        "_Editor: listar definiciones, acrónimos y términos del dominio._"
    )
    intro_references = _projected_references(sources_summary)
    intro_overview = (
        "Este documento se organiza en las siguientes secciones: "
        "Sección 1 (Introducción), Sección 2 (Descripción general), "
        "Sección 3 (Objetivos y modelo de goals), "
        "Sección 4 (Requerimientos funcionales), "
        "Sección 5 (Reglas de negocio), "
        "Sección 6 (Requerimientos no funcionales), "
        "Sección 7 (Restricciones y cumplimiento), "
        "Sección 8 (Requerimientos de interfaces y datos), "
        "y los Anexos A-C (Calidad, Cobertura y Trazabilidad)."
    )

    # --- Subsections: overall ----------------------------------------------
    overall_perspective_parts = [
        "_Editor: describir la perspectiva del producto y sus dependencias._",
    ]
    if desc_line:
        overall_perspective_parts.append(desc_line)
    counts_parts = [
        f"- Requerimientos en el SRS: **{total}** (funcionales: {func_count}, "
        f"no funcionales: {nfr_count}).",
        f"- Goals modelados: **{goals_summary.get('goals', 0)}** "
        f"(softgoals: {goals_summary.get('softgoals', 0)}, obstáculos: "
        f"{goals_summary.get('obstacles', 0)}).",
        f"- Hallazgos de calidad: **{findings}** (bloqueantes: {blockers}).",
    ]
    if sources_summary and sources_summary.get("total_files"):
        extra = ""
        if sources_summary.get("media_files"):
            extra = (
                f" (+ anexo OCR de {sources_summary['media_files']} imágenes "
                f"que respaldan {sources_summary['media_reqs']} requerimientos)"
            )
        counts_parts.append(
            f"- Fuentes de captura: **{len(sources_summary['documents'])}** "
            f"documento(s){extra}."
        )
    overall_perspective_parts.append(
        "\n**Resumen del alcance especificado:**\n" + "\n".join(counts_parts)
    )
    overall_perspective = "\n\n".join(overall_perspective_parts)

    # Deterministic: funcionalidades agrupadas por goal funcional (sección 2.2).
    # Cada ítem muestra MoSCoW + tipo; el catálogo formal vive en secciones 4-8.
    if live_items:
        func_items = [
            it for it in live_items
            if it.type.value == "functional" and it.status in _LIVE_STATUSES
        ]
        if func_items:
            linked: set[int] = set()
            features_lines: list[str] = []
            for goal_code, goal_stmt, gitems in goal_groups or []:
                features_lines.append(f"**`{goal_code}`** — {goal_stmt}")
                features_lines.append("")
                for it in gitems:
                    linked.add(id(it))
                    features_lines.append(_feature_line(it))
                features_lines.append("")
            unlinked = [it for it in func_items if id(it) not in linked]
            if unlinked:
                features_lines.append("**Sin goal asociado**")
                features_lines.append("")
                for it in unlinked:
                    features_lines.append(_feature_line(it))
            overall_features = "\n".join(features_lines).strip()
        else:
            overall_features = "_Sin requerimientos funcionales para listar._"
    else:
        overall_features = "_Sin requerimientos funcionales para listar._"

    # Con catálogo de actores, el fallback determinista ya lista los roles
    # (sin LLM): el texto solo queda pendiente de privilegios/frecuencia.
    if actors_block_text:
        actor_lines = [
            line
            for line in actors_block_text.splitlines()
            if line.startswith("- ")
        ]
        overall_users = (
            "Actores definidos durante la captura:\n"
            + "\n".join(actor_lines)
            + "\n\n_Editor: completar frecuencia de uso, privilegios y "
            "nivel de experiencia de cada actor._"
        )
    else:
        overall_users = (
            "_Editor: describir las clases de usuario, su frecuencia de uso, "
            "privilegios y nivel de experiencia._"
        )
    overall_environment = (
        "_Editor: describir el entorno operativo (plataforma, sistema "
        "operativo, navegadores, integraciones)._"
    )
    overall_assumptions = (
        "_Editor: listar supuestos y dependencias del proyecto._"
    )

    # --- Build subsection-keyed narrative ----------------------------------
    narrative: dict[str, str] = {
        "intro.purpose": intro_purpose,
        "intro.scope": intro_scope,
        "intro.definitions": intro_definitions,
        "intro.references": intro_references,
        "intro.overview": intro_overview,
        "overall.perspective": overall_perspective,
        "overall.features": overall_features,
        "overall.users": overall_users,
        "overall.environment": overall_environment,
        "overall.assumptions": overall_assumptions,
    }

    # --- Legacy aggregated keys for backward compatibility -----------------
    narrative["intro"] = "\n\n".join(
        narrative[k]
        for k in [
            "intro.purpose",
            "intro.scope",
            "intro.definitions",
            "intro.references",
            "intro.overview",
        ]
    )
    narrative["overall"] = "\n\n".join(
        narrative[k]
        for k in [
            "overall.perspective",
            "overall.features",
            "overall.users",
            "overall.environment",
            "overall.assumptions",
        ]
    )

    return narrative


# ---------------------------------------------------------------------------
# Draft narrativo asistido por LLM (8 secciones authored).
# ---------------------------------------------------------------------------


async def _rag_context_for_section(project_id: int, section_type: str) -> str:
    """Busca en los documentos de captura contexto relevante para una sección.

    Cada sección tiene una query adaptada a su propósito; ``used_in_capture_only``
    restringe a los documentos que originaron los requerimientos vivos (no
    documentos sueltos del proyecto sin capturar).
    """
    queries = {
        "purpose": "propósito objetivo producto sistema",
        "scope": "alcance del producto límites",
        "definitions": "términos técnicos definiciones glosario acrónimos",
        "perspective": "contexto del producto dependencias integraciones",
        "users": "roles de usuario tipos de usuario",
        "environment": "entorno operativo plataforma tecnologías",
        "assumptions": "supuestos dependencias del proyecto",
    }
    query = queries.get(section_type, section_type)
    try:
        hits = await retrieval.search(
            project_id, query, top_k=3, used_in_capture_only=True
        )
    except Exception:  # noqa: BLE001 — RAG es best-effort
        logger.warning(
            "_rag_context_for_section: retrieval failed for %s",
            section_type,
            exc_info=True,
        )
        return ""
    if not hits:
        return ""
    return "\n".join(
        f"[{h.section_path} p.{h.page}] {h.text[:300]}" for h in hits
    )


async def _build_narrative_context(
    project_id: int,
    project_name: str,
    project_description: str,
    live_items: list,
    quality_summary: dict[str, Any],
    coverage: dict[str, Any],
    goals_summary: dict[str, Any],
) -> str:
    """Construye el mensaje de usuario para la llamada LLM narrativa.

    Incluye: nombre y descripción del proyecto, top-20 requerimientos vivos
    ordenados por prioridad (MUST primero), métricas agregadas y fragmentos
    RAG por tipo de sección.
    """
    parts: list[str] = []
    parts.append(f"# Proyecto: {project_name or '(sin nombre)'}")
    if project_description:
        parts.append(f"\n## Descripción del proyecto\n{project_description}")

    # Reglas persistentes del proyecto (scope srs + all): consideraciones
    # duraderas del usuario que dirigen la redacción. Best-effort — si el
    # harness no carga, la narrativa sigue sin reglas.
    try:
        from backend.database import AsyncSessionLocal
        from backend.models.project_rule import RuleScope
        from backend.services import project_rules_store

        async with AsyncSessionLocal() as session:
            rules_block = await project_rules_store.rules_block_for(
                session, project_id, RuleScope.SRS
            )
        if rules_block:
            parts.append("\n## Reglas del proyecto\n" + rules_block)
    except Exception:  # noqa: BLE001 — best-effort
        import logging

        logging.getLogger(__name__).warning(
            "narrative: no se pudieron cargar las reglas del proyecto",
            exc_info=True,
        )

    # Catálogo de actores definido en la captura: §2.4 (usuarios) se ancla a
    # estos roles en vez de re-inferirlos por RAG. Best-effort igual que
    # reglas — si el catálogo no existe o el harness falla, la narrativa
    # sigue y el redactor vuelve a inferir de fragmentos.
    try:
        from backend.database import AsyncSessionLocal
        from backend.services import actor_store

        async with AsyncSessionLocal() as session:
            actors_block = await actor_store.actors_block(session, project_id)
        if actors_block:
            parts.append(
                "\n## Actores del proyecto (catálogo definido en la captura)\n"
                + actors_block
            )
    except Exception:  # noqa: BLE001 — best-effort
        import logging

        logging.getLogger(__name__).warning(
            "narrative: no se pudo cargar el catálogo de actores",
            exc_info=True,
        )

    # Top-20 requerimientos por prioridad.
    parts.append("\n## Requerimientos (muestra)")
    sorted_items = sorted(
        live_items,
        key=lambda it: _PRIORITY_RANK.get(
            getattr(it, "priority", Priority.WONT), 4
        ),
    )
    for it in sorted_items[:20]:
        stmt = (it.statement or "")[:150]
        prio = getattr(getattr(it, "priority", None), "value", "?")
        parts.append(f"- [{it.code}] ({prio}): {stmt}")

    # Métricas.
    totals = coverage.get("totals", {})
    parts.append("\n## Métricas")
    parts.append(f"- Total requerimientos vivos: {totals.get('live', len(live_items))}")
    parts.append(f"- Funcionales: {totals.get('functional', 0)}")
    parts.append(f"- No funcionales: {totals.get('nfr', 0)}")
    parts.append(f"- Goals modelados: {goals_summary.get('goals', 0)}")
    parts.append(f"- Hallazgos de calidad: {quality_summary.get('total_findings', 0)}")

    # Contexto RAG por sección (§1.4 Referencias no se redacta: es
    # proyección determinista del catálogo de fuentes).
    parts.append("\n## Fragmentos de documentos fuente por sección")
    for section in (
        "purpose",
        "scope",
        "definitions",
        "perspective",
        "users",
        "environment",
        "assumptions",
    ):
        ctx = await _rag_context_for_section(project_id, section)
        if ctx:
            parts.append(f"\n### {section}\n{ctx}")

    return "\n".join(parts)


async def draft_narrative_llm(
    narrative: dict[str, str],
    *,
    project_id: int,
    project_name: str,
    project_description: str,
    live_items: list,
    quality_summary: dict[str, Any],
    coverage: dict[str, Any],
    goals_summary: dict[str, Any],
    instructions: str | None = None,
    previous_narrative: dict[str, str] | None = None,
) -> dict[str, str]:
    """Enriquece la narrativa determinista con prosa generada por LLM.

    Sobrescribe las 7 subsecciones authored (purpose, scope, definitions,
    perspective, users, environment, assumptions) con texto del LLM,
    preservando las claves deterministas (``intro.overview``,
    ``intro.references`` y ``overall.features``). El bloque de conteos de
    ``overall.perspective`` se reapende después del texto del LLM para
    mantener el resumen de alcance.

    ``instructions`` (opcional) son indicaciones narrativas del usuario
    (p. ej. "incorporar el carácter multi-industria en propósito y alcance").
    Sin este canal las indicaciones conversacionales NUNCA llegan al
    redactor: el prompt se arma solo desde store + RAG.

    ``previous_narrative`` (opcional) es el texto authored de la versión
    anterior (runs sembrados): se incluye como base a REVISAR, para que con
    instrucciones el redactor preserve verbatim lo no alcanzado por ellas en
    vez de re-draftar de cero (sesión 53 v11: sin la base, reemplazó prosa
    curada por notas provisionales).

    Si la llamada LLM falla, devuelve ``narrative`` sin cambios (la base que
    el caller pasó: carry-forward en runs sembrados, determinista puro en
    pipeline completo).
    """
    try:
        user_msg = await _build_narrative_context(
            project_id,
            project_name,
            project_description,
            live_items,
            quality_summary,
            coverage,
            goals_summary,
        )
        if previous_narrative:
            parts = [
                "\n\n## NARRATIVA AUTHORED ACTUAL "
                "(texto vigente de la versión anterior; es tu base)"
            ]
            for key in (
                "intro.purpose",
                "intro.scope",
                "intro.definitions",
                "overall.perspective",
                "overall.users",
                "overall.environment",
                "overall.assumptions",
            ):
                text = previous_narrative.get(key)
                if not (isinstance(text, str) and text.strip()):
                    continue
                if key == "overall.perspective":
                    text, _ = _split_counts_block(text)
                parts.append(f"\n### {key}\n{text.strip()}")
            parts.append(
                "\nEl texto anterior ya está curado: REVÍSALO aplicando las "
                "indicaciones y preserva VERBATIM todo lo que estas no pidan "
                "cambiar. NO emitas notas provisionales ni marcadores de "
                "reemplazo."
            )
            user_msg += "\n".join(parts)
        if instructions:
            user_msg += (
                "\n\n## INDICACIONES DEL USUARIO SOBRE LA NARRATIVA "
                "(prioridad maxima)\n"
                + instructions.strip()
                + "\n\nIncorpora estas indicaciones en las subsecciones que "
                "correspondan, respetando el resto del contexto y sin inventar "
                "hechos sin respaldo."
            )
        runner = structured_llm(SrsNarrativeDraft)
        draft = await runner.ainvoke(
            [("system", _NARRATIVE_SYSTEM), ("user", user_msg)]
        )
    except Exception:  # noqa: BLE001 — fallback graceful
        logger.warning(
            "draft_narrative_llm: fallo LLM, usando narrativa determinista",
            exc_info=True,
        )
        return narrative

    # Extraer el bloque de conteos de la perspectiva determinista.
    _, counts_block = _split_counts_block(
        narrative.get("overall.perspective", "")
    )

    updated = dict(narrative)
    # 7 subsecciones authored -> prosa LLM. §1.4 Referencias queda con la
    # proyección determinista que trae ``narrative`` (catálogo vivo).
    updated["intro.purpose"] = draft.purpose
    updated["intro.scope"] = draft.scope
    updated["intro.definitions"] = draft.definitions
    updated["overall.perspective"] = draft.perspective + (
        "\n\n" + counts_block if counts_block else ""
    )
    updated["overall.users"] = draft.users
    updated["overall.environment"] = draft.environment
    updated["overall.assumptions"] = draft.assumptions

    # Preservar deterministicos: intro.overview, intro.references,
    # overall.features (ya vienen en ``updated`` desde ``narrative``).

    # Reconstruir claves legacy.
    updated["intro"] = "\n\n".join(
        updated[k]
        for k in [
            "intro.purpose",
            "intro.scope",
            "intro.definitions",
            "intro.references",
            "intro.overview",
        ]
    )
    updated["overall"] = "\n\n".join(
        updated[k]
        for k in [
            "overall.perspective",
            "overall.features",
            "overall.users",
            "overall.environment",
            "overall.assumptions",
        ]
    )
    return updated


async def _functional_goal_groups(
    session: AsyncSession,
    project_id: int,
    live_items: list,
) -> list[tuple[str, str, list]]:
    """Agrupa requerimientos funcionales vivos bajo su goal funcional.

    Devuelve ``(goal_code, goal_statement, items)`` para cada goal funcional
    que tenga al menos un requerimiento funcional vivo enlazado (relación
    ``realizes``/``contributes``). Los ítems sin goal quedan fuera; la sección
    2.2 los lista bajo "Sin goal asociado" en ``_draft_narrative``.
    """
    goals = list(
        await session.scalars(
            select(Goal).where(
                Goal.project_id == project_id,
                Goal.kind == "functional_goal",
            )
        )
    )
    if not goals:
        return []
    links = list(
        await session.scalars(
            select(GoalLink).where(
                GoalLink.goal_id.in_([g.id for g in goals])
            )
        )
    )
    live_by_id = {it.id: it for it in live_items if it.id is not None}
    groups: list[tuple[str, str, list]] = []
    for g in goals:
        gitems = []
        for gl in links:
            if gl.goal_id != g.id:
                continue
            it = live_by_id.get(gl.req_id)
            if it is None:
                continue
            if it.type.value != "functional" or it.status not in _LIVE_STATUSES:
                continue
            gitems.append(it)
        if gitems:
            groups.append((g.code, g.statement, gitems))
    return groups


async def assemble_srs(
    session: AsyncSession,
    project_id: int,
    *,
    project_name: str = "",
    project_description: str = "",
) -> dict[str, Any]:
    """Genera un SRS candidato y devuelve el payload para ``create_srs``.

    Orden: quality -> goals -> coverage -> traceability -> markdown. Cada motor
    lee el estado anterior (coverage lee los goals que infer_goals persistió).
    Los hallazgos de calidad + cobertura se fusionan y se persisten al final.
    """
    # 1. Calidad (programática + LLM).
    quality_summary, q_findings = await analyze_quality(session, project_id)

    # 2. Goals (LLM; persiste goals + links).
    goals_summary = await infer_goals(session, project_id)

    # 3. Cobertura (programática; lee reqs + goals).
    coverage, cov_findings = await compute_coverage(session, project_id)

    # 4. Persistir hallazgos (calidad + cobertura).
    await replace_findings(session, project_id, q_findings + cov_findings)

    # 5. Trazabilidad (lee goals + links).
    traceability = await build_traceability(session, project_id)

    # 6. Items + códigos vivos.
    items = await list_requirements(session, project_id, include_deleted=True)
    live = [it for it in items if it.status in _LIVE_STATUSES]
    codes = [it.code for it in live]

    # 7. Narrativa editable (borrador con subsections).
    # Agrupación de funcionales por goal para la sección 2.2 (overview).
    goal_groups = await _functional_goal_groups(session, project_id, live)

    # Fallback determinista de §2.4 anclado al catálogo de actores (si hay).
    from backend.services import actor_store

    actors_block_text = await actor_store.actors_block(session, project_id)
    # §1.4 Referencias proyectada desde el catálogo de fuentes vivo.
    sources_summary = await _capture_sources(session, project_id)

    narrative = _draft_narrative(
        project_name,
        project_description,
        quality_summary,
        coverage,
        goals_summary,
        len(live),
        live_items=live,
        goal_groups=goal_groups,
        actors_block_text=actors_block_text,
        sources_summary=sources_summary,
    )

    # 8. Cuerpo Markdown (proyección con narrative + structure).
    built = await build_srs(
        session,
        project_id,
        project_name=project_name,
        project_description=project_description,
        narrative=narrative,
        structure=SRS_STRUCTURE,
    )

    return {
        "structure": SRS_STRUCTURE,
        "narrative": narrative,
        "markdown": built["markdown"],
        "quality_summary": {**quality_summary, "goals": goals_summary},
        "coverage": coverage,
        "traceability": traceability,
        "requirement_codes": codes,
        "requirement_count": len(live),
        "generated_by": "agent",
    }
