"""NFR pipeline: architectural decisions derived from non-functional requirements.

Multi-pass pipeline (5 passes):

  Pass 1 — decision discovery (narrow schema: req_code + category + one-line
  decision_summary per NFR, no stack/patterns).
  Pass 2 — gap pass: re-scan NFR items whose REQ codes were not traced.
  Pass 3 — enrichment (candidate-constrained): full decisions (decision,
  stack_component, rationale, impact) + per-layer stack + data_consistency +
  patterns.
  Pass 4 — deterministic validation (orphan codes, valid categories,
  duplicate codes, minimum stack layers).
  Pass 5 — LLM critique covering documented failure modes (optional).

Takes the non-functional RequirementItems (types: performance, security,
usability, reliability, maintainability, compliance, constraint) and produces
concrete architectural decisions plus a recommended stack.

Design notes (mirrors mer_pipeline.py patterns):

- Does NOT write to the DB. Returns an NfrResult dataclass consumed by the
  analysis assembler / store layer.
- Uses structured_llm with narrow per-pass schemas for fence-tolerant
  structured output (Z.ai GLM wraps JSON in fences).
- Retry policy: centralized via _invoke_with_retry from _resilience.
- Prompts are in Spanish neutro (no voseo, no spanglish) following project
  conventions for LLM-facing text.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from backend.agents.llm import structured_llm
from backend.agents.pipelines._resilience import (
    DEFAULT_CONCURRENCY,
    _chunk,
    _format_feedback,
    _format_goals,
    _invoke_with_retry,
)

logger = logging.getLogger(__name__)

# Batch size for NFR discovery and enrichment passes. With 142 NFR items and
# batch=15, that's ~10 batches of ~15 items each — small enough for structured
# output to not truncate. Env-tunable for calibration.
_NFR_BATCH_SIZE = int(os.environ.get("INFOFACT_NFR_BATCH", "15"))

# RequirementItem types that feed this pipeline (anything that is not
# functional/data/process is an NFR). Kept in sync with ReqType.
_NFR_TYPES = frozenset(
    {
        "performance",
        "security",
        "usability",
        "reliability",
        "maintainability",
        "compliance",
        "constraint",
    }
)

_VALID_CATEGORIES = frozenset(
    {
        "performance",
        "security",
        "reliability",
        "data_consistency",
        "scalability",
        "maintainability",
        "usability",
    }
)


# ---------------------------------------------------------------------------
# LLM-facing schemas
# ---------------------------------------------------------------------------


class NfrDecision(BaseModel):
    """One architectural decision derived from a single NFR."""

    req_code: str = Field(
        description="Codigo REQ-XXXX del requerimiento no funcional.",
    )
    category: str = Field(
        description=(
            "Categoria del NFR: exactamente uno de performance | security | "
            "reliability | data_consistency | scalability | maintainability | "
            "usability."
        ),
    )
    decision: str = Field(
        description=(
            "Decision arquitectonica concreta derivada del NFR (ej. "
            "'Usar Redis para cache de sesion')."
        ),
    )
    stack_component: str = Field(
        default="",
        description=(
            "Tecnologia o patron especifico recomendado (ej. 'Redis 7', "
            "'JWT + refresh tokens', 'CQRS')."
        ),
    )
    rationale: str = Field(
        description="Por que esta decision aborda el NFR.",
    )
    impact: str = Field(
        default="",
        description="Impacto en otras partes del sistema (opcional).",
    )


class StackDecision(BaseModel):
    """One technology recommendation for a layer of the stack."""

    layer: str = Field(
        description=(
            "Capa a la que aplica: exactamente uno de frontend | backend | "
            "database | messaging | cache | deployment | monitoring."
        ),
    )
    technology: str = Field(
        description="Tecnologia recomendada (ej. 'PostgreSQL 16').",
    )
    rationale: str = Field(
        description="Por que esta tecnologia para esta capa.",
    )


# ---------------------------------------------------------------------------
# Narrow per-pass schemas (multi-pass pipeline)
# ---------------------------------------------------------------------------


class NfrDecisionCandidate(BaseModel):
    """Narrow schema for Pass 1/2: one-line decision per NFR."""

    req_code: str = Field(description="Codigo REQ-XXXX")
    category: str = Field(
        description=(
            "performance | security | reliability | data_consistency | "
            "scalability | maintainability | usability"
        ),
    )
    decision_summary: str = Field(
        description="Decision arquitectonica en una oracion",
    )


class NfrDiscoverySchema(BaseModel):
    """Pass 1 and Pass 2 output schema."""

    decisions: list[NfrDecisionCandidate]


class NfrBatchDetailSchema(BaseModel):
    """Pass 3 per-batch output: decisions only (stack is derived separately)."""

    decisions: list[NfrDecision]


class NfrDetailSchema(BaseModel):
    """Pass 3 output schema: full decisions + stack + patterns."""

    decisions: list[NfrDecision]
    stack: list[StackDecision]
    data_consistency: str = ""
    patterns: str = ""


class NfrStackSchema(BaseModel):
    """Pass 3b output schema: global stack derived from all decisions."""

    stack: list[StackDecision]
    data_consistency: str = ""
    patterns: str = ""


# Backward compat alias (old single-pass code referenced NfrResultSchema).
NfrResultSchema = NfrDetailSchema


class NfrCritiqueFinding(BaseModel):
    """Pass 5: critic finding."""

    req_code: str = Field(description="REQ afectado o 'GLOBAL'")
    issue_type: str = Field(
        description=(
            "generic_decision | orphan_nfr | unjustified_stack | "
            "contradiction | category_mismatch | missing_layer"
        ),
    )
    description: str
    severity: str = Field(description="blocker | warning | info")


class NfrCritiqueSchema(BaseModel):
    """Pass 5 output schema."""

    findings: list[NfrCritiqueFinding]


# ---------------------------------------------------------------------------
# Prompts (Spanish neutro — no voseo, no spanglish)
# ---------------------------------------------------------------------------

_NFR_DISCOVERY_PROMPT = (
    "Eres un analista de arquitectura de software. Tu UNICA tarea es "
    "identificar, para cada requerimiento no funcional, su categoria y una "
    "decision arquitectonica resumida en una oracion. NO disenes el stack "
    "ni los patrones globales — solo la decision por cada NFR.\n\n"
    "Reglas:\n"
    "- IDIOMA: manten el idioma original de los requerimientos.\n"
    "- category debe ser exactamente uno de: performance, security, "
    "reliability, data_consistency, scalability, maintainability, usability.\n"
    "- decision_summary: una oracion concreta y accionable (evita "
    "generalidades como 'usar buenas practicas').\n"
    "- req_code debe tomarse literalmente del input; no inventes codigos.\n"
    "- Si un NFR es ambiguo o no accionable, omitilo.\n"
    "Devuelve SOLO el objeto estructurado."
)

_NFR_GAP_PASS_PROMPT = (
    "Eres un analista de arquitectura. Los siguientes requerimientos no "
    "funcionales NO fueron cubiertos en la primera pasada de analisis. "
    "Revisa cada uno y determina si justifica una decision arquitectonica "
    "nueva. Si ninguno genera una decision nueva, devuelve una lista vacia.\n"
    "Devuelve SOLO el objeto estructurado."
)

_NFR_DETAIL_PROMPT = (
    "Eres un analista de arquitectura de software. Analiza los requerimientos "
    "no funcionales y produce decisiones arquitectonicas concretas con su "
    "tecnologia y patron asociados.\n\n"
    "Recibiras una lista de requerimientos no funcionales (tipos: performance, "
    "security, usability, reliability, maintainability, compliance, "
    "constraint) con sus codigos REQ-XXXX y enunciados, ademas de un listado "
    "de decisiones preliminares (req_code + category + decision_summary) que "
    "debes enriquecer. Tu tarea es transformar cada decision preliminar en "
    "una decision arquitectonica completa y proponer el stack tecnologico "
    "por capa.\n\n"
    "Reglas:\n"
    "- IDIOMA: manten el idioma original de los requerimientos en las "
    "descripciones. NO traduzcas.\n"
    "- Para cada decision preliminar, emite una decision arquitectonica "
    "completa y accionable (evita generalidades).\n"
    "- category debe ser exactamente uno de: performance, security, "
    "reliability, data_consistency, scalability, maintainability, usability.\n"
    "- stack: cubre como minimo las capas backend y database. Anade messaging, "
    "cache, deployment o monitoring solo si los NFRs las justifican.\n"
    "- rationale debe explicar por que la decision aborda el NFR, no "
    "simplemente repetir el enunciado.\n"
    "- req_code debe tomarse literalmente del input; no inventes codigos.\n"
    "- Si un NFR es ambiguo o no accionable, omitilo en lugar de inventar una "
    "decision generica.\n"
    "- data_consistency: describe la estrategia transaccional/de consistencia "
    "global del sistema (saga, outbox, eventual, strong) cuando aplique.\n"
    "- patterns: lista patrones arquitectonicos transversales recomendados.\n\n"
    "Devuelve SOLO el objeto estructurado."
)

_NFR_BATCH_DETAIL_PROMPT = (
    "Eres un analista de arquitectura de software. Recibiras un lote de "
    "decisiones preliminares (req_code + category + decision_summary) junto "
    "con los requerimientos originales que las originaron. Tu tarea es "
    "enriquecer cada decision preliminar con detalle completo.\n\n"
    "Reglas:\n"
    "- IDIOMA: manten el idioma original de los requerimientos en las "
    "descripciones. NO traduzcas.\n"
    "- Para cada decision preliminar, emite una decision arquitectonica "
    "completa y accionable (evita generalidades).\n"
    "- category debe ser exactamente uno de: performance, security, "
    "reliability, data_consistency, scalability, maintainability, usability.\n"
    "- stack_component: tecnologia o patron especifico recomendado.\n"
    "- rationale debe explicar por que la decision aborda el NFR, no "
    "simplemente repetir el enunciado.\n"
    "- req_code debe tomarse literalmente del input; no inventes codigos.\n"
    "- Si un NFR es ambiguo o no accionable, omitilo.\n"
    "Devuelve SOLO el objeto estructurado."
)

_NFR_STACK_PROMPT = (
    "Eres un analista de arquitectura de software. A partir del conjunto de "
    "decisiones arquitectonicas ya tomadas, deriva el stack tecnologico "
    "recomendado por capas y las estrategias transversales.\n\n"
    "Reglas:\n"
    "- IDIOMA: manten el idioma original de las decisiones.\n"
    "- stack: cubre como minimo las capas backend y database. Anade "
    "messaging, cache, deployment o monitoring solo si las decisiones las "
    "justifican.\n"
    "- rationale de cada layer debe explicar por que esa tecnologia, "
    "referenciando las decisiones que la motivan.\n"
    "- data_consistency: describe la estrategia transaccional/de consistencia "
    "global del sistema (saga, outbox, eventual, strong) cuando aplique.\n"
    "- patterns: lista patrones arquitectonicos transversales recomendados.\n"
    "Devuelve SOLO el objeto estructurado."
)

_NFR_CRITIQUE_PROMPT = (
    "Eres un crítico de arquitectura de software. Se te da el analisis NFR "
    "(decisiones + stack) generado a partir de requerimientos no funcionales. "
    "Tu tarea es identificar problemas:\n\n"
    "Categorias de fallo a revisar:\n"
    "1. generic_decision: una decision vaga o no accionable.\n"
    "2. orphan_nfr: un NFR del input que no tiene decision asociada.\n"
    "3. unjustified_stack: una tecnologia del stack sin justificacion clara.\n"
    "4. contradiction: dos decisiones que se contradicen entre si.\n"
    "5. category_mismatch: la categoria asignada no corresponde al NFR.\n"
    "6. missing_layer: una capa critica del stack ausente (ej. database).\n\n"
    "Si el analisis esta correcto, devuelve findings vacio.\n"
    "Devuelve SOLO el objeto estructurado."
)

# Original prompt kept for backward compatibility.
NFR_PROMPT = _NFR_DETAIL_PROMPT


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class NfrResult:
    """Output of the NFR pipeline (decisions, stack, patterns, stats)."""

    decisions: list[NfrDecision] = field(default_factory=list)
    stack: list[StackDecision] = field(default_factory=list)
    data_consistency: str = ""
    patterns: str = ""
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_nfr_items_text(
    items,
    project_name: str = "",
    project_description: str = "",
    softgoals: list[Any] | None = None,
    feedback: str = "",
) -> str:
    """Build the user message text from NFR requirement items."""
    lines: list[str] = []
    if feedback:
        lines.append(_format_feedback(feedback).rstrip())
        lines.append("")
    if softgoals:
        lines.append(
            _format_goals(
                softgoals,
                section_title="ATRIBUTOS DE CALIDAD (SOFTGOALS)",
            ).rstrip()
        )
        lines.append("")
    if project_name:
        lines.append(f"PROYECTO: {project_name}")
    if project_description:
        lines.append(f"DESCRIPCION: {project_description}")
    lines.append("")

    for it in items:
        lines.append(f"REQ_CODE: {it.code}")
        lines.append(f"TYPE: {it.type.value}")
        lines.append(f"STATEMENT: {it.statement}")
        if it.source:
            src = it.source
            if isinstance(src, dict):
                quote = src.get("quote") or src.get("section") or ""
            elif isinstance(src, list) and src:
                quote = (
                    src[0].get("quote", "")
                    if isinstance(src[0], dict)
                    else str(src[0])
                )
            else:
                quote = str(src)
            if quote:
                lines.append(f"SOURCE: {quote[:300]}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pass 1: decision discovery (narrow schema)
# ---------------------------------------------------------------------------


async def _discover_nfr_decisions(
    items,
    project_name: str,
    project_description: str,
    softgoals: list[Any] | None = None,
    feedback: str = "",
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[NfrDecisionCandidate]:
    """Pass 1: batched discovery of one-line decisions per NFR.

    Items are split into batches of ``_NFR_BATCH_SIZE`` to avoid structured
    output truncation with large NFR sets. Batches run concurrently up to
    ``concurrency`` parallel calls.
    """
    batches = list(_chunk(items, _NFR_BATCH_SIZE))
    if len(batches) <= 1:
        return await _discover_nfr_batch(
            items, project_name, project_description,
            softgoals=softgoals, feedback=feedback,
        )

    sem = asyncio.Semaphore(concurrency)

    async def _guarded(batch):
        async with sem:
            return await _discover_nfr_batch(
                batch, project_name, project_description,
                softgoals=softgoals, feedback=feedback,
            )

    results = await asyncio.gather(
        *[_guarded(b) for b in batches], return_exceptions=True
    )
    all_candidates: list[NfrDecisionCandidate] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.warning(
                "_discover_nfr_decisions: batch %d/%d failed: %s",
                i + 1, len(batches), r,
            )
            continue
        all_candidates.extend(r)
    logger.info(
        "_discover_nfr_decisions: %d batches -> %d candidates",
        len(batches), len(all_candidates),
    )
    return all_candidates


async def _discover_nfr_batch(
    items_batch,
    project_name: str,
    project_description: str,
    softgoals: list[Any] | None = None,
    feedback: str = "",
) -> list[NfrDecisionCandidate]:
    """Discover decisions for a single batch of NFR items."""
    user_text = _build_nfr_items_text(
        items_batch, project_name, project_description,
        softgoals=softgoals, feedback=feedback,
    )
    msgs = [("system", _NFR_DISCOVERY_PROMPT), ("human", user_text)]
    llm = structured_llm(NfrDiscoverySchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label=f"nfr_discover ({len(items_batch)} items)"
        )
        return list(result.decisions)
    except Exception:
        logger.exception(
            "_discover_nfr_batch: all retries exhausted for %d items",
            len(items_batch),
        )
        return []


# ---------------------------------------------------------------------------
# Pass 2: gap pass for uncovered NFRs
# ---------------------------------------------------------------------------


async def _gap_pass_nfr(
    items,
    candidates: list[NfrDecisionCandidate],
    project_name: str,
    project_description: str,
    softgoals: list[Any] | None = None,
    feedback: str = "",
) -> list[NfrDecisionCandidate]:
    """Pass 2: detect uncovered REQ codes and re-scan them."""
    input_codes = {it.code for it in items}
    covered_codes = {c.req_code for c in candidates}
    uncovered = input_codes - covered_codes
    if not uncovered:
        return []

    uncovered_items = [it for it in items if it.code in uncovered]
    user_text = _build_nfr_items_text(
        uncovered_items, project_name, project_description,
        softgoals=softgoals, feedback=feedback,
    )
    already = ", ".join(c.req_code for c in candidates) or "(ninguna)"
    user_text += (
        f"\nDECISIONES YA IDENTIFICADAS: {already}\n"
        "Revisa SOLO los requerimientos anteriores y determina si generan "
        "decisiones NUEVAS (no listadas arriba).\n"
    )
    msgs = [("system", _NFR_GAP_PASS_PROMPT), ("human", user_text)]
    llm = structured_llm(NfrDiscoverySchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="nfr_gap_pass"
        )
        return list(result.decisions)
    except Exception:
        logger.exception("_gap_pass_nfr: all retries exhausted")
        return []


# ---------------------------------------------------------------------------
# Pass 3: enrichment (candidate-constrained full decisions + stack)
# ---------------------------------------------------------------------------


async def _enrich_nfr_decisions(
    items,
    candidates: list[NfrDecisionCandidate],
    project_name: str,
    project_description: str,
    softgoals: list[Any] | None = None,
    feedback: str = "",
    concurrency: int = DEFAULT_CONCURRENCY,
) -> tuple[list[NfrDecision], list[StackDecision], str, str] | None:
    """Pass 3: batched enrichment of candidates into full decisions.

    Candidates are split into batches of ``_NFR_BATCH_SIZE`` to avoid
    structured output truncation. Each batch produces enriched decisions
    for its candidates only. After all batches, a separate call derives the
    global stack/patterns from the full decision set.

    Returns ``(decisions, stack, data_consistency, patterns)`` or ``None``
    on total failure.
    """
    if not candidates:
        return [], [], "", ""

    # Build a lookup of items by code so each batch only sends its own items.
    items_by_code = {it.code: it for it in items}

    batches = list(_chunk(candidates, _NFR_BATCH_SIZE))
    if len(batches) <= 1:
        decisions = await _enrich_nfr_batch(
            items, candidates, project_name, project_description,
            softgoals=softgoals, feedback=feedback,
        )
    else:
        sem = asyncio.Semaphore(concurrency)

        async def _guarded(batch):
            async with sem:
                batch_items = [
                    items_by_code[c.req_code]
                    for c in batch
                    if c.req_code in items_by_code
                ]
                return await _enrich_nfr_batch(
                    batch_items, batch, project_name, project_description,
                    softgoals=softgoals, feedback=feedback,
                )

        results = await asyncio.gather(
            *[_guarded(b) for b in batches], return_exceptions=True
        )
        decisions: list[NfrDecision] = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning(
                    "_enrich_nfr_decisions: batch %d/%d failed: %s",
                    i + 1, len(batches), r,
                )
                continue
            decisions.extend(r)
        logger.info(
            "_enrich_nfr_decisions: %d batches -> %d decisions",
            len(batches), len(decisions),
        )

    # Derive global stack/patterns from the full decision set.
    stack, data_consistency, patterns = await _derive_stack(
        decisions, project_name, project_description,
        softgoals=softgoals,
    )
    return decisions, stack, data_consistency, patterns


async def _enrich_nfr_batch(
    items_batch,
    candidates_batch: list[NfrDecisionCandidate],
    project_name: str,
    project_description: str,
    softgoals: list[Any] | None = None,
    feedback: str = "",
) -> list[NfrDecision]:
    """Enrich a single batch of candidates into full decisions."""
    user_text = _build_nfr_items_text(
        items_batch, project_name, project_description,
        softgoals=softgoals, feedback=feedback,
    )
    candidate_lines = []
    for c in candidates_batch:
        candidate_lines.append(
            f"- [{c.req_code}] ({c.category}) {c.decision_summary}"
        )
    user_text += (
        "\nDECISIONES PRELIMINARES (enriquece cada una):\n"
        + "\n".join(candidate_lines)
        + "\n"
    )
    msgs = [("system", _NFR_BATCH_DETAIL_PROMPT), ("human", user_text)]
    llm = structured_llm(NfrBatchDetailSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs,
            context_label=f"nfr_enrich ({len(candidates_batch)} candidates)",
        )
        return list(result.decisions)
    except Exception:
        logger.exception(
            "_enrich_nfr_batch: all retries exhausted for %d candidates",
            len(candidates_batch),
        )
        return []


async def _derive_stack(
    decisions: list[NfrDecision],
    project_name: str,
    project_description: str,
    softgoals: list[Any] | None = None,
) -> tuple[list[StackDecision], str, str]:
    """Derive global stack/patterns from the consolidated decision set.

    A separate LLM call that only asks for stack layers + consistency +
    patterns — small output, no truncation risk regardless of input size.
    """
    if not decisions:
        return [], "", ""

    decision_lines = []
    for d in decisions[:50]:
        entry = f"- [{d.req_code}] ({d.category}) {d.decision}"
        if d.stack_component:
            entry += f" — {d.stack_component}"
        decision_lines.append(entry)
    decision_summary = "\n".join(decision_lines)
    if len(decisions) > 50:
        decision_summary += f"\n... ({len(decisions) - 50} mas)"

    header_lines = []
    if project_name:
        header_lines.append(f"PROYECTO: {project_name}")
    if project_description:
        header_lines.append(f"DESCRIPCION: {project_description}")
    header = "\n".join(header_lines)

    softgoals_block = ""
    if softgoals:
        softgoals_block = _format_goals(
            softgoals, section_title="ATRIBUTOS DE CALIDAD (SOFTGOALS)"
        )

    user_text = (
        f"{header}\n\n"
        f"{softgoals_block}"
        f"DECISIONES ARQUITECTONICAS TOMADAS ({len(decisions)} total):\n"
        f"{decision_summary}\n\n"
        f"Deriva el stack tecnologico por capas a partir de estas decisiones.\n"
    )
    msgs = [("system", _NFR_STACK_PROMPT), ("human", user_text)]
    llm = structured_llm(NfrStackSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="nfr_derive_stack"
        )
        return list(result.stack), result.data_consistency or "", result.patterns or ""
    except Exception:
        logger.exception("_derive_stack: all retries exhausted")
        return [], "", ""


# ---------------------------------------------------------------------------
# Pass 4: deterministic validation
# ---------------------------------------------------------------------------


def _validate_nfr(
    decisions: list[NfrDecision],
    stack: list[StackDecision],
    input_codes: set[str],
) -> list[str]:
    """Pass 4: deterministic validation checks (no LLM).

    Returns a list of warning strings.
    """
    warnings: list[str] = []

    # Check: all decision req_codes exist in input.
    for d in decisions:
        if d.req_code not in input_codes:
            warnings.append(
                f"Decision con req_code no presente en input: {d.req_code}"
            )

    # Check: valid categories.
    for d in decisions:
        if d.category not in _VALID_CATEGORIES:
            warnings.append(
                f"Categoria invalida '{d.category}' en decision {d.req_code}"
            )

    # Check: no duplicate req_codes.
    seen: set[str] = set()
    for d in decisions:
        if d.req_code in seen:
            warnings.append(f"Decision duplicada para {d.req_code}")
        seen.add(d.req_code)

    # Check: stack has at least backend and database layers.
    stack_layers = {s.layer for s in stack}
    for required in ("backend", "database"):
        if required not in stack_layers:
            warnings.append(f"Stack sin capa obligatoria: {required}")

    return warnings


# ---------------------------------------------------------------------------
# Pass 5: LLM critique
# ---------------------------------------------------------------------------


async def _critique_nfr(
    decisions: list[NfrDecision],
    stack: list[StackDecision],
    items,
    project_name: str,
    project_description: str,
    softgoals: list[Any] | None = None,
) -> list[NfrCritiqueFinding]:
    """Pass 5: LLM critique covering documented failure modes."""
    decision_lines = []
    for d in decisions:
        entry = f"- [{d.req_code}] ({d.category}) {d.decision}"
        if d.stack_component:
            entry += f" — {d.stack_component}"
        decision_lines.append(entry)
    decision_summary = "\n".join(decision_lines) or "(ninguna)"

    stack_lines = []
    for s in stack:
        stack_lines.append(f"- [{s.layer}] {s.technology} — {s.rationale}")
    stack_summary = "\n".join(stack_lines) or "(ninguno)"

    req_lines = [f"- {it.code}: {it.statement[:100]}" for it in items[:30]]
    req_summary = "\n".join(req_lines)
    if len(items) > 30:
        req_summary += f"\n... ({len(items) - 30} mas)"

    header_lines = []
    if project_name:
        header_lines.append(f"PROYECTO: {project_name}")
    if project_description:
        header_lines.append(f"DESCRIPCION: {project_description}")
    header = "\n".join(header_lines)

    softgoals_block = ""
    if softgoals:
        softgoals_block = _format_goals(
            softgoals, section_title="ATRIBUTOS DE CALIDAD (SOFTGOALS)"
        )

    user_text = (
        f"{header}\n\n"
        f"{softgoals_block}"
        f"REQUERIMIENTOS NO FUNCIONALES ORIGINALES:\n{req_summary}\n\n"
        f"DECISIONES GENERADAS:\n{decision_summary}\n\n"
        f"STACK RECOMENDADO:\n{stack_summary}\n"
    )
    msgs = [("system", _NFR_CRITIQUE_PROMPT), ("human", user_text)]
    llm = structured_llm(NfrCritiqueSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="nfr_critique"
        )
        return list(result.findings)
    except Exception:
        logger.exception("_critique_nfr: all retries exhausted")
        return []


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


async def analyze_nfrs(
    items,
    *,
    project_name: str = "",
    project_description: str = "",
    concurrency: int = DEFAULT_CONCURRENCY,
    softgoals: list[Any] | None = None,
    enable_critique: bool = True,
    feedback: str = "",
) -> NfrResult:
    """Analyze non-functional requirements and produce architectural decisions.

    Multi-pass pipeline:

      Pass 1 — decision discovery (narrow schema).
      Pass 2 — gap pass for uncovered REQ codes.
      Pass 3 — enrichment (full decisions + stack + patterns).
      Pass 4 — deterministic validation.
      Pass 5 — LLM critique (optional, enabled by default).

    Takes RequirementItem objects (caller should filter to NFR types —
    performance, security, usability, reliability, maintainability,
    compliance, constraint) and produces an NfrResult with concrete
    architectural decisions, a per-layer stack recommendation, and global
    consistency/patterns guidance.

    Each pass has its own retry loop (via _invoke_with_retry). Failed passes
    degrade gracefully instead of aborting the whole pipeline.
    """
    if not items:
        return NfrResult(stats={"input": 0})

    input_codes = {it.code for it in items}

    # Pass 1: batched decision discovery.
    candidates = await _discover_nfr_decisions(
        items, project_name, project_description,
        softgoals=softgoals, feedback=feedback,
        concurrency=concurrency,
    )

    # Pass 2: gap pass for uncovered requirements.
    gap_candidates = await _gap_pass_nfr(
        items, candidates, project_name, project_description,
        softgoals=softgoals, feedback=feedback,
    )
    all_candidates = candidates + gap_candidates

    # Pass 3: batched enrichment + separate stack derivation.
    enriched = await _enrich_nfr_decisions(
        items, all_candidates, project_name, project_description,
        softgoals=softgoals, feedback=feedback,
        concurrency=concurrency,
    )

    if enriched is None:
        return NfrResult(stats={
            "input": len(items),
            "error": "enrichment_failed",
            "decisions": 0,
            "stack": 0,
        })

    decisions, stack, data_consistency, patterns = enriched

    # Pass 4: deterministic validation.
    warnings = _validate_nfr(decisions, stack, input_codes)

    # Pass 5: LLM critique (optional).
    critique_findings: list[NfrCritiqueFinding] = []
    if enable_critique:
        critique_findings = await _critique_nfr(
            decisions, stack, items, project_name, project_description,
            softgoals=softgoals,
        )

    # Compute coverage stats.
    covered_codes = {d.req_code for d in decisions}
    uncovered = input_codes - covered_codes

    stats = {
        "input": len(items),
        "decisions": len(decisions),
        "stack": len(stack),
        "has_consistency_strategy": bool(data_consistency),
        "has_patterns": bool(patterns),
        "gap_pass_found": len(gap_candidates),
        "req_coverage": f"{len(covered_codes)}/{len(input_codes)}",
        "uncovered_codes": sorted(uncovered),
        "validation_warnings": warnings,
        "critique_findings": [f.model_dump() for f in critique_findings],
        "critique_blockers": sum(
            1 for f in critique_findings if f.severity == "blocker"
        ),
    }
    logger.info(
        "analyze_nfrs: %d items -> %d decisions, %d stack layers "
        "(gap +%d, %d warnings, %d critique findings)",
        stats["input"],
        stats["decisions"],
        stats["stack"],
        stats["gap_pass_found"],
        len(warnings),
        len(critique_findings),
    )
    return NfrResult(
        decisions=decisions,
        stack=stack,
        data_consistency=data_consistency,
        patterns=patterns,
        stats=stats,
    )
