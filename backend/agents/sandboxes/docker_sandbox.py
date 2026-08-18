"""Docker-backed sandbox backend for DeepAgents.

One container `infofact-{profile}` per user. Implements the full
`SandboxBackendProtocol` surface (`id`, `execute`, `upload_files`,
`download_files`) via `docker exec`. Every other filesystem tool
(ls/read/write/edit/delete/glob/grep) is derived from these by `BaseSandbox`.

Verified contract (deepagents source, `backends/protocol.py` +
`backends/sandbox.py`):

    @dataclass
    class ExecuteResponse:
        output: str                       # combined stdout + stderr
        exit_code: int | None = None
        truncated: bool = False

    @dataclass
    class FileUploadResponse:
        path: str
        error: str | None                 # None on success

    @dataclass
    class FileDownloadResponse:
        path: str
        content: bytes | None
        error: str | None

    class BaseSandbox(ABC):
        __abstractmethods__ = {'id', 'execute', 'upload_files', 'download_files'}

NOTE: the docs claim only `execute()` is required — that is wrong. All four are
abstract as of the installed version; this module implements them all.

Secrets are never injected here: the agent runs inside the container and could
exfiltrate anything in its environment.
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    LsResult,
    WriteResult,
)
from deepagents.backends.sandbox import BaseSandbox

from backend.config import WORKSPACE_CONTAINER_PATH, settings

logger = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 100_000


class DockerSandbox(BaseSandbox):
    """Shells out to a running container via `docker exec`.

    The container must already be provisioned (see `container_service`).
    Commands run as uid 1000:1000 with working directory
    `{WORKSPACE_CONTAINER_PATH}/{project_slug}`, so the agent only sees its
    own project workspace and cannot escape into another project's directory.
    """

    def __init__(
        self,
        profile: str,
        project_slug: str,
        container: str | None = None,
    ) -> None:
        super().__init__()
        self._profile = profile
        self.project_slug = project_slug
        # CWD for every docker exec in this sandbox. The container's bind-mount
        # is `/workspaces`, so per-project rooting prevents two projects from
        # stomping each other's files.
        self.workspace_root = f"{WORKSPACE_CONTAINER_PATH}/{project_slug}"
        self._container = container or f"infofact-{profile}"

    @property
    def id(self) -> str:
        return f"{self._profile}:{self.project_slug}"

    def _safe_path(self, path: str) -> str:
        """Normalize `path` relative to this project's workspace root.

        Absolute paths must be anchored at `self.workspace_root`; any other
        absolute path (e.g. `/etc/passwd`, `/workspaces/other-project/...`)
        is rejected with ValueError to prevent cross-project reads/writes.
        Relative paths must not escape via `..`.
        """
        normalized = os.path.normpath(path)
        if os.path.isabs(normalized):
            # Allow /tmp as ephemeral container storage for scripts and temp
            # files. It does not cross the inter-project boundary that
            # _safe_path protects; the agent already has shell access via
            # execute(), so this does not expand the attack surface.
            if normalized == "/tmp" or normalized.startswith("/tmp/"):
                return normalized
            if normalized == self.workspace_root:
                return "."
            if normalized.startswith(self.workspace_root + "/"):
                rest = normalized[len(self.workspace_root) + 1 :]
                return rest if rest else "."
            raise ValueError(f"path escapes project workspace: {path!r}")
        # Relative path: reject parent-traversal.
        if normalized in ("", "."):
            return "."
        if normalized == ".." or normalized.startswith("../"):
            raise ValueError(f"path escapes workspace: {path!r}")
        return normalized

    def _glob_search_path(self, path: str | None) -> str:
        """Anchor glob searches inside the project workspace.

        `BaseSandbox.glob` defaults `path or "/"`, so a bare
        `glob("**/*.md")` walks the WHOLE container filesystem (CPU storm
        plus permission noise as uid 1000). Default to the project root
        (`.` — `execute` already runs with cwd there) and reject escapes
        via `_safe_path` (cross-project reads, /etc, ...).
        """
        if path is None or path.strip() == "":
            return "."
        return self._safe_path(path)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Glob bounded to this project's workspace, never the container root."""
        try:
            search = self._glob_search_path(path)
        except ValueError as exc:
            return GlobResult(error=self._bound_error(exc))
        return super().glob(pattern, search)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Async version of `glob`, same workspace bound."""
        try:
            search = self._glob_search_path(path)
        except ValueError as exc:
            return GlobResult(error=self._bound_error(exc))
        return await super().aglob(pattern, search)

    def _bound_error(self, exc: ValueError) -> str:
        """Error que nombra el root valido, para que el modelo se corrija en
        un turno en vez de iterar paths a ciegas."""
        return (
            f"{exc} Use paths relative to the project workspace or absolute "
            f"under {self.workspace_root}."
        )

    def ls(self, path: str) -> LsResult:
        """Ls bounded to this project's workspace.

        ``BaseSandbox.ls`` corre el path CRUDO via ``execute`` sin pasar por
        ``_safe_path``, asi que un path absoluto fuera del workspace (p.ej.
        ``/workspaces`` — proyectos hermanos) listaba bien dentro del
        container. Misma guardia que ``glob``.
        """
        try:
            safe = self._safe_path(path)
        except ValueError as exc:
            return LsResult(entries=None, error=self._bound_error(exc))
        return super().ls(safe)

    async def als(self, path: str) -> LsResult:
        """Async version of `ls`, same workspace bound."""
        try:
            safe = self._safe_path(path)
        except ValueError as exc:
            return LsResult(entries=None, error=self._bound_error(exc))
        return await super().als(safe)

    # --- core exec ---------------------------------------------------------

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Run `command` inside the user's container.

        Passed to `sh -c` so pipes/redirection work. stdout and stderr merge
        into `output` (DeepAgents expects a single stream). Large output is
        hard-truncated and flagged so the agent falls back to `read_file`.

        Assumption: `self.workspace_root` already exists on disk. It is created
        host-side by the projects router on project creation
        (`{workspaces_host_root}/{profile}/{slug}`). For the very first write
        the agent goes through `upload_files`, which `mkdir -p`s the parent of
        the target — and `dirname(/workspaces/{slug}/foo.md)` is exactly
        `/workspaces/{slug}`, so the root gets created on demand anyway. Do
        NOT prepend `mkdir -p` to every exec call: wasteful for the hot path.
        """
        # DeepAgents no pasa timeout; cae al default configurable de settings
        # (ver config.Settings.sandbox_exec_timeout).
        effective_timeout = timeout or settings.sandbox_exec_timeout
        # CONTAINER-SIDE timeout: killing the `docker exec` client on
        # TimeoutExpired does NOT kill the process inside the container —
        # a recursive glob from `/` kept burning CPU minutes after the tool
        # had already returned "timed out". `timeout -k` runs inside and
        # reaps the process regardless of what happens to the client. The
        # extra `sh -c` preserves pipelines: `timeout N a | b` would parse
        # as a pipeline of (timeout N a) | b instead.
        wrapped = f"timeout -k 5 {effective_timeout} sh -c {shlex.quote(command)}"
        cmd = [
            "docker", "exec",
            "-u", "1000:1000",
            "-w", self.workspace_root,
            self._container,
            "sh", "-c", wrapped,
        ]
        logger.debug("docker_exec container=%s cmd=%s", self._container, command)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=effective_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output=f"[Command timed out after {effective_timeout}s]",
                exit_code=124,
                truncated=False,
            )

        output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
        truncated = False
        encoded = output.encode("utf-8")
        if len(encoded) > MAX_OUTPUT_BYTES:
            output = encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore")
            truncated = True
        return ExecuteResponse(
            output=output,
            exit_code=proc.returncode,
            truncated=truncated,
        )

    # --- file transfer -----------------------------------------------------

    def write(self, file_path: str, content: str) -> WriteResult:
        """Create or replace `file_path` atomically via `upload_files`.

        Override of `BaseSandbox.write`: the base implementation refuses to
        overwrite existing files (it runs `_write_preflight`, which fails if
        the target exists). That "create-only" semantic forces the agent into
        a `rm`-then-`write` workaround that is NOT atomic — if the stream is
        interrupted between the rm and the write (client disconnect, network
        drop, recursion limit), the file is lost. For a doc-generation agent
        where rewrites are the norm, clobber-on-write is safer than the
        non-atomic rm workaround. `upload_files` already does mkdir -p on the
        parent and overwrites in place via `cat >`.
        """
        responses = self.upload_files([(file_path, content.encode("utf-8"))])
        response = responses[0]
        if response.error:
            return WriteResult(
                error=f"Failed to write file '{file_path}': {response.error}"
            )
        return WriteResult(path=file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Write each (path, content) into the container workspace.

        Content is streamed via stdin to avoid shell-escaping large binaries.
        """
        results: list[FileUploadResponse] = []
        for path, content in files:
            try:
                safe = self._safe_path(path)
            except ValueError:
                results.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            parent = os.path.dirname(safe) or "."
            target = shlex.quote(safe)
            cmd = [
                "docker", "exec", "-i",
                "-u", "1000:1000",
                "-w", self.workspace_root,
                self._container,
                "sh", "-c",
                f"mkdir -p {shlex.quote(parent)} && cat > {target}",
            ]
            proc = subprocess.run(
                cmd, input=content, capture_output=True, check=False
            )
            if proc.returncode == 0:
                results.append(FileUploadResponse(path=path, error=None))
            else:
                err = proc.stderr.decode("utf-8", errors="replace").strip()
                results.append(FileUploadResponse(path=path, error=err or "write_failed"))
        return results

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Read each path from the container workspace as raw bytes."""
        results: list[FileDownloadResponse] = []
        for path in paths:
            try:
                safe = self._safe_path(path)
            except ValueError:
                results.append(
                    FileDownloadResponse(path=path, content=None, error="invalid_path")
                )
                continue
            cmd = [
                "docker", "exec",
                "-u", "1000:1000",
                "-w", self.workspace_root,
                self._container,
                "cat", safe,
            ]
            proc = subprocess.run(cmd, capture_output=True, check=False)
            if proc.returncode == 0:
                results.append(
                    FileDownloadResponse(path=path, content=proc.stdout, error=None)
                )
            else:
                err = proc.stderr.decode("utf-8", errors="replace")
                kind = "file_not_found" if "No such file" in err else "read_failed"
                results.append(
                    FileDownloadResponse(path=path, content=None, error=kind)
                )
        return results
