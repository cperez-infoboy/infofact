"""Grouping review: detect duplicate requirements in the LIVE store and
serialize a human-editable merge plan (Markdown).

Reuses the consolidation primitives (exact_dedup + embeddings + LLM judge +
union-find clustering) but operates on PERSISTED ``RequirementItem`` rows
instead of freshly-extracted ``RawRequirement`` objects, and captures BOTH
verbatim and semantic duplicates. ``consolidate`` collapses verbatim buckets
BEFORE clustering, so it would surface them only as merged reps and never as
``DuplicateGroup``; this module expands each cluster's reps to their full
verbatim buckets so the plan folds twins together.

The plan persists as DB rows (``grouping_plans`` + ``grouping_groups``, see
backend/services/grouping_store.py); the Markdown is a read-only export
(``serialize_plan_md``). The user edits it structurally via the requirements
UI or the grouping tools; ``apply_grouping_plan`` runs the merges.
``serialize_plan_md`` / ``parse_plan_md`` are exact inverses (round-trip).

Never mutates the store — building a plan is read-only.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from backend.agents.pipelines.consolidation import (
    DEFAULT_DUPLICATE_THRESHOLD,
    DEFAULT_MAX_DUPLICATE_CANDIDATES,
    DEFAULT_STRICT_DUPLICATE_THRESHOLD,
    _cluster_duplicates,
    _duplicate_candidates,
    _judge_duplicates,
    embed_texts,
    exact_dedup,
)
from sqlalchemy import select

from backend.agents.pipelines.extraction import RawRequirement
from backend.models.project_document import ProjectDocument
from backend.models.requirement import ReqType
from backend.agents.pipelines.consolidation import embed_texts_cached
from backend.services.doc_id import rebase_document_id
from backend.services.requirement_store import _source_list, list_requirements

logger = logging.getLogger(__name__)


# MoSCoW ranking for keeper selection (lower wins): must > should > could > wont.
_PRIORITY_RANK = {"must": 0, "should": 1, "could": 2, "wont": 3}


def _keeper_key(it) -> tuple:
    """Sort key for the grouping keeper (lower tuple wins).

    The client's EXPLICIT priority takes precedence: an item whose MoSCoW came
    from the document (``explicit_priority=True``) wins over a verb-inferred
    one, even when the inferred MoSCoW is "higher". Among the same
    explicit-ness, higher MoSCoW rank wins; extraction confidence is the last
    tiebreaker (the historical behavior).
    """
    prio_rank = _PRIORITY_RANK.get(
        it.priority.value if it.priority else "", 9
    )
    return (not bool(it.explicit_priority), prio_rank, -(it.confidence or 0.0))


def _cluster_keeper(cluster, meta):
    """Pick a cluster's survivor via ``_keeper_key`` (explicit > MoSCoW > confidence)."""
    return min(
        (rep for _idx, rep in cluster),
        key=lambda r: _keeper_key(meta[r.id]),
    )


# ---------------------------------------------------------------------------
# Plan data shapes
# ---------------------------------------------------------------------------

@dataclass
class Group:
    """One proposed merge: the keeper absorbs the members (sources unioned)."""
    keeper_code: str
    member_codes: list[str]
    reason: str = "duplicado detectado"
    confidence: float = 0.0


@dataclass
class GroupingPlan:
    """A review-time grouping proposal over the live store. Never mutates."""
    groups: list[Group] = field(default_factory=list)
    generated_at: str = ""
    project: str = ""
    status: str = "proposed"  # proposed | applied | partially-applied
    # Alcance del run (solo informativo): considered = items vivos tras aplicar
    # los filtros types/documents; scope = etiqueta legible del alcance. NO se
    # persisten: persist_plan solo lee .groups.
    considered: int = 0
    scope: str = ""
    # Wall-clock (ms) per stage when the run carries ``on_progress`` (progress
    # banner). Informational only: persist_plan reads ``.groups`` alone.
    timings: dict[str, int] = field(default_factory=dict)

    @classmethod
    def empty(
        cls,
        *,
        project: str = "",
        considered: int = 0,
        scope: str = "",
        timings: dict[str, int] | None = None,
    ) -> "GroupingPlan":
        return cls(
            generated_at=_now_iso(),
            project=project,
            considered=considered,
            scope=scope,
            timings=timings or {},
        )


