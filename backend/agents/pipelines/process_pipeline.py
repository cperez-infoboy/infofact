"""Process pipeline: state machines and sequence diagrams from functional requirements.

Multi-pass pipeline (5 passes):

  Pass 1a — lifecycle identification (narrow: which entities have lifecycles).
  Pass 1b — interaction identification (narrow: which sequence diagrams).
  Pass 2  — gap pass: re-scan uncovered functional REQ codes.
  Pass 3  — Mermaid generation (constrained by lifecycles + interactions).
  Pass 4  — deterministic checks on structured data (dangling refs, empty
  diagrams). No LLM repair needed — Mermaid is rendered in code.
  Pass 5  — LLM critique (MermaidSeqBench dimensions + process-specific).

Takes the functional RequirementItems (with acceptance criteria in Gherkin)
plus the MerResult (for entity names) and produces:
- State machines (Mermaid stateDiagram-v2) for entities with a lifecycle.
- Sequence diagrams (Mermaid sequenceDiagram) for key interactions.

Like the MER pipeline, the Mermaid stateDiagram-v2 and sequenceDiagram
strings are rendered deterministically in code from structured LLM output
(states, transitions, participants, messages) — the LLM never produces raw
Mermaid syntax. This guarantees syntactically valid diagrams that match
the structured data exactly.

Design notes (mirrors mer_pipeline.py patterns):

- Does NOT write to the DB. Returns a ProcessResult dataclass consumed by the
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
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from backend.agents.llm import structured_llm
from backend.agents.pipelines._diagram_colors import (
    STATE_ERROR,
    STATE_FINAL,
    STATE_INITIAL,
    STATE_NORMAL,
    class_def,
)
from backend.agents.pipelines._resilience import (
    DEFAULT_CONCURRENCY,
    _format_feedback,
    _format_goals,
    _invoke_with_retry,
    _sanitize_mermaid_id,
    _sanitize_mermaid_label,
)

if TYPE_CHECKING:
    from backend.agents.pipelines.mer_pipeline import MerResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM-facing schemas
# ---------------------------------------------------------------------------


class StateTransitionData(BaseModel):
    """One transition in a state machine."""

    from_state: str = Field(description="Nombre del estado origen")
    to_state: str = Field(description="Nombre del estado destino")
    label: str = Field(default="", description="Etiqueta de la transicion (opcional)")


class StateMachineSchema(BaseModel):
    """One state machine for an entity with a lifecycle.

    The LLM produces structured states + transitions. The Mermaid
    stateDiagram-v2 string is rendered deterministically by the
    ``mermaid`` property — the LLM never writes Mermaid syntax.
    """

    entity_name: str = Field(
        description=(
            "Nombre PascalCase de la entidad que tiene un ciclo de vida "
            "(ej. Order, Invoice, UserAccount)."
        ),
    )
    initial_state: str = Field(
        default="",
        description="Estado inicial del ciclo de vida (ej. 'pending').",
    )
    states: list[str] = Field(
        default_factory=list,
        description="Lista de todos los estados de la entidad.",
    )
    transitions: list[StateTransitionData] = Field(
        default_factory=list,
        description="Transiciones entre estados (ej. pending -> paid).",
    )
    traced_req_codes: list[str] = Field(
        default_factory=list,
        description=(
            "Codigos REQ-XXXX de los requerimientos que justifican este ciclo "
            "de vida. Tomarlos literalmente del input."
        ),
    )
    description: str = Field(
        default="",
        description="Descripcion del ciclo de vida (opcional).",
    )

    @property
    def mermaid(self) -> str:
        """Render a valid Mermaid stateDiagram-v2 deterministically."""
        return _render_state_diagram(self)


class SeqMessageData(BaseModel):
    """One message in a sequence diagram."""

    from_participant: str = Field(description="Participante que envia el mensaje")
    to_participant: str = Field(description="Participante que recibe el mensaje")
    arrow_type: str = Field(
        default="->>",
        description="Tipo de flecha: '->>' (solido) o '-->>' (discontinuo)",
    )
    label: str = Field(default="", description="Texto del mensaje")


class SequenceDiagramSchema(BaseModel):
    """One sequence diagram for a key interaction.

    The LLM produces structured participants + messages. The Mermaid
    sequenceDiagram string is rendered deterministically by the
    ``mermaid`` property — the LLM never writes Mermaid syntax.
    """

    name: str = Field(
        description=(
            "Nombre de la interaccion (ej. 'Checkout flow', 'User registration')."
        ),
    )
    participants: list[str] = Field(
        default_factory=list,
        description=(
            "Actores y entidades participantes (ej. ['User', 'OrderService', "
            "'PaymentGateway'])."
        ),
    )
    messages: list[SeqMessageData] = Field(
        default_factory=list,
        description="Mensajes entre participantes en orden cronologico.",
    )
    traced_req_codes: list[str] = Field(
        default_factory=list,
        description=(
            "Codigos REQ-XXXX de los requerimientos que justifican esta "
            "interaccion. Tomarlos literalmente del input."
        ),
    )
    description: str = Field(default="")

    @property
    def mermaid(self) -> str:
        """Render a valid Mermaid sequenceDiagram deterministically."""
        return _render_sequence_diagram(self)


class ProcessResultSchema(BaseModel):
    """Wrapper for the Mermaid generation result (Pass 3)."""

    state_machines: list[StateMachineSchema] = Field(
        default_factory=list,
    )
    sequence_diagrams: list[SequenceDiagramSchema] = Field(
        default_factory=list,
    )


# ---------------------------------------------------------------------------
# Narrow per-pass schemas (multi-pass pipeline)
# ---------------------------------------------------------------------------


class LifecycleCandidate(BaseModel):
    """Narrow schema for Pass 1a: lifecycle identification only."""

    entity_name: str = Field(description="Nombre PascalCase de la entidad")
    has_lifecycle: bool = Field(
        description="True si la entidad tiene un ciclo de vida con estados y transiciones"
    )
    lifecycle_summary: str = Field(
        default="",
        description="Descripcion breve del ciclo de vida (estados principales)",
    )


class LifecycleSchema(BaseModel):
    """Pass 1a output schema."""

    lifecycles: list[LifecycleCandidate]


class InteractionCandidate(BaseModel):
    """Narrow schema for Pass 1b: interaction identification only."""

    name: str = Field(description="Nombre de la interaccion")
    participants: list[str] = Field(
        default_factory=list,
        description="Actores y entidades participantes",
    )
    interaction_summary: str = Field(
        default="",
        description="Descripcion breve de la interaccion",
    )


class InteractionSchema(BaseModel):
    """Pass 1b output schema."""

    interactions: list[InteractionCandidate]


class ProcessCritiqueFinding(BaseModel):
    """Pass 5: critic finding."""

    diagram_name: str = Field(description="Diagrama afectado o 'GLOBAL'")
    issue_type: str = Field(
        description=(
            "syntax | logic | completeness | activation_handling | "
            "error_tracking | spurious_lifecycle | missing_lifecycle | "
            "orphan_diagram"
        ),
    )
    description: str
    severity: str = Field(description="blocker | warning | info")


class ProcessCritiqueSchema(BaseModel):
    """Pass 5 output schema."""

    findings: list[ProcessCritiqueFinding]


# ---------------------------------------------------------------------------
# Prompts (Spanish neutro — no voseo, no spanglish)
# ---------------------------------------------------------------------------

_LIFECYCLE_DISCOVERY_PROMPT = (
    "Eres un analista de procesos de software. Tu UNICA tarea es identificar "
    "que entidades del dominio tienen un ciclo de vida (estados y "
    "transiciones). NO generes el codigo Mermaid — solo identifica que "
    "entidades tienen lifecycles y un resumen breve.\n\n"
    "Reglas:\n"
    "- IDIOMA: manten el idioma original.\n"
    "- has_lifecycle: True solo si la entidad tiene estados y transiciones "
    "claros (ej. Order: pending -> paid -> shipped). Un simple CRUD no tiene "
    "lifecycle.\n"
    "- lifecycle_summary: describe los estados principales en una oracion.\n"
    "- No inventes entidades que no esten en la lista del MER.\n"
    "Devuelve SOLO el objeto estructurado."
)

_INTERACTION_DISCOVERY_PROMPT = (
    "Eres un analista de procesos de software. Tu UNICA tarea es identificar "
    "las interacciones clave entre entidades/actores que justifican un "
    "diagrama de secuencia. NO generes el codigo Mermaid — solo identifica "
    "que interacciones existen, quienes participan y un resumen breve.\n\n"
    "Reglas:\n"
    "- IDIOMA: manten el idioma original.\n"
    "- Identifica flujos de negocio principales, no operaciones CRUD simples.\n"
    "- Prefiere calidad sobre cantidad.\n"
    "- participants: lista actores y entidades que participan.\n"
    "Devuelve SOLO el objeto estructurado."
)

_PROCESS_GAP_PASS_PROMPT = (
    "Eres un analista de procesos. Los siguientes requerimientos funcionales "
    "NO fueron cubiertos en la primera pasada de identificacion de ciclos de "
    "vida e interacciones. Revisa cada uno y determina si justifica un "
    "diagrama nuevo. Si ninguno genera un diagrama nuevo, devuelve listas "
    "vacias.\n"
    "Devuelve SOLO el objeto estructurado."
)

_MERMAID_GENERATION_PROMPT = (
    "Eres un analista de procesos de software. A partir de los requerimientos "
    "funcionales y las entidades del dominio, genera diagramas Mermaid para "
    "las entidades con ciclo de vida y las interacciones identificadas.\n\n"
    "Reglas:\n"
    "- IDIOMA: manten el idioma original de los requerimientos.\n"
    "- Para maquinas de estados usa 'stateDiagram-v2' como primera linea.\n"
    "- Para secuencias usa 'sequenceDiagram' como primera linea.\n"
    "- El codigo Mermaid debe ser sintaxis valida.\n"
    "- traced_req_codes: incluye los codigos REQ-XXXX que justifican cada "
    "diagrama.\n"
    "- participants: lista los actores y entidades en cada diagrama de "
    "secuencia.\n"
    "- Genera SOLO los diagramas para las entidades e interacciones listadas.\n"
    "- description: para cada diagrama, escribe una descripcion breve (1-2 oraciones) "
    "del ciclo de vida o interaccion que representa.\n"
    "Devuelve SOLO el objeto estructurado."
)

_PROCESS_CRITIQUE_PROMPT = (
    "Eres un crítico de diagramas de proceso. Se te dan los diagramas "
    "(maquinas de estados + secuencias) generados a partir de los "
    "requerimientos funcionales. Tu tarea es identificar problemas:\n\n"
    "Categorias de fallo a revisar:\n"
    "1. logic: logica del flujo incorrecta o incompleta.\n"
    "2. completeness: faltan estados, transiciones o mensajes clave.\n"
    "3. activation_handling: manejo incorrecto de activaciones en secuencias.\n"
    "4. error_tracking: flujos de error no modelados.\n"
    "5. spurious_lifecycle: ciclo de vida para una entidad CRUD simple.\n"
    "6. missing_lifecycle: entidad con ciclo de vida claro sin diagrama.\n"
    "7. orphan_diagram: diagrama sin trazabilidad a requerimientos.\n\n"
    "Si los diagramas estan correctos, devuelve findings vacio.\n"
    "Devuelve SOLO el objeto estructurado."
)

# Original prompt kept for backward compatibility.
PROCESS_PROMPT = _MERMAID_GENERATION_PROMPT


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ProcessResult:
    """Output of the process pipeline (state machines, sequence diagrams, stats)."""

    state_machines: list[StateMachineSchema] = field(default_factory=list)
    sequence_diagrams: list[SequenceDiagramSchema] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_process_items_text(
    items,
    mer_result: "MerResult | None" = None,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
    feedback: str = "",
) -> str:
    """Build the user message text from functional requirement items + entities."""
    lines: list[str] = []
    if feedback:
        lines.append(_format_feedback(feedback).rstrip())
        lines.append("")
    if goals:
        lines.append(
            _format_goals(
                goals, section_title="OBJETIVOS FUNCIONALES"
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
            + ", ".join(ent_names[:30])
        )

    lines.append("")
    lines.append("REQUERIMIENTOS FUNCIONALES:")
    lines.append("")

    for it in items:
        lines.append(f"REQ_CODE: {it.code}")
        lines.append(f"TYPE: {it.type.value}")
        lines.append(f"STATEMENT: {it.statement}")
        ac = getattr(it, "acceptance_criteria", None)
        if ac:
            lines.append("ACCEPTANCE_CRITERIA:")
            for criterion in ac:
                lines.append(f"  - {criterion}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mermaid deterministic rendering (from structured output)
# ---------------------------------------------------------------------------

# Valid arrow types for sequenceDiagram messages.
_VALID_SEQ_ARROWS = {"->>", "-->>", "->", "-->", "-x", "--x"}


def _render_state_diagram(sm: "StateMachineSchema") -> str:
    """Render a valid Mermaid stateDiagram-v2 from structured data.

    Produces syntax like::

        stateDiagram-v2
            [*] --> Pending
            Pending --> Paid : submit payment
            Paid --> [*]

    All identifiers are sanitized; labels are escaped. The output is
    guaranteed to be syntactically valid Mermaid because Python controls
    every character.

    States are colored by role:
    - initial → green border (stroke-width 3px)
    - final (no outgoing transitions) → red border (stroke-width 3px)
    - error (name contains cancel/error/fail/reject) → bright red fill
    - normal → blue fill
    """
    lines: list[str] = ["stateDiagram-v2"]

    # Build the set of all known states (explicit list + transition refs).
    all_states: list[str] = list(sm.states)
    for trans in sm.transitions:
        if trans.from_state not in all_states:
            all_states.append(trans.from_state)
        if trans.to_state not in all_states:
            all_states.append(trans.to_state)

    # Initial transition.
    if sm.initial_state:
        init_id = _sanitize_mermaid_id(sm.initial_state)
        lines.append(f"    [*] --> {init_id}")
    elif all_states:
        init_id = _sanitize_mermaid_id(all_states[0])
        lines.append(f"    [*] --> {init_id}")

    # Transitions.
    for trans in sm.transitions:
        from_id = _sanitize_mermaid_id(trans.from_state)
        to_id = _sanitize_mermaid_id(trans.to_state)
        label = _sanitize_mermaid_label(trans.label)
        if label:
            lines.append(f"    {from_id} --> {to_id} : {label}")
        else:
            lines.append(f"    {from_id} --> {to_id}")

    # Final states: states with no outgoing transition.
    sources = {t.from_state for t in sm.transitions}
    for state in all_states:
        if state not in sources:
            state_id = _sanitize_mermaid_id(state)
            lines.append(f"    {state_id} --> [*]")

    # classDef + class assignments for semantic coloring by state role.
    lines.append("    " + class_def("st_initial", *STATE_INITIAL, sw=3))
    lines.append("    " + class_def("st_final", *STATE_FINAL, sw=3))
    lines.append("    " + class_def("st_error", *STATE_ERROR))
    lines.append("    " + class_def("st_normal", *STATE_NORMAL))
    error_keywords = ("cancel", "error", "fail", "reject", "invalid")
    for state in all_states:
        sid = _sanitize_mermaid_id(state)
        state_lower = state.lower()
        if state == sm.initial_state:
            lines.append(f"    class {sid} st_initial")
        elif any(kw in state_lower for kw in error_keywords):
            lines.append(f"    class {sid} st_error")
        elif state not in sources:
            lines.append(f"    class {sid} st_final")
        else:
            lines.append(f"    class {sid} st_normal")

    return "\n".join(lines)


def _render_sequence_diagram(sq: "SequenceDiagramSchema") -> str:
    """Render a valid Mermaid sequenceDiagram from structured data.

    Produces syntax like::

        sequenceDiagram
            participant User
            participant OrderService
            User ->> OrderService : create order
            OrderService -->> User : confirmation

    All identifiers are sanitized; labels are escaped. Participant
    declarations are generated from the explicit list + message refs.
    The output is guaranteed to be syntactically valid Mermaid.
    """
    lines: list[str] = ["sequenceDiagram"]

    # Collect participants: explicit list + any referenced in messages.
    seen: set[str] = set()
    declared: list[str] = []

    for p in sq.participants:
        pid = _sanitize_mermaid_id(p)
        if pid not in seen:
            seen.add(pid)
            declared.append(p)

    for msg in sq.messages:
        for p in (msg.from_participant, msg.to_participant):
            pid = _sanitize_mermaid_id(p)
            if pid not in seen:
                seen.add(pid)
                declared.append(p)

    # Declare participants.
    for p in declared:
        pid = _sanitize_mermaid_id(p)
        display = p.replace('"', "")
        if _sanitize_mermaid_id(display) == pid:
            lines.append(f"    participant {pid}")
        else:
            lines.append(f'    participant {pid} as "{display}"')

    # Messages.
    for msg in sq.messages:
        from_id = _sanitize_mermaid_id(msg.from_participant)
        to_id = _sanitize_mermaid_id(msg.to_participant)
        arrow = msg.arrow_type if msg.arrow_type in _VALID_SEQ_ARROWS else "->>"
        label = _sanitize_mermaid_label(msg.label)
        lines.append(f"    {from_id} {arrow} {to_id} : {label}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pass 1a: lifecycle identification (narrow)
# ---------------------------------------------------------------------------


async def _identify_lifecycles(
    items,
    mer_result: "MerResult | None" = None,
    *,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
    feedback: str = "",
) -> list[LifecycleCandidate]:
    """Pass 1a: identify which entities have lifecycles."""
    user_text = _build_process_items_text(
        items, mer_result, project_name, project_description,
        goals=goals, feedback=feedback,
    )
    msgs = [("system", _LIFECYCLE_DISCOVERY_PROMPT), ("human", user_text)]
    llm = structured_llm(LifecycleSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="process_lifecycle"
        )
        return list(result.lifecycles)
    except Exception:
        logger.exception("_identify_lifecycles: all retries exhausted")
        return []


# ---------------------------------------------------------------------------
# Pass 1b: interaction identification (narrow)
# ---------------------------------------------------------------------------


async def _identify_interactions(
    items,
    mer_result: "MerResult | None" = None,
    *,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
    feedback: str = "",
) -> list[InteractionCandidate]:
    """Pass 1b: identify which sequence diagrams to produce."""
    user_text = _build_process_items_text(
        items, mer_result, project_name, project_description,
        goals=goals, feedback=feedback,
    )
    msgs = [("system", _INTERACTION_DISCOVERY_PROMPT), ("human", user_text)]
    llm = structured_llm(InteractionSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="process_interaction"
        )
        return list(result.interactions)
    except Exception:
        logger.exception("_identify_interactions: all retries exhausted")
        return []


# ---------------------------------------------------------------------------
# Pass 2: gap pass for uncovered functional reqs
# ---------------------------------------------------------------------------


async def _gap_pass_process(
    items,
    lifecycles: list[LifecycleCandidate],
    interactions: list[InteractionCandidate],
    mer_result: "MerResult | None" = None,
    *,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
    feedback: str = "",
) -> tuple[list[LifecycleCandidate], list[InteractionCandidate]]:
    """Pass 2: detect uncovered REQ codes and re-scan them.

    Returns (extra_lifecycles, extra_interactions).
    """
    input_codes = {it.code for it in items}
    covered_codes: set[str] = set()
    # Lifecycles and interactions don't carry req codes in the narrow schema;
    # we can't do a precise gap pass on codes. Instead, check if the total
    # number of lifecycles + interactions seems to cover the input items.
    # If there are many items but zero diagrams identified, re-scan everything.
    if not input_codes:
        return ([], [])

    total_diagrams = len(lifecycles) + len(interactions)
    if total_diagrams > 0 and total_diagrams >= len(items) * 0.3:
        # Reasonable coverage — no gap pass needed.
        return ([], [])

    # Re-scan all items for additional lifecycles and interactions.
    user_text = _build_process_items_text(
        items, mer_result, project_name, project_description,
        goals=goals, feedback=feedback,
    )
    existing_lc = ", ".join(
        lc.entity_name for lc in lifecycles if lc.has_lifecycle
    ) or "(ninguna)"
    existing_int = ", ".join(i.name for i in interactions) or "(ninguna)"
    user_text += (
        f"\nCICLOS DE VIDA YA IDENTIFICADOS: {existing_lc}\n"
        f"INTERACCIONES YA IDENTIFICADAS: {existing_int}\n"
        "Revisa los requerimientos y determina si hay ciclos de vida o "
        "interacciones NUEVAS que se hayan pasado por alto.\n"
    )
    msgs = [("system", _PROCESS_GAP_PASS_PROMPT), ("human", user_text)]
    # Use a combined schema for the gap pass.
    class _GapSchema(BaseModel):
        lifecycles: list[LifecycleCandidate] = Field(default_factory=list)
        interactions: list[InteractionCandidate] = Field(default_factory=list)

    llm = structured_llm(_GapSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="process_gap_pass"
        )
        return (list(result.lifecycles), list(result.interactions))
    except Exception:
        logger.exception("_gap_pass_process: all retries exhausted")
        return ([], [])


# ---------------------------------------------------------------------------
# Pass 3: Mermaid generation (constrained by lifecycles + interactions)
# ---------------------------------------------------------------------------


async def _generate_mermaid_diagrams(
    items,
    lifecycles: list[LifecycleCandidate],
    interactions: list[InteractionCandidate],
    mer_result: "MerResult | None" = None,
    *,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
    feedback: str = "",
) -> ProcessResultSchema | None:
    """Pass 3: generate Mermaid diagrams constrained by the identified lifecycles + interactions."""
    user_text = _build_process_items_text(
        items, mer_result, project_name, project_description,
        goals=goals, feedback=feedback,
    )
    lc_lines = []
    for lc in lifecycles:
        if lc.has_lifecycle:
            lc_lines.append(
                f"- {lc.entity_name}: {lc.lifecycle_summary}"
            )
    int_lines = []
    for i in interactions:
        parts = ", ".join(i.participants) if i.participants else "(sin participantes)"
        int_lines.append(f"- {i.name} ({parts}): {i.interaction_summary}")

    user_text += (
        "\nENTIDADES CON CICLO DE VIDA (genera stateDiagram-v2 para cada una):\n"
        + ("\n".join(lc_lines) if lc_lines else "(ninguna)")
        + "\n\nINTERACCIONES (genera sequenceDiagram para cada una):\n"
        + ("\n".join(int_lines) if int_lines else "(ninguna)")
        + "\n"
    )
    msgs = [("system", _MERMAID_GENERATION_PROMPT), ("human", user_text)]
    llm = structured_llm(ProcessResultSchema)
    try:
        return await _invoke_with_retry(
            llm, msgs, context_label="process_generate"
        )
    except Exception:
        logger.exception("_generate_mermaid_diagrams: all retries exhausted")
        return None


# ---------------------------------------------------------------------------
# Pass 4: validation + Mermaid repair
# ---------------------------------------------------------------------------


def _validate_diagrams(
    state_machines: list[StateMachineSchema],
    sequence_diagrams: list[SequenceDiagramSchema],
) -> list[str]:
    """Pass 4: deterministic checks on structured diagram data.

    With deterministic rendering, Mermaid syntax is always valid — no
    sanitize/validate/repair cycle needed. This function catches logical
    issues: empty diagrams, dangling state references, undeclared
    participants.
    """
    warnings: list[str] = []

    for sm in state_machines:
        if not sm.transitions:
            warnings.append(
                f"Maquina de estados '{sm.entity_name}' sin transiciones"
            )
        # Check for states referenced in transitions but not in states list.
        known = set(sm.states)
        for trans in sm.transitions:
            if trans.from_state not in known:
                warnings.append(
                    f"Maquina de estados '{sm.entity_name}': "
                    f"transicion referencia estado desconocido "
                    f"'{trans.from_state}'"
                )
            if trans.to_state not in known:
                warnings.append(
                    f"Maquina de estados '{sm.entity_name}': "
                    f"transicion referencia estado desconocido "
                    f"'{trans.to_state}'"
                )

    for sq in sequence_diagrams:
        if not sq.messages:
            warnings.append(
                f"Diagrama de secuencia '{sq.name}' sin mensajes"
            )
        # Check for participants referenced in messages but not declared.
        declared = set(sq.participants)
        for msg in sq.messages:
            if msg.from_participant not in declared:
                warnings.append(
                    f"Diagrama de secuencia '{sq.name}': "
                    f"participante '{msg.from_participant}' no declarado"
                )
            if msg.to_participant not in declared:
                warnings.append(
                    f"Diagrama de secuencia '{sq.name}': "
                    f"participante '{msg.to_participant}' no declarado"
                )

    return warnings


# ---------------------------------------------------------------------------
# Pass 5: LLM critique
# ---------------------------------------------------------------------------


async def _critique_processes(
    state_machines: list[StateMachineSchema],
    sequence_diagrams: list[SequenceDiagramSchema],
    items,
    *,
    project_name: str = "",
    project_description: str = "",
    goals: list[Any] | None = None,
) -> list[ProcessCritiqueFinding]:
    """Pass 5: LLM critique covering MermaidSeqBench + process-specific issues."""
    sm_lines = []
    for sm in state_machines:
        sm_lines.append(
            f"- {sm.entity_name}: {sm.description or '(sin descripcion)'}"
        )
    sm_summary = "\n".join(sm_lines) or "(ninguna)"

    sq_lines = []
    for sq in sequence_diagrams:
        parts = ", ".join(sq.participants) if sq.participants else ""
        sq_lines.append(f"- {sq.name} ({parts})")
    sq_summary = "\n".join(sq_lines) or "(ninguno)"

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

    goals_block = ""
    if goals:
        goals_block = _format_goals(
            goals, section_title="OBJETIVOS FUNCIONALES"
        )

    user_text = (
        f"{header}\n\n"
        f"{goals_block}"
        f"REQUERIMIENTOS FUNCIONALES:\n{req_summary}\n\n"
        f"MAQUINAS DE ESTADOS GENERADAS:\n{sm_summary}\n\n"
        f"DIAGRAMAS DE SECUENCIA GENERADOS:\n{sq_summary}\n"
    )
    msgs = [("system", _PROCESS_CRITIQUE_PROMPT), ("human", user_text)]
    llm = structured_llm(ProcessCritiqueSchema)
    try:
        result = await _invoke_with_retry(
            llm, msgs, context_label="process_critique"
        )
        return list(result.findings)
    except Exception:
        logger.exception("_critique_processes: all retries exhausted")
        return []


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


async def generate_processes(
    items,
    mer_result: "MerResult | None" = None,
    *,
    project_name: str = "",
    project_description: str = "",
    concurrency: int = DEFAULT_CONCURRENCY,
    goals: list[Any] | None = None,
    enable_critique: bool = True,
    feedback: str = "",
) -> ProcessResult:
    """Generate state machines and sequence diagrams from functional requirements.

    Multi-pass pipeline:

      Pass 1a — lifecycle identification (narrow).
      Pass 1b — interaction identification (narrow).
      Pass 2  — gap pass for uncovered functional REQ codes.
      Pass 3  — Mermaid generation (constrained by lifecycles + interactions).
      Pass 4  — deterministic + Mermaid syntax validation; auto-repair.
      Pass 5  — LLM critique (optional, enabled by default).

    Takes RequirementItem objects (functional/data/process types, with
    acceptance_criteria Gherkin) and a MerResult (for entity names). Produces
    a ProcessResult with Mermaid stateDiagram-v2 and sequenceDiagram strings.
    """
    if not items:
        return ProcessResult(stats={"input": 0})

    # Pass 1a + 1b: lifecycle + interaction identification (run in parallel).
    lifecycles, interactions = await asyncio.gather(
        _identify_lifecycles(
            items, mer_result,
            project_name=project_name,
            project_description=project_description,
            goals=goals, feedback=feedback,
        ),
        _identify_interactions(
            items, mer_result,
            project_name=project_name,
            project_description=project_description,
            goals=goals, feedback=feedback,
        ),
    )

    # Pass 2: gap pass for uncovered requirements.
    gap_lifecycles, gap_interactions = await _gap_pass_process(
        items, lifecycles, interactions, mer_result,
        project_name=project_name,
        project_description=project_description,
        goals=goals, feedback=feedback,
    )
    all_lifecycles = lifecycles + gap_lifecycles
    all_interactions = interactions + gap_interactions

    # Pass 3: Mermaid generation (constrained).
    result = await _generate_mermaid_diagrams(
        items, all_lifecycles, all_interactions, mer_result,
        project_name=project_name,
        project_description=project_description,
        goals=goals, feedback=feedback,
    )

    if result is None:
        return ProcessResult(stats={
            "input": len(items),
            "error": "generation_failed",
            "state_machines": 0,
            "sequence_diagrams": 0,
        })

    state_machines = list(result.state_machines)
    sequence_diagrams = list(result.sequence_diagrams)

    # Pass 4: deterministic validation (no LLM repair needed).
    validation_warnings = _validate_diagrams(state_machines, sequence_diagrams)

    # Pass 5: LLM critique (optional).
    critique_findings: list[ProcessCritiqueFinding] = []
    if enable_critique:
        critique_findings = await _critique_processes(
            state_machines, sequence_diagrams, items,
            project_name=project_name,
            project_description=project_description,
            goals=goals,
        )

    stats = {
        "input": len(items),
        "state_machines": len(state_machines),
        "sequence_diagrams": len(sequence_diagrams),
        "lifecycles_identified": sum(
            1 for lc in all_lifecycles if lc.has_lifecycle
        ),
        "interactions_identified": len(all_interactions),
        "gap_pass_found": len(gap_lifecycles) + len(gap_interactions),
        "mermaid_repaired": 0,
        "validation_warnings": validation_warnings,
        "critique_findings": [f.model_dump() for f in critique_findings],
        "critique_blockers": sum(
            1 for f in critique_findings if f.severity == "blocker"
        ),
    }
    logger.info(
        "generate_processes: %d items -> %d state machines, %d sequence "
        "diagrams (%d lifecycles, %d interactions, %d repaired, "
        "%d warnings, %d critique findings)",
        stats["input"],
        stats["state_machines"],
        stats["sequence_diagrams"],
        stats["lifecycles_identified"],
        stats["interactions_identified"],
        0,  # mermaid_repaired — always 0 with deterministic rendering
        len(validation_warnings),
        len(critique_findings),
    )
    return ProcessResult(
        state_machines=state_machines,
        sequence_diagrams=sequence_diagrams,
        stats=stats,
    )
