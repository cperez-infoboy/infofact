"""Pre-flight environment check for the requirements-capture pipeline.

Pure, fast, side-effect-free probes run BEFORE the capture pipeline so a broken
dependency (libxcb/opencv, missing docling, unreachable LLM, read-only
workspace) fails fast with a remediation hint instead of crashing mid-capture.

Host-side (FastAPI process). Never loads the heavy models — import + endpoint
ping only, so the whole check completes in a second or two.

The capture subagent calls ``check_capture_health`` (a thin @tool wrapper in
backend/agents/subagents/requirements_capture_agent.py) before the capture
stages; the wrapper streams ``preflight.progress`` / ``preflight.failed`` over
the SSE relay and surfaces this report to the model and the UI.
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from urllib import request as urllib_request

from backend.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    """Outcome of one probe: ok flag + detail + a remediation hint on failure."""
    name: str
    ok: bool
    detail: str = ""
    hint: str = ""


@dataclass
class PreflightReport:
    """Aggregate of all probes. ``ok`` is True iff every probe passed."""
    ok: bool
    probes: list[ProbeResult] = field(default_factory=list)

    def failures(self) -> list[ProbeResult]:
        return [p for p in self.probes if not p.ok]

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "probes": [
                {"name": p.name, "ok": p.ok, "detail": p.detail, "hint": p.hint}
                for p in self.probes
            ],
        }


# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------

def _probe_import(name: str, import_path: str) -> ProbeResult:
    """Import-time probe: the libxcb/opencv case fails here, not at call time."""
    try:
        __import__(import_path)
        return ProbeResult(name=name, ok=True, detail=f"{import_path} importable")
    except Exception as exc:  # noqa: BLE001 — any import error is a fail
        return ProbeResult(
            name=name, ok=False, detail=f"{type(exc).__name__}: {exc}",
            hint=(
                f"La dependencia '{import_path}' no carga. Reconstruir la "
                f"imagen (docker compose up --build -d) o revisar su instalación."
            ),
        )


def _probe_llm() -> ProbeResult:
    """Reachability + auth check against the configured LLM endpoint.

    A single GET /models with the API key: any HTTP response means the endpoint
    is reachable; a connection error means network/key/base-url is wrong. Does
    not validate the model name or quota — that surfaces at capture time.
    """
    if not settings.llm_api_key:
        return ProbeResult(
            name="llm_endpoint", ok=False, detail="LLM_API_KEY no configurado",
            hint="Setear LLM_API_KEY en .env; sin clave el pipeline no puede extraer.",
        )
    url = f"{settings.llm_base_url.rstrip('/')}/models"
    try:
        req = urllib_request.Request(
            url,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            method="GET",
        )
        with urllib_request.urlopen(req, timeout=8) as resp:
            status = getattr(resp, "status", 200)
        return ProbeResult(
            name="llm_endpoint", ok=True,
            detail=f"{settings.llm_base_url} alcanzable (HTTP {status})",
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            name="llm_endpoint", ok=False, detail=f"{type(exc).__name__}: {exc}",
            hint=(
                f"El endpoint LLM ({settings.llm_base_url}) no responde o "
                "rechaza la clave. Verificar red, LLM_BASE_URL y LLM_API_KEY."
            ),
        )


def _probe_workspace() -> ProbeResult:
    """Write probe: the workspace mount must be writable (HA/NFS can be RO)."""
    root = settings.workspaces_root
    try:
        root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=root, suffix=".preflight")
        os.close(fd)
        os.unlink(tmp)
        # Sin el path absoluto: el destinatario es el LLM y el path del host
        # no existe dentro del sandbox del agente (solo invita a explorarlo).
        return ProbeResult(
            name="workspace_writable", ok=True, detail="workspace escribible"
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            name="workspace_writable",
            ok=False,
            # Sin str(exc): los mensajes de PermissionError embeben el path
            # absoluto del host.
            detail=type(exc).__name__,
            hint=(
                "WORKSPACES_HOST_ROOT no es escribible. Verificar permisos y "
                "el montaje HA/NFS."
            ),
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def check_capture_environment() -> PreflightReport:
    """Run every probe. Fast (<2s): four imports + one LLM ping + one temp write.

    Does NOT load the heavy Docling/torch models — import only. A failing
    import here means the dependency is broken in the image (the libxcb case);
    a failing ping means network/key misconfig; a failing write means the
    workspace mount is read-only.
    """
    probes = [
        _probe_import("docling", "docling.document_converter"),
        _probe_import("opencv", "cv2"),
        _probe_import("tiktoken", "tiktoken"),
        _probe_import("sentence_transformers", "sentence_transformers"),
        _probe_llm(),
        _probe_workspace(),
    ]
    return PreflightReport(ok=all(p.ok for p in probes), probes=probes)
