"""Shared retry primitives for LLM-backed pipeline stages.

Z.ai GLM (and other vendors) intermittently fail in two ways that batch
pipelines must absorb instead of aborting a whole ``asyncio.gather``:

- Transient: 429 / 5xx / timeout / connection error. Recoverable with
  exponential backoff up to ``_TRANSIENT_RETRIES`` attempts.
- Parse: truncated or malformed structured JSON. A retry may catch a
  clean response; if every retry fails the caller wraps the item in a
  sentinel (stage-specific shape) so it lands in human-review buckets.

Imported by ``critique._judge_item`` and ``classification._classify_item``
to keep the transient classification in sync across stages — drifting
here would silently change retry behaviour under load.

Centralized multi-pass helpers (``_invoke_with_retry``, ``_format_goals``,
``_validate_mermaid``, ``_repair_mermaid``) are also exported here so every
pipeline (mer, nfr, adr, process, subproject) shares the same retry,
formatting, and Mermaid-validation behaviour.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError

logger = logging.getLogger(__name__)

# Transient (429/5xx/timeout) retries with exponential backoff (cap _BACKOFF_CAP).
# Parse-failure attempts are defined per stage (critique: _JUDGE_ATTEMPTS,
# classification: _CLASSIFY_ATTEMPTS) because the right depth depends on
# how expensive each call is and how the sentinel downstream is consumed.
_TRANSIENT_RETRIES = 8

# --------------------------------------------------------------------------- #
# Concurrency + backoff (plan: aceleración del pipeline de captura)
# --------------------------------------------------------------------------- #
# Env-tunable so concurrency can be calibrated against the real Z.ai rate
# limit without code changes. Default 4 (historically lowered from 8 after a
# Z.ai 429 spike under e2e load; batch mode has since cut total calls, so
# tuning up is now safer — change one variable at a time via the env var).
DEFAULT_CONCURRENCY = int(os.environ.get("INFOFACT_CONCURRENCY", "4"))

# Hard cap (seconds) on a single transient-retry backoff. Env-tunable
# (INFOFACT_BACKOFF_CAP). Default 30 (Phase 2: halved from 60 now that jitter
# spreads the retry collisions; revert to 60 via the env var if Z.ai's
# rate-limit window proves longer than ~30s and shorter caps re-collide).
_BACKOFF_CAP = int(os.environ.get("INFOFACT_BACKOFF_CAP", "30"))


def transient_backoff_seconds(fails: int) -> float:
    """Jittered exponential backoff for transient LLM errors (429/5xx/timeout).

    Pure exponential backoff makes concurrent retries collide (thundering
    herd): N requests 429'd at the same instant sleep the exact same duration
    and retry together, causing another 429. The ``random.uniform(0.5, 1.5)``
    jitter spreads each retry over half-to-1.5x of the base wait so only a
    fraction collide on each cycle.
    """
    return min(2 ** fails, _BACKOFF_CAP) * random.uniform(0.5, 1.5)


# --------------------------------------------------------------------------- #
# Batch defaults (plan §13.B)
# --------------------------------------------------------------------------- #
DEFAULT_BATCH_SIZE = int(os.environ.get("INFOFACT_BATCH_SIZE", "5"))  # M; calibrate vs golden (plan §I.4); env-overridable for A/B tuning
BATCH_MAX_TOKENS = 4000       # M=5 x ~300 tokens worst case + margin; mitigates truncation
_BATCH_PARSE_RETRIES = 2      # batch-level parse retries before per-item fallback


@dataclass
class BatchStats:
    """Aggregate counters for batch-mode pipeline stages.

    - batch_calls: LLM invocations issued at the batch level (one per batch,
      regardless of how many items it carried).
    - omitted: items that the model skipped in its batch response and that
      were routed back through the per-item path.
    - fallback: items processed per-item after the whole batch failed
      (transient or parse) and was decomposed to individual calls.
    """
    batch_calls: int = 0
    omitted: int = 0
    fallback: int = 0


def is_transient(exc: Exception) -> bool:
    """429, timeout, conn error, 5xx — recoverable with backoff.

    Public name (no leading underscore) so pipeline modules call
    `is_transient(exc)` rather than re-defining local copies that drift.
    """
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    # Defensive: some SDK versions raise RateLimitError/InternalServerError
    # subclasses; match by name in case langchain wraps them.
    return type(exc).__name__ in {"RateLimitError", "InternalServerError"}


# --------------------------------------------------------------------------- #
# Centralized multi-pass helpers (imported by every pipeline)
# --------------------------------------------------------------------------- #


async def _invoke_with_retry(
    llm,
    msgs,
    *,
    context_label: str = "pipeline",
    max_parse: int = 3,
    max_transient: int | None = None,
    extra: dict | None = None,
):
    """Centralized transient backoff + parse retry. Raises on exhaustion.

    - Transient errors (429/5xx/timeout): exponential backoff up to
      ``max_transient`` attempts (default ``_TRANSIENT_RETRIES``).
    - Parse errors (truncated/malformed JSON): retried up to ``max_parse``
      times; after that the exception propagates so the caller can apply
      a stage-specific fallback (sentinel, empty list, etc.).
    - ``extra`` (optional): extra kwargs forwarded verbatim to every
      ``llm.ainvoke`` call (e.g. ``max_tokens``), same precedent as the
      direct ``ainvoke(..., max_tokens=...)`` calls in critique/classification.

    Imported by every multi-pass pipeline (mer, nfr, adr, process,
    subproject) so retry behaviour stays in sync across stages.
    """
    if max_transient is None:
        max_transient = _TRANSIENT_RETRIES
    parse_fails = 0
    transient_fails = 0

    while True:
        try:
            return await llm.ainvoke(msgs, **(extra or {}))
        except Exception as exc:  # noqa: BLE001 — split below
            if is_transient(exc):
                transient_fails += 1
                if transient_fails >= max_transient:
                    logger.warning(
                        "%s: transient exhausted after %d retries (%s)",
                        context_label,
                        transient_fails,
                        type(exc).__name__,
                    )
                    raise
                wait = transient_backoff_seconds(transient_fails)
                logger.warning(
                    "%s: transient %s; backoff %.1fs (%d/%d)",
                    context_label,
                    type(exc).__name__,
                    wait,
                    transient_fails,
                    max_transient,
                )
                await asyncio.sleep(wait)
            else:
                parse_fails += 1
                if parse_fails >= max_parse:
                    logger.warning(
                        "%s: parse failed after %d attempts (%s)",
                        context_label,
                        parse_fails,
                        type(exc).__name__,
                    )
                    raise
                logger.warning(
                    "%s: parse failed (%s); retry (%d/%d)",
                    context_label,
                    exc,
                    parse_fails,
                    max_parse,
                )


async def invoke_structured_resilient(
    make_llm,
    msgs,
    *,
    context_label: str = "pipeline",
    max_parse: int = 2,
    max_transient: int | None = None,
    thinking_off_body: dict | None = None,
):
    """Invocación estructurada con degradación por truncado de salida.

    Primera pasada con el default del proveedor (thinking activo). Si el único
    fallo es un truncado por presupuesto — ``StructuredOutputTruncatedError``
    (finish_reason=length con JSON incompleto): el pensamiento interno de
    Z.ai comparte el presupuesto de salida y lo quema antes de emitir nada —
    reintenta con thinking desactivado, que produce el objeto estructurado
    corto en una fracción del tiempo. El resto de los fallos (transient
    429/5xx, parse sin truncar) conserva la semántica de ``_invoke_with_retry``
    y propaga la excepción al agotarse.

    ``make_llm(**kwargs)`` construye el runnable estructurado (p. ej.
    ``lambda **kw: structured_llm(schema, **kw)``); ``thinking_off_body``
    permite inyectar el body sin thinking del caller (los módulos pasan su
    ``disable_thinking_body()`` importado, así los tests pueden stubearlo).

    Compartido por ``goals_engine`` (fase goals / lote de links) y
    ``srs_quality`` (batch / per-ítem) para que la degradación por truncado
    no diverja entre etapas.
    """
    from backend.agents.llm import (
        StructuredOutputTruncatedError,
        disable_thinking_body,
    )

    try:
        return await _invoke_with_retry(
            make_llm(),
            msgs,
            context_label=context_label,
            max_parse=max_parse,
            max_transient=max_transient,
        )
    except StructuredOutputTruncatedError:
        logger.warning(
            "%s: salida truncada por presupuesto de tokens; reintento con "
            "thinking desactivado",
            context_label,
        )
    body = (
        thinking_off_body
        if thinking_off_body is not None
        else disable_thinking_body()
    )
    return await _invoke_with_retry(
        make_llm(extra_body=body),
        msgs,
        context_label=f"{context_label}/sin-thinking",
        max_parse=max_parse,
        max_transient=max_transient,
    )


def _format_goals(
    goals: list[Any] | None,
    section_title: str = "OBJETIVOS",
) -> str:
    """Format goals as context text for the LLM prompt.

    Shared by every pipeline so goal formatting stays consistent.
    Each caller passes its preferred ``section_title`` (e.g. softgoals
    use "ATRIBUTOS DE CALIDAD (SOFTGOALS)").
    """
    if not goals:
        return ""
    lines = [section_title + ":"]
    for g in goals:
        kind_str = g.kind.value if hasattr(g, "kind") else "goal"
        lines.append(f"- [{g.code}] ({kind_str}) {g.statement}")
    return "\n".join(lines) + "\n\n"


def _chunk(seq, size):
    """Split a sequence into batches of ``size`` (last batch may be shorter).

    Shared by multi-pass pipelines that need to batch large item sets into
    multiple LLM calls to avoid output truncation on structured responses.
    """
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _format_feedback(feedback: str) -> str:
    """Format user feedback as a correction block for refinement mode.

    When non-empty, this block is prepended to the user message text so the
    LLM sees the user's correction alongside the original requirements/data.
    Shared by every pipeline so feedback formatting stays consistent.
    """
    if not feedback:
        return ""
    return f"CORRECCION DEL USUARIO:\n{feedback}\n\n"


def _sanitize_mermaid_id(name: str) -> str:
    """Sanitize a name for use as a Mermaid identifier.

    Mermaid identifiers must be alphanumeric + underscore. We replace any
    other character with underscore and prefix with ``_`` if it starts
    with a digit.
    """
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", name.strip())
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized or "unnamed"


def _sanitize_mermaid_label(text: str) -> str:
    """Sanitize a label for use inside Mermaid syntax.

    Removes characters that break Mermaid parsing (especially in
    sequenceDiagram, where they cause the 'Cannot read properties of
    null (reading firstChild)' rendering crash):

    - Newlines (\\n, \\r) — break the single-line syntax.
    - Parentheses () — Mermaid confuses them with Note/autonumber syntax.
    - Single quotes ' — interpreted as string delimiters by some renderers.
    - Curly braces {} — interpreted as shape markers.
    - Pipes | — participant/message separators.
    - Colons : — conflict with the arrow label separator.
    """
    result = text.strip()
    result = result.replace("\n", " ").replace("\r", "")
    result = result.replace("(", "").replace(")", "")
    result = result.replace("'", "")
    result = result.replace("{", "").replace("}", "")
    result = result.replace('"', "")
    result = result.replace("|", "/")
    result = result.replace(":", " -")
    return result


def _sanitize_graph_label(text: str) -> str:
    """Sanitize a label for use inside Mermaid graph TD node labels.

    Removes characters that break the graph TD parser in common Mermaid
    renderers:

    - Newlines (\\n, \\r) — break the single-line syntax.
    - Brackets ``[`` ``]`` — conflict with node shape syntax.
    - Parentheses ``(`` ``)`` — confuse the parser with shape markers.
    - Double quotes ``"`` — close the ``["..."]`` node label prematurely.
    - Pipes ``|`` — edge label separators.
    - ``%%`` — Mermaid comment marker.
    """
    result = text.strip()
    result = result.replace("\n", " ").replace("\r", "")
    result = result.replace("[", "").replace("]", "")
    result = result.replace("(", "").replace(")", "")
    result = result.replace('"', "")
    result = result.replace("|", "/")
    result = result.replace("%%", "")
    return result


def _sanitize_mermaid(mermaid_str: str, diagram_type: str) -> str:
    """Fix common Mermaid syntax issues caused by LLM generation.

    Applied deterministically before validation and rendering. Does NOT use
    the LLM — purely mechanical character replacements.
    """
    if not mermaid_str:
        return mermaid_str
    import re

    result = mermaid_str

    if "graph" in diagram_type.lower() or "flowchart" in diagram_type.lower():
        # In graph/flowchart, {} are shape markers (diamond). When they appear
        # inside edge labels |...|, the parser interprets { as DIAMOND_START and
        # the diagram breaks. Replace {} with () inside edge labels.
        def _fix_edge_label(match: re.Match) -> str:
            label = match.group(1)
            # Remove ALL bracket/parenthesis types from edge labels — they
            # confuse the Mermaid graph parser inside |...| edge labels.
            label = label.replace("{", "").replace("}", "")
            label = label.replace("[", "").replace("]", "")
            label = label.replace("(", "").replace(")", "")
            return f"|{label}|"

        result = re.sub(r"\|([^|]+)\|", _fix_edge_label, result)

    elif "sequence" in diagram_type.lower():
        # Fix participant declarations where the LLM inverts the syntax:
        #   participant "Display Name" as Alias  →  participant Alias as "Display Name"
        # Mermaid expects the alias FIRST. The inverted form registers the
        # quoted string as the identifier, so arrow references (using the
        # bare alias) find no participant → null crash during render.
        result = re.sub(
            r'(participant\s+)"([^"]+)"\s+as\s+(\S+)',
            r'\1\3 as "\2"',
            result,
        )
        # Replace 'actor' with 'participant' — 'actor' is only supported in
        # Mermaid >= 9.4 and some bundled renderers still choke on it.
        result = re.sub(
            r'^(\s*)actor\s+',
            r'\1participant ',
            result,
            flags=re.MULTILINE,
        )

        # In sequenceDiagram, certain characters in message text (after the ':'
        # on arrow lines) cause rendering bugs ("Cannot read properties of null"):
        # - () parentheses confuse the renderer with Note/autonumber syntax
        # - '' single quotes can be interpreted as string delimiters
        # - {} are interpreted as shape markers
        # - | is a participant/message separator
        # Clean these from the message portion of arrow lines only.
        def _fix_seq_message(line: str) -> str:
            # Preserve original indentation.
            indent = line[: len(line) - len(line.lstrip())]
            stripped = line.strip()
            # Match arrow lines: "A->>B: message" or "A-->>B: message" etc.
            m = re.match(
                r"^(\s*.*?(?:->>|-->>|->|-x|--x|-->).*?:)(.*)$",
                stripped,
            )
            if not m:
                return line  # not a message line (participant, Note, alt, etc.)
            prefix = m.group(1)
            message = m.group(2)
            # Remove problematic characters from the message text.
            message = message.replace("(", "").replace(")", "")
            message = message.replace("'", "")
            message = message.replace("{", "").replace("}", "")
            message = message.replace("|", "-")
            return f"{indent}{prefix} {message}".rstrip()

        result = "\n".join(
            _fix_seq_message(line) for line in result.split("\n")
        )

    return result


def _validate_mermaid(mermaid_str: str, diagram_type: str) -> tuple[bool, str]:
    """Validate Mermaid syntax heuristically. Returns (is_valid, reason_if_invalid).

    Checks the diagram type declaration line and minimal content requirements
    per type. Does not guarantee the diagram will render in every Mermaid
    renderer, but catches the most common LLM mistakes (wrong declaration,
    empty diagram, missing transitions/messages/nodes).
    """
    if not mermaid_str or not mermaid_str.strip():
        return False, "empty diagram"
    lines = [l.strip() for l in mermaid_str.strip().split("\n") if l.strip()]
    if not lines:
        return False, "no content"
    first = lines[0].strip()
    # Check correct diagram type declaration.
    type_map = {
        "stateDiagram-v2": "stateDiagram-v2",
        "stateDiagram": "stateDiagram",
        "sequenceDiagram": "sequenceDiagram",
        "erDiagram": "erDiagram",
        "graph TD": "graph TD",
        "graph LR": "graph LR",
        "flowchart TD": "flowchart TD",
        "flowchart LR": "flowchart LR",
    }
    expected = type_map.get(diagram_type, diagram_type)
    if not first.startswith(expected.split()[0]):
        # Be lenient: check if first line contains the diagram type keyword.
        if expected.lower() not in first.lower():
            return False, f"expected '{expected}' as first line, got '{first}'"
    # Check minimum content per type.
    body = "\n".join(lines[1:])
    if "state" in diagram_type.lower():
        if "-->" not in body and "->" not in body:
            return False, "no state transitions found (expected '-->')"
    elif "sequence" in diagram_type.lower():
        if "->>" not in body and "->" not in body and "-->" not in body:
            return False, "no messages found (expected '->>' or '->')"
    elif "graph" in diagram_type.lower() or "flowchart" in diagram_type.lower():
        if "[" not in body and "(" not in body and "-->" not in body and "---" not in body:
            return False, "no nodes or edges found"
    elif "erDiagram" in diagram_type:
        if "{" not in body:
            return False, "no entity blocks found (expected '{}')"
    # Check for common syntax errors.
    # Unclosed quotes in labels.
    quote_count = body.count('"')
    if quote_count % 2 != 0:
        return False, "unclosed quote in diagram"
    # Curly braces in graph/flowchart edge labels break Mermaid (DIAMOND_START).
    if "graph" in diagram_type.lower() or "flowchart" in diagram_type.lower():
        import re
        for m in re.finditer(r"\|([^|]+)\|", body):
            if "{" in m.group(1) or "}" in m.group(1):
                return False, (
                    f"curly braces in edge label break Mermaid parser: "
                    f"|{m.group(1)}|"
                )
    # Reasonable length.
    if len(lines) > 500:
        return False, f"diagram too large ({len(lines)} lines)"
    return True, ""


async def _repair_mermaid(mermaid_str: str, reason: str, diagram_type: str) -> str:
    """Ask the LLM to repair a Mermaid diagram with syntax errors.

    Returns the repaired Mermaid string, or the original on failure.
    """
    from backend.agents.llm import build_pipeline_llm

    prompt = (
        f"El siguiente diagrama Mermaid tipo {diagram_type} tiene un error de "
        f"sintaxis: {reason}. "
        f"Corrigelo y devuelve SOLO el codigo Mermaid valido, sin explicaciones "
        f"ni markdown."
    )
    try:
        llm = build_pipeline_llm(temperature=0.0)
        result = await llm.ainvoke([("system", prompt), ("human", mermaid_str)])
        content = result.content if hasattr(result, "content") else str(result)
        # Strip markdown code fences if present.
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(
                lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            )
        return content.strip()
    except Exception:
        return mermaid_str  # return original on failure
