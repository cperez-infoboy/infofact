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
]

# NFR que exigen un target medible (número / umbral / unidad), por valor de ReqType.
MEASURABLE_TYPES = frozenset({"performance", "reliability"})

_NUMBER_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s?(?:%|ms|seg|s\b|min|gb|mb|tps|rps|veces|x\b|horas?|horas)",
    re.IGNORECASE,
)

# Enunciado muy largo -> probablemente agrupa varios requerimientos.
TOO_LONG_CHARS = 220


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


def programmatic_findings_for_text(
    statement: str, *, req_type: str | None = None
) -> list[ProgrammaticFinding]:
    """Pre-checks deterministas sobre el texto de un enunciado. Cero LLM.

    ``req_type`` habilita ``incose.unmeasurable_nfr``; ``None`` la omite (caso
    captura pre-clasificación, donde el tipo aún no existe).
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
)
