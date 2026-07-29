"""Opaque, collision-free REQ-XXXX code allocation (Crockford base32).

Codes carry no sequential meaning, so gaps after merge/delete don't read as
lost requirements. Replaces the former contiguous REQ-NNN scheme.
"""
from __future__ import annotations

import re
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.requirement import RequirementItem

# Crockford base32: 0-9 + A-Z excluding I, L, O, U (visually ambiguous).
_CROCKFORD_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_OPAQUE_LEN = 4
OPAQUE_CODE_RE = re.compile(r"^REQ-[0-9A-HJ-KM-NP-TV-Z]{4}$")
_MAX_ATTEMPTS = 10


async def gen_opaque_code(
    session: AsyncSession, project_id: int, *, reserved: set[str] | None = None
) -> str:
    """Allocate a unique opaque REQ-XXXX code for the project.

    Checks existing rows plus an optional in-batch ``reserved`` set (codes
    pre-assigned but not yet flushed, e.g. inside ``_persist``'s pre-assign
    pass). Allocated codes are added to ``reserved`` so a single batch never
    hands out the same code twice.
    """
    batch = reserved if reserved is not None else set()
    rows = await session.execute(
        select(RequirementItem.code).where(
            RequirementItem.project_id == project_id
        )
    )
    existing = {c for (c,) in rows.all() if c}
    for _ in range(_MAX_ATTEMPTS):
        seg = "".join(secrets.choice(_CROCKFORD_B32) for _ in range(_OPAQUE_LEN))
        code = f"REQ-{seg}"
        if code not in existing and code not in batch:
            batch.add(code)
            return code
    raise RuntimeError("could not allocate a unique REQ code (10 attempts)")