# ---------------------------------------------------------------------------
# Build (reuse consolidation primitives over the live store)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _to_raw(item) -> RawRequirement:
    """Adapt a RequirementItem to the consolidation input shape.

    The code (REQ-NNN) becomes ``RawRequirement.id`` so every DuplicateGroup
    reference survives serialization in REQ-codes. ``source_span`` / ``section``
    are unused by the dedup path; ``confidence`` is preserved so the
    representative pick (highest confidence, non-dangling) is stable.
    """
    return RawRequirement(
        id=item.code,
        statement=item.statement,
        source_span="",
        section="",
        confidence=float(item.confidence or 0.0),
    )


# ---------------------------------------------------------------------------
# Scope filters (types / documents)
# ---------------------------------------------------------------------------

def _normalize_types(types: list[str]) -> set[ReqType]:
    """Valida y normaliza la lista de ``types`` a valores ``ReqType``.

    Cada valor se acepta tal cual el enum (case-insensitive, sin espacios).
    Un valor invalido lanza ``ValueError`` listando los validos para que el
    modelo (o el usuario) corrija sin adivinar.
    """
    valid = {t.value: t for t in ReqType}
    out: set[ReqType] = set()
    invalid: list[str] = []
    for raw in types:
        val = raw.strip().lower()
        if val in valid:
            out.add(valid[val])
        else:
            invalid.append(raw)
    if invalid:
        raise ValueError(
            f"types invalidos: {invalid}. Valores validos: "
            + ", ".join(t.value for t in ReqType)
        )
    return out


async def _resolve_document_filter(
    session, project_id: int, documents: list[str]
) -> set[str]:
    """Resuelve nombres de documento (pedidos por el usuario) a ``rel_path``.

    Acepta filename (``spec.pdf``), rel_path (``docs/spec.pdf``) o path
    absoluto del container (``/workspaces/{slug}/docs/spec.pdf``); matchea
    contra las filas ``ProjectDocument`` del proyecto y devuelve la UNION de
    los rel_paths cubiertos (un filename repetido en dos directorios cubre
    ambos). Un nombre sin match lanza ``ValueError`` listando los documentos
    disponibles del proyecto.
    """
    rows = (
        await session.scalars(
            select(ProjectDocument).where(
                ProjectDocument.project_id == project_id
            )
        )
    ).all()
    allowed: set[str] = set()
    unmatched: list[str] = []
    for name in documents:
        wanted = name.strip()
        matched = {
            d.rel_path
            for d in rows
            if wanted == d.rel_path
            or wanted == d.filename
            or wanted.endswith("/" + d.rel_path)
        }
        if matched:
            allowed.update(matched)
        else:
            unmatched.append(wanted)
    if unmatched:
        available = ", ".join(sorted(d.rel_path for d in rows)) or "(ninguno)"
        raise ValueError(
            f"documentos no encontrados: {unmatched}. Documentos del "
            f"proyecto: {available}"
        )
    return allowed


def _item_in_documents(item, allowed: set[str]) -> bool:
    """True si el item proviene de alguno de los rel_paths permitidos.

    ``document_id`` viene rebasado a la convencion del container
    (``/workspaces/{slug}/{rel_path}``) via ``_source_list``, asi que el join
    por sufijo ``"/" + rel_path`` tambien matchea filas legacy (host path).
    Items manuales (``source`` vacio) quedan FUERA cuando hay filtro de
    documento activo. Los sources en LISTA (post-merge) tambien participan:
    antes solo el dict single matcheaba y los items fusionados quedaban fuera
    del scope sin avisar.
    """
    for src in _source_list(item):
        doc_id = src.get("document_id")
        if isinstance(doc_id, str) and any(
            doc_id.endswith("/" + rel) for rel in allowed
        ):
            return True
    return False


