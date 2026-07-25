"""Shared retry primitives for LLM-backed pipeline stages.

Z.ai GLM (and other vendors) intermittently fail in two ways that batch
pipelines must absorb instead of aborting a whole `asyncio.gather`:

- Transient: 429 / 5xx / timeout / connection error. Recoverable with
  exponential backoff up to `_TRANSIENT_RETRIES` attempts.
- Parse: truncated or malformed structured JSON. A retry may catch a
  clean response; if every retry fails the caller wraps the item in a
  sentinel (stage-specific shape) so it lands in human-review buckets.

Imported by `critique._judge_item` and `classification._classify_item`
to keep the transient classification in sync across stages — drifting
here would silently change retry behaviour under load.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, APITimeoutError

# Transient (429/5xx/timeout) retries with exponential backoff (cap 60s).
# Parse-failure attempts are defined per stage (critique: _JUDGE_ATTEMPTS,
# classification: _CLASSIFY_ATTEMPTS) because the right depth depends on
# how expensive each call is and how the sentinel downstream is consumed.
_TRANSIENT_RETRIES = 8

# ---------------------------------------------------------------------------
# Batch defaults (plan §13.B)
# ---------------------------------------------------------------------------
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
