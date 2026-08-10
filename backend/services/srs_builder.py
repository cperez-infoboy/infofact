"""Build a Software Requirements Specification (SRS) Markdown from the
structured RequirementItem store.

The SRS is GENERATED, never hand-written by the LLM. Source of truth is the
RequirementItem table; this module is a pure projection to Markdown. Rationale
(cláusula 10 del plan y CLAUDE.md §6): persistencia estructurada → markdown
como vista, nunca al revés.

Salida (alineada a ISO/IEC/IEEE 29148:2018):
- Secciones authored (narrativa editable) tejidas con secciones projected
  (requerimientos, goals) en el orden definido por ``structure``.
- Cada requerimiento lleva: code, statement, cita literal (source.quote),
  criteria de aceptación (Gherkin), flags (derivado / sin cita).
- Back-matter: resumen de totales, conflictos no resueltos, auditoría.
- Items UNVERIFIED / REJECTED / MERGED / SUPERSEDED se omiten del cuerpo
  principal. MERGED/SUPERSEDED se listan al final como notas de auditoría.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.requirement import (
    Priority,
    ReqStatus,
    ReqType,
    RequirementItem,
    RequirementRelation,
    RelationKind,
)
from backend.models.srs import Goal, GoalLink
from backend.services.requirement_store import _source_list, list_requirements


# --------------------------------------------------------------------------- #
# Document structure (ISO/IEC/IEEE 29148:2018 + Volere)                       #
# --------------------------------------------------------------------------- #

# Árbol de secciones del SRS. ``kind``: "authored" = prosa editable por el
# usuario; "projected" = proyección de RequirementItem / goals (solo lectura).
# ``subsections`` (opcional): subdivisions dentro de una sección authored.
SRS_STRUCTURE: list[dict[str, Any]] = [
    {
        "id": "intro",
        "title": "1. Introducción",
        "kind": "authored",
        "subsections": [
            {"id": "intro.purpose", "title": "1.1 Propósito"},
            {"id": "intro.scope", "title": "1.2 Alcance"},
            {
                "id": "intro.definitions",
                "title": "1.3 Definiciones, acrónimos y glosario",
            },
            {"id": "intro.references", "title": "1.4 Referencias"},
            {"id": "intro.overview", "title": "1.5 Visión general del documento"},
        ],
    },
    {
        "id": "overall",
        "title": "2. Descripción general",
        "kind": "authored",
        "subsections": [
            {
                "id": "overall.perspective",
                "title": "2.1 Perspectiva del producto",
            },
            {
                "id": "overall.features",
                "title": "2.2 Funcionalidades del producto",
            },
            {
                "id": "overall.users",
                "title": "2.3 Clases y características de usuarios",
            },
            {"id": "overall.environment", "title": "2.4 Entorno operativo"},
            {
                "id": "overall.assumptions",
                "title": "2.5 Supuestos y dependencias",
            },
        ],
    },
    {"id": "goals", "title": "3. Objetivos y modelo de goals", "kind": "projected"},
    {"id": "functional", "title": "4. Requerimientos funcionales", "kind": "projected"},
    {"id": "business_rules", "title": "5. Reglas de negocio", "kind": "projected"},
    {"id": "nfr", "title": "6. Requerimientos no funcionales", "kind": "projected"},
    {"id": "constraints", "title": "7. Restricciones y cumplimiento", "kind": "projected"},
    {
        "id": "data",
        "title": "8. Requerimientos de interfaces y datos",
        "kind": "projected",
    },
    {"id": "quality", "title": "Anexo A. Análisis de calidad", "kind": "projected"},
    {
        "id": "coverage",
        "title": "Anexo B. Cobertura (ISO/IEC 25010)",
        "kind": "projected",
    },
    {
        "id": "traceability",
        "title": "Anexo C. Matriz de trazabilidad",
        "kind": "projected",
    },
]

# Maps projected section IDs to the ReqTypes that belong in that section.
SECTION_REQTYPE_MAP: dict[str, set[ReqType]] = {
    "functional": {ReqType.FUNCTIONAL},
    "business_rules": {ReqType.PROCESS},
    "nfr": {
        ReqType.PERFORMANCE,
        ReqType.SECURITY,
        ReqType.USABILITY,
        ReqType.RELIABILITY,
        ReqType.MAINTAINABILITY,
    },
    "constraints": {ReqType.CONSTRAINT, ReqType.COMPLIANCE},
    "data": {ReqType.DATA},
}

# Sub-labels for NFR section grouping (keeps per-type granularity).
_NFR_SUBLABELS: dict[ReqType, str] = {
    ReqType.PERFORMANCE: "Rendimiento",
    ReqType.SECURITY: "Seguridad",
    ReqType.USABILITY: "Usabilidad",
    ReqType.RELIABILITY: "Confiabilidad",
    ReqType.MAINTAINABILITY: "Mantenibilidad",
}

# Goal kind labels for the goals section.
_GOAL_KIND_LABELS: dict[str, str] = {
    "functional_goal": "Goals funcionales",
    "softgoal": "Softgoals",
    "obstacle": "Obstáculos",
}

# Link relation labels for the goals section.
_LINK_RELATION_LABELS: dict[str, str] = {
    "realizes": "Realiza",
    "contributes": "Contribuye a",
    "conflicts": "Conflicta con",
}

# Annex section IDs — stored as structured JSON in SrsDocument, not in
# the markdown body (the frontend renders them in dedicated tabs).
_ANNEX_IDS = frozenset({"quality", "coverage", "traceability"})


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

_TYPE_LABELS: dict[ReqType, str] = {
    ReqType.FUNCTIONAL: "Requerimientos funcionales",
    ReqType.PERFORMANCE: "Rendimiento",
    ReqType.SECURITY: "Seguridad",
    ReqType.USABILITY: "Usabilidad",
    ReqType.RELIABILITY: "Confiabilidad",
    ReqType.MAINTAINABILITY: "Mantenibilidad",
    ReqType.COMPLIANCE: "Cumplimiento normativo",
    ReqType.CONSTRAINT: "Restricciones técnicas",
    ReqType.PROCESS: "Proceso",
    ReqType.DATA: "Datos e integraciones",
}

_PRIORITY_ORDER: tuple[Priority, ...] = (
    Priority.MUST,
    Priority.SHOULD,
    Priority.COULD,
    Priority.WONT,
)

_PRIORITY_LABELS: dict[Priority, str] = {
    Priority.MUST: "Must (obligatorio)",
    Priority.SHOULD: "Should (deseable)",
    Priority.COULD: "Could (opcional)",
    Priority.WONT: "Wont (fuera de alcance)",
}

# Estados que aparecen en el cuerpo principal del SRS.
_LIVE_STATUSES: frozenset[ReqStatus] = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)


def _fmt_source(item: RequirementItem) -> str:
    """Cita literal con referencia de sección/página; '' si no hay source.

    ``item.source`` se guarda como dict, **lista de dicts** (tras un merge de
    varios spans) o None. ``_source_list`` normaliza a una lista; formateamos
    cada fuente y unimos las múltiples con salto de línea.
    """
    blocks: list[str] = []
    for s in _source_list(item):
        if not isinstance(s, dict):
            continue
        quote = s.get("quote") or ""
        section = s.get("section") or ""
        page = s.get("page")
        ref_bits: list[str] = []
        if section:
            ref_bits.append(str(section))
        if page is not None:
            ref_bits.append(f"p.{page}")
        ref = ", ".join(ref_bits)
        if quote and ref:
            blocks.append(f'> "{quote}" ({ref})')
        elif quote:
            blocks.append(f'> "{quote}"')
        elif ref:
            blocks.append(f"> ({ref})")
    return "\n".join(blocks)


def _fmt_criteria(item: RequirementItem) -> str:
    criteria = list(item.acceptance_criteria or [])
    if not criteria:
        return ""
    return "\n".join(f"- {c}" for c in criteria)


def _flags_label(item: RequirementItem) -> str:
    flags: list[str] = []
    if item.derived:
        flags.append("derivado")
    if not item.span_verified:
        flags.append("sin cita verificada")
    if not item.explicit:
        flags.append("implícito")
    return ", ".join(flags)


# --------------------------------------------------------------------------- #
# Section renderers                                                            #
# --------------------------------------------------------------------------- #


def _render_authored(
    section: dict[str, Any],
    narrative: dict[str, str] | None,
) -> list[str]:
    """Render an authored section, weaving subsection text from narrative."""
    narrative = narrative or {}
    sid = section["id"]
    subsections = section.get("subsections")

    lines: list[str] = [f"## {section['title']}", ""]

    if subsections:
        # Check if narrative has subsection-level keys (new format).
        has_sub_keys = any(s["id"] in narrative for s in subsections)

        if has_sub_keys:
            for sub in subsections:
                lines += [f"### {sub['title']}", ""]
                text = narrative.get(sub["id"], "")
                if text:
                    lines += [text, ""]
                else:
                    lines += [
                        "_Sin contenido. Completar desde el editor._",
                        "",
                    ]
        else:
            # Legacy: narrative has a section-level key → render as block.
            text = narrative.get(sid, "")
            if text:
                lines += [text, ""]
            else:
                # No narrative at all: render subsection headers with
                # placeholders so the structure is visible.
                for sub in subsections:
                    lines += [
                        f"### {sub['title']}",
                        "",
                        "_Sin contenido. Completar desde el editor._",
                        "",
                    ]
    else:
        text = narrative.get(sid, "")
        if text:
            lines += [text, ""]

    return lines


def _render_goals(
    goals: list[Goal],
    goal_links: list[GoalLink],
    id_to_code: dict[int, str],
) -> list[str]:
    """Render the goals section (GORE model: KAOS + i*/Tropos + NFR)."""
    lines: list[str] = ["## 3. Objetivos y modelo de goals", ""]

    if not goals:
        lines += ["_Sin goals inferidos._", ""]
        return lines

    # Build link index: goal_id -> list of (relation, req_code).
    link_index: dict[int, list[tuple[str, str]]] = {}
    for gl in goal_links:
        req_code = id_to_code.get(gl.req_id, f"#{gl.req_id}")
        link_index.setdefault(gl.goal_id, []).append(
            (gl.relation.value, req_code)
        )

    # Group goals by kind in canonical order.
    by_kind: dict[str, list[Goal]] = {}
    for g in goals:
        by_kind.setdefault(g.kind.value, []).append(g)

    for kind_val in ("functional_goal", "softgoal", "obstacle"):
        group = by_kind.get(kind_val)
        if not group:
            continue
        label = _GOAL_KIND_LABELS.get(kind_val, kind_val)
        lines += [f"### {label}", ""]
        for g in group:
            lines.append(f"- `{g.code}` — {g.statement}")
            links = link_index.get(g.id, [])
            for rel, code in links:
                rel_label = _LINK_RELATION_LABELS.get(rel, rel)
                lines.append(f"  - {rel_label}: `{code}`")
            if g.rationale:
                lines.append(f"  - _{g.rationale}_")
        lines.append("")

    return lines


def _render_single_item(it: RequirementItem) -> list[str]:
    """Render a single requirement item as Markdown lines."""
    lines: list[str] = [f"#### `{it.code}`", ""]
    lines.append(it.statement)
    lines.append("")
    src = _fmt_source(it)
    if src:
        lines += [src, ""]
    crit = _fmt_criteria(it)
    if crit:
        lines += ["**Criterios de aceptación:**", "", crit, ""]
    flags = _flags_label(it)
    if flags:
        lines.append(f"_{flags}_")
        lines.append("")
    return lines


def _render_items_by_priority(items: list[RequirementItem]) -> list[str]:
    """Render items grouped by priority (Must → Should → Could → Wont)."""
    lines: list[str] = []
    by_priority: dict[Priority, list[RequirementItem]] = {}
    for it in items:
        by_priority.setdefault(it.priority, []).append(it)

    for p in _PRIORITY_ORDER:
        bucket = by_priority.get(p)
        if not bucket:
            continue
        lines += [f"### {_PRIORITY_LABELS[p]}", ""]
        for it in bucket:
            lines += _render_single_item(it)
    return lines


def _render_requirements(
    title: str,
    section_id: str,
    items: list[RequirementItem],
) -> list[str]:
    """Render a projected requirements section."""
    lines: list[str] = [f"## {title}", ""]

    if not items:
        lines += ["_Sin requerimientos en esta sección._", ""]
        return lines

    if section_id == "nfr":
        # Group NFRs by subtype to preserve per-type granularity.
        for nfr_type, sublabel in _NFR_SUBLABELS.items():
            subtype_items = [it for it in items if it.type == nfr_type]
            if not subtype_items:
                continue
            lines += [f"### {sublabel}", ""]
            for it in subtype_items:
                lines += _render_single_item(it)
    else:
        lines += _render_items_by_priority(items)

    return lines


# --------------------------------------------------------------------------- #
# Back-matter renderers                                                        #
# --------------------------------------------------------------------------- #


def _render_summary(
    live: list[RequirementItem],
    by_type: dict[str, int],
    by_priority: dict[str, int],
) -> list[str]:
    lines: list[str] = [
        "---",
        "",
        "## Resumen",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Requerimientos en SRS | {len(live)} |",
        f"| Funcionales | {by_type.get('functional', 0)} |",
        f"| No funcionales | "
        f"{sum(v for k, v in by_type.items() if k != 'functional')} |",
        f"| Derivados | {sum(1 for it in live if it.derived)} |",
        f"| Sin cita verificada | {sum(1 for it in live if not it.span_verified)} |",
        "",
        "### Por prioridad",
        "",
        "| Prioridad | Cantidad |",
        "|---|---|",
    ]
    for p in _PRIORITY_ORDER:
        lines.append(
            f"| {_PRIORITY_LABELS[p]} | {by_priority.get(p.value, 0)} |"
        )
    lines.append("")
    return lines


def _render_conflicts(
    open_relations: list[RequirementRelation],
    id_to_code: dict[int, str],
) -> list[str]:
    if not open_relations:
        return []
    lines: list[str] = [
        "## Conflictos y dependencias abiertos",
        "",
        "Pares marcados por el agente pendientes de resolución humana.",
        "",
    ]
    for rel in open_relations:
        a = id_to_code.get(rel.from_id, f"#{rel.from_id}")
        b = id_to_code.get(rel.to_id, f"#{rel.to_id}")
        kind_label = (
            "contradictorios"
            if rel.kind == RelationKind.CONTRADICTS
            else "con dependencia"
        )
        lines.append(f"- `{a}` y `{b}` son **{kind_label}**")
        if rel.note:
            lines.append(f"  - _{rel.note}_")
    lines.append("")
    return lines


def _render_audit(
    soft_deleted: list[RequirementItem],
    id_to_code: dict[int, str],
) -> list[str]:
    if not soft_deleted:
        return []
    lines: list[str] = [
        "## Auditoría de cambios",
        "",
        "Ítems descartados, fusionados o reemplazados (trazabilidad).",
        "",
        "| Código | Estado final | Motivo |",
        "|---|---|---|",
    ]
    for it in soft_deleted:
        reason = ""
        if it.status == ReqStatus.MERGED and it.merged_into:
            target = id_to_code.get(it.merged_into, f"#{it.merged_into}")
            reason = f"Fusionado en `{target}`"
        elif it.status == ReqStatus.SUPERSEDED and it.superseded_by:
            target = id_to_code.get(it.superseded_by, f"#{it.superseded_by}")
            reason = f"Reemplazado por `{target}`"
        elif it.status == ReqStatus.REJECTED:
            reason = "Rechazado"
        elif it.status == ReqStatus.UNVERIFIED:
            reason = "Cita no verificada"
        lines.append(f"| `{it.code}` | {it.status.value} | {reason} |")
    lines.append("")
    return lines


# --------------------------------------------------------------------------- #
# Main builder                                                                 #
# --------------------------------------------------------------------------- #


async def build_srs(
    session: AsyncSession,
    project_id: int,
    *,
    project_name: str = "",
    project_description: str = "",
    narrative: dict[str, str] | None = None,
    structure: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate the SRS Markdown for a project.

    When ``structure`` is provided, iterates over it to weave authored
    narrative sections with projected requirement/goal sections (ISO/IEC/IEEE
    29148 layout). When ``structure`` is ``None``, falls back to the legacy
    flat rendering (all types in a single ``## Detalle por tipo`` block).

    Args:
        narrative: Subsection-keyed prose dict (e.g. ``{"intro.purpose": "..."}``).
        structure: Section tree (see ``SRS_STRUCTURE``).

    Returns:
        {"markdown": str, "generated_at": iso, "counts": {...}}
    """
    items = await list_requirements(session, project_id, include_deleted=True)
    live = [it for it in items if it.status in _LIVE_STATUSES]
    soft_deleted = [it for it in items if it.status not in _LIVE_STATUSES]

    # Relaciones no resueltas (status PROPOSED o CONFIRMED con kind != duplicate
    # porque los duplicates se resuelven vía merge y no son conflictos abiertos).
    rel_rows = await session.scalars(
        select(RequirementRelation).where(
            RequirementRelation.status.in_(["proposed", "confirmed"]),
            RequirementRelation.kind != RelationKind.DUPLICATE,
        )
    )
    open_relations = list(rel_rows)

    # Goals (para la sección de modelo de goals).
    goal_rows = await session.scalars(
        select(Goal).where(Goal.project_id == project_id)
    )
    goals = list(goal_rows)

    goal_links: list[GoalLink] = []
    if goals:
        goal_link_rows = await session.scalars(
            select(GoalLink).where(
                GoalLink.goal_id.in_([g.id for g in goals])
            )
        )
        goal_links = list(goal_link_rows)

    # Índice id → code para referenciar relaciones y goals.
    id_to_code = {it.id: it.code for it in items}

    # ----- Counts ----------------------------------------------------------
    by_type: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for it in live:
        by_type[it.type.value] = by_type.get(it.type.value, 0) + 1
        by_priority[it.priority.value] = by_priority.get(
            it.priority.value, 0
        ) + 1

    # ----- Header ----------------------------------------------------------
    gen_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    title = project_name or "Especificación de Requerimientos de Software"
    lines: list[str] = [
        f"# {title}",
        "",
        f"> Generado: {gen_at}  ·  Total en SRS: {len(live)}  ·  "
        f"Conflictos abiertos: {len(open_relations)}",
    ]
    if project_description:
        lines += ["", f"_{project_description}_"]

    # ----- Cuerpo principal ------------------------------------------------
    if structure:
        # New rendering: iterate over structure, weave authored + projected.
        for section in structure:
            sid = section["id"]
            kind = section.get("kind", "projected")

            # Skip annexes — stored as structured JSON, rendered in their
            # own frontend tabs.
            if sid in _ANNEX_IDS:
                continue

            if kind == "authored":
                lines += _render_authored(section, narrative)
            elif sid == "goals":
                lines += _render_goals(goals, goal_links, id_to_code)
            else:
                # Projected requirements section.
                reqtypes = SECTION_REQTYPE_MAP.get(sid, set())
                section_items = [it for it in live if it.type in reqtypes]
                lines += _render_requirements(
                    section["title"], sid, section_items
                )
    else:
        # Legacy fallback: flat rendering without narrative.
        lines += ["", "## Detalle por tipo", ""]
        by_type_bucket: dict[ReqType, dict[Priority, list[RequirementItem]]] = {}
        for it in live:
            by_type_bucket.setdefault(it.type, {}).setdefault(
                it.priority, []
            ).append(it)
        for t in ReqType:
            buckets = by_type_bucket.get(t)
            if not buckets:
                continue
            lines += [f"### {_TYPE_LABELS[t]}", ""]
            for p in _PRIORITY_ORDER:
                bucket = buckets.get(p)
                if not bucket:
                    continue
                lines += [f"#### {_PRIORITY_LABELS[p]}", ""]
                for it in bucket:
                    lines += _render_single_item(it)

    # ----- Back-matter -----------------------------------------------------
    lines += _render_summary(live, by_type, by_priority)
    lines += _render_conflicts(open_relations, id_to_code)
    lines += _render_audit(soft_deleted, id_to_code)

    return {
        "markdown": "\n".join(lines),
        "generated_at": gen_at,
        "counts": {
            "live": len(live),
            "soft_deleted": len(soft_deleted),
            "open_relations": len(open_relations),
            "by_type": by_type,
            "by_priority": by_priority,
        },
    }
