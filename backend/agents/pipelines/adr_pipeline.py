"""ADR pipeline: Architecture Decision Records derived from the NFR analysis.

Multi-pass pipeline (5 passes):

  Pass 1 — skeleton discovery (narrow schema: title + decision_summary +
  nfr_codes per ADR).
  Pass 2 — gap pass: re-scan NFR codes not covered by any skeleton.
  Pass 3 — detailing (skeleton-constrained): full ADRs with context,
  decision, alternatives, rationale.
  Pass 4 — deterministic validation (nfr_codes exist, coverage, no
  duplicates, non-empty fields).
  Pass 5 — LLM critique covering documented failure modes (optional).

Takes the NfrResult (and the MerResult for entity context) and produces a set
of Architecture Decision Records (ADRs) in Nygard format. Each ADR traces back
to the NFR codes that motivate it.

Design notes (mirrors mer_pipeline.py patterns):

- Does NOT write to the DB. Returns an AdrResult dataclass consumed by the
  analysis assembler / store layer.
- Uses structured_llm with narrow per-pass schemas for fence-tolerant
  structured output.
- Retry policy: centralized via _invoke_with_retry from _resilience.
- Prompts are in Spanish neutro (no voseo, no spanglish) following project
  conventions for LLM-facing text.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from backend.agents.llm import structured_llm
from backend.agents.pipelines._resilience import (
    DEFAULT_CONCURRENCY,
    _chunk,
    _format_feedback,
    _format_goals,
    _invoke_with_retry,
)

if TYPE_CHECKING:
    from backend.agents.pipelines.mer_pipeline import MerResult
    from backend.agents.pipelines.nfr_pipeline import NfrResult

logger = logging.getLogger(__name__)

# Max skeletons per detailing batch. Each full ADR (context, decision,
# alternatives, rationale) is verbose — 8 per call stays within truncation
# limits for structured output.
_ADR_BATCH_SIZE = int(os.environ.get("INFOFACT_ADR_BATCH", "8"))
# ---------------------------------------------------------------------------


class AdrAlternative(BaseModel):
    """One alternative considered for an architectural decision."""

    name: str = Field(
        description="Nombre de la alternativa considerada.",
    )
    pros: str = Field(
        default="",
        description="Ventajas de esta alternativa.",
    )
    cons: str = Field(
        default="",
        description="Desventajas de esta alternativa.",
    )


class AdrSchema(BaseModel):
    """One Architecture Decision Record (Nygard format)."""

    title: str = Field(
        description=(
            "Titulo conciso de la decision (ej. 'Usar PostgreSQL para "
            "persistencia')."
        ),
    )
    context: str = Field(
        description=(
            "Problema y contexto que motiva la decision. Describir las "
            "fuerzas en tension (requisitos, restricciones, incertidumbre)."
        ),
    )
    decision: str = Field(
        description="Decision tomada, expresada de forma clara y accionable.",
    )
    alternatives: list[AdrAlternative] = Field(
        default_factory=list,
        description="Alternativas consideradas (al menos una cuando aplique).",
    )
    rationale: str = Field(
        description=(
            "Por que la decision elegida es mejor que las alternativas, "
            "referenciando los NFRs relevantes."
        ),
    )
    nfr_codes: list[str] = Field(
        default_factory=list,
        description=(
            "Codigos REQ-XXXX de los NFRs que motivan esta decision. Tomarlos "
            "literalmente del input."
        ),
    )


class AdrBatchSchema(BaseModel):
    """Wrapper for the ADR detailing result (Pass 3)."""

    adrs: list[AdrSchema] = Field(
        default_factory=list,
    )


# ---------------------------------------------------------------------------
# Narrow per-pass schemas (multi-pass pipeline)
# ---------------------------------------------------------------------------


class AdrSkeleton(BaseModel):
    """Narrow schema for Pass 1/2: ADR boundary only."""

    title: str = Field(description="Titulo conciso de la decision")
    decision_summary: str = Field(
        description="Resumen de la decision en una oracion"
    )
    nfr_codes: list[str] = Field(
        default_factory=list,
        description="Codigos REQ-XXXX de los NFRs que motivan esta decision",
    )


class AdrSkeletonSchema(BaseModel):
    """Pass 1 and Pass 2 output schema."""

    adrs: list[AdrSkeleton]


class AdrCritiqueFinding(BaseModel):
    """Pass 5: critic finding."""

    adr_title: str = Field(description="ADR afectado o 'GLOBAL'")
    issue_type: str = Field(
        description=(
            "trivial_adr | missing_alternatives | uncovered_nfr | "
            "duplicate_adr | unsupported_rationale | vague_decision | "
            "strawman_alternative"
        ),
    )
    description: str
    severity: str = Field(description="blocker | warning | info")


class AdrCritiqueSchema(BaseModel):
    """Pass 5 output schema."""

    findings: list[AdrCritiqueFinding]


# ---------------------------------------------------------------------------
# Prompts (Spanish neutro — no voseo, no spanglish)
# ---------------------------------------------------------------------------

_ADR_DISCOVERY_PROMPT = (
    "Eres un arquitecto de software. A partir del analisis de requerimientos "
    "no funcionales, identifica que Architecture Decision Records (ADRs) son "
    "necesarios. Tu UNICA tarea es identificar el titulo, el resumen de la "
    "decision y los NFRs que la motivan — NO redactes contexto, alternativas "
    "ni justificacion completa.\n\n"
    "Reglas:\n"
    "- IDIOMA: manten el idioma original.\n"
    "- Cada ADR debe trazar a al menos un NFR via nfr_codes.\n"
    "- Agrupa decisiones relacionadas en un solo ADR cuando tengan sentido "
    "juntas.\n"
    "- Prioriza ADRs sobre decisiones de alto impacto; omite micro-decisiones.\n"
    "- nfr_codes: tomar literalmente del input.\n"
    "Devuelve SOLO el objeto estructurado."
)

_ADR_GAP_PASS_PROMPT = (
    "Eres un arquitecto de software. Los siguientes NFRs NO fueron cubiertos "
    "por ningun ADR en la primera pasada. Revisa cada uno y determina si "
    "justifica un ADR nuevo. Si ninguno genera un ADR nuevo, devuelve una "
    "lista vacia.\n"
    "Devuelve SOLO el objeto estructurado."
)

_ADR_DETAIL_PROMPT = (
    "Eres un arquitecto de software. A partir del analisis de requerimientos "
    "no funcionales, redacta Architecture Decision Records (ADRs) siguiendo el "
    "formato Nygard (contexto, decision, alternativas, justificacion).\n\n"
    "Recibiras el listado de decisiones arquitectonicas derivadas de los NFRs "
    "(con sus codigos REQ-XXXX), el stack recomendado por capa y un listado "
    "de ADRs preliminares (titulo + decision_summary + nfr_codes) que debes "
    "enriquecer con contexto, alternativas y justificacion completos.\n\n"
    "Reglas:\n"
    "- IDIOMA: manten el idioma original de los requerimientos en context y "
    "rationale. NO traduzcas.\n"
    "- Cada ADR debe trazar a al menos un NFR via nfr_codes.\n"
    "- title: frase corta en imperativo o infinitivo.\n"
    "- context: describe el problema, las fuerzas en tension y los NFRs que "
    "presionan la decision.\n"
    "- decision: una frase clara y accionable.\n"
    "- alternatives: lista al menos una alternativa real considerada con sus "
    "pros y contras cuando la decision no sea trivial.\n"
    "- rationale: por que la opcion elegida supera a las alternativas.\n"
    "- No inventes NFRs ni codigos que no aparezcan en el input.\n"
    "Devuelve SOLO el objeto estructurado."
)

_ADR_CRITIQUE_PROMPT = (
    "Eres un crítico de arquitectura de software. Se te dan los ADRs generados "
    "a partir del analisis NFR. Tu tarea es identificar problemas:\n\n"
    "Categorias de fallo a revisar:\n"
    "1. trivial_adr: un ADR que no anade valor sobre la decision NFR original.\n"
    "2. missing_alternatives: un ADR sin alternativas cuando deberia tenerlas.\n"
    "3. uncovered_nfr: un NFR del input que no esta cubierto por ningun ADR.\n"
    "4. duplicate_adr: dos ADRs que cubren la misma decision.\n"
    "5. unsupported_rationale: justificacion sin referencia a NFRs.\n"
    "6. vague_decision: decision vaga o no accionable.\n"
    "7. strawman_alternative: alternativa obviamente inferior (turca de paja).\n\n"
    "Si los ADRs estan correctos, devuelve findings vacio.\n"
    "Devuelve SOLO el objeto estructurado."
)

# Original prompt kept for backward compatibility.
ADR_PROMPT = _ADR_DETAIL_PROMPT


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class AdrResult:
    """Output of the ADR pipeline (list of Nygard-format ADRs + stats)."""

    adrs: list[AdrSchema] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_nfr_summary(nfr_result) -> str:
    """Build the user-facing summary of NFR decisions + stack."""
    lines: list[str] = []

    if nfr_result.decisions:
        lines.append(f"DECISIONES DERIVADAS DE NFRs ({len(nfr_result.decisions)} total):")
        # Cap at 40 decisions to keep context manageable; group by category
        # for the rest so the LLM still sees the full coverage picture.
        shown = nfr_result.decisions[:40]
        for d in shown:
            entry = f"- [{d.req_code}] ({d.category}) {d.decision}"
            if d.stack_component:
                entry += f" — {d.stack_component}"
            if d.rationale:
                entry += f" | porque: {d.rationale}"
            lines.append(entry)
        remaining = nfr_result.decisions[40:]
        if remaining:
            from collections import Counter
            cat_counts = Counter(d.category for d in remaining)
            summary = ", ".join(
                f"{c} ({n})" for c, n in cat_counts.most_common()
            )
            lines.append(f"... ({len(remaining)} mas: {summary})")
        lines.append("")

    if nfr_result.stack:
        lines.append("STACK RECOMENDADO POR CAPA:")
        for s in nfr_result.stack:
            lines.append(f"- [{s.layer}] {s.technology} — {s.rationale}")
        lines.append("")

    if nfr_result.data_consistency:
        lines.append(f"ESTRATEGIA DE CONSISTENCIA: {nfr_result.data_consistency}")
        lines.append("")

    if nfr_result.patterns:
        lines.append(f"PATRONES ARQUITECTONICOS: {nfr_result.patterns}")
        lines.append("")

    return "\n".join(lines)


def _build_adr_context(
    nfr_result,
    mer_result,
    project_name: str,
    project_description: str,
    softgoals: list[Any] | None = None,
    feedback: str = "",
) -> str:
    """Build shared context text (project + softgoals + MER + NFR summary)."""
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

    if mer_result is not None and getattr(mer_result, "entities", None):
        ent_names = [e.name for e in mer_result.entities]
        lines.append(
            f"ENTIDADES DE DOMINIO ({len(ent_names)}): "
            + ", ".join(ent_names[:20])
        )

    lines.append("")
    lines.append(_build_nfr_summary(nfr_result))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pass 1: skeleton discovery (narrow schema)
# ---------------------------------------------------------------------------


async def _discover_adr_skeletons(
    nfr_result,
    mer_result=None,
    *,
    project_name: str = "",
    project_description: str = "",
    softgoals: list[Any] | None = None,
    feedback: str = "",
) -> list[AdrSkeleton]:
    """Pass 1: discover ADR skeletons (title + decision_summary + nfr_codes)."""
    context = _build_adr_context(
        nfr_result, mer_result, project_name, project_description,
        softgoals, feedback=feedback,
    )
    msgs = [("system", _ADR_DISCOVERY_PROMPT), ("human", context)]
    llm = structured_llm(AdrSkeletonSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="adr_discover"
        )
        return list(result.adrs)
    except Exception:
        logger.exception("_discover_adr_skeletons: all retries exhausted")
        return []


# ---------------------------------------------------------------------------
# Pass 2: gap pass for uncovered NFR codes
# ---------------------------------------------------------------------------


async def _gap_pass_adr(
    nfr_result,
    skeletons: list[AdrSkeleton],
    mer_result=None,
    *,
    project_name: str = "",
    project_description: str = "",
    softgoals: list[Any] | None = None,
    feedback: str = "",
) -> list[AdrSkeleton]:
    """Pass 2: detect uncovered NFR codes and re-scan them."""
    input_codes = {d.req_code for d in nfr_result.decisions}
    covered_codes = {c for s in skeletons for c in s.nfr_codes}
    uncovered = input_codes - covered_codes
    if not uncovered:
        return []

    # Build a focused NFR summary with only the uncovered decisions.
    uncovered_decisions = [
        d for d in nfr_result.decisions if d.req_code in uncovered
    ]
    from types import SimpleNamespace

    focused_nfr = SimpleNamespace(
        decisions=uncovered_decisions,
        stack=[],
        data_consistency="",
        patterns="",
    )

    context = _build_adr_context(
        focused_nfr, mer_result, project_name, project_description,
        softgoals, feedback=feedback,
    )
    already_titles = ", ".join(s.title for s in skeletons) or "(ninguno)"
    context += (
        f"\nADRs YA IDENTIFICADOS: {already_titles}\n"
        "Revisa SOLO los NFRs anteriores y determina si justifican ADRs "
        "NUEVOS (no listados arriba).\n"
    )
    msgs = [("system", _ADR_GAP_PASS_PROMPT), ("human", context)]
    llm = structured_llm(AdrSkeletonSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="adr_gap_pass"
        )
        return list(result.adrs)
    except Exception:
        logger.exception("_gap_pass_adr: all retries exhausted")
        return []


# ---------------------------------------------------------------------------
# Pass 3: detailing (skeleton-constrained full ADRs)
# ---------------------------------------------------------------------------


async def _detail_adrs(
    nfr_result,
    skeletons: list[AdrSkeleton],
    mer_result=None,
    *,
    project_name: str = "",
    project_description: str = "",
    softgoals: list[Any] | None = None,
    feedback: str = "",
    concurrency: int = DEFAULT_CONCURRENCY,
) -> AdrBatchSchema | None:
    """Pass 3: batched detailing of skeletons into full ADRs.

    Skeletons are split into batches of ``_ADR_BATCH_SIZE`` to avoid
    structured output truncation when there are many ADRs. Each batch
    shares the same NFR/MER context but only details its own skeletons.
    """
    if not skeletons:
        return AdrBatchSchema(adrs=[])

    batches = list(_chunk(skeletons, _ADR_BATCH_SIZE))
    if len(batches) <= 1:
        return await _detail_adr_batch(
            nfr_result, skeletons, mer_result,
            project_name, project_description,
            softgoals, feedback,
        )

    sem = asyncio.Semaphore(concurrency)

    async def _guarded(batch):
        async with sem:
            return await _detail_adr_batch(
                nfr_result, batch, mer_result,
                project_name, project_description,
                softgoals, feedback,
            )

    results = await asyncio.gather(
        *[_guarded(b) for b in batches], return_exceptions=True
    )
    all_adrs: list[AdrSchema] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.warning(
                "_detail_adrs: batch %d/%d failed: %s",
                i + 1, len(batches), r,
            )
            continue
        if r is not None:
            all_adrs.extend(r.adrs)
    logger.info(
        "_detail_adrs: %d batches -> %d ADRs", len(batches), len(all_adrs)
    )
    return AdrBatchSchema(adrs=all_adrs)


async def _detail_adr_batch(
    nfr_result,
    skeletons_batch: list[AdrSkeleton],
    mer_result=None,
    project_name: str = "",
    project_description: str = "",
    softgoals: list[Any] | None = None,
    feedback: str = "",
) -> AdrBatchSchema | None:
    """Detail a single batch of skeletons into full ADRs."""
    context = _build_adr_context(
        nfr_result, mer_result, project_name, project_description,
        softgoals, feedback=feedback,
    )
    skeleton_lines = []
    for s in skeletons_batch:
        codes = ", ".join(s.nfr_codes) if s.nfr_codes else "(sin NFR)"
        skeleton_lines.append(
            f"- {s.title} — {s.decision_summary} [NFRs: {codes}]"
        )
    context += (
        "\nADRs PRELIMINARES (enriquece cada uno con contexto, alternativas "
        "y justificacion):\n"
        + "\n".join(skeleton_lines)
        + "\n"
    )
    msgs = [("system", _ADR_DETAIL_PROMPT), ("human", context)]
    llm = structured_llm(AdrBatchSchema)
    try:
        return await _invoke_with_retry(
            llm, msgs,
            context_label=f"adr_detail ({len(skeletons_batch)} ADRs)",
        )
    except Exception:
        logger.exception(
            "_detail_adr_batch: all retries exhausted for %d skeletons",
            len(skeletons_batch),
        )
        return None


# ---------------------------------------------------------------------------
# Pass 4: deterministic validation
# ---------------------------------------------------------------------------


def _validate_adrs(
    adrs: list[AdrSchema],
    input_nfr_codes: set[str],
) -> list[str]:
    """Pass 4: deterministic validation checks (no LLM).

    Returns a list of warning strings.
    """
    warnings: list[str] = []

    # Check: all nfr_codes in ADRs exist in the input NFR decisions.
    for adr in adrs:
        for code in adr.nfr_codes:
            if code not in input_nfr_codes:
                warnings.append(
                    f"ADR '{adr.title}' referencia NFR inexistente: {code}"
                )

    # Check: no duplicate titles.
    seen_titles: set[str] = set()
    for adr in adrs:
        lower = adr.title.lower()
        if lower in seen_titles:
            warnings.append(f"ADR duplicado (titulo): {adr.title}")
        seen_titles.add(lower)

    # Check: non-empty fields.
    for adr in adrs:
        if not adr.context.strip():
            warnings.append(f"ADR '{adr.title}' con context vacio")
        if not adr.decision.strip():
            warnings.append(f"ADR '{adr.title}' con decision vacia")
        if not adr.rationale.strip():
            warnings.append(f"ADR '{adr.title}' con rationale vacio")

    # Check: coverage — every input NFR code should be referenced.
    referenced: set[str] = set()
    for adr in adrs:
        referenced.update(adr.nfr_codes)
    uncovered = input_nfr_codes - referenced
    if uncovered:
        warnings.append(
            f"NFRs no cubiertos por ningun ADR: {sorted(uncovered)}"
        )

    return warnings


# ---------------------------------------------------------------------------
# Pass 5: LLM critique
# ---------------------------------------------------------------------------


async def _critique_adrs(
    adrs: list[AdrSchema],
    nfr_result,
    *,
    project_name: str = "",
    project_description: str = "",
    softgoals: list[Any] | None = None,
) -> list[AdrCritiqueFinding]:
    """Pass 5: LLM critique covering documented failure modes."""
    adr_lines = []
    for a in adrs:
        alts = ", ".join(alt.name for alt in a.alternatives) or "(ninguna)"
        adr_lines.append(
            f"- {a.title} — {a.decision} "
            f"[NFRs: {', '.join(a.nfr_codes)}] "
            f"[Alternativas: {alts}]"
        )
    adr_summary = "\n".join(adr_lines) or "(ninguno)"

    nfr_lines = []
    for d in nfr_result.decisions[:50]:
        nfr_lines.append(f"- [{d.req_code}] ({d.category}) {d.decision}")
    if len(nfr_result.decisions) > 50:
        nfr_lines.append(
            f"... ({len(nfr_result.decisions) - 50} mas)"
        )
    nfr_summary = "\n".join(nfr_lines) or "(ninguna)"

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
        f"DECISIONES NFR ORIGINALES:\n{nfr_summary}\n\n"
        f"ADRS GENERADOS:\n{adr_summary}\n"
    )
    msgs = [("system", _ADR_CRITIQUE_PROMPT), ("human", user_text)]
    llm = structured_llm(AdrCritiqueSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="adr_critique"
        )
        return list(result.findings)
    except Exception:
        logger.exception("_critique_adrs: all retries exhausted")
        return []


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


async def generate_adrs(
    nfr_result,
    mer_result=None,
    *,
    project_name: str = "",
    project_description: str = "",
    concurrency: int = DEFAULT_CONCURRENCY,
    softgoals: list[Any] | None = None,
    enable_critique: bool = True,
    feedback: str = "",
) -> AdrResult:
    """Generate Architecture Decision Records from the NFR analysis.

    Multi-pass pipeline:

      Pass 1 — skeleton discovery (title + decision_summary + nfr_codes).
      Pass 2 — gap pass for uncovered NFR codes.
      Pass 3 — detailing (full ADRs, skeleton-constrained).
      Pass 4 — deterministic validation.
      Pass 5 — LLM critique (optional, enabled by default).

    Takes an NfrResult (and optionally a MerResult for entity context) and
    produces an AdrResult with a list of Nygard-format ADRs. Each ADR traces
    back to the NFR codes that motivate it.
    """
    if nfr_result is None or not getattr(nfr_result, "decisions", None):
        return AdrResult(stats={"input_decisions": 0})

    input_nfr_codes = {d.req_code for d in nfr_result.decisions}

    # Pass 1: skeleton discovery.
    skeletons = await _discover_adr_skeletons(
        nfr_result, mer_result,
        project_name=project_name,
        project_description=project_description,
        softgoals=softgoals, feedback=feedback,
    )

    # Pass 2: gap pass for uncovered NFR codes.
    gap_skeletons = await _gap_pass_adr(
        nfr_result, skeletons, mer_result,
        project_name=project_name,
        project_description=project_description,
        softgoals=softgoals, feedback=feedback,
    )
    all_skeletons = skeletons + gap_skeletons

    # Pass 3: detailing (skeleton-constrained full ADRs).
    batch = await _detail_adrs(
        nfr_result, all_skeletons, mer_result,
        project_name=project_name,
        project_description=project_description,
        softgoals=softgoals, feedback=feedback,
    )

    if batch is None:
        return AdrResult(stats={
            "input_decisions": len(nfr_result.decisions),
            "error": "detailing_failed",
            "adrs": 0,
        })

    adrs = list(batch.adrs)

    # Pass 4: deterministic validation.
    warnings = _validate_adrs(adrs, input_nfr_codes)

    # Pass 5: LLM critique (optional).
    critique_findings: list[AdrCritiqueFinding] = []
    if enable_critique:
        critique_findings = await _critique_adrs(
            adrs, nfr_result,
            project_name=project_name,
            project_description=project_description,
            softgoals=softgoals,
        )

    # Compute coverage stats.
    referenced: set[str] = set()
    for adr in adrs:
        referenced.update(adr.nfr_codes)
    uncovered = sorted(input_nfr_codes - referenced)

    stats = {
        "input_decisions": len(nfr_result.decisions),
        "adrs": len(adrs),
        "nfr_codes_referenced": len(referenced),
        "nfr_codes_uncovered": len(uncovered),
        "gap_pass_found": len(gap_skeletons),
        "validation_warnings": warnings,
        "critique_findings": [f.model_dump() for f in critique_findings],
        "critique_blockers": sum(
            1 for f in critique_findings if f.severity == "blocker"
        ),
    }
    logger.info(
        "generate_adrs: %d NFR decisions -> %d ADRs "
        "(gap +%d, %d warnings, %d critique findings)",
        stats["input_decisions"],
        stats["adrs"],
        stats["gap_pass_found"],
        len(warnings),
        len(critique_findings),
    )
    return AdrResult(adrs=adrs, stats=stats)
