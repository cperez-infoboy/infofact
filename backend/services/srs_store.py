"""SRS store: typed CRUD sobre RequirementFinding, Goal, GoalLink y SrsDocument.

Capa de edición para todo lo que rodea al SRS: hallazgos de calidad, goals
(GORE) y sus vínculos, y el documento SRS versionado. Los motores
(``srs_quality``, ``goals_engine``, ``srs_coverage``) producen datos que se
persisten aquí; el router y las herramientas del agente leen y mutan a través
de este módulo, nunca directo a la base.

Convenciones (espejo de ``requirement_store``):
- Todas las funciones son async y toman una AsyncSession + project_id.
- Comitean ellas mismas (un AsyncSessionLocal fresco por llamada es el patrón
  esperado en las herramientas del agente).
- Los ``replace_*`` borran las filas previas del proyecto y reinsertan: el
  análisis del SRS se recalcula entero en cada ``/srs``, igual que la captura.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.project import Project
from backend.models.requirement import ReqStatus, RequirementItem
from backend.models.srs import (
    FindingDimension,
    FindingSeverity,
    FindingStatus,
    FindingScope,
    Goal,
    GoalLink,
    GoalStatus,
    RequirementFinding,
    SrsDocument,
    SrsStatus,
)
from backend.services.srs_builder import build_srs
from backend.services.srs_quality import quality_fingerprint

# Mismo alfabeto opaque que REQ (Crockford base32, sin I/L/O/U).
_CROCKFORD_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_GOAL_OPAQUE_LEN = 4
_MAX_ATTEMPTS = 10

# Estados de requerimiento que cuentan como "vivos" para el SRS y la
# trazabilidad (consistente con srs_builder._LIVE_STATUSES).
_LIVE_STATUSES = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)

# token_sort_ratio mínimo (0-100) para considerar que un goal re-inferido es
# el mismo objetivo que uno ya persistido (upsert_goals). Mismo umbral alto
# del dedupe del merge en goals_engine: fusiona reformulaciones evidentes del
# mismo objetivo, no objetivos vecinos legítimos.
_GOAL_MATCH_RATIO = 90


def _norm_stmt(statement: str) -> str:
    """Normaliza un statement para el matching difuso (minúsculas + espacios)."""
    return " ".join((statement or "").lower().split())


# --- helpers ----------------------------------------------------------------


async def gen_goal_code(
    session: AsyncSession, project_id: int, *, reserved: set[str] | None = None
) -> str:
    """Asigna un código opaque ``GOAL-XXXX`` único por proyecto.

    Réplica local de ``_req_codes.gen_opaque_code`` para goals: no tocamos el
    helper indexado de REQ. Gaps no leen como goals perdidos (mismo criterio
    que los códigos de requerimiento).
    """
    batch = reserved if reserved is not None else set()
    rows = await session.execute(
        select(Goal.code).where(Goal.project_id == project_id)
    )
    existing = {c for (c,) in rows.all() if c}
    for _ in range(_MAX_ATTEMPTS):
        seg = "".join(secrets.choice(_CROCKFORD_B32) for _ in range(_GOAL_OPAQUE_LEN))
        code = f"GOAL-{seg}"
        if code not in existing and code not in batch:
            batch.add(code)
            return code
    raise RuntimeError("could not allocate a unique GOAL code (10 attempts)")


# --------------------------------------------------------------------------- #
# RequirementFinding                                                          #
# --------------------------------------------------------------------------- #


def finding_to_dict(
    f: RequirementFinding, *, req_code: str | None = None
) -> dict[str, Any]:
    return {
        "id": f.id,
        "project_id": f.project_id,
        "req_id": f.req_id,
        # Codigo opaque REQ-XXXX del requerimiento (resuelto por el caller via
        # _req_code_map); req_id solo es el id interno y no identifica al req
        # para el usuario. None en hallazgos de conjunto (scope=SET).
        "req_code": req_code,
        "scope": f.scope.value,
        "dimension": f.dimension.value,
        "rule_id": f.rule_id,
        "severity": f.severity.value,
        "message": f.message,
        "suggestion": f.suggestion,
        "status": f.status.value,
        "ears_pattern": f.ears_pattern,
        "detected_by": f.detected_by,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def findings_to_dicts(
    findings: list[RequirementFinding],
    code_map: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    """Serializa una lista de hallazgos resolviendo req_id -> codigo opaque.

    ``code_map`` se construye con ``_req_code_map`` (una sola consulta por
    proyecto). Asi los hallazgos llevan ``req_code`` (REQ-XXXX) en vez de solo
    el id interno, que no identifica al requerimiento para el usuario.
    """
    m = code_map or {}
    return [
        finding_to_dict(f, req_code=m.get(f.req_id) if f.req_id else None)
        for f in findings
    ]


async def _req_code_map(
    session: AsyncSession, project_id: int
) -> dict[int, str]:
    """Mapa ``requirement_items.id -> code`` del proyecto (una consulta).

    Incluye todos los requerimientos (vivos y soft-deleted): los hallazgos
    pueden referenciar cualquiera, y el codigo debe resolver siempre.
    """
    rows = await session.execute(
        select(RequirementItem.id, RequirementItem.code).where(
            RequirementItem.project_id == project_id
        )
    )
    return {rid: code for rid, code in rows.all() if code}


async def list_findings(
    session: AsyncSession,
    project_id: int,
    *,
    req_id: int | None = None,
    status: FindingStatus | None = None,
) -> list[RequirementFinding]:
    """Hallazgos de un proyecto, opcionalmente filtrados por req o estado."""
    stmt = select(RequirementFinding).where(
        RequirementFinding.project_id == project_id
    )
    if req_id is not None:
        stmt = stmt.where(RequirementFinding.req_id == req_id)
    if status is not None:
        stmt = stmt.where(RequirementFinding.status == status)
    stmt = stmt.order_by(
        RequirementFinding.severity, RequirementFinding.created_at
    )
    rows = await session.scalars(stmt)
    return list(rows)


async def list_findings_for_req(
    session: AsyncSession, req_id: int, *, project_id: int
) -> list[RequirementFinding]:
    return await list_findings(session, project_id, req_id=req_id)


# ---------------------------------------------------------------------------
# Deduplicación de hallazgos antes de persistir (guarda contra la constraint).
#
# ``UniqueConstraint(req_id, rule_id)`` prohíbe dos hallazgos con el mismo
# (req_id, rule_id). El motor LLM de calidad (``srs_quality._verdict_to_findings``)
# puede emitir varios hallazgos con el mismo ``rule_id`` para un mismo req
# (p. ej. dos términos ambiguos -> ``ambiguous_term`` x2). Sin dedupe, el
# segundo INSERT revienta la constraint y hace rollback de TODA la tanda,
# dejando el SRS sin hallazgos. Colapsamos por (req_id, rule_id) quedándonos
# con el más severo y concatenando los mensajes distintos.
#
# Los hallazgos de conjunto (scope=SET, req_id=None) NO colisionan: SQLite
# trata los NULL como distintos bajo UNIQUE -> se conservan todos.
# ---------------------------------------------------------------------------

# Rank por severidad: menor número = más crítico (se conserva en el merge).
_SEVERITY_RANK = {
    FindingSeverity.BLOCKER.value: 0,
    FindingSeverity.MAJOR.value: 1,
    FindingSeverity.MINOR.value: 2,
    FindingSeverity.INFO.value: 3,
}


def _severity_value(fd: dict[str, Any]) -> str:
    """Normaliza ``severity`` del dict a su valor string ('blocker', ...).

    El motor puede entregar el enum o el string; homogeneizamos a string para
    indexar ``_SEVERITY_RANK`` y para comparar sin distinguir tipos.
    """
    sev = fd.get("severity")
    return sev.value if hasattr(sev, "value") else str(sev)


def _dedupe_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Colapsa hallazgos duplicados por ``(req_id, rule_id)``.

    Mantiene intactos los hallazgos de conjunto (``req_id`` None). Para los de
    ítem agrupa por ``(req_id, rule_id)`` y se queda con uno solo conservando
    la severidad más alta, y la unión de mensajes distintos separados por
    ``' · '`` (dimension/suggestion/ears_pattern del ganador más severo).
    """
    deduped: list[dict[str, Any]] = []
    buckets: dict[tuple[int, str], list[dict[str, Any]]] = {}

    for fd in findings:
        req_id = fd.get("req_id")
        if req_id is None:
            # Hallazgo de conjunto: UNIQUE(NULL, ...) no colisiona -> directo.
            deduped.append(fd)
            continue
        key = (req_id, fd.get("rule_id"))
        buckets.setdefault(key, []).append(fd)

    for group in buckets.values():
        if len(group) == 1:
            deduped.append(group[0])
            continue
        # Ganador = más severo (rank menor). Empate -> el primero del grupo.
        winner = min(
            group, key=lambda g: _SEVERITY_RANK.get(_severity_value(g), 99)
        )
        merged = dict(winner)
        seen: set[str] = set()
        messages: list[str] = []
        for g in group:
            msg = (g.get("message") or "").strip()
            if msg and msg not in seen:
                seen.add(msg)
                messages.append(msg)
        if messages:
            merged["message"] = " · ".join(messages)
        deduped.append(merged)

    return deduped


