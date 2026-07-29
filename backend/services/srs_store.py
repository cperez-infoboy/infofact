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

import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.requirement import ReqStatus, RequirementItem
from backend.models.srs import (
    FindingSeverity,
    FindingStatus,
    Goal,
    GoalLink,
    GoalStatus,
    RequirementFinding,
    SrsDocument,
    SrsStatus,
)

# Mismo alfabeto opaque que REQ (Crockford base32, sin I/L/O/U).
_CROCKFORD_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_GOAL_OPAQUE_LEN = 4
_MAX_ATTEMPTS = 10

# Estados de requerimiento que cuentan como "vivos" para el SRS y la
# trazabilidad (consistente con srs_builder._LIVE_STATUSES).
_LIVE_STATUSES = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)


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


def finding_to_dict(f: RequirementFinding) -> dict[str, Any]:
    return {
        "id": f.id,
        "project_id": f.project_id,
        "req_id": f.req_id,
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

    El análisis de calidad se recalcula entero en cada ``/srs``, así que se
    borran las filas previas (curación humana incluida) y se reinsertan las
    nuevas en estado ``OPEN``. ``findings`` viene de los motores; cada dict
    lleva scope/dimension/rule_id/severity/message/suggestion y opcionalmente
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
    """La versión más reciente del SRS del proyecto (o None)."""
    return await session.scalar(
        select(SrsDocument)
        .where(SrsDocument.project_id == project_id)
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
