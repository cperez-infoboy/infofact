"""Holder con estado del run de generación de SRS (espejo de capture_run_holder).

El subagente ``srs-agent`` razona etapa por etapa (calidad -> goals ->
cobertura -> commit). Cada herramienta de etapa lee/escribe este holder en
lugar de mutar la DB directamente; solo ``commit_srs`` persiste. Esto deja que
el agente (y el usuario via SSE) vea el progreso de cada etapa y razone entre
ellas, igual que ``/captura_agente``.

Reutiliza ``StageLoopExceeded`` del holder de captura (mismo contrato) para no
duplicar la excepción. El holder se guarda en un registro en memoria keyed por
``project_id`` (un run SRS activo por proyecto).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.agents.subagents.capture_run_holder import StageLoopExceeded

# Etapas del run SRS. El ORDEN importa: cobertura lee los goals persistidos,
# por eso va despues de goals (no antes, como diría una lectura ingenua del plan).
STAGE_QUALITY = "quality"
STAGE_GOALS = "goals"
STAGE_COVERAGE = "coverage"
STAGE_COMMIT = "commit"

DEFAULT_STAGE_CAP = 3

_REQUIRED_BEFORE_COMMIT = (STAGE_QUALITY, STAGE_GOALS, STAGE_COVERAGE)


@dataclass
class SrsRun:
    """Estado acumulado del run de generación de SRS para un proyecto.

    Cada etapa escribe su salida aqui; ``commit_srs`` las combina en el
    payload que ``srs_store.create_srs`` persiste como ``SrsDocument``.
    """

    project_id: int
    project_name: str = ""
    project_description: str = ""
    # Salidas por etapa.
    quality_summary: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    goals_summary: dict[str, Any] | None = None
    coverage: dict[str, Any] | None = None
    traceability: dict[str, Any] | None = None
    markdown: str = ""
    requirement_codes: list[str] = field(default_factory=list)
    requirement_count: int = 0
    # Bookkeeping (igual que CaptureRun).
    calls: dict[str, int] = field(default_factory=dict)
    stages_done: set[str] = field(default_factory=set)

    def bump(self, stage: str, cap: int = DEFAULT_STAGE_CAP) -> int:
        n = self.calls.get(stage, 0) + 1
        self.calls[stage] = n
        if n > cap:
            raise StageLoopExceeded(stage=stage, calls=n, cap=cap)
        return n

    def reset_pipeline_outputs(self) -> None:
        """Limpia las salidas de etapas pero conserva los contadores de loop."""
        self.quality_summary = None
        self.findings = []
        self.goals_summary = None
        self.coverage = None
        self.traceability = None
        self.markdown = ""
        self.requirement_codes = []
        self.requirement_count = 0
        self.stages_done.clear()

    def missing_stages_before_commit(self) -> list[str]:
        return [s for s in _REQUIRED_BEFORE_COMMIT if s not in self.stages_done]


# ---------------------------------------------------------------------------
# Registro de runs activos (un SrsRun por proyecto, keyed por project_id).
# ---------------------------------------------------------------------------

_ACTIVE_RUNS: dict[int, SrsRun] = {}


def get_or_create_run(
    project_id: int,
    *,
    project_name: str = "",
    project_description: str = "",
) -> SrsRun:
    existing = _ACTIVE_RUNS.get(project_id)
    if existing is not None:
        return existing
    run = SrsRun(
        project_id=project_id,
        project_name=project_name,
        project_description=project_description,
    )
    _ACTIVE_RUNS[project_id] = run
    return run


def get_run(project_id: int) -> SrsRun | None:
    return _ACTIVE_RUNS.get(project_id)


def clear_run(project_id: int) -> SrsRun | None:
    return _ACTIVE_RUNS.pop(project_id, None)
