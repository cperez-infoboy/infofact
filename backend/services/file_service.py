"""File operations against a user's workspace container.

Deliberately thin: reusa DockerSandbox (mismo backend del agente) y
DockerSandbox._safe_path (normalización + rechazo de path traversal). El
backend no conoce la lógica del agente, pero comparte el sandbox para
operaciones de filesystem pedidas por la UI (explorar workspace, editar un
documento).

Wirea siempre via ensure_container(profile) antes de exec/download/upload: si el
container estaba parado por idle timeout, lo revive.

Cada llamada acota el sandbox al subdirectorio del proyecto (profile/slug),
para que dos proyectos del mismo usuario no pisen archivos.

`list_tree` corre un micro-script Python dentro del container (Python 3.12
presente en la imagen `infofact-agent`). Eso evita parsear la salida de
`ls -la` o del tree de `eza` (la build instalada en la imagen no soporta
`--json`); el JSON es estructurado y estable.
"""
from __future__ import annotations

import json
import shlex
from typing import Any

from backend.agents.sandboxes.docker_sandbox import DockerSandbox
from backend.services.container_service import ensure_container

# Script Python embebido. Recorre `root` con os.walk hasta `max_depth` y emite
# JSON estable: {name, path, type: "file"|"dir", size, children?}.
# Se ejecuta con `python3 -c "<script>" <root> <max_depth>` dentro del
# container, working dir = workspace_root del proyecto. Esto evita acoplar la
# salida a `eza` (que en la imagen actual no soporta --json) o a `ls -la`
# (parseo frágil con espacios / caracteres raros).
_TREE_SCRIPT = (
    "import json,os,sys\n"
    "root=sys.argv[1];max_depth=int(sys.argv[2])\n"
    "def walk(path,depth):\n"
    "    try:\n"
    "        entries=sorted(os.scandir(path),key=lambda e:e.name.lower())\n"
    "    except OSError:\n"
    "        return []\n"
    "    out=[]\n"
    "    for e in entries:\n"
    "        rel=os.path.relpath(e.path,root)\n"
    "        if e.is_dir(follow_symlinks=False):\n"
    "            node={'name':e.name,'path':rel,'type':'dir','size':0}\n"
    "            if depth<max_depth:\n"
    "                node['children']=walk(e.path,depth+1)\n"
    "            else:\n"
    "                node['children']=[]\n"
    "        else:\n"
    "            try:sz=e.stat(follow_symlinks=False).st_size\n"
    "            except OSError:sz=0\n"
    "            node={'name':e.name,'path':rel,'type':'file','size':sz}\n"
    "        out.append(node)\n"
    "    return out\n"
    "print(json.dumps({'name':os.path.basename(root) or '.','path':'.',"
    "    'type':'dir','size':0,'children':walk(root,0)}))\n"
)


async def _sandbox(profile: str, project_slug: str) -> DockerSandbox:
    """Asegura el container del perfil y devuelve un DockerSandbox listo
    acotado al proyecto indicado."""
    await ensure_container(profile)
    return DockerSandbox(profile, project_slug=project_slug)


async def list_tree(
    profile: str,
    project_slug: str,
    path: str = ".",
    max_depth: int = 2,
) -> dict[str, Any]:
    """Devuelve un árbol JSON del workspace del proyecto bajo `path`.

    Estructura de cada nodo:
      {name, path, type: "file"|"dir", size, children?: [...]}
    La raíz es siempre un dir con path=".".
    """
    sandbox = await _sandbox(profile, project_slug)
    safe = sandbox._safe_path(path)
    # python3 -c recibe script + args; el working dir del container es
    # workspace_root del proyecto, así que `safe` (relativo, sin leading /)
    # resuelve bajo el mount del usuario. shlex.quote para que un nombre con
    # espacios no rompa el invocation.
    result = sandbox.execute(
        f"python3 -c {shlex.quote(_TREE_SCRIPT)} {shlex.quote(safe)} {int(max_depth)}",
        timeout=15,
    )
    if result.exit_code != 0:
        # El script solo falla si python3 no existe (imposible en la imagen
        # actual) o si `safe` no es un dir accesible. Mensaje genérico al
        # llamador; el detalle queda en output.
        raise RuntimeError(f"tree listing failed: {result.output.strip()}")
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as exc:
        # No debería ocurrir: el script siempre print un JSON válido o nada.
        raise RuntimeError(f"tree listing returned non-JSON: {exc}") from exc


async def read_file(profile: str, project_slug: str, path: str) -> str:
    """Lee un archivo del workspace como texto UTF-8.

    Usa DockerSandbox.download_files (vía `cat` dentro del container) en vez
    de un execute(`cat`) a mano: así aprovechamos el mismo código que el
    agente y el manejo uniforme de _safe_path + error file_not_found.
    """
    sandbox = await _sandbox(profile, project_slug)
    safe = sandbox._safe_path(path)
    downloads = sandbox.download_files([safe])
    if not downloads:
        raise FileNotFoundError(path)
    result = downloads[0]
    if result.error == "file_not_found":
        raise FileNotFoundError(path)
    if result.error is not None:
        raise RuntimeError(f"read failed: {result.error}")
    if result.content is None:
        raise RuntimeError("read returned no content")
    return result.content.decode("utf-8", errors="replace")


async def write_file(profile: str, project_slug: str, path: str, content: str) -> None:
    """Escribe `content` (UTF-8) en `path` dentro del workspace del proyecto.

    Usa DockerSandbox.upload_files (vía stdin cat en el container). Crea dirs
    padres automáticamente (lo hace upload_files internamente).
    """
    sandbox = await _sandbox(profile, project_slug)
    safe = sandbox._safe_path(path)
    uploads = sandbox.upload_files([(safe, content.encode("utf-8"))])
    if not uploads:
        raise RuntimeError("upload returned no result")
    result = uploads[0]
    if result.error is not None:
        raise RuntimeError(f"write failed: {result.error}")