async def replace_findings(
    session: AsyncSession,
    project_id: int,
    findings: list[dict[str, Any]],
) -> list[RequirementFinding]:
    """Reemplaza TODOS los hallazgos del proyecto por una nueva tanda.

    .. deprecated:: a favor de :func:`merge_findings`, que preserva la
       curación humana (fixed/waived) cuando el enunciado no cambió. Este
       replace borra TODO (curación incluida) y reinserta en OPEN — es el
       mecanismo que hacía reaparecer blockers ya resueltos en cada commit.
       Queda para callers que genuinamente quieran un reset (p. ej. captura
       desde cero). ``findings`` viene de los motores; cada dict lleva
       scope/dimension/rule_id/severity/message/suggestion y opcionalmente
       req_id/ears_pattern/detected_by.
    """
    findings = _dedupe_findings(findings)
    await session.execute(
        delete(RequirementFinding).where(
            RequirementFinding.project_id == project_id
        )
    )
    rows: list[RequirementFinding] = []
    for fd in findings:
        rows.append(
            RequirementFinding(
                project_id=project_id,
                req_id=fd.get("req_id"),
                scope=fd["scope"],
                dimension=fd["dimension"],
                rule_id=fd["rule_id"],
                severity=fd["severity"],
                message=fd["message"],
                suggestion=fd.get("suggestion"),
                status=FindingStatus.OPEN,
                ears_pattern=fd.get("ears_pattern"),
                detected_by=fd.get("detected_by", "agent"),
            )
        )
    session.add_all(rows)
    await session.commit()
    return rows


async def merge_findings(
    session: AsyncSession,
    project_id: int,
    findings: list[dict[str, Any]],
    *,
    fresh_judged_req_ids: list[int] | set[int] | None = None,
) -> dict[str, int]:
    """Fusiona la tanda de hallazgos con las filas existentes (curación con memoria).

    Diferencia clave con ``replace_findings`` (DELETE all + reinsert OPEN, que
    borraba la curación humana en cada commit): acá el match es por
    (req_id, rule_id) — o (dimension, rule_id, message) en hallazgos de
    conjunto — y el estado de curación (fixed/waived) con su auditoría
    SOBREVIVE cuando el enunciado no cambió (mismo ``req_fingerprint``). Si el
    enunciado cambió, el hallazgo se reabre (OPEN): es un veredicto nuevo
    sobre texto nuevo. Los hallazgos que la tanda ya no detecta se eliminan
    (el problema desapareció o el req dejó de estar vivo).

    ``fresh_judged_req_ids`` (del summary de ``analyze_quality``) sella los
    ``quality_fingerprint`` de los ítems re-juzgados EN LA MISMA transacción:
    si el run aborta antes de este commit no hay sello y la próxima corrida
    los re-juzga (nunca quedan ítems sellados sin hallazgos persistidos).

    Devuelve {inserted, matched, preserved, reopened, deleted, total}.
    """
    findings = _dedupe_findings(findings)

    # Fingerprint vigente por req (lo que el juez vería HOY): decide si la
    # curación previa de un hallazgo sobrevive al merge.
    rows = await session.execute(
        select(RequirementItem.id, RequirementItem.statement, RequirementItem.type)
        .where(RequirementItem.project_id == project_id)
    )
    fp_map: dict[int, str | None] = {
        rid: quality_fingerprint(stmt, rtype.value if rtype else None)
        for rid, stmt, rtype in rows.all()
    }

    def _dim_val(d: Any) -> str:
        return d.value if hasattr(d, "value") else str(d)

    def _key(fd: dict[str, Any]):
        if fd.get("req_id") is not None:
            return ("item", fd["req_id"], fd.get("rule_id"))
        # Identidad del hallazgo de conjunto: (dimension, rule_id) SIN el
        # message. El mensaje describe el estado del conteo («N reqs sin
        # goal»), que varía en cada corrida: incluirlo en la clave re-creaba
        # la fila en cada merge (insert+delete) y destruía la curación
        # fixed/waived del hallazgo agregado. El mensaje se refresca in-place.
        return ("set", _dim_val(fd.get("dimension")), fd.get("rule_id"))

    existing_by_key: dict[tuple, RequirementFinding] = {}
    for f in await list_findings(session, project_id):
        if f.req_id is not None:
            existing_by_key[("item", f.req_id, f.rule_id)] = f
        else:
            existing_by_key[("set", f.dimension.value, f.rule_id)] = f

    seen: set[tuple] = set()
    inserted = matched = preserved = reopened = 0
    for fd in findings:
        key = _key(fd)
        cur_fp: str | None = (
            fp_map.get(fd["req_id"]) if fd.get("req_id") is not None else None
        )
        # Normaliza enums: la tanda fresca trae instancias (Fase A/B) pero un
        # run sembrado trae los dicts serializados del documento (strings).
        sev = fd["severity"]
        sev = sev if isinstance(sev, FindingSeverity) else FindingSeverity(str(sev))
        dim = fd["dimension"]
        dim = dim if isinstance(dim, FindingDimension) else FindingDimension(str(dim))
        row = existing_by_key.get(key)
        if row is None:
            session.add(
                RequirementFinding(
                    project_id=project_id,
                    req_id=fd.get("req_id"),
                    scope=fd["scope"],
                    dimension=dim,
                    rule_id=fd["rule_id"],
                    severity=sev,
                    message=fd["message"],
                    suggestion=fd.get("suggestion"),
                    status=FindingStatus.OPEN,
                    ears_pattern=fd.get("ears_pattern"),
                    detected_by=fd.get("detected_by", "agent"),
                    req_fingerprint=cur_fp,
                )
            )
            inserted += 1
        else:
            matched += 1
            # Descriptores frescos siempre; el estado solo si el enunciado no cambió.
            row.severity = sev
            row.dimension = dim
            row.message = fd["message"]
            row.suggestion = fd.get("suggestion")
            row.ears_pattern = fd.get("ears_pattern")
            row.detected_by = fd.get("detected_by", "agent")
            # Los hallazgos de CONJUNTO (req_id NULL) no tienen enunciado
            # propio: su identidad es la regla agregada y la curación (waive
            # de un «reqs sin goal» decidido a mano) sobrevive SIEMPRE — no
            # hay fingerprint que cambie bajo ellos. Los item-level mantienen
            # la regla del fingerprint (enunciado cambió = veredicto nuevo).
            is_set_finding = fd.get("req_id") is None
            if (
                row.status != FindingStatus.OPEN
                and (
                    is_set_finding
                    or (
                        row.req_fingerprint is not None
                        and row.req_fingerprint == cur_fp
                    )
                )
            ):
                preserved += 1
            else:
                if row.status != FindingStatus.OPEN:
                    reopened += 1
                row.status = FindingStatus.OPEN
                row.req_fingerprint = cur_fp
                row.resolved_at = None
                row.resolved_by = None
                row.resolution_note = None
        seen.add(key)

    deleted = 0
    for key, f in existing_by_key.items():
        if key not in seen:
            await session.delete(f)
            deleted += 1

    # Sello de fingerprints de lo re-juzgado, atómico con la tanda.
    if fresh_judged_req_ids:
        judged = set(fresh_judged_req_ids)
        stamp_rows = await session.scalars(
            select(RequirementItem).where(
                RequirementItem.project_id == project_id,
                RequirementItem.id.in_(judged),
            )
        )
        now = datetime.utcnow()
        for it in stamp_rows:
            it.quality_fingerprint = fp_map.get(it.id)
            it.quality_judged_at = now

    await session.commit()
    return {
        "inserted": inserted,
        "matched": matched,
        "preserved": preserved,
        "reopened": reopened,
        "deleted": deleted,
        "total": inserted + matched,
    }


