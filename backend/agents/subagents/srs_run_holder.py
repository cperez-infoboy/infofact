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
STAGE_NARRATIVE = "narrative"
STAGE_COMMIT = "commit"
# No es una etapa del pipeline: es el atajo que siembra el holder desde el
# ultimo SRS persistido (actualizacion de solo-narrativa) y marca 1-3 como
# hechas para saltar directo a draft_narrative.
STAGE_SEED = "seed"

DEFAULT_STAGE_CAP = 3

_REQUIRED_BEFORE_COMMIT = (STAGE_QUALITY, STAGE_GOALS, STAGE_COVERAGE, STAGE_NARRATIVE)


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
    narrative: dict[str, str] | None = None
    markdown: str = ""
    requirement_codes: list[str] = field(default_factory=list)
    requirement_count: int = 0
    # Bookkeeping (igual que CaptureRun).
    calls: dict[str, int] = field(default_factory=dict)
    stages_done: set[str] = field(default_factory=set)
    # Materialización temprana (P2): versión DRAFT persistida por
    # draft_narrative; commit_srs la promueve in-place a CANDIDATE.
    draft_version: int | None = None
    # Olas de curación: requerimientos editados desde el último análisis de
    # calidad (los marcan update_requirements/apply_cures; lo limpia
    # analyze_quality/close_curation_wave). La ola abre con un snapshot del
    # inventario de hallazgos para que el reporte de cierre sea DELTA
    # (nuevos/resueltos contra el estado previo a curar), no el inventario
    # completo — sin eso cada cura parecía descubrir problemas nuevos aunque
    # fueran los mismos (sesión 18: 913 tool calls sin noción de progreso).
    dirty_req_ids: set[int] = field(default_factory=set)
    wave_snapshot: dict[int, set[tuple[str, str]]] | None = None

    def bump(self, stage: str, cap: int = DEFAULT_STAGE_CAP) -> int:
        n = self.calls.get(stage, 0) + 1
        self.calls[stage] = n
        if n > cap:
            raise StageLoopExceeded(stage=stage, calls=n, cap=cap)
        return n

    def rearm_stages(self) -> None:
        """Reinicia los contadores de loop sin tocar salidas ni stages_done.

        El cap acota el loop de una etapa DENTRO de un episodio de
        razonamiento; sin esto, un run reanudado en un turno posterior
        hereda el contador agotado de episodios previos y el guard bloquea
        la etapa instantáneamente (incidente 2026-08-29: dos «reintenta»
        del usuario chocaron contra 4/3 y 5/3 sin ni llamar al LLM).
        """
        self.calls.clear()

    # ------------------------------------------------------------------
    # Olas de curación (delta de hallazgos: 1 ola = 1 re-análisis).
    # ------------------------------------------------------------------

    def _finding_key(self, f: dict[str, Any]) -> tuple[str, str] | None:
        rid = f.get("req_id")
        rule = f.get("rule_id")
        if rid is None or rule is None:
            return None
        return (str(rid), str(rule))

    def mark_dirty(self, req_ids) -> None:
        """Registra ediciones de enunciados y abre la ola si aún no existe."""
        ids = {int(r) for r in req_ids if r is not None}
        if not ids:
            return
        if self.wave_snapshot is None:
            # Snapshot del inventario vigente (ANTES de curar): el reporte de
            # cierre compara contra esto. Solo llaves (req_id, rule_id) — el
            # mensaje cambia entre corridas y no es identidad.
            self.wave_snapshot = {
                self._finding_key(f)
                for f in self.findings
                if self._finding_key(f) is not None
            }
        self.dirty_req_ids |= ids

    def clear_dirty(self, req_ids) -> None:
        ids = {int(r) for r in req_ids if r is not None}
        self.dirty_req_ids -= ids
        if not self.dirty_req_ids:
            self.close_wave()

    def close_wave(self) -> None:
        self.wave_snapshot = None

    def wave_report(
        self, new_findings: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Delta del inventario tras el re-análisis de cierre de ola.

        Compara las llaves (req_id, rule_id) del inventario fresco contra el
        snapshot de apertura: resueltos = estaban y ya no están; nuevos = no
        estaban y aparecieron. Devuelve el payload para la tool (listas cap
        40 + conteos completos).
        """
        before = self.wave_snapshot
        if before is None:
            before = set()
        fresh_keys = {
            self._finding_key(f)
            for f in new_findings
            if self._finding_key(f) is not None
        }
        resolved_keys = before - fresh_keys
        new_keys = fresh_keys - before

        def _shape(key: tuple[str, str]) -> dict[str, str]:
            return {"req_id": key[0], "rule_id": key[1]}

        report: dict[str, Any] = {
            "resolved_count": len(resolved_keys),
            "new_count": len(new_keys),
            "resolved": [_shape(k) for k in sorted(resolved_keys)[:40]],
            "new": [_shape(k) for k in sorted(new_keys)[:40]],
            "resolved_truncated": len(resolved_keys) > 40,
            "new_truncated": len(new_keys) > 40,
        }
        self.close_wave()
        return report

    def reset_pipeline_outputs(self) -> None:
        """Limpia las salidas de etapas pero conserva los contadores de loop."""
        self.quality_summary = None
        self.findings = []
        self.goals_summary = None
        self.coverage = None
        self.traceability = None
        self.narrative = None
        self.markdown = ""
        self.requirement_codes = []
        self.requirement_count = 0
        self.draft_version = None
        self.stages_done.clear()
        self.dirty_req_ids = set()
        self.wave_snapshot = None

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


def rearm_run(project_id: int) -> SrsRun | None:
    """Rearma los contadores de loop del run activo (si lo hay).

    Router-owned (mismo principio que la guardia de captura): lo invoca el
    handler de ``/srs`` al arrancar un episodio nuevo, así el subagente no
    puede rearmarse por su cuenta. Preserva salidas de etapas y
    ``stages_done`` para que el run continúe donde quedó.
    """
    run = _ACTIVE_RUNS.get(project_id)
    if run is not None:
        run.rearm_stages()
    return run


def clear_run(project_id: int) -> SrsRun | None:
    return _ACTIVE_RUNS.pop(project_id, None)
