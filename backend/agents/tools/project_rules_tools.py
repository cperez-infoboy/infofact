"""Project-rules tools: the persistent per-project harness, agent-facing.

When the user says something like "from now on, pay attention to X" or "always
treat audit items as regulatory", that is a RULE — it must survive this chat,
this capture run and this session. These tools persist it to the project
harness (``project_rules``), where every pipeline (capture / analysis / SRS)
injects the active rules of its scope into its LLM user messages.

Registered with the phase subagents (requirements-capture, analysis, srs) so
the rule can be added from any phase's conversation. ``project_id`` is closed
over (same isolation contract as ``make_requirements_tools``).
"""
from __future__ import annotations

from langchain_core.tools import tool

from backend.database import AsyncSessionLocal
from backend.models.project_rule import RuleScope, RuleStatus
from backend.services import project_rules_store as store


def _rule_summary(rule) -> dict:
    return {
        "id": rule.id,
        "scope": rule.scope.value,
        "content": rule.content,
        "reason": rule.reason,
        "source": rule.source.value,
        "status": rule.status.value,
    }


def make_project_rules_tools(project_id: int) -> list:
    """Build add/list/retire tools over the project rules harness."""

    @tool
    async def add_project_rule(
        scope: str, content: str, reason: str = ""
    ) -> dict:
        """Register a persistent project rule the pipelines must honor.

        Use when the user states a lasting consideration ("de ahora en más,
        presta atención a X", "siempre marca Y como regulatorio", "trata los
        términos Z como glosario del cliente"). The rule is injected from the
        NEXT stage/run onward into every pipeline matching `scope`:
        - "capture": extraction, critique, classification
        - "analysis": architecture/analysis pipelines
        - "srs": SRS narrative drafting
        - "all": every phase
        `reason` records WHY the rule exists (audit only, never injected).
        Near-duplicate active rules are returned as `conflicts` — the rule is
        still created; report them to the user and retire the loser with
        retire_project_rule.
        """
        try:
            rule_scope = RuleScope(scope)
        except ValueError:
            return {
                "error": (
                    f"scope inválido: {scope!r}. "
                    "Válidos: capture | analysis | srs | all."
                )
            }
        try:
            async with AsyncSessionLocal() as session:
                result = await store.add_rule(
                    session,
                    project_id,
                    scope=rule_scope,
                    content=content,
                    reason=reason or None,
                )
                await session.commit()
            out = {"created": _rule_summary(result["rule"])}
            if result["conflicts"]:
                out["conflicts"] = result["conflicts"]
                out["note"] = (
                    "Regla creada, pero choca con reglas activas casi "
                    "idénticas (ver conflicts). Reportalo al usuario y retirá "
                    "la perdedora con retire_project_rule."
                )
            return out
        except Exception as exc:  # noqa: BLE001
            return {"error": f"add_project_rule failed: {exc}"}

    @tool
    async def list_project_rules(
        scope: str | None = None, include_retired: bool = False
    ) -> dict:
        """List the project's persistent rules (id, scope, content, status).

        Use it before adding a rule (avoid duplicates) and whenever the user
        asks "what rules do you follow?". `scope` filters by exact scope;
        include_retired adds the retired ones for audit.
        """
        try:
            rule_scope = RuleScope(scope) if scope else None
        except ValueError:
            return {
                "error": (
                    f"scope inválido: {scope!r}. "
                    "Válidos: capture | analysis | srs | all."
                )
            }
        try:
            async with AsyncSessionLocal() as session:
                rules = await store.list_rules(
                    session,
                    project_id,
                    scope=rule_scope,
                    include_retired=include_retired,
                )
            return {
                "count": len(rules),
                "rules": [_rule_summary(r) for r in rules],
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"list_project_rules failed: {exc}"}

    @tool
    async def retire_project_rule(rule_id: int, reason: str = "") -> dict:
        """Retire a persistent rule (soft: it stays for audit, stops applying).

        Use when the user revokes a consideration, or when add_project_rule
        reported a conflict and the loser was chosen. The rule stops being
        injected from the next stage/run; `reason` records why it was retired.
        """
        try:
            async with AsyncSessionLocal() as session:
                rule = await store.set_rule_status(
                    session,
                    project_id,
                    rule_id,
                    RuleStatus.RETIRED,
                    note=reason or None,
                )
                await session.commit()
            return _rule_summary(rule)
        except KeyError:
            return {"error": f"rule {rule_id} no existe en este proyecto"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"retire_project_rule failed: {exc}"}

    return [add_project_rule, list_project_rules, retire_project_rule]
