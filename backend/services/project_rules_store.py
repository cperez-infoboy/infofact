"""Store de ProjectRule: el harness persistente de consideraciones del proyecto.

Las reglas son la evolución durable de dos mecanismos previos que morían con
su contexto: las ``DocumentRules`` de CONVENTIONS (en memoria del run) y las
instrucciones one-shot de ``/captura <texto>`` (embebidas en la directiva y
olvidadas). Acá viven en DB, a nivel proyecto: cualquier sesión actual o
futura, y cualquier corrida de captura / análisis / SRS, fetchea las mismas.

Responsabilidades:
- CRUD soft (retire/reactivate, nunca delete físico — auditoría).
- Formateo del bloque ``PROJECT_RULES`` que se inyecta en los USER messages
  de los pipelines (mismo contrato que ``DOCUMENT_CONVENTIONS``: string vacío
  cuando no hay reglas → prompt intacto).
- Detección de conflictos al agregar: similitud coseno contra las reglas
  activas del mismo alcance. AVISA, no bloquea — el resultado de ``add_rule``
  lleva los conflicts para que el orquestador (o la UI) decida retirar la
  perdedora. Un fallo del embedder degrada a "sin conflictos" (log warning):
  agregar una regla nunca debe romperse por el modelo local.
- Export/import Markdown: vista humana editable del harness (la DB sigue
  siendo la fuente de verdad; el archivo es read-only + re-ingreso explícito).
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import select

from backend.models.project_rule import (
    ProjectRule,
    RuleScope,
    RuleSource,
    RuleStatus,
)

logger = logging.getLogger(__name__)

# Mismo umbral que la dedup de consolidación (0.85): por encima, dos reglas
# dicen sustancialmente lo mismo y deben resolverse (retirar una), no convivir.
RULE_CONFLICT_THRESHOLD = 0.85

RULES_BLOCK_HEADER = (
    "PROJECT_RULES (persistent project considerations — honor them from now on):"
)

# Etiquetas del export Markdown (orden estable por sección).
_SCOPE_LABELS: dict[RuleScope, str] = {
    RuleScope.CAPTURE: "Captura",
    RuleScope.ANALYSIS: "Análisis",
    RuleScope.SRS: "SRS",
    RuleScope.ALL: "Todas",
}
_LABEL_TO_SCOPE: dict[str, RuleScope] = {v: k for k, v in _SCOPE_LABELS.items()}

_SOURCE_LABELS: dict[RuleSource, str] = {
    RuleSource.USER: "usuario",
    RuleSource.AGENT: "agente",
    RuleSource.CONVENTIONS: "convenciones",
}
_LABEL_TO_SOURCE: dict[str, RuleSource] = {v: k for k, v in _SOURCE_LABELS.items()}

_STATUS_LABELS: dict[RuleStatus, str] = {
    RuleStatus.ACTIVE: "activa",
    RuleStatus.RETIRED: "retirada",
}
_LABEL_TO_STATUS: dict[str, RuleStatus] = {v: k for k, v in _STATUS_LABELS.items()}

# Entrada: "- [activa] (R-3, usuario) Contenido de la regla."
_ENTRY_RE = re.compile(
    r"^- \[(?P<status>activa|retirada)\] "
    r"\((?:R-(?P<rid>\d+), )?(?P<source>usuario|agente|convenciones)\) "
    r"(?P<content>.+)$"
)


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------


async def list_rules(
    session,
    project_id: int,
    *,
    scope: RuleScope | None = None,
    include_retired: bool = False,
) -> list[ProjectRule]:
    """Reglas del proyecto, orden estable por id (orden de creación).

    ``scope`` filtra por alcance exacto; ``include_retired`` suma las retiradas
    (solo auditoría/UI — la inyección usa siempre las activas).
    """
    q = select(ProjectRule).where(ProjectRule.project_id == project_id)
    if scope is not None:
        q = q.where(ProjectRule.scope == scope)
    if not include_retired:
        q = q.where(ProjectRule.status == RuleStatus.ACTIVE)
    q = q.order_by(ProjectRule.id)
    return list((await session.scalars(q)).all())


async def rules_block_for(
    session, project_id: int, scope: RuleScope
) -> str:
    """Bloque ``PROJECT_RULES`` listo para inyectar en un USER message.

    Incluye las activas del alcance pedido MÁS las de ``all``. String vacío
    cuando no hay nada — el prompt del caller queda byte-idéntico al histórico.
    """
    rules = await list_rules(session, project_id, scope=scope)
    rules += await list_rules(session, project_id, scope=RuleScope.ALL)
    rules.sort(key=lambda r: r.id)
    return format_rules_block(rules)


def format_rules_block(rules: list[ProjectRule]) -> str:
    """Formatea el bloque de inyección (solo ``content`` — nunca ``reason``)."""
    active = [r for r in rules if r.status is RuleStatus.ACTIVE and r.content]
    if not active:
        return ""
    lines = [RULES_BLOCK_HEADER]
    lines.extend(f"- {r.content.strip()}" for r in active)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Escritura (soft)
# ---------------------------------------------------------------------------


async def add_rule(
    session,
    project_id: int,
    *,
    scope: RuleScope,
    content: str,
    reason: str | None = None,
    source: RuleSource = RuleSource.USER,
    check_conflicts: bool = True,
) -> dict:
    """Crea una regla activa y devuelve ``{"rule", "conflicts"}``.

    Los conflictos NO bloquean la creación: se devuelven como evidencia
    (``[{"rule_id", "content", "similarity"}]``) para que el caller decida —
    el orquestador retira la perdedora, la UI la muestra. Sin dead-ends.

    ``check_conflicts=False`` salta el embedder (path de CONVENTIONS, que ya
    deduplica por contenido exacto y corre en lote).
    """
    content = content.strip()
    if not content:
        raise ValueError("content vacío")
    rule = ProjectRule(
        project_id=project_id,
        scope=scope,
        content=content,
        reason=(reason or None),
        source=source,
        status=RuleStatus.ACTIVE,
    )
    session.add(rule)
    await session.flush()
    # Refresh carga los server defaults (created_at) para que el caller pueda
    # serializar la regla después de cerrar la sesión.
    await session.refresh(rule)

    conflicts: list[dict] = []
    if check_conflicts:
        conflicts = await find_conflicts(session, project_id, scope, content)
        # La regla recién creada participa del set activo: excluirla.
        conflicts = [c for c in conflicts if c["rule_id"] != rule.id]
    return {"rule": rule, "conflicts": conflicts}


async def find_conflicts(
    session,
    project_id: int,
    scope: RuleScope,
    content: str,
    *,
    threshold: float = RULE_CONFLICT_THRESHOLD,
) -> list[dict]:
    """Similitud coseno del contenido contra las reglas activas del alcance.

    Usa el embedder local de consolidación (vectores L2-normalizados → coseno
    = producto punto). Un fallo del embedder degrada a lista vacía con warning:
    la detección de conflictos es best-effort, nunca un requisito para agregar.
    """
    candidates = await list_rules(session, project_id, scope=scope)
    candidates += await list_rules(session, project_id, scope=RuleScope.ALL)
    candidates = [r for r in candidates if r.content]
    if not candidates:
        return []
    try:
        import numpy as np

        from backend.agents.pipelines.consolidation import embed_texts

        vecs = embed_texts([content] + [r.content for r in candidates])
        new_v, other_vs = vecs[0], vecs[1:]
        sims = np.dot(other_vs, new_v)
        return [
            {
                "rule_id": r.id,
                "content": r.content,
                "similarity": round(float(s), 4),
            }
            for r, s in zip(candidates, sims)
            if float(s) >= threshold
        ]
    except Exception:  # noqa: BLE001 — best-effort
        logger.warning(
            "project_rules_store: conflict check degradado (embedder falló); "
            "la regla se agrega sin verificación de duplicados",
            exc_info=True,
        )
        return []


async def set_rule_status(
    session,
    project_id: int,
    rule_id: int,
    rule_status: RuleStatus,
    *,
    note: str | None = None,
) -> ProjectRule:
    """Retira o reactiva una regla (soft). ``note`` se agrega al reason."""
    rule = await session.get(ProjectRule, rule_id)
    if rule is None or rule.project_id != project_id:
        raise KeyError(f"rule {rule_id} no existe en el proyecto {project_id}")
    rule.status = rule_status
    if note:
        suffix = f"[{rule_status.value}: {note.strip()}]"
        rule.reason = f"{rule.reason} {suffix}".strip() if rule.reason else suffix
    await session.flush()
    await session.refresh(rule)
    return rule


async def update_rule(
    session,
    project_id: int,
    rule_id: int,
    *,
    content: str | None = None,
    reason: str | None = None,
) -> ProjectRule:
    """Edita contenido o motivo de una regla existente."""
    rule = await session.get(ProjectRule, rule_id)
    if rule is None or rule.project_id != project_id:
        raise KeyError(f"rule {rule_id} no existe en el proyecto {project_id}")
    if content is not None:
        content = content.strip()
        if not content:
            raise ValueError("content vacío")
        rule.content = content
    if reason is not None:
        rule.reason = reason.strip() or None
    await session.flush()
    await session.refresh(rule)
    return rule


# ---------------------------------------------------------------------------
# Export / import Markdown (vista humana; la DB manda)
# ---------------------------------------------------------------------------


def export_rules_md(rules: list[ProjectRule], project_name: str = "") -> str:
    """Serializa el harness a Markdown editable (incluye retiradas)."""
    title = project_name or "proyecto"
    lines = [f"# Reglas del proyecto — {title}", ""]
    for scope in (RuleScope.CAPTURE, RuleScope.ANALYSIS, RuleScope.SRS, RuleScope.ALL):
        bucket = [r for r in rules if r.scope is scope]
        if not bucket:
            continue
        lines.append(f"## {_SCOPE_LABELS[scope]}")
        for r in bucket:
            status_lbl = _STATUS_LABELS[r.status]
            source_lbl = _SOURCE_LABELS[r.source]
            lines.append(f"- [{status_lbl}] (R-{r.id}, {source_lbl}) {r.content}")
            if r.reason:
                lines.append(f"  > Motivo: {r.reason}")
        lines.append("")
    if len(lines) == 2:  # solo título + línea vacía — sin reglas
        lines.append("(sin reglas)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


async def import_rules_md(
    session,
    project_id: int,
    markdown: str,
    *,
    source: RuleSource = RuleSource.USER,
) -> dict:
    """Re-ingresa reglas desde el Markdown del export (upsert por id).

    Líneas con ``R-<id>`` actualizan esa regla (content/status/motivo); líneas
    sin id crean reglas nuevas con el ``source`` dado. Las líneas que no
    matchean el formato se ignoran y se cuentan en ``skipped``. Sin verificación
    de conflictos: el import es una edición curada por humano.
    """
    created = 0
    updated = 0
    skipped = 0
    scope = RuleScope.ALL
    pending_reason: str | None = None

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("## "):
            label = line[3:].strip()
            scope = _LABEL_TO_SCOPE.get(label, RuleScope.ALL)
            continue
        if line.startswith("  > Motivo:"):
            pending_reason = line[len("  > Motivo:"):].strip() or None
            continue
        m = _ENTRY_RE.match(line)
        if not m:
            skipped += 1
            continue
        new_status = _LABEL_TO_STATUS[m.group("status")]
        content = m.group("content").strip()
        reason = pending_reason
        pending_reason = None
        rid = int(m.group("rid")) if m.group("rid") else None
        if rid is not None:
            rule = await session.get(ProjectRule, rid)
            if rule is not None and rule.project_id == project_id:
                rule.scope = scope
                rule.content = content
                rule.status = new_status
                if reason is not None:
                    rule.reason = reason
                updated += 1
                continue
            # Id ausente o de OTRO proyecto (p. ej. import de un export ajeno):
            # crear una regla nueva en vez de retargetear/acceder la fila ajena.
        session.add(
            ProjectRule(
                project_id=project_id,
                scope=scope,
                content=content,
                reason=reason,
                source=source,
                status=new_status,
            )
        )
        created += 1
    await session.flush()
    return {"created": created, "updated": updated, "skipped": skipped}
