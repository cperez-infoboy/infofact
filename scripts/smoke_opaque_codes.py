"""Smoke: opaque REQ-XXXX allocation — 2000 codes, zero collisions.

Runs gen_opaque_code 2000 times against an in-memory store sharing one
``reserved`` set. 2000 > the ~1177 birthday-collision point for a 4-char
Crockford space, so without the uniqueness check duplicates would be
near-certain. The check must hold.

Run: .venv/bin/python scripts/smoke_opaque_codes.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.models.project
import backend.models.requirement
from backend.models.base import Base
from backend.models.project import Project
from backend.services._req_codes import OPAQUE_CODE_RE, gen_opaque_code


N = 2000


async def main() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        proj = Project(
            user_id=1, name="P", slug="p",
            description="d", phase="requirements",
        )
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        pid = proj.id

    reserved: set[str] = set()
    start = time.perf_counter()
    async with session_factory() as session:
        for _ in range(N):
            code = await gen_opaque_code(session, pid, reserved=reserved)
            assert OPAQUE_CODE_RE.match(code), code
    elapsed = time.perf_counter() - start

    ambiguous = [c for c in reserved if any(ch in c for ch in "ILOU")]
    assert not ambiguous, f"ambiguous chars leaked: {ambiguous[:5]}"
    assert len(reserved) == N, f"collisions: {N - len(reserved)}"
    print(f"OK: {N} opaque codes, 0 collisions, {elapsed:.2f}s")
    print(f"sample: {sorted(reserved)[:5]}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
