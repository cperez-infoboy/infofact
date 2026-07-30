"""Stateful run-holder for the agent-driven staged capture (Phase 2).

The deterministic capture runs ``run_requirements_pipeline`` as ONE atomic tool:
all seven stages execute back-to-back with no LLM reasoning between them. Phase 2
splits that into seven stage tools so the agent reasons BETWEEN stages (it sees
intermediate counts, conflicts, verdicts and decides whether to continue or
adjust). The holder carries the typed output of each stage so the next stage
tool can consume it without re-running the previous one.

The holder is in-process, keyed by ``project_id`` (one active capture per
project). It is created by ``ingest_documents`` only AFTER the up-front
existing-data guard resolves: the guard (``_count_existing`` +
``_existing_gate``) runs BEFORE any holder, so a blocked/pending capture leaves
no run behind -- the follow-up ``on_existing="reset"|"append"`` call creates a
fresh holder and parses. It is cleared only after a successful commit.

Safety contract (why the holder cannot be used to inject requirements):
``commit_capture`` reads ONLY from the holder, and the holder items are
populated exclusively by ``extract_requirements`` (which runs ``extract_all``
with ``verify_spans`` inside -- the anti-hallucination anchor). No tool writes
items into the holder from any other source. So every persisted requirement is
backed by a verified source span from the pipeline, exactly as in the atomic
path. See the plan at ~/.claude/plans/quiero-explorar-una-nueva-purring-swing.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from backend.agents.pipelines.classification import ClassificationResult
from backend.agents.pipelines.consolidation import ConsolidationResult
from backend.agents.pipelines.critique import CritiqueResult
from backend.agents.pipelines.extraction import DocumentRules, RawRequirement
from backend.agents.pipelines.ingestion import StructureMap

logger = logging.getLogger(__name__)

# Stages a capture passes through, in order. commit_capture requires every
# stage below (except commit itself) to have run before it persists.
STAGE_INGEST = "ingest"
STAGE_CONVENTIONS = "conventions"
STAGE_EXTRACT = "extract"
STAGE_CONSOLIDATE = "consolidate"
STAGE_CRITIQUE = "critique"
STAGE_CLASSIFY = "classify"
STAGE_COMMIT = "commit"

# Default per-stage loop cap. An agent re-running one stage more than this many
# times is almost certainly stuck; the stage tool refuses past the cap and asks
# the model to surface the problem to the user instead.
DEFAULT_STAGE_CAP = 3

_REQUIRED_BEFORE_COMMIT = (
    STAGE_INGEST,
    STAGE_CONVENTIONS,
    STAGE_EXTRACT,
    STAGE_CONSOLIDATE,
    STAGE_CRITIQUE,
    STAGE_CLASSIFY,
)


class StageLoopExceeded(Exception):
    """Raised when a stage tool is called more than its loop cap.

    Caps stop an agent from re-running a stage forever (e.g. re-extracting in a
    loop hoping for different output). The cap is per stage and the counters
    survive a re-ingest (see ``CaptureRun.reset_pipeline_outputs``), so
    re-ingesting cannot dodge the cap on later stages.
    """

    def __init__(self, *, stage: str, calls: int, cap: int) -> None:
        self.stage = stage
        self.calls = calls
        self.cap = cap
        super().__init__(
            f"stage {stage!r} exceeded its loop cap: {calls}/{cap} calls"
        )


@dataclass
class CaptureRun:
    """Mutable per-project state shared across the seven stage tools.

    Each field holds the typed output of one pipeline stage, mirroring the data
    flow of ``run_requirements_pipeline``. A stage tool writes its output here;
    the next stage reads it. ``commit_capture`` is the only writer to the DB and
    it reads ``crit`` + ``cls`` (both populated by prior stages).
    """

    project_id: int
    target: Path
    project_name: str = ""
    project_description: str = ""
    # INGEST outputs
    docs: list[Path] = field(default_factory=list)
    per_doc: list[tuple[list, StructureMap]] = field(default_factory=list)
    doc_texts: dict[str, str] = field(default_factory=dict)
    all_chunks: list = field(default_factory=list)
    # CONVENTIONS output (merged per-doc DocumentRules; None until stage runs)
    document_rules: DocumentRules | None = None
    # EXTRACT output
    extracted: list[RawRequirement] = field(default_factory=list)
    # CONSOLIDATE / CRITIQUE / CLASSIFY outputs
    cons: ConsolidationResult | None = None
    crit: CritiqueResult | None = None
    cls: ClassificationResult | None = None
    # COMMIT output
    committed_ids: list[int] = field(default_factory=list)
    # Profiling: wall-clock (ms) per stage, accumulated across the six tools so
    # commit_capture can relay the full breakdown on the `done` progress event.
    timings: dict[str, float] = field(default_factory=dict)
    # Bookkeeping
    calls: dict[str, int] = field(default_factory=dict)
    stages_done: set[str] = field(default_factory=set)

    def bump(self, stage: str, cap: int = DEFAULT_STAGE_CAP) -> int:
        """Count one call of ``stage``; raise past ``cap``.

        Returns the new call count. Caps are PER STAGE and PERSIST across a
        re-ingest (the counters are not cleared when the ingest fields are
        reset), so re-ingesting cannot dodge the loop cap on later stages.
        """
        n = self.calls.get(stage, 0) + 1
        self.calls[stage] = n
        if n > cap:
            raise StageLoopExceeded(stage=stage, calls=n, cap=cap)
        return n

    def reset_pipeline_outputs(self) -> None:
        """Drop every stage output (called when ingest re-runs from scratch).

        Keeps ``calls`` (loop-cap history) so a re-ingest still counts against
        the cap. ``stages_done`` is cleared because the pipeline is starting
        over.
        """
        self.docs = []
        self.per_doc = []
        self.doc_texts = {}
        self.all_chunks = []
        self.document_rules = None
        self.extracted = []
        self.cons = None
        self.crit = None
        self.cls = None
        self.committed_ids = []
        self.timings = {}
        self.stages_done.clear()

    def missing_stages_before_commit(self) -> list[str]:
        """Stages that must run before commit but have not, in pipeline order."""
        return [s for s in _REQUIRED_BEFORE_COMMIT if s not in self.stages_done]


# In-process registry: one active capture per project. Cleared on successful
# commit; the existing-data guard runs before the holder is created, so a
# blocked/pending capture leaves no run behind.
_ACTIVE_RUNS: dict[int, CaptureRun] = {}


def get_or_create_run(
    project_id: int,
    target: Path,
    *,
    project_name: str = "",
    project_description: str = "",
) -> CaptureRun:
    """Return the active run for ``project_id``, creating it if absent.

    Does NOT reset an existing run: only ``ingest_documents`` (the entry stage)
    is allowed to restart a capture, and it calls ``reset_pipeline_outputs``
    explicitly so the loop-cap counters survive.
    """
    existing = _ACTIVE_RUNS.get(project_id)
    if existing is not None:
        return existing
    run = CaptureRun(
        project_id=project_id,
        target=target,
        project_name=project_name,
        project_description=project_description,
    )
    _ACTIVE_RUNS[project_id] = run
    return run


def get_run(project_id: int) -> CaptureRun | None:
    """Return the active run for ``project_id``, or None."""
    return _ACTIVE_RUNS.get(project_id)


def clear_run(project_id: int) -> CaptureRun | None:
    """Drop the active run for ``project_id``; return it (or None)."""
    return _ACTIVE_RUNS.pop(project_id, None)