def _scope_label(
    type_filter: set[ReqType] | None, doc_filter: set[str] | None
) -> str:
    """Etiqueta legible del alcance (para el resumen del chat y del plan)."""
    parts: list[str] = []
    if type_filter is not None:
        parts.append(
            "tipos: " + ", ".join(sorted(t.value for t in type_filter))
        )
    if doc_filter is not None:
        parts.append(
            "documentos: " + ", ".join(sorted(doc_filter))
        )
    return " · ".join(parts)


async def build_grouping_plan(
    session,
    project_id: int,
    *,
    project: str = "",
    duplicate_threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
    strict_duplicate_threshold: float = DEFAULT_STRICT_DUPLICATE_THRESHOLD,
    max_duplicate_candidates: int = DEFAULT_MAX_DUPLICATE_CANDIDATES,
    types: list[str] | None = None,
    documents: list[str] | None = None,
    on_progress=None,
) -> GroupingPlan:
    """Detect duplicate groups among the live (non-soft-deleted) requirements.

    Two sources of duplicates, both surfaced (``consolidate`` only reports the
    second):
      (A) Verbatim buckets — ``exact_dedup`` groups by normalized statement; a
          bucket with >1 item is a near-identical set.
      (B) Semantic clusters — embedding similarity >= strict auto-merges, the
          borderline band [dup, strict) is LLM-judged, union-find clusters the
          confirmed pairs.

    Each cluster's representative is the keeper; members expand to the FULL
    verbatim bucket of every rep in the cluster (so a semantic link between two
    reps also folds their verbatim twins). Buckets whose rep joins no cluster
    are emitted as standalone verbatim groups.

    Optional scope filters (applied POST-LOAD over the live items, so the
    store signature stays untouched):
      - ``types``: only these ReqType values (invalid -> ValueError).
      - ``documents``: only items whose source document matches one of these
        names (filename | rel_path | absolute container path, resolved against
        the ProjectDocument rows of the project). Manual items (source=None)
        are EXCLUDED when a document filter is active.
    ``considered``/``scope`` record the effective scope (informational only).

    Idempotent by construction: it always reads the current store, so after a
    merge the merged rows are excluded and re-running proposes only what's left.
    """
    timings: dict[str, int] = {}
    stage_t0: dict[str, float] = {}

    async def _emit_stage(stage: str, message: str) -> None:
        """phase:'start' + t0 for the stage elapsed (the /agrupar banner)."""
        if on_progress is None:
            return
        stage_t0[stage] = time.monotonic()
        await on_progress({
            "stage": stage, "message": message, "phase": "start",
        })

    async def _end_stage(stage: str) -> None:
        """phase:'end' with elapsed_ms; accumulates the run's timing."""
        if on_progress is None:
            return
        t0 = stage_t0.pop(stage, None)
        if t0 is None:
            return
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        timings[stage] = elapsed_ms
        await on_progress({
            "stage": stage, "message": "", "phase": "end",
            "elapsed_ms": elapsed_ms,
        })

    await _emit_stage("load", "cargando requerimientos del store…")
    items = await list_requirements(session, project_id)
    type_filter = _normalize_types(types) if types else None
    doc_filter = (
        await _resolve_document_filter(session, project_id, documents)
        if documents
        else None
    )
    if type_filter is not None:
        items = [it for it in items if it.type in type_filter]
    if doc_filter is not None:
        items = [it for it in items if _item_in_documents(it, doc_filter)]
    considered = len(items)
    scope = _scope_label(type_filter, doc_filter)
    await _end_stage("load")
    if considered < 2:
        return GroupingPlan.empty(
            project=project, considered=considered, scope=scope,
            timings=timings,
        )

    shims = [_to_raw(it) for it in items]
    # code -> RequirementItem, so keeper selection can read priority + explicit_priority.
    meta = {it.code: it for it in items}

    # (A) verbatim buckets; pick the keeper per bucket by EXPLICIT priority >
    # MoSCoW rank > confidence (was: confidence only).
    await _emit_stage("dedup", f"{considered} requerimientos · dedup exacto")
    buckets = exact_dedup(shims)
    rep_of: dict[str, list[RawRequirement]] = {}
    reps: list[RawRequirement] = []
    for bucket in buckets:
        rep = min(bucket, key=lambda r: _keeper_key(meta[r.id]))
        rep_of[rep.id] = bucket
        reps.append(rep)
    await _end_stage("dedup")

    # (B) semantic clustering over the reps.
    await _emit_stage("embedding", f"embeddings de {len(reps)} enunciados")
    vectors = await embed_texts_cached(session, [r.statement for r in reps])
    sim = vectors @ vectors.T
    await _end_stage("embedding")
    candidates = _duplicate_candidates(
        reps, sim, duplicate_threshold, strict_duplicate_threshold,
        max_duplicate_candidates,
    )
    await _emit_stage("judge", f"juzgando pares borderline ({len(candidates)})")
    confirmed = await _judge_duplicates(reps, candidates, on_progress=on_progress)
    await _end_stage("judge")
    await _emit_stage("cluster", "clustering y armado de grupos")
    clusters = _cluster_duplicates(
        reps, sim, strict_duplicate_threshold, confirmed,
    )

    # Assemble groups: expand each cluster rep to its full verbatim bucket.
    groups: list[Group] = []
    clustered_rep_ids: set[str] = set()
    for cluster in clusters:
        if len(cluster) <= 1:
            continue
        keeper = _cluster_keeper(cluster, meta)
        clustered_rep_ids.add(keeper.id)
        members: list[str] = []
        for _idx, rep in cluster:
            clustered_rep_ids.add(rep.id)
            if rep.id == keeper.id:
                continue
            for twin in rep_of.get(rep.id, [rep]):
                if twin.id != keeper.id and twin.id not in members:
                    members.append(twin.id)
        # Fold the keeper's own verbatim twins too.
        for twin in rep_of.get(keeper.id, [keeper]):
            if twin.id != keeper.id and twin.id not in members:
                members.append(twin.id)
        if members:
            groups.append(Group(
                keeper_code=keeper.id,
                member_codes=members,
                reason="duplicado semántico",
                confidence=round(float(keeper.confidence or 0.0), 2),
            ))

    # Standalone verbatim buckets whose rep joined no semantic cluster.
    for rep_id, bucket in rep_of.items():
        if rep_id in clustered_rep_ids or len(bucket) < 2:
            continue
        members = [m.id for m in bucket if m.id != rep_id]
        if members:
            groups.append(Group(
                keeper_code=rep_id,
                member_codes=members,
                reason="duplicado exacto",
                confidence=1.0,
            ))

    await _end_stage("cluster")
    return GroupingPlan(
        groups=groups,
        generated_at=_now_iso(),
        project=project,
        status="proposed",
        considered=considered,
        scope=scope,
        timings=timings,
    )


