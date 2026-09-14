"""Holder con estado del run de analisis y diseno (espejo de srs_run_holder).

El subagente ``analysis-agent`` razona etapa por etapa (MER -> NFR -> procesos
-> ADRs -> sub-proyectos -> commit). Cada herramienta de etapa lee/escribe este
holder en lugar de mutar la DB directamente; solo ``commit_analysis`` persiste.
Esto deja que el agente (y el usuario via SSE) vea el progreso de cada etapa y
razone entre ellas, igual que ``/captura_agente`` y ``/srs``.

Reutiliza ``StageLoopExceeded`` del holder de captura (mismo contrato) para no
duplicar la excepcion. El holder se guarda en un registro en memoria keyed por
``project_id`` (un run de analisis activo por proyecto).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.agents.subagents.capture_run_holder import StageLoopExceeded

if TYPE_CHECKING:
    from backend.agents.pipelines.adr_pipeline import AdrResult
    from backend.agents.pipelines.architecture_pipeline import ArchitectureResult
    from backend.agents.pipelines.mer_pipeline import MerResult
    from backend.agents.pipelines.nfr_pipeline import NfrResult
    from backend.agents.pipelines.process_pipeline import ProcessResult
    from backend.agents.pipelines.project_pipeline import ProjectResult
    from backend.agents.pipelines.subproject_pipeline import SubProjectResult

# Etapas del run de analisis. El ORDEN importa: procesos depende de MER,
# ADRs depende de NFR, proyectos depende de MER + ADRs, y sub-proyectos
# depende de MER + ADRs + proyectos.
STAGE_MER = "mer"
STAGE_NFR = "nfr"
STAGE_PROCESS = "process"
STAGE_ADR = "adr"
STAGE_PROJECTS = "projects"
STAGE_SUBPROJECT = "subproject"
STAGE_ARCHITECTURE = "architecture"
STAGE_COMMIT = "commit"

DEFAULT_STAGE_CAP = 3

_REQUIRED_BEFORE_COMMIT = (
    STAGE_MER,
    STAGE_NFR,
    STAGE_PROCESS,
    STAGE_ADR,
    STAGE_PROJECTS,
    STAGE_SUBPROJECT,
    STAGE_ARCHITECTURE,
)


@dataclass
class AnalysisRun:
    """Estado acumulado del run de analisis para un proyecto.

    Cada etapa escribe su salida aqui; ``commit_analysis`` las combina en el
    payload que ``analysis_store.create_analysis`` persiste como
    ``AnalysisDocument`` con sus filas hijas.
    """

    project_id: int
    project_name: str = ""
    project_description: str = ""
    # Salidas por etapa (tipadas bajo TYPE_CHECKING para evitar imports
    # circulares; en runtime son Any).
    mer_result: "MerResult | None" = None
    nfr_result: "NfrResult | None" = None
    process_result: "ProcessResult | None" = None
    adr_result: "AdrResult | None" = None
    project_result: "ProjectResult | None" = None
    subproject_result: "SubProjectResult | None" = None
    architecture_result: "ArchitectureResult | None" = None
    # Snapshot de inputs.
    srs_version: int | None = None
    requirement_codes: list[str] = field(default_factory=list)
    requirement_count: int = 0
    # Profiling: wall-clock (ms) por etapa.
    timings: dict[str, float] = field(default_factory=dict)
    # Bookkeeping (igual que CaptureRun / SrsRun).
    calls: dict[str, int] = field(default_factory=dict)
    stages_done: set[str] = field(default_factory=set)
    # Refinement mode: feedback del usuario + version previa para patch_commit.
    feedback: str = ""
    previous_analysis_id: int | None = None
    # Guarda anti doble despacho (corrida 2026-09-11): un solo pipeline en
    # vuelo por holder. Con dos runs concurrentes (reenvío del comando), la
    # segunda etapa se rechaza al instante en vez de duplicar horas de LLM y
    # pisar resultados (un propose con 25 contratos fue pisado por uno con 0,
    # y el commit del run A borró el holder que el run B seguía usando).
    in_flight: bool = False

    def begin_stage(self, stage: str) -> bool:
        """Reserva el holder para ejecutar ``stage``; False si está ocupado."""
        if self.in_flight:
            return False
        self.in_flight = True
        return True

    def end_stage(self, stage: str) -> None:
        """Libera la reserva (llamar siempre en finally)."""
        self.in_flight = False

    def bump(self, stage: str, cap: int = DEFAULT_STAGE_CAP) -> int:
        """Cuenta una llamada de ``stage``; lanza pasada ``cap``.

        Devuelve el nuevo conteo. Los caps son POR ETAPA y persisten a traves
        de un reset, para que un re-run no esquive el limite.
        """
        n = self.calls.get(stage, 0) + 1
        self.calls[stage] = n
        if n > cap:
            raise StageLoopExceeded(stage=stage, calls=n, cap=cap)
        return n

    def reset_pipeline_outputs(self) -> None:
        """Limpia las salidas de etapas pero conserva los contadores de loop."""
        self.mer_result = None
        self.nfr_result = None
        self.process_result = None
        self.adr_result = None
        self.project_result = None
        self.subproject_result = None
        self.architecture_result = None
        self.srs_version = None
        self.requirement_codes = []
        self.requirement_count = 0
        self.timings = {}
        self.stages_done.clear()

    def missing_stages_before_commit(self) -> list[str]:
        """Etapas que deben correr antes de commit pero no lo han hecho."""
        return [s for s in _REQUIRED_BEFORE_COMMIT if s not in self.stages_done]


# ---------------------------------------------------------------------------
# Registro de runs activos (un AnalysisRun por proyecto, keyed por project_id).
# ---------------------------------------------------------------------------

_ACTIVE_RUNS: dict[int, AnalysisRun] = {}


def get_or_create_run(
    project_id: int,
    *,
    project_name: str = "",
    project_description: str = "",
) -> AnalysisRun:
    """Devuelve el run activo para ``project_id``, creandolo si no existe."""
    existing = _ACTIVE_RUNS.get(project_id)
    if existing is not None:
        return existing
    run = AnalysisRun(
        project_id=project_id,
        project_name=project_name,
        project_description=project_description,
    )
    _ACTIVE_RUNS[project_id] = run
    return run


def get_run(project_id: int) -> AnalysisRun | None:
    """Devuelve el run activo para ``project_id``, o None."""
    return _ACTIVE_RUNS.get(project_id)


def clear_run(project_id: int) -> AnalysisRun | None:
    """Elimina el run activo para ``project_id``; lo devuelve (o None)."""
    return _ACTIVE_RUNS.pop(project_id, None)
