"""Build a Software Requirements Specification (SRS) Markdown from the
structured RequirementItem store.

The SRS is GENERATED, never hand-written by the LLM. Source of truth is the
RequirementItem table; this module is a pure projection to Markdown. Rationale
(cláusula 10 del plan y CLAUDE.md §6): persistencia estructurada → markdown
como vista, nunca al revés.

Salida:
- Encabezado con metadata del proyecto + timestamp de generación.
- Resumen de totales por tipo y por prioridad.
- Sección por tipo (functional, performance, ...) → bloques por prioridad
  (MUST, SHOULD, COULD, WONT).
- Cada requerimiento lleva: code, statement, cita literal (source.quote),
  criteria de aceptación (Gherkin), flags (derivado / sin cita).
- Sección final de conflictos no resueltos (contradicts/depends_on propuestos).
- Items UNVERIFIED / REJECTED / MERGED / SUPERSEDED se omiten del cuerpo
  principal (no son SRS confirmed). MERGED/SUPERSEDED se listan al final como
  notas de auditoría (trazabilidad de fusiones/splits).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.requirement import (
    Priority,
    ReqStatus,
    ReqType,
    RequirementItem,
    RequirementRelation,
    RelationKind,
)
from backend.services.requirement_store import _source_list, list_requirements
from sqlalchemy import select


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
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
# Main builder                                                                #
# --------------------------------------------------------------------------- #

async def build_srs(
    session: AsyncSession,
    project_id: int,
    *,
    project_name: str = "",
    project_description: str = "",
) -> dict[str, Any]:
    """Generate the SRS Markdown for a project.

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

    # Índice id → code para referenciar relaciones.
    id_to_code = {it.id: it.code for it in items}

    # ----- Counts ----------------------------------------------------------
    by_type: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for it in live:
        by_type[it.type.value] = by_type.get(it.type.value, 0) + 1
        by_priority[it.priority.value] = by_priority.get(it.priority.value, 0) + 1

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
    lines += [
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
        lines.append(f"| {_PRIORITY_LABELS[p]} | {by_priority.get(p.value, 0)} |")

    # ----- Cuerpo: agrupado por tipo → prioridad --------------------------
    by_type_bucket: dict[ReqType, dict[Priority, list[RequirementItem]]] = {}
    for it in live:
        by_type_bucket.setdefault(it.type, {}).setdefault(it.priority, []).append(it)

    lines += ["", "## Detalle por tipo", ""]
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
                lines.append(f"##### `{it.code}`")
                lines.append("")
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

    # ----- Conflictos abiertos --------------------------------------------
    if open_relations:
        lines += [
            "## Conflictos y dependencias abiertos",
            "",
            "Pares marcados por el agente pendientes de resolución humana.",
            "",
        ]
        for rel in open_relations:
            a = id_to_code.get(rel.from_id, f"#{rel.from_id}")
            b = id_to_code.get(rel.to_id, f"#{rel.to_id}")
            kind_label = (
                "contradictorios" if rel.kind == RelationKind.CONTRADICTS
                else "con dependencia"
            )
            lines.append(f"- `{a}` y `{b}` son **{kind_label}**")
            if rel.note:
                lines.append(f"  - _{rel.note}_")
        lines.append("")

    # ----- Auditoría: soft-deletes ----------------------------------------
    if soft_deleted:
        lines += [
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
                target = id_to_code.get(
                    it.superseded_by, f"#{it.superseded_by}"
                )
                reason = f"Reemplazado por `{target}`"
            elif it.status == ReqStatus.REJECTED:
                reason = "Rechazado"
            elif it.status == ReqStatus.UNVERIFIED:
                reason = "Cita no verificada"
            lines.append(f"| `{it.code}` | {it.status.value} | {reason} |")
        lines.append("")

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