# ---------------------------------------------------------------------------
# Serialize / parse (round-trip Markdown)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_FM_LINE_RE = re.compile(r"^(\w+):\s*(.*?)\s*$")
_GROUP_HEADER_RE = re.compile(r"^##\s+Grupo\s+\d+", re.IGNORECASE)

_FIELD_PREFIXES = (
    "- **mantener:**",
    "- **fusionar:**",
    "- **razón:**",
    "- **confianza:**",
)


class PlanParseError(ValueError):
    """Raised when an edited plan breaks the rigid format.

    Actionable: the message names the line and what was expected, so the user
    (or the subagent) can fix the plan instead of guessing.
    """


def serialize_plan_md(plan: GroupingPlan) -> str:
    """Render a GroupingPlan as Markdown with YAML frontmatter.

    The format is rigid on purpose: ``parse_plan_md`` reads it back
    line-by-line, so every field has a fixed prefix. Field matching is
    case-insensitive on the prefix to tolerate light edits, but the structure
    (frontmatter + ``## Grupo N`` blocks) must be preserved.
    """
    lines = ["---"]
    lines.append(f"status: {plan.status}")
    lines.append(f"generated_at: {plan.generated_at}")
    lines.append(f"project: {plan.project}")
    lines.append("---")
    lines.append("")
    if not plan.groups:
        lines.append("_(Sin grupos de duplicados detectados.)_")
        return "\n".join(lines) + "\n"
    for n, group in enumerate(plan.groups, start=1):
        lines.append(f"## Grupo {n}")
        lines.append(f"- **Mantener:** {group.keeper_code}")
        lines.append(f"- **Fusionar:** {', '.join(group.member_codes)}")
        lines.append(f"- **Razón:** {group.reason}")
        lines.append(f"- **Confianza:** {group.confidence}")
        lines.append("")
    return "\n".join(lines)