async def delete_all_findings(
    session: AsyncSession, project_id: int
) -> int:
    """Hard-delete every quality finding for a project.

    Wipes ``requirement_findings`` so the SRS quality tab does not show stale
    findings pointing to requirement IDs that were recycled after a capture
    reset. Does NOT commit; the caller owns the transaction. Returns the count.
    """
    count = await session.scalar(
        select(func.count()).select_from(RequirementFinding).where(
            RequirementFinding.project_id == project_id
        )
    )
    total = int(count or 0)
    if total:
        await session.execute(
            delete(RequirementFinding).where(
                RequirementFinding.project_id == project_id
            )
        )
        await session.flush()
    return total


async def delete_all_goals(
    session: AsyncSession, project_id: int
) -> int:
    """Hard-delete every GORE goal + goal-link for a project.

    Goal-links reference both goals and requirement_items, so they must be
    wiped alongside goals to avoid orphans after a capture reset. Does NOT
    commit; the caller owns the transaction. Returns the goal count.
    """
    goal_ids = select(Goal.id).where(Goal.project_id == project_id)
    await session.execute(
        delete(GoalLink).where(GoalLink.goal_id.in_(goal_ids))
    )
    count = await session.scalar(
        select(func.count()).select_from(Goal).where(
            Goal.project_id == project_id
        )
    )
    total = int(count or 0)
    if total:
        await session.execute(
            delete(Goal).where(Goal.project_id == project_id)
        )
        await session.flush()
    return total


async def delete_all_srs(
    session: AsyncSession, project_id: int
) -> int:
    """Hard-delete every SRS document version for a project.

    Wipes ``srs_documents`` so stale versions with frozen requirement codes
    and traceability matrices do not survive a capture reset. Does NOT commit;
    the caller owns the transaction. Returns the version count.
    """
    count = await session.scalar(
        select(func.count()).select_from(SrsDocument).where(
            SrsDocument.project_id == project_id
        )
    )
    total = int(count or 0)
    if total:
        await session.execute(
            delete(SrsDocument).where(
                SrsDocument.project_id == project_id
            )
        )
        await session.flush()
    return total


async def set_finding_status(
    session: AsyncSession,
    finding_id: int,
    *,
    project_id: int,
    status: FindingStatus,
) -> RequirementFinding:
    """Cambia el estado de curación de un hallazgo (OPEN/FIXED/WAIVED)."""
    f = await session.scalar(
        select(RequirementFinding).where(
            RequirementFinding.id == finding_id,
            RequirementFinding.project_id == project_id,
        )
    )
    if f is None:
        raise KeyError(f"finding {finding_id} not found")
    f.status = status
    await session.commit()
    return f


async def resolve_findings(
    session: AsyncSession,
    project_id: int,
    *,
    targets: list[tuple[int, str]],
    status: FindingStatus,
    note: str | None = None,
    resolved_by: str = "agent",
) -> dict[str, Any]:
    """Cierre formal y auditable de hallazgos por lote: (req_id, rule_id) → estado.

    ``status`` debe ser FIXED (corregido) o WAIVED (descartado con criterio):
    ambos sobreviven a los re-análisis mientras el enunciado no cambie (los
    respeta ``merge_findings``), que es lo que corta el goteo de «lo descartado
    reaparece en el siguiente run». ``note`` documenta el por qué (auditoría:
    resolved_at/by/note). Devuelve {resolved, missing} con los targets que no
    encontraron hallazgo persistido.
    """
    if status not in (FindingStatus.FIXED, FindingStatus.WAIVED):
        raise ValueError("status debe ser FindingStatus.FIXED o WAIVED")
    now = datetime.utcnow()
    resolved = 0
    missing: list[list] = []
    for req_id, rule_id in targets:
        f = await session.scalar(
            select(RequirementFinding).where(
                RequirementFinding.project_id == project_id,
                RequirementFinding.req_id == req_id,
                RequirementFinding.rule_id == rule_id,
            )
        )
        if f is None:
            missing.append([req_id, rule_id])
            continue
        f.status = status
        f.resolved_at = now
        f.resolved_by = resolved_by
        f.resolution_note = note
        resolved += 1
    await session.commit()
    return {"resolved": resolved, "missing": missing}


# --------------------------------------------------------------------------- #
# Goal / GoalLink                                                             #
# --------------------------------------------------------------------------- #


