"""Lifecycle for per-user agent containers.

Port of the Hermes `hermes_service.py` pattern, simplified: the container
serves no HTTP, so there is no port allocation. The backend only talks to it
via `docker exec` (see `DockerSandbox`).

Three lazy branches in `ensure_container`:
  1. running + healthy -> reuse
  2. exists but stopped -> start
  3. missing -> create + mount workspace

`check_idle_containers` is a background sweep that stops containers idle longer
than `CONTAINER_IDLE_TIMEOUT` (default 30 min).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from backend.config import WORKSPACE_CONTAINER_PATH, settings

logger = logging.getLogger(__name__)

IDLE_TIMEOUT = settings.container_idle_timeout
REGISTRY_PATH = Path(settings.registry_file)
_lock = asyncio.Lock()


def container_name(profile: str) -> str:
    return f"infofact-{profile}"


def _load_registry() -> dict[str, dict]:
    if not REGISTRY_PATH.exists():
        return {}
    try:
        return json.loads(REGISTRY_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_registry(data: dict[str, dict]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(data, indent=2))


def _touch(profile: str, data: dict[str, dict]) -> None:
    data.setdefault(profile, {})["last_activity"] = time.time()
    _save_registry(data)


async def _run(*cmd: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(), stderr.decode()


async def _is_running(name: str) -> bool:
    rc, out, _ = await _run(
        "docker", "inspect", "--format", "{{.State.Running}}", name
    )
    return rc == 0 and out.strip() == "true"


async def _create_container(profile: str) -> str:
    name = container_name(profile)
    # Host-side workspace dir: la fuente del bind-mount. Resolvemos a ABSOLUTO
    # antes de pasarlo a `docker run -v` (docker rechaza paths relativos).
    # En dev (uvicorn en host) esto cae bajo <repo>/data/workspaces/<profile>;
    # en compose cae bajo /app/data/workspaces/<profile> dentro del WebUI
    # container, que a su vez bind-mountea al host. El target del mount dentro
    # del container del agente SIEMPRE es WORKSPACE_CONTAINER_PATH.
    # workspaces_root ya es absoluto (anclado al repo-root o host-path tal cual);
    # el .resolve() final es idempotente y mantiene el path visible al host
    # para el bind-mount DinD del agente.
    ws_path = (settings.workspaces_root / profile).resolve()
    ws_path.mkdir(parents=True, exist_ok=True)
    # El agente corre uid 1000 en su container; el workspace debe ser
    # escribible por ese uid. Si el backend corre como root (producción),
    # mkdir deja el dir root-owned y el agente no podría escribir. Best-effort:
    # si el backend ya corre como uid 1000 (dev host), el dir ya es 1000 y
    # chown al mismo uid es no-op o EPERM silencioso.
    try:
        os.chown(ws_path, 1000, 1000)
    except (PermissionError, OSError) as exc:
        logger.warning("no se pudo chown %s a 1000:1000: %s", ws_path, exc)
    rc, _, err = await _run(
        "docker", "run", "-d",
        "--name", name,
        "--restart", "unless-stopped",
        "-v", f"{ws_path}:{WORKSPACE_CONTAINER_PATH}",
        "-e", "HOME=/home/agent",
        settings.agent_image,
        "sleep", "infinity",
    )
    if rc != 0:
        raise RuntimeError(f"docker run failed for {name}: {err.strip()}")
    logger.info("created container %s", name)
    return name


async def ensure_container(profile: str) -> str:
    """Idempotent: return a running container name for `profile`."""
    name = container_name(profile)
    async with _lock:
        data = _load_registry()
        if await _is_running(name):
            _touch(profile, data)
            return name
        # Exists but stopped?
        rc, _, _ = await _run("docker", "inspect", name)
        if rc == 0:
            await _run("docker", "start", name)
            logger.info("restarted container %s", name)
        else:
            await _create_container(profile)
        _touch(profile, data)
        return name


async def check_idle_containers() -> None:
    """Background sweep: stop containers idle past IDLE_TIMEOUT."""
    data = _load_registry()
    now = time.time()
    changed = False
    for profile, meta in list(data.items()):
        last = meta.get("last_activity", 0)
        if now - last > IDLE_TIMEOUT:
            name = container_name(profile)
            rc, _, _ = await _run("docker", "stop", name)
            if rc == 0:
                logger.info("stopped idle container %s", name)
            data.pop(profile, None)
            changed = True
    if changed:
        _save_registry(data)
