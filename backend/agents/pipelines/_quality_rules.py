"""Reglas deterministas de calidad de requerimientos (fuente única de verdad).

Pre-checks programáticos ITEM-level (INCOSE + requirement smells + EARS)
extraídos de ``srs_quality.py`` para compartirlos con el pipeline de captura
(shift-left de calidad). **Puro**: sin dependencias de modelos ORM ni de
servicios, de modo que pueda importarse tanto desde ``srs_quality`` (Fase A del
SRS) como desde ``critique`` (pre-check de la crítica de captura) sin
acoplamiento circular.

``programmatic_findings_for_text`` devuelve ``ProgrammaticFinding`` (dataclass
plano). Cada consumidor mapea a su propia representación:

- ``srs_quality.programmatic_findings`` → dicts de ``RequirementFinding``
  (agrega ``scope=ITEM``, ``req_id``, ``detected_by="programmatic"``).
- ``critique._apply_quality_flags`` → ``CritiqueVerdict`` (``atomic`` /
  ``verifiable`` / ``reasons``).

``req_type`` (string con el valor de ``ReqType``, p. ej. ``"performance"``)
habilita ``incose.unmeasurable_nfr`` (NFR sin target medible). En captura el
``ReqType`` aún no existe al evaluar la crítica (Paso 5, anterior a la
clasificación Paso 6), por lo que se pasa ``None`` y esa regla se omite.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Modal de obligación (español + inglés). Su ausencia es una regla INCOSE.
MODAL_RE = re.compile(
    r"\b(debe|deber[aá]|deb[ií]an|tienen?\s+que|requiere|shall|must|should|will|needs?\s+to)\b",
    re.IGNORECASE,
)

# Combinadores que sugieren >1 requerimiento en un enunciado.
COMBINATOR_RE = re.compile(r"\b(and/or|y/o|o bien|either\s+or)\b|/", re.IGNORECASE)

# Negación: requerimientos negativos son difíciles de verificar.
NEGATION_RE = re.compile(
    r"\b(shall\s+not|must\s+not|no\s+debe|no\s+deber[aá]|nunca\s+debe)\b",
    re.IGNORECASE,
)

# Absolutos no verificables.
ABSOLUTE_RE = re.compile(
    r"(?:100\s*%|\b(?:siempre|nunca|todos?|always|never|all)\b)",
    re.IGNORECASE,
)

# Pronombres: referencia ambigua.
PRONOUN_RE = re.compile(
    r"\b(él|ella|ello|ellos|ellas|lo|la|los|las|sus|su)\b|\b(it|they|them|its|their|he|she)\b",
    re.IGNORECASE,
)

# Términos vagos curados (bilingüe). Case-insensitive, palabra completa.
VAGUE_TERMS = [
    "rápido", "rapida", "rápidos", "eficiente", "robusto", "amigable",
    "fácil", "facil", "intuitivo", "flexible", "apropiado", "adecuada",
    "adecuado", "óptimo", "optimo", "moderno", "escalable", "seguro",
    "confiable", "alto rendimiento", "buena performance", "calidad",
    "user-friendly", "fast", "efficient", "robust", "friendly", "easy",
    "intuitive", "flexible", "appropriate", "adequate", "optimal", "modern",
    " scalable", "reliable", "high performance", "good",
    # Promesas no accionables (obs_2: «el sistema debe considerar/contemplar X»
    # no compromete nada verificable).
    "considerar", "contemplar", "consider", "take into account",
]

# NFR que exigen un target medible (número / umbral / unidad), por valor de ReqType.
MEASURABLE_TYPES = frozenset({"performance", "reliability"})

_NUMBER_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s?(?:%|ms|seg|s\b|min|gb|mb|tps|rps|veces|x\b|horas?|horas)",
    re.IGNORECASE,
)

# Enunciado muy largo -> probablemente agrupa varios requerimientos.
TOO_LONG_CHARS = 220

# --- Actores (obs §1 de Planitrack: roles mal asignados / «el sistema debe») -

# Léxico base de roles canónicos (minúsculas). El catálogo propio del proyecto
# se suma vía ``role_terms`` (futuro ProjectActor). NO incluye «sistema»: el
# sistema como ejecutor de una capacidad de usuario es justo el smell que
# buscamos; si el ejecutor es genuinamente la máquina, el hallazgo pide
# justificarlo, no lo prohibe.
ROLE_TERMS = [
    "administrador del tenant", "administrador", "supervisor", "coordinador",
    "gestor de terreno", "gestor", "operador de flota", "operador",
    "personal administrativo", "cliente final", "cliente", "destinatario",
    "auditor", "gerente", "dueño", "propietario", "visitante", "invitado",
    "analista", "aprobador", "solicitante", "responsable", "encargado",
    "conductor", "chofer", "integración externa", "servicio externo",
]

# Usuario genérico: no identifica ni rol ni canal ni responsabilidad.
GENERIC_USER_RE = re.compile(
    r"\b(los\s+usuarios?|el\s+usuario|users?)\b", re.IGNORECASE
)

# Marcadores de enunciado paraguas: prometen cobertura total en una sola frase.
UMBRELLA_MARKER_RE = re.compile(
    r"todos\s+los\s+aspectos|todas\s+las\s+funcionalidades|de\s+manera\s+completa"
    r"|gesti[oó]n\s+completa|administraci[oó]n\s+completa|todas\s+las\s+tareas",
    re.IGNORECASE,
)

# Verbos que inician una capacidad enumerable (infinitivos ES + EN básicos).
UMBRELLA_VERBS = frozenset({
    "crear", "editar", "eliminar", "modificar", "consultar", "exportar",
    "importar", "generar", "enviar", "descargar", "visualizar", "registrar",
    "asignar", "configurar", "aprobar", "rechazar", "autorizar", "imprimir",
    "notificar", "buscar", "filtrar", "ordenar", "archivar", "restaurar",
    "validar", "calcular", "mostrar", "listar", "actualizar", "cargar",
    "guardar", "firmar", "reenviar", "conciliar", "cruzar", "auditar",
})

# Umbral: >= 3 capacidades enumeradas en una frase => paraguas.
UMBRELLA_MIN_ACTIONS = 3

# Reglas deterministas completas: se pueden re-verificar sobre el texto sin
# LLM. Los consumidores (edición de requerimientos) cierran el hallazgo OPEN
# cuya regla dejó de disparar tras una edición.
DETERMINISTIC_RULE_IDS = frozenset({
    "incose.modal_missing", "smell.negation", "smell.combinator",
    "smell.vague_term", "smell.pronoun", "smell.absolute",
    "incose.too_long", "incose.unmeasurable_nfr", "ears.missing_condition",
    "smell.actor_missing", "actor.generic_user", "smell.umbrella",
})


@dataclass(frozen=True)
class ProgrammaticFinding:
    """Hallazgo determinista plano (model-agnostic).

    ``dimension``: ``"incose_rule" | "requirement_smell" | "ears_violation" |
    "ambiguity"``. ``severity``: ``"blocker" | "major" | "minor" | "info"``.
    """

    rule_id: str
    dimension: str
    severity: str
    message: str
    suggestion: str | None = None
    ears_pattern: str | None = None


def detect_ears_pattern(statement: str) -> str | None:
    """Devuelve la plantilla EARS detectada o None (ubiquitous implícita)."""
    s = statement.lstrip().lower()
    if s.startswith(("when ", "cuando ", "al ")):
        return "event_driven"
    if s.startswith(("while ", "mientras ", "durante ")):
        return "state_driven"
    if s.startswith(("where ", "donde ", "en caso de ")):
        return "optional_feature"
    if re.search(r"\b(if|si|en\s+caso)\b.*\b(then|entonces)\b", s):
        return "unwanted"
    return None


def detect_actor(
    statement: str, role_terms: list[str] | None = None
) -> str:
    """Clasifica el actor mencionado: ``named`` | ``generic`` | ``none``.

    ``role_terms`` suma los roles canónicos del proyecto al léxico base. Un
    rol específico gana aunque el texto también mencione al «usuario» genérico.
    """
    lower = statement.lower()
    for term in ROLE_TERMS + [t.lower() for t in (role_terms or [])]:
        if re.search(rf"\b{re.escape(term)}\b", lower):
            return "named"
    if GENERIC_USER_RE.search(lower):
        return "generic"
    return "none"


def _enumerated_actions(text: str) -> int:
    """Segmentos de la enumeración que arrancan con un verbo de acción."""
    count = 0
    for seg in re.split(r",|;|\by\b|\be\b|/", text.lower()):
        words = seg.strip().split(maxsplit=1)
        if words and words[0] in UMBRELLA_VERBS:
            count += 1
    return count


def programmatic_findings_for_text(
    statement: str,
    *,
    req_type: str | None = None,
    role_terms: list[str] | None = None,
) -> list[ProgrammaticFinding]:
    """Pre-checks deterministas sobre el texto de un enunciado. Cero LLM.

    ``req_type`` habilita ``incose.unmeasurable_nfr``; ``None`` la omite (caso
    captura pre-clasificación, donde el tipo aún no existe). Las reglas de
    actor (``smell.actor_missing`` / ``actor.generic_user``) exigen además
    ``req_type == "functional"``: un NFR no lleva actor de negocio, y en
    captura lo cubre la prevención (PREVENTION_RULES). ``role_terms`` suma el
    catálogo de roles del proyecto al léxico base.
    """
    text = statement or ""
    findings: list[ProgrammaticFinding] = []

    ears = detect_ears_pattern(text)
    has_modal = bool(MODAL_RE.search(text))

    if not has_modal:
        findings.append(
            ProgrammaticFinding(
                rule_id="incose.modal_missing",
                dimension="incose_rule",
                severity="minor",
                message=(
                    "El enunciado no contiene un verbo de obligación claro "
                    "(«debe»/«shall»). Un requerimiento debe expresar la "
                    "obligación del sistema."
                ),
                suggestion=None,
                ears_pattern=ears,
            )
        )

    if NEGATION_RE.search(text):
        findings.append(
            ProgrammaticFinding(
                rule_id="smell.negation",
                dimension="requirement_smell",
                severity="minor",
                message=(
                    "Requerimiento negativo («no debe»/«shall not»): es difícil "
                    "de verificar de forma exhaustiva. Preferir una forma "
                    "afirmativa que diga qué debe hacer el sistema."
                ),
                suggestion=None,
            )
        )

    if COMBINATOR_RE.search(text):
        findings.append(
            ProgrammaticFinding(
                rule_id="smell.combinator",
                dimension="requirement_smell",
                severity="major",
                message=(
                    "El enunciado combina alternativas (y/o, /, or). Es probable "
                    "que contenga más de un requerimiento; conviene separarlo."
                ),
                suggestion=None,
            )
        )

    lower = text.lower()
    for term in VAGUE_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", lower):
            findings.append(
                ProgrammaticFinding(
                    rule_id="smell.vague_term",
                    dimension="requirement_smell",
                    severity="major",
                    message=(
                        f"Término vago «{term}»: no es verificable. Reemplazar "
                        f"por un objetivo medible (umbral, métrica, criterio)."
                    ),
                    suggestion=None,
                )
            )
            break  # un hallazgo por ítem basta como señal

    if PRONOUN_RE.search(text):
        findings.append(
            ProgrammaticFinding(
                rule_id="smell.pronoun",
                dimension="requirement_smell",
                severity="minor",
                message=(
                    "Uso de pronombre: la referencia es ambigua. Reescribir "
                    "nombrando el sujeto concreto."
                ),
                suggestion=None,
            )
        )

    if ABSOLUTE_RE.search(text):
        findings.append(
            ProgrammaticFinding(
                rule_id="smell.absolute",
                dimension="requirement_smell",
                severity="minor",
                message=(
                    "Término absoluto (100%, siempre, nunca, todos): rara vez "
                    "verificable. Acotar con una condición o umbral."
                ),
                suggestion=None,
            )
        )

    if len(text) > TOO_LONG_CHARS:
        findings.append(
            ProgrammaticFinding(
                rule_id="incose.too_long",
                dimension="incose_rule",
                severity="minor",
                message=(
                    f"Enunciado muy largo ({len(text)} caracteres): "
                    f"probablemente agrupa varios requerimientos. Separar."
                ),
                suggestion=None,
            )
        )

    if req_type is not None and req_type in MEASURABLE_TYPES and not _NUMBER_RE.search(text):
        findings.append(
            ProgrammaticFinding(
                rule_id="incose.unmeasurable_nfr",
                dimension="ambiguity",
                severity="major",
                message=(
                    f"Requerimiento de {req_type} sin target medible "
                    f"(número/umbral/unidad). Debe cuantificarse para ser "
                    f"verificable."
                ),
                suggestion=None,
            )
        )

    # Actores: solo funcionales con tipo confirmado (ver docstring).
    if req_type == "functional":
        actor = detect_actor(text, role_terms)
        if actor == "none":
            findings.append(
                ProgrammaticFinding(
                    rule_id="smell.actor_missing",
                    dimension="requirement_smell",
                    severity="major",
                    message=(
                        "El enunciado no nombra el rol que ejecuta la acción o "
                        "se beneficia de ella (típico «El sistema debe...»). "
                        "Nombrar el rol canónico del proyecto; si el ejecutor "
                        "es genuinamente el sistema (job programado, "
                        "integración externa), justificarlo en la observación; "
                        "si la fuente no lo permite, usar [ROL-PENDIENTE] — "
                        "nunca inventar un rol que el documento no menciona."
                    ),
                    suggestion=None,
                )
            )
        elif actor == "generic":
            findings.append(
                ProgrammaticFinding(
                    rule_id="actor.generic_user",
                    dimension="requirement_smell",
                    severity="minor",
                    message=(
                        "Actor genérico «usuario»: no identifica responsabilidad "
                        "ni canal. Reemplazar por el rol canónico que corresponda."
                    ),
                    suggestion=None,
                )
            )

    # Enunciado paraguas: promete varias capacidades en una sola frase.
    actions = _enumerated_actions(text)
    if actions >= UMBRELLA_MIN_ACTIONS or UMBRELLA_MARKER_RE.search(text):
        if actions >= UMBRELLA_MIN_ACTIONS:
            detail = f"enumera {actions} capacidades"
        else:
            detail = (
                "usa marcadores de cobertura total («todos los aspectos», "
                "«de manera completa»)"
            )
        findings.append(
            ProgrammaticFinding(
                rule_id="smell.umbrella",
                dimension="requirement_smell",
                severity="major",
                message=(
                    f"Enunciado paraguas: {detail}. Un requerimiento por "
                    "capacidad (dividir con split_requirement). Si el cliente "
                    "pidió conservarlo agrupado, hacer waive del hallazgo y "
                    "registrar la decisión como regla del proyecto."
                ),
                suggestion=None,
            )
        )

    # EARS: si parece condicional pero no encaja en una plantilla -> INFO.
    looks_conditional = any(
        w in lower for w in ("cuando ", "when ", "mientras ", "while ", "si ", "if ")
    )
    if looks_conditional and ears is None:
        findings.append(
            ProgrammaticFinding(
                rule_id="ears.missing_condition",
                dimension="ears_violation",
                severity="info",
                message=(
                    "El enunciado parece condicional pero no sigue una "
                    "plantilla EARS (When/While/Where/If-then). Estructurarlo "
                    "mejora la claridad y la verificabilidad."
                ),
                suggestion=None,
                ears_pattern=None,
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Guía de PREVENCIÓN (Capa 1) — texto reutilizable para los prompts de
# extracción / descomposición. Mismo vocabulario que la detección: el generador
# evita exactamente lo que ``programmatic_findings_for_text`` penaliza.
# ---------------------------------------------------------------------------


def _vague_blocklist() -> str:
    """Lista legible y sin duplicados de términos vagos (para prompts)."""
    return ", ".join(sorted({t.strip().lower() for t in VAGUE_TERMS if t.strip()}))


PREVENTION_RULES = (
    "- WELL-FORMED STATEMENTS — a later programmatic check rejects these, so write them right now:\n"
    "    * EARS: when a requirement is conditional, phrase it with an EARS template "
    "in the SAME LANGUAGE as the source_span. "
    "Spanish: «Cuando <trigger>, el sistema debe...» / "
    "«Mientras <estado>, el sistema debe...» / "
    "«Donde <característica>, el sistema debe...» / "
    "«Si <condición>, entonces el sistema debe...». "
    "English: «When <trigger>, the system shall...» / "
    "«While <state>, the system shall...» / "
    "«Where <feature>, the system shall...» / "
    "«If <condition>, then the system shall...».\n"
    "    * MEASURABLE: do NOT use vague qualifiers such as "
    + _vague_blocklist()
    + ". When the source_span carries a number, threshold, or unit "
      "(especially for performance/reliability), keep it verbatim.\n"
    "    * ATOMIC & SINGULAR: one obligation per statement; never join alternatives "
    "with 'and/or', 'y/o', or a slash '/'.\n"
    "    * LANGUAGE (MANDATORY): write the statement in the SAME LANGUAGE as the "
    "source_span. Do NOT translate, normalize to English, or mix languages. A "
    "Spanish source_span yields a Spanish statement — including EARS phrasing.\n"
    "    * ACTOR (MANDATORY in functional requirements): name the canonical role "
    "that performs the action or receives its benefit — p. ej. «El supervisor "
    "debe...», «El administrador del tenant debe...». NEVER leave a user-facing "
    "capability as «El sistema debe...» and NEVER use a bare «usuario»: pick the "
    "specific role. If the source_span truly does not allow naming the role, "
    "write [ROL-PENDIENTE] as the actor — NEVER invent a role the documents do "
    "not mention. Non-functional requirements (performance, security, ...) do "
    "NOT need a business actor.\n"
    "    * NO UMBRELLA STATEMENTS: never bundle several capabilities into one "
    "sentence («gestionar X: crear, editar, eliminar...», «todos los aspectos "
    "de Y», «de manera completa»). One capability per statement. If the client "
    "explicitly asked to keep a grouped capability, keep it but add an "
    "observation saying so — the check flags it as smell.umbrella for an "
    "explicit keep-or-split decision.\n"
)