def goal_to_dict(g: Goal) -> dict[str, Any]:
    return {
        "id": g.id,
        "project_id": g.project_id,
        "code": g.code,
        "statement": g.statement,
        "kind": g.kind.value,
        "parent_id": g.parent_id,
        "rationale": g.rationale,
        "source": g.source,
        "confidence": g.confidence,
        "status": g.status.value,
        "created_by": g.created_by,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }


def goal_link_to_dict(l: GoalLink) -> dict[str, Any]:
    return {
        "id": l.id,
        "goal_id": l.goal_id,
        "req_id": l.req_id,
        "relation": l.relation.value,
        "rationale": l.rationale,
        "status": l.status.value,
        "detected_by": l.detected_by,
        "created_at": l.created_at.isoformat() if l.created_at else None,
    }


async def list_goals(session: AsyncSession, project_id: int) -> list[Goal]:
    rows = await session.scalars(
        select(Goal)
        .where(Goal.project_id == project_id)
        .order_by(Goal.kind, Goal.id)
    )
    return list(rows)


async def get_goal(
    session: AsyncSession, goal_id: int, *, project_id: int | None = None
) -> Goal:
    stmt = select(Goal).where(Goal.id == goal_id)
    if project_id is not None:
        stmt = stmt.where(Goal.project_id == project_id)
    g = await session.scalar(stmt)
    if g is None:
        raise KeyError(f"goal {goal_id} not found")
    return g


async def list_goal_links(session: AsyncSession, project_id: int) -> list[GoalLink]:
    """Vínculos de todos los goals del proyecto (join por project_id)."""
    stmt = (
        select(GoalLink)
        .join(Goal, GoalLink.goal_id == Goal.id)
        .where(Goal.project_id == project_id)
        .order_by(GoalLink.goal_id, GoalLink.req_id)
    )
    rows = await session.scalars(stmt)
    return list(rows)


async def replace_goals(
    session: AsyncSession,
    project_id: int,
    goals: list[dict[str, Any]],
    links: list[dict[str, Any]],
    *,
    req_by_code: dict[str, int],
) -> dict[str, Any]:
    """Reemplaza goals + links del proyecto por una nueva inferencia.

    ``goals``: lista de dicts {statement, kind, rationale?, source?, confidence?,
    parent_code?}. ``links``: lista de {goal_code, req_code, relation, rationale?}.
    ``req_by_code`` mapea REQ-XXXX -> requirement_items.id (para resolver los
    links). Los códigos GOAL-XXXX se asignan aquí; ``parent_code`` y
    ``goal_code`` de los links se resuelven a ids tras la inserción.
    """
    await session.execute(
        delete(GoalLink).where(
            GoalLink.goal_id.in_(select(Goal.id).where(Goal.project_id == project_id))
        )
    )
    await session.execute(
        delete(Goal).where(Goal.project_id == project_id)
    )

    # 1) Inserta goals raíz, asignando códigos opaque. Resuelve parent_code.
    reserved: set[str] = set()
    code_to_id: dict[str, int] = {}
    # Primero los padres (parent_code None), luego los hijos.
    ordered = sorted(goals, key=lambda g: (g.get("parent_code") is not None))
    for gd in ordered:
        code = await gen_goal_code(session, project_id, reserved=reserved)
        parent_code = gd.get("parent_code")
        parent_id = code_to_id.get(parent_code) if parent_code else None
        row = Goal(
            project_id=project_id,
            code=code,
            statement=gd["statement"],
            kind=gd["kind"],
            parent_id=parent_id,
            rationale=gd.get("rationale"),
            source=gd.get("source"),
            confidence=gd.get("confidence", 0.0),
            status=GoalStatus.PROPOSED,
            created_by="agent",
        )
        session.add(row)
        await session.flush()  # necesita el id
        code_to_id[code] = row.id
        # Si el payload trae su propio code estable (referenciado por links),
        # lo mapeamos también por ese alias.
        if gd.get("code"):
            code_to_id[gd["code"]] = row.id

    # 2) Inserta links resolviendo goal_code -> id y req_code -> id.
    # Dedupe por (goal_id, req_id, relation): UniqueConstraint("goal_id",
    # "req_id", "relation") prohibiría dos aristas idénticas y haría rollback
    # de goals + links enteros. El LLM puede repetir un mismo link.
    link_rows: list[GoalLink] = []
    seen_edges: set[tuple[int, int, str]] = set()
    for ld in links:
        goal_id = code_to_id.get(ld["goal_code"])
        req_id = req_by_code.get(ld["req_code"])
        if goal_id is None or req_id is None:
            continue  # referencia no resuelta: se descarta silenciosamente
        relation = ld["relation"]
        relation_val = relation.value if hasattr(relation, "value") else str(relation)
        edge = (goal_id, req_id, relation_val)
        if edge in seen_edges:
            continue  # arista duplicada (mismo goal+req+relation) -> descartada
        seen_edges.add(edge)
        link_rows.append(
            GoalLink(
                goal_id=goal_id,
                req_id=req_id,
                relation=relation,
                rationale=ld.get("rationale"),
            )
        )
    session.add_all(link_rows)
    await session.commit()

    # Distinct goal ids: code_to_id mapea (opaque + alias) -> id, así que su
    # len() duplica cuando el payload trae alias. Contamos ids reales para no
    # inflar el reporte del agente (bug "28 goals vs 14 persistidos").
    return {
        "goals": len({gid for gid in code_to_id.values()}),
        "links": len(link_rows),
    }