def parse_plan_md(md: str) -> GroupingPlan:
    """Parse a (possibly user-edited) plan Markdown back into a GroupingPlan.

    Strict on field lines (a malformed one raises PlanParseError with the line
    number); lenient on structure (added/removed groups are fine, prose between
    groups is ignored). An empty ``Fusionar`` list drops the group (the user
    cleared the members → no-op). Unknown frontmatter keys are ignored.
    """
    fm_match = _FRONTMATTER_RE.match(md)
    if not fm_match:
        raise PlanParseError(
            "Falta el bloque de frontmatter (--- ... ---) al inicio del plan."
        )
    fm_body, doc_body = fm_match.group(1), fm_match.group(2)

    status = "proposed"
    generated_at = ""
    project = ""
    for i, raw in enumerate(fm_body.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _FM_LINE_RE.match(line)
        if not m:
            raise PlanParseError(
                f"Frontmatter línea {i}: se esperaba 'clave: valor', se obtuvo '{raw}'."
            )
        key, val = m.group(1), m.group(2)
        if key == "status":
            status = val
        elif key == "generated_at":
            generated_at = val
        elif key == "project":
            project = val

    groups: list[Group] = []
    current: dict | None = None
    doc_lines = doc_body.splitlines()
    for i, raw in enumerate(doc_lines, start=1):
        line = raw.rstrip()
        if not line.strip():
            continue
        if _GROUP_HEADER_RE.match(line):
            if current is not None:
                _finalize_group(current, groups, i)
            current = {
                "keeper": None, "members": [], "reason": "",
                "confidence": 0.0,
            }
            continue
        if current is None:
            continue  # prose between groups (intro / notes) is ignored
        stripped = line.strip().lower()
        if stripped.startswith("- **mantener:**"):
            current["keeper"] = line.strip()[len("- **Mantener:**"):].strip()
        elif stripped.startswith("- **fusionar:**"):
            raw_members = line.strip()[len("- **Fusionar:**"):].strip()
            current["members"] = [
                c.strip() for c in raw_members.split(",") if c.strip()
            ]
        elif stripped.startswith("- **razón:**"):
            current["reason"] = line.strip()[len("- **Razón:**"):].strip()
        elif stripped.startswith("- **confianza:**"):
            val = line.strip()[len("- **Confianza:**"):].strip()
            try:
                current["confidence"] = float(val)
            except ValueError:
                raise PlanParseError(
                    f"Línea {i}: 'Confianza' debe ser un número, se obtuvo '{val}'."
                )
        elif stripped.startswith("- "):
            # Unknown bullet under a group — surface it so a half-edited field
            # line is not silently dropped (a typo in 'Mantener' would otherwise
            # lose the keeper and the group silently).
            raise PlanParseError(
                f"Línea {i}: campo no reconocido en el grupo: '{line.strip()}'. "
                "Usar solo: Mantener / Fusionar / Razón / Confianza."
            )
        # Non-bullet lines under a group (continuation prose) are ignored.
    if current is not None:
        _finalize_group(current, groups, len(doc_lines))

    return GroupingPlan(
        groups=groups,
        generated_at=generated_at,
        project=project,
        status=status,
    )


def _finalize_group(current: dict, groups: list[Group], line: int) -> None:
    keeper = current.get("keeper")
    if not keeper:
        raise PlanParseError(
            f"Grupo cerrado cerca de la línea {line} sin campo 'Mantener' (keeper)."
        )
    members = current.get("members") or []
    if not members:
        return  # user cleared the members → drop the group (no-op merge)
    groups.append(Group(
        keeper_code=keeper,
        member_codes=members,
        reason=current.get("reason") or "duplicado detectado",
        confidence=float(current.get("confidence") or 0.0),
    ))
