"""Pure helper to derive a project slug from a free-form name.

Slug rules (validated against `^[a-z0-9_-]{2,48}$`):
  - Unicode-normalize (NFKD) and strip combining marks (accents -> ascii).
  - Lowercase.
  - Replace any run of chars outside [a-z0-9_-] with a single `-`.
  - Collapse repeated `-` and strip leading/trailing `-`.
  - If the result is empty or shorter than 2 chars, raise ValueError.
  - If longer than 48 chars, truncate to 48 (re-strip trailing `-`).

This function does NOT handle collision suffixing. The caller (router or
migration) is responsible for appending `-2`, `-3`, ... when multiple
projects within the same user derive the same base slug.
"""
from __future__ import annotations

import re
import unicodedata

_MAX_LEN = 48
_MIN_LEN = 2
_VALID_RUN = re.compile(r"[^a-z0-9_-]+")
_COLLAPSE_DASH = re.compile(r"-{2,}")


def slugify(name: str) -> str:
    """Derive a project slug from a free-form name.

    Raises:
        ValueError: if the input cannot yield a slug of at least _MIN_LEN chars
            after normalization (e.g. empty string, only punctuation).
    """
    if not isinstance(name, str):
        raise TypeError(f"name must be str, got {type(name).__name__}")

    # NFKD decomposition separates combining marks (accents) from base chars;
    # dropping the combining marks turns `á` -> `a`, `ñ` -> `n`.
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()

    # Replace any run of invalid chars with a single dash, then collapse.
    dashed = _VALID_RUN.sub("-", lowered)
    dashed = _COLLAPSE_DASH.sub("-", dashed)
    dashed = dashed.strip("-")

    if len(dashed) < _MIN_LEN:
        raise ValueError(f"cannot derive slug from name: {name!r}")

    if len(dashed) > _MAX_LEN:
        dashed = dashed[:_MAX_LEN].rstrip("-")
        if len(dashed) < _MIN_LEN:
            # Pathological input: everything past char 48 was a dash that got
            # stripped and left us too short. Treat as unslugifiable.
            raise ValueError(f"cannot derive slug from name: {name!r}")

    return dashed