async def upsert_goals(
    session: AsyncSession,
    project_id: int,
    goals: list[dict[str, Any]],
    links: list[dict[str, Any]],
    *,
    req_by_code: dict[str, int],
) -> dict[str, Any]:
    """Upsert idempotente de goals + links: preserva los códigos GOAL-XXXX.

    Diferencia clave con ``replace_goals`` (que borra TODO y reasigna códigos:
    re-correr la inferencia huérfanaba los links escritos a mano y hacía
    cambiar los códigos de la matriz de trazabilidad entre corridas). Acá:

    - El goal cuya frase (normalizada) y kind ya existen CONSERVA su fila
      (id, code y el status de curación humana); se actualizan
      statement/rationale/confidence y el parent resuelto del payload.
    - El goal nuevo se inserta con ``gen_goal_code``.
    - El goal PROPOSED que la nueva inferencia ya no menciona se elimina con
      sus links; CONFIRMED/REJECTED (decisión humana) se conservan intactos,
      con sus links.
    - Los links de los goals que volvieron en el payload se reemplazan por la
      nueva inferencia; los de los goals preservados fuera del payload no se
      tocan.

    Dedupe de aristas por (goal_id, req_id, relation), igual que
    ``replace_goals``. Devuelve {goals, links, goals_kept, goals_added,
    goals_removed}.
    """
    existing = {g.id: g for g in await list_goals(session, project_id)}
    reserved: set[str] = set()
    alias_to_id: dict[str, int] = {}
    code_to_id: dict[str, int] = {}
    rows_by_id: dict[int, Goal] = {}
    payload_parent: dict[int, str | None] = {}
    matched_ids: set[int] = set()

    # Raíces primero (misma convención de replace_goals): la resolución de
    # parents corre DESPUÉS del loop completo, así que el orden solo fija el
    # orden de inserción de los goals nuevos.
    ordered = sorted(goals, key=lambda g: g.get("parent_code") is not None)
    for gd in ordered:
        kind = gd["kind"]
        kind_val = kind.value if hasattr(kind, "value") else str(kind)
        stmt_norm = _norm_stmt(gd["statement"])
        target: Goal | None = None
        for g in existing.values():
            if g.id in matched_ids or g.kind.value != kind_val:
                continue
            if (
                fuzz.token_sort_ratio(_norm_stmt(g.statement), stmt_norm)
                >= _GOAL_MATCH_RATIO
            ):
                target = g
                break
        if target is None:
            code = await gen_goal_code(session, project_id, reserved=reserved)
            row = Goal(
                project_id=project_id,
                code=code,
                statement=gd["statement"],
                kind=kind,
                rationale=gd.get("rationale"),
                source=gd.get("source"),
                confidence=gd.get("confidence", 0.0),
                status=GoalStatus.PROPOSED,
                created_by="agent",
            )
            session.add(row)
            await session.flush()  # necesita el id
            target = row
            code_to_id[code] = row.id
        else:
            # Mismo objetivo: la fila (id/code/status humano) no se toca;
            # solo se refresca lo que la nueva inferencia trae.
            matched_ids.add(target.id)
            target.statement = gd["statement"]
            if gd.get("rationale"):
                target.rationale = gd["rationale"]
            if gd.get("confidence") is not None:
                target.confidence = gd["confidence"]
            if target.status == GoalStatus.STALE:
                # La inferencia volvió a detectarlo: revive como PROPOSED.
                target.status = GoalStatus.PROPOSED
            code_to_id[target.code] = target.id
        rows_by_id[target.id] = target
        if gd.get("code"):
            alias_to_id[gd["code"]] = target.id
        payload_parent[target.id] = gd.get("parent_code")

    # Jerarquía: parent por alias del payload (resuelve a match o a nuevo);
    # fantasma o auto-referencia -> raíz, igual que en replace_goals.
    for gid, parent_alias in payload_parent.items():
        parent_id = alias_to_id.get(parent_alias) if parent_alias else None
        rows_by_id[gid].parent_id = None if parent_id == gid else parent_id

    # Links: los de los goals del payload se reemplazan por la nueva
    # inferencia, SALVO los curados a mano (detected_by="human"), que
    # sobreviven siempre; los de los preservados fuera del payload quedan
    # intactos.
    payload_goal_ids = set(rows_by_id)
    human_edges: set[tuple[int, int, str]] = set()
    if payload_goal_ids:
        existing_links = await session.scalars(
            select(GoalLink).where(GoalLink.goal_id.in_(payload_goal_ids))
        )
        for l in existing_links:
            if l.detected_by == "human":
                rel = (
                    l.relation.value
                    if hasattr(l.relation, "value")
                    else str(l.relation)
                )
                human_edges.add((l.goal_id, l.req_id, rel))
        await session.execute(
            delete(GoalLink).where(
                GoalLink.goal_id.in_(payload_goal_ids),
                GoalLink.detected_by != "human",
            )
        )
    link_rows: list[GoalLink] = []
    seen_edges: set[tuple[int, int, str]] = set(human_edges)
    for ld in links:
        goal_id = alias_to_id.get(ld["goal_code"]) or code_to_id.get(
            ld["goal_code"]
        )
        req_id = req_by_code.get(ld["req_code"])
        if goal_id is None or req_id is None:
            continue  # referencia no resuelta: se descarta silenciosamente
        relation = ld["relation"]
        relation_val = (
            relation.value if hasattr(relation, "value") else str(relation)
        )
        edge = (goal_id, req_id, relation_val)
        if edge in seen_edges:
            continue  # arista duplicada del LLM o ya cubierta por curación humana
        seen_edges.add(edge)
        link_rows.append(
            GoalLink(
                goal_id=goal_id,
                req_id=req_id,
                relation=relation,
                rationale=ld.get("rationale"),
                detected_by=ld.get("detected_by", "agent"),
            )
        )
    session.add_all(link_rows)

    # Stale: el PROPOSED que la inferencia ya no menciona se MARCA stale —
    # fila y links quedan para decisión (antes se borraba con sus links y cada
    # re-run pisaba la curación). El confirmado/rechazado por un humano es
    # decisión persistente y queda igual. Un stale re-mencionado por una
    # inferencia posterior revive a PROPOSED (arriba, al matchear).
    marked_stale = 0
    for gid, g in existing.items():
        if gid in matched_ids:
            continue
        if g.status == GoalStatus.PROPOSED:
            g.status = GoalStatus.STALE
            marked_stale += 1

    await session.commit()
    return {
        # Total de goals tras el upsert: re-inferidos + nuevos + decisiones
        # humanas conservadas + marcados stale (que siguen en la tabla).
        "goals": len(rows_by_id) + (len(existing) - len(matched_ids)),
        "links": len(link_rows),
        "goals_kept": len(matched_ids),
        "goals_added": len(rows_by_id) - len(matched_ids),
        "goals_stale": marked_stale,
        # Alias legado (era hard-delete); hoy cuenta los marcados stale.
        "goals_removed": marked_stale,
    }


async def add_goal_link(
    session: AsyncSession,
    project_id: int,
    *,
    goal_id: int,
    req_id: int,
    relation,
    rationale: str | None = None,
    detected_by: str = "agent",
) -> dict[str, Any]:
    """Crea (o refresca) un vínculo goal <-> req; idempotente por la tripleta.

    Si la arista (goal_id, req_id, relation) ya existe, no se duplica: se
    actualiza el rationale provisto y se devuelve ``created=False``.
    """
    goal = await get_goal(session, goal_id, project_id=project_id)
    relation_val = (
        relation.value if hasattr(relation, "value") else str(relation)
    )
    links = await list_goal_links(session, project_id)
    existing = next(
        (
            l
            for l in links
            if l.goal_id == goal.id
            and l.req_id == req_id
            and l.relation.value == relation_val
        ),
        None,
    )
    if existing is not None:
        if rationale:
            existing.rationale = rationale
        await session.commit()
        return {"link": goal_link_to_dict(existing), "created": False}
    row = GoalLink(
        goal_id=goal.id,
        req_id=req_id,
        relation=relation,
        rationale=rationale,
        detected_by=detected_by,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"link": goal_link_to_dict(row), "created": True}


async def remove_goal_link(
    session: AsyncSession,
    project_id: int,
    *,
    goal_id: int,
    req_id: int,
    relation,
) -> bool:
    """Borra el vínculo (goal, req, relation) del proyecto. True si existía."""
    goal = await get_goal(session, goal_id, project_id=project_id)
    relation_val = (
        relation.value if hasattr(relation, "value") else str(relation)
    )
    links = await list_goal_links(session, project_id)
    target = next(
        (
            l
            for l in links
            if l.goal_id == goal.id
            and l.req_id == req_id
            and l.relation.value == relation_val
        ),
        None,
    )
    if target is None:
        return False
    await session.delete(target)
    await session.commit()
    return True


