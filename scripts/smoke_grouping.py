#!/usr/bin/env python
"""Smoke: grouping plan serialize/parse round-trip + parser edge cases.

Sin DB. Cubre el riesgo principal del diseno (el parser determinista estricto):
- serialize_plan_md -> parse_plan_md preserva la estructura (round-trip).
- Plan vacio serializa y parsea limpio.
- Sintaxis rota (falta frontmatter, campo desconocido, confianza no numerica)
  lanza PlanParseError accionable con la linea.
- Un grupo cuyos miembros el usuario vacio se descarta (no-op merge).

build_grouping_plan + apply_grouping_plan (que tocan la DB) se validan aparte
en una sesion real o un smoke con store sembrado.
"""
import asyncio
import sys

from backend.agents.pipelines.grouping import (
    Group,
    GroupingPlan,
    PlanParseError,
    parse_plan_md,
    serialize_plan_md,
)


def _round_trip() -> None:
    plan = GroupingPlan(
        groups=[
            Group(
                keeper_code="REQ-005", member_codes=["REQ-012", "REQ-018"],
                reason="login social duplicado", confidence=0.92,
            ),
            Group(
                keeper_code="REQ-007", member_codes=["REQ-021"],
                reason="exportacion PDF", confidence=0.88,
            ),
        ],
        generated_at="2026-07-27T14:30:00",
        project="test1",
        status="proposed",
    )
    md = serialize_plan_md(plan)
    print("--- serialized plan ---")
    print(md)
    print("--- end ---")
    parsed = parse_plan_md(md)
    assert parsed.status == plan.status, f"status {parsed.status!r} != {plan.status!r}"
    assert parsed.generated_at == plan.generated_at
    assert parsed.project == plan.project
    assert len(parsed.groups) == len(plan.groups), "group count mismatch"
    for orig, got in zip(plan.groups, parsed.groups):
        assert got.keeper_code == orig.keeper_code
        assert got.member_codes == orig.member_codes, (
            f"members {got.member_codes} != {orig.member_codes}"
        )
        assert got.reason == orig.reason
        assert got.confidence == orig.confidence
    print("[round-trip] OK")


def _empty_plan() -> None:
    md = serialize_plan_md(GroupingPlan.empty(project="p"))
    parsed = parse_plan_md(md)
    assert parsed.groups == [], f"esperaba 0 grupos, got {len(parsed.groups)}"
    print("[empty plan] OK")


def _expect_parse_error(md: str, label: str) -> None:
    try:
        parse_plan_md(md)
    except PlanParseError as exc:
        print(f"[{label}] OK -> {exc}")
        return
    raise AssertionError(f"debio lanzar PlanParseError ({label})")


def _broken_syntax() -> None:
    # Missing frontmatter.
    _expect_parse_error(
        "## Grupo 1\n- **Mantener:** REQ-001\n- **Fusionar:** REQ-002\n",
        "no frontmatter",
    )
    # Unknown bullet field under a group.
    _expect_parse_error(
        "---\nstatus: proposed\n---\n\n"
        "## Grupo 1\n- **Mantener:** REQ-001\n- **Fusionar:** REQ-002\n"
        "- **Color:** rojo\n",
        "unknown field",
    )
    # Non-numeric confidence.
    _expect_parse_error(
        "---\nstatus: proposed\n---\n\n"
        "## Grupo 1\n- **Mantener:** REQ-001\n- **Fusionar:** REQ-002\n"
        "- **Confianza:** alto\n",
        "bad confidence",
    )
    # Group closed without a keeper.
    _expect_parse_error(
        "---\nstatus: proposed\n---\n\n"
        "## Grupo 1\n- **Fusionar:** REQ-002\n",
        "missing keeper",
    )


def _cleared_members_dropped() -> None:
    # A group whose user cleared the members must be dropped (no-op merge),
    # while a well-formed sibling survives.
    md = (
        "---\nstatus: proposed\n---\n\n"
        "## Grupo 1\n- **Mantener:** REQ-001\n- **Fusionar:**\n"
        "## Grupo 2\n- **Mantener:** REQ-010\n- **Fusionar:** REQ-011\n"
    )
    parsed = parse_plan_md(md)
    assert len(parsed.groups) == 1, f"esperaba 1 grupo, got {len(parsed.groups)}"
    assert parsed.groups[0].keeper_code == "REQ-010"
    print("[cleared members dropped] OK")


async def main() -> int:
    _round_trip()
    _empty_plan()
    _broken_syntax()
    _cleared_members_dropped()
    print("OK: grouping serialize/parse round-trip + edge cases")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
