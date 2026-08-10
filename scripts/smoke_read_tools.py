#!/usr/bin/env python
"""Smoke: make_requirements_read_tools exposes exactly the 3 read-only tools
(list / get / build_srs) and none of the mutations.

Valida el refactor de Fase A1: el orquestador recibe solo lectura, las
mutaciones siguen exclusivas del subagente. Sin DB.
"""
import asyncio
import sys

from backend.agents.tools.requirements_tools import (
    make_requirements_read_tools,
    make_requirements_tools,
)

EXPECTED_READ = {"build_srs", "get_requirement", "list_requirements"}
# Every mutation tool the editing factory must still own (sanity: the refactor
# did not accidentally drop or rename any).
EXPECTED_MUTATIONS = {
    "add_requirement", "update_requirement", "delete_requirement",
    "merge_requirements", "split_requirement", "link_requirements",
    "resolve_conflict", "approve_requirement", "verify_span",
    "reject_requirement",
    "add_acceptance_criterion",
}


async def main() -> int:
    reads = make_requirements_read_tools(project_id=1)
    read_names = sorted(t.name for t in reads)
    print(f"read tools: {read_names}")
    assert set(read_names) == EXPECTED_READ, (
        f"read tools {set(read_names)} != {EXPECTED_READ}"
    )

    all_tools = make_requirements_tools(project_id=1)
    all_names = {t.name for t in all_tools}
    print(f"editing factory tool count: {len(all_names)}")
    # The editing factory still owns every read tool (twins) + every mutation.
    assert EXPECTED_READ.issubset(all_names), (
        f"editing factory lost read tools: {EXPECTED_READ - all_names}"
    )
    assert EXPECTED_MUTATIONS.issubset(all_names), (
        f"editing factory lost mutations: {EXPECTED_MUTATIONS - all_names}"
    )
    # No mutation leaks into the read-only set.
    leaks = set(read_names) & EXPECTED_MUTATIONS
    assert not leaks, f"mutation tools leaked into read-only set: {leaks}"

    print(f"OK: {len(read_names)} read tools, {len(all_names)} editing tools, 0 leaks")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