async def goal_coverage(session: AsyncSession, project_id: int) -> dict[str, Any]:
    """Cobertura goal <-> req: reqs vivos con y sin al menos un link.

    La vista que faltaba: ``srs_coverage`` mira el DOCUMENTO (secciones vs
    reqs); esta mira el MODELO de goals. Un req vivo sin ningún link no
    aporta a ningún objetivo — no tiene justificación de «por qué» en el
    modelo — y es la cola de trabajo para completar la trazabilidad.
    """
    goals = await list_goals(session, project_id)
    links = await list_goal_links(session, project_id)
    linked_req_ids = {l.req_id for l in links}
    rows = (
        await session.execute(
            select(RequirementItem.id, RequirementItem.code)
            .where(
                RequirementItem.project_id == project_id,
                RequirementItem.status.in_(_LIVE_STATUSES),
            )
            .order_by(RequirementItem.code)
        )
    ).all()
    total = len(rows)
    without = [code for rid, code in rows if rid not in linked_req_ids]
    per_goal = []
    for g in goals:
        g_links = [l for l in links if l.goal_id == g.id]
        per_goal.append(
            {
                "code": g.code,
                "kind": g.kind.value,
                "status": g.status.value,
                "statement": g.statement,
                "links": len(g_links),
                "realizes": sum(
                    1 for l in g_links if l.relation.value == "realizes"
                ),
            }
        )
    return {
        "goals": len(goals),
        "live_requirements": total,
        "with_goal_link": total - len(without),
        "without_goal_link": len(without),
        "without_goal_codes": without,
        "per_goal": per_goal,
    }


async def update_goal(
    session: AsyncSession,
    goal_id: int,
    *,
    project_id: int,
    statement: str | None = None,
    rationale: str | None = None,
    status: GoalStatus | None = None,
) -> Goal:
    """Edita un goal (statement / rationale / status). Solo campos provistos."""
    g = await get_goal(session, goal_id, project_id=project_id)
    if statement is not None:
        g.statement = statement
    if rationale is not None:
        g.rationale = rationale
    if status is not None:
        g.status = status
    await session.commit()
    return g


async def merge_goals(
    session: AsyncSession,
    project_id: int,
    *,
    keeper_code: str,
    absorbed_codes: list[str],
    statement: str | None = None,
    rationale: str | None = None,
) -> dict[str, Any]:
    """Fusiona goals absorbidos en un keeper (consolidación del catálogo).

    Camino de curación quirúrgica del modelo GORE: re-apunta los links de los
    absorbidos AL keeper IN-PLACE (conserva detected_by/resolution — la
    curación humana de las aristas sobrevive), retira los absorbidos como
    REJECTED (soft, misma línea que el descarte soft de versiones: la fila y
    su historial quedan), re-parenta los sub-goals que colgaban de un
    absorbido y opcionalmente reescribe el enunciado del keeper con la
    consolidación. NO es destructivo: los absorbidos quedan consultables y
    una re-inferencia NO los revive automática (status REJECTED es decisión
    humana: el upsert la preserva).

    Dedupe de aristas por (keeper, req, relation): si el keeper ya tenía la
    arista, la del absorbido se elimina (queda la del keeper con su
    detected_by original). Devuelve {keeper, merged_links, dropped_links,
    absorbed, subgoals_reparented}.
    """
    by_code: dict[str, Goal] = {
        g.code: g for g in await list_goals(session, project_id)
    }
    keeper = by_code.get(keeper_code)
    if keeper is None:
        raise KeyError(f"goal {keeper_code} not found")
    keep_status = {
        GoalStatus.CONFIRMED.value,
        GoalStatus.REJECTED.value,
    }
    absorbed_goals: list[Goal] = []
    for code in absorbed_codes:
        if code == keeper_code:
            raise ValueError(f"{code} es el keeper: no puede absorberse a sí mismo")
        g = by_code.get(code)
        if g is None:
            raise KeyError(f"goal {code} not found")
        if g.status.value in keep_status and g.kind != keeper.kind:
            raise ValueError(
                f"{code} tiene decisión humana ({g.status.value}) y kind "
                f"distinto del keeper: fusionar requiere reversión explícita"
            )
        absorbed_goals.append(g)
    if not absorbed_goals:
        raise ValueError("absorbed_codes vacío")

    links = await list_goal_links(session, project_id)
    absorbed_ids = {g.id for g in absorbed_goals}
    keeper_links = {
        (l.req_id, l.relation.value) for l in links if l.goal_id == keeper.id
    }
    merged_links = dropped_links = 0
    for l in links:
        if l.goal_id not in absorbed_ids:
            continue
        key = (l.req_id, l.relation.value)
        if key in keeper_links:
            # La arista ya existe en el keeper: la del absorbido se elimina
            # (el keeper conserva la SUYA con su detected_by original).
            await session.delete(l)
            dropped_links += 1
        else:
            # Re-apuntar IN-PLACE: detected_by y resolution del link viajan
            # con él (curación humana preservada).
            l.goal_id = keeper.id
            keeper_links.add(key)
            merged_links += 1
    subgoals_reparented = 0
    for g in by_code.values():
        if g.parent_id in absorbed_ids:
            g.parent_id = keeper.id
            subgoals_reparented += 1
    for g in absorbed_goals:
        g.parent_id = None
        g.status = GoalStatus.REJECTED
    if statement is not None and statement.strip():
        keeper.statement = statement.strip()
    if rationale is not None and rationale.strip():
        keeper.rationale = rationale.strip()
    await session.commit()
    return {
        "keeper": keeper.code,
        "merged_links": merged_links,
        "dropped_links": dropped_links,
        "absorbed": [g.code for g in absorbed_goals],
        "subgoals_reparented": subgoals_reparented,
    }


async def set_link_status(
    session: AsyncSession,
    link_id: int,
    *,
    project_id: int,
    status,
) -> GoalLink:
    """Confirma/desestima un vínculo goal <-> req inferido."""
    stmt = (
        select(GoalLink)
        .join(Goal, GoalLink.goal_id == Goal.id)
        .where(GoalLink.id == link_id, Goal.project_id == project_id)
    )
    l = await session.scalar(stmt)
    if l is None:
        raise KeyError(f"goal_link {link_id} not found")
    l.status = status
    await session.commit()
    return l


# --------------------------------------------------------------------------- #
# Trazabilidad (matriz goal <-> req <-> fuente)                               #
# --------------------------------------------------------------------------- #


async def build_traceability(session: AsyncSession, project_id: int) -> dict[str, Any]:
    """Construye la matriz de trazabilidad goal -> req -> fuente.

    Snapshot consumido por el builder y persistido en SrsDocument.traceability.
    Devuelve {goals, matrix} donde cada fila de matrix lleva goal_code, req_code,
    req_statement, relation y el source del requerimiento.
    """
    goals = await list_goals(session, project_id)
    links = await list_goal_links(session, project_id)
    req_ids = {l.req_id for l in links}
    req_map: dict[int, RequirementItem] = {}
    if req_ids:
        rows = await session.scalars(
            select(RequirementItem).where(RequirementItem.id.in_(req_ids))
        )
        req_map = {r.id: r for r in rows}

    matrix: list[dict[str, Any]] = []
    for l in links:
        req = req_map.get(l.req_id)
        matrix.append(
            {
                "goal_id": l.goal_id,
                "req_id": l.req_id,
                "relation": l.relation.value,
                "req_code": req.code if req else None,
                "req_statement": req.statement if req else None,
                "req_source": req.source if req else None,
            }
        )

    return {
        "goals": [goal_to_dict(g) for g in goals],
        "matrix": matrix,
    }


# --------------------------------------------------------------------------- #
# SrsDocument                                                                 #
# --------------------------------------------------------------------------- #


def srs_to_dict(
    s: SrsDocument, *, with_markdown: bool = True
) -> dict[str, Any]:
    return {
        "id": s.id,
        "project_id": s.project_id,
        "version": s.version,
        "status": s.status.value,
        "structure": s.structure,
        "narrative": s.narrative,
        "markdown": s.markdown if with_markdown else None,
        "quality_summary": s.quality_summary,
        "coverage": s.coverage,
        "traceability": s.traceability,
        "review_flags": s.review_flags,
        "requirement_codes": s.requirement_codes,
        "requirement_count": s.requirement_count,
        "generated_at": s.generated_at.isoformat() if s.generated_at else None,
        "generated_by": s.generated_by,
        "reviewed_at": s.reviewed_at.isoformat() if s.reviewed_at else None,
        "locked_at": s.locked_at.isoformat() if s.locked_at else None,
    }


async def get_latest_srs(
    session: AsyncSession, project_id: int
) -> SrsDocument | None:
    """La versión más reciente del SRS del proyecto, ignorando descartadas/draft.

    Las DISCARDED no cuentan: descartar v11 hace que la «última» vuelva a ser
    la v10 previa, que es la base del seed y de la cobertura del router. Las
    DRAFT tampoco: son materialización temprana del run EN CURSO (aún sin
    commit) y no deben ser base de seed ni «última» — el commit las promueve.
    """
    return await session.scalar(
        select(SrsDocument)
        .where(
            SrsDocument.project_id == project_id,
            SrsDocument.status.notin_(
                [SrsStatus.DISCARDED, SrsStatus.DRAFT]
            ),
        )
        .order_by(SrsDocument.version.desc())
        .limit(1)
    )


async def list_srs_versions(
    session: AsyncSession, project_id: int
) -> list[SrsDocument]:
    rows = await session.scalars(
        select(SrsDocument)
        .where(SrsDocument.project_id == project_id)
        .order_by(SrsDocument.version.desc())
    )
    return list(rows)


async def get_srs_version(
    session: AsyncSession, project_id: int, version: int
) -> SrsDocument | None:
    return await session.scalar(
        select(SrsDocument).where(
            SrsDocument.project_id == project_id,
            SrsDocument.version == version,
        )
    )


async def create_srs(
    session: AsyncSession, project_id: int, payload: dict[str, Any]
) -> SrsDocument:
    """Crea una nueva versión CANDIDATE del SRS.

    ``payload`` lleva structure/narrative/markdown/quality_summary/coverage/
    traceability/requirement_codes/requirement_count (salida del builder).
    La versión se autoincrementa por proyecto.
    """
    cur = await session.scalar(
        select(func.max(SrsDocument.version)).where(
            SrsDocument.project_id == project_id
        )
    )
    version = (cur or 0) + 1
    row = SrsDocument(
        project_id=project_id,
        version=version,
        status=SrsStatus.CANDIDATE,
        structure=payload.get("structure", []),
        narrative=payload.get("narrative", {}),
        markdown=payload.get("markdown", ""),
        quality_summary=payload.get("quality_summary", {}),
        coverage=payload.get("coverage", {}),
        traceability=payload.get("traceability", {}),
        review_flags=payload.get("review_flags", {}),
        requirement_codes=payload.get("requirement_codes", []),
        requirement_count=payload.get("requirement_count", 0),
        generated_by=payload.get("generated_by", "agent"),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def create_srs_draft(
    session: AsyncSession, project_id: int, payload: dict[str, Any]
) -> SrsDocument:
    """Crea una versión DRAFT del SRS: materialización temprana del run.

    La llama ``draft_narrative`` apenas la prosa está lista, ANTES del commit:
    el usuario ve el documento en el visor (badge DRAFT) sin esperar a que
    termine el pipeline. ``commit_srs`` la promueve in-place a CANDIDATE
    (misma fila, mismo número); un run abortado deja el DRAFT visible como
    foto del intento. No es base de seed ni «última» (``get_latest_srs``
    salta los DRAFT). La numeración usa max(version)+1 sobre TODAS las filas,
    así que un DRAFT huérfano nunca colisiona.
    """
    cur = await session.scalar(
        select(func.max(SrsDocument.version)).where(
            SrsDocument.project_id == project_id
        )
    )
    version = (cur or 0) + 1
    row = SrsDocument(
        project_id=project_id,
        version=version,
        status=SrsStatus.DRAFT,
        structure=payload.get("structure", []),
        narrative=payload.get("narrative", {}),
        markdown=payload.get("markdown", ""),
        quality_summary=payload.get("quality_summary", {}),
        coverage=payload.get("coverage", {}),
        traceability=payload.get("traceability", {}),
        review_flags=payload.get("review_flags", {}),
        requirement_codes=payload.get("requirement_codes", []),
        requirement_count=payload.get("requirement_count", 0),
        generated_by=payload.get("generated_by", "agent"),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def promote_srs_draft(
    session: AsyncSession,
    project_id: int,
    version: int,
    payload: dict[str, Any],
) -> SrsDocument:
    """Promueve in-place un DRAFT a CANDIDATE con el payload final del commit.

    Misma fila y mismo número de versión: el DRAFT que el usuario vio durante
    el run se convierte en la versión formal. Solo aplica desde DRAFT (un
    CANDIDATE/LOCKED/DISCARDED con ese número es un error de programación).
    """
    s = await get_srs_version(session, project_id, version)
    if s is None:
        raise KeyError(f"srs version {version} not found")
    if s.status != SrsStatus.DRAFT:
        raise ValueError(
            f"srs version {version} no es DRAFT (status={s.status.value})"
        )
    s.status = SrsStatus.CANDIDATE
    s.structure = payload.get("structure", s.structure)
    s.narrative = payload.get("narrative", s.narrative)
    s.markdown = payload.get("markdown", s.markdown)
    s.quality_summary = payload.get("quality_summary", s.quality_summary)
    s.coverage = payload.get("coverage", s.coverage)
    s.traceability = payload.get("traceability", s.traceability)
    s.review_flags = payload.get("review_flags", s.review_flags)
    s.requirement_codes = payload.get("requirement_codes", s.requirement_codes)
    s.requirement_count = payload.get("requirement_count", s.requirement_count)
    await session.commit()
    await session.refresh(s)
    return s


async def discard_srs_version(
    session: AsyncSession, project_id: int, version: int
) -> SrsDocument:
    """Descarta (soft) una versión del SRS: estado DISCARDED, la fila queda.

    Soft y no hard delete por dos razones: ``create_srs`` numera con
    max(version)+1 sobre TODAS las filas, así que el número jamás se reutiliza
    (con hard delete, descartar v9-v11 haría que la próxima versión vuelva a
    llamarse v9); y la fila preservada mantiene la auditoría. ``get_latest_srs``
    filtra las DISCARDED, así que descartar la última restaura a la versión
    previa como base del seed. LOCKED no se descarta (snapshot inmutable que
    consume la fase de diseño).
    """
    s = await get_srs_version(session, project_id, version)
    if s is None:
        raise KeyError(f"srs version {version} not found")
    if s.status == SrsStatus.LOCKED:
        raise ValueError("LOCKED SRS no se puede descartar")
    if s.status == SrsStatus.DISCARDED:
        raise ValueError("SRS version already discarded")
    s.status = SrsStatus.DISCARDED
    await session.commit()
    await session.refresh(s)
    return s


async def update_srs(
    session: AsyncSession,
    project_id: int,
    version: int,
    *,
    status: SrsStatus | None = None,
    narrative: dict | None = None,
    review_flags: dict | None = None,
) -> SrsDocument:
    """Mutación controlada de una versión del SRS.

    - ``status``: transición del ciclo de vida. Al pasar a IN_REVIEW se fija
      ``reviewed_at``; al pasar a LOCKED se fija ``locked_at`` y se impide
      seguir editando (el router valida que no venga de LOCKED).
    - ``narrative`` / ``review_flags``: edición humana de la prosa y de la cola
      de pendientes. Solo aplican si el documento no está LOCKED.
    """
    s = await get_srs_version(session, project_id, version)
    if s is None:
        raise KeyError(f"srs version {version} not found")
    if s.status == SrsStatus.LOCKED and status is None:
        raise ValueError("LOCKED SRS is immutable")
    if status is SrsStatus.DISCARDED:
        # El descarte tiene sus propias guardas (LOCKED no se descarta,
        # re-discard rechazado): solo pasa por discard_srs_version.
        raise ValueError("use discard_srs_version to discard a version")
    if status is not None:
        s.status = status
        if status == SrsStatus.IN_REVIEW and s.reviewed_at is None:
            s.reviewed_at = datetime.utcnow()
        elif status == SrsStatus.LOCKED:
            s.locked_at = datetime.utcnow()
    if narrative is not None and s.status != SrsStatus.LOCKED:
        s.narrative = narrative
    if review_flags is not None and s.status != SrsStatus.LOCKED:
        s.review_flags = review_flags
    await session.commit()
    await session.refresh(s)
    return s


async def patch_narrative_section(
    session: AsyncSession,
    project_id: int,
    version: int,
    *,
    section_id: str,
    text: str,
    note: str | None = None,
) -> SrsDocument:
    """Edita UNA subsección authored de una versión del SRS en el lugar.

    Anti-cascada (sesión 17 de Planitrack2.0): hasta ahora la única vía para
    materializar un ajuste de redacción era seed + draft_narrative + commit
    (una versión NUEVA con re-proyección completa por más mínimo el cambio).
    Este parche escribe la clave en el JSON de narrative, regenera el
    markdown proyectado (mismas secciones projected, prosa actualizada) y
    queda auditado en review_flags.narrative_patches (últimos 20). Sin LLM,
    sin versión nueva. LOCKED es inmutable y las DISCARDED no se editan.
    """
    s = await get_srs_version(session, project_id, version)
    if s is None:
        raise KeyError(f"srs version {version} not found")
    if s.status == SrsStatus.LOCKED:
        raise ValueError("LOCKED SRS is immutable")
    if s.status == SrsStatus.DISCARDED:
        raise ValueError("SRS version is discarded")
    old = s.narrative.get(section_id)
    if old is None:
        raise KeyError(f"narrative section {section_id} not found")
    if isinstance(old, str) and old.strip() == text.strip():
        raise ValueError("patch is a no-op: the section already has that text")

    narrative = dict(s.narrative)
    narrative[section_id] = text
    built = await build_srs(
        session,
        project_id,
        project_name=(
            await session.scalar(
                select(Project.name).where(Project.id == project_id)
            )
        )
        or "",
        narrative=narrative,
        structure=s.structure or None,
    )
    s.narrative = narrative
    s.markdown = built["markdown"]

    # Auditoría: cola acotada (20) dentro de review_flags; la fila NO cambia
    # de estado, así el visor no ve saltos de versión ni de badge.
    flags = dict(s.review_flags or {})
    patches = list(flags.get("narrative_patches") or [])
    patches.append(
        {
            "section_id": section_id,
            "note": note,
            "at": datetime.utcnow().isoformat(timespec="seconds"),
        }
    )
    flags["narrative_patches"] = patches[-20:]
    s.review_flags = flags

    await session.commit()
    await session.refresh(s)
    return s


def srs_commit_signature(
    narrative: dict,
    *,
    statements: list[tuple[str, str]],
    requirement_count: int,
    goals_summary: dict | None = None,
    coverage_totals: dict | None = None,
) -> str:
    """Huella del contenido de un SRS candidato (anti-commit-no-op).

    Cubre lo authored de la prosa, el catálogo de requerimientos con sus
    enunciados (una edición de texto sin cambio de código TAMBIÉN cambia la
    versión: §2.2 se re-proyecta desde el store), el conteo, el resumen de
    goals y los totales de cobertura. El markdown NO entra: es derivado.

    El commit persiste la firma en ``review_flags.commit_signature``; el
    guard compara la firma nueva contra la PERSISTIDA (versiones previas sin
    firma → commit normal, retrocompatible).
    """
    basis = {
        "narrative": {
            k: v
            for k, v in sorted(narrative.items())
            if isinstance(v, str) and v.strip()
        },
        "statements": sorted(f"{code}\n{stmt or ''}" for code, stmt in statements),
        "requirement_count": requirement_count,
        "goals": goals_summary or {},
        "coverage_totals": coverage_totals or {},
    }
    return hashlib.sha256(
        json.dumps(basis, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


async def reproject_srs(
    session: AsyncSession,
    project_id: int,
    version: int,
    *,
    markdown: str,
    requirement_codes: list[str],
    requirement_count: int,
    coverage: dict | None = None,
    traceability: dict | None = None,
) -> SrsDocument:
    """Refresca SOLO las secciones proyectadas de una versión.

    Re-renderiza el markdown desde RequirementItem y actualiza codes/count (y
    opcionalmente coverage/traceability), sin tocar la prosa editada
    (``narrative``) ni el estado. No se permite si está LOCKED.
    """
    s = await get_srs_version(session, project_id, version)
    if s is None:
        raise KeyError(f"srs version {version} not found")
    if s.status == SrsStatus.LOCKED:
        raise ValueError("LOCKED SRS is immutable")
    s.markdown = markdown
    s.requirement_codes = requirement_codes
    s.requirement_count = requirement_count
    if coverage is not None:
        s.coverage = coverage
    if traceability is not None:
        s.traceability = traceability
    await session.commit()
    await session.refresh(s)
    return s
