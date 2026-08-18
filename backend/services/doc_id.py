"""Rebase de document_id: convencion unica de path del contenedor.

Tres convenciones convivian: los parsers asignaban ``document_id = str(path)``
con el path ABSOLUTO DEL HOST (el pipeline corre host-side), mientras las
tools de documentos ya exponian paths del contenedor y ProjectDocument guarda
``rel_path``. El resultado: cada tool de requerimientos le mostraba al agente
paths de un filesystem que no existe dentro de su sandbox, y el agente
improvisaba exploracion para reconciliarlos.

La convencion canonica es el path del contenedor ``/workspaces/{slug}/{rel}``:
es lo que el agente puede resolver dentro de su sandbox y lo que
``list_documents``/``search_documents`` ya devuelven.
"""
from __future__ import annotations

from backend.config import WORKSPACE_CONTAINER_PATH, settings


def rebase_document_id(doc_id: str) -> str:
    """Host-absolute -> container path. Idempotente; el resto pasa intacto.

    ``{workspaces_root}/{profile}/{slug}/{rel}`` -> ``/workspaces/{slug}/{rel}``
    (el bind-mount del contenedor tiene como raiz el directorio del PERFIL,
    por eso el segmento del perfil se descarta). Valores ya-rebasados,
    relativos o ajenos al workspace se devuelven tales cuales.
    """
    if not doc_id:
        return doc_id
    root = str(settings.workspaces_root).rstrip("/") + "/"
    if not doc_id.startswith(root):
        return doc_id
    parts = doc_id[len(root):].split("/", 1)
    if len(parts) != 2 or not parts[1]:
        return doc_id
    return f"{WORKSPACE_CONTAINER_PATH}/{parts[1]}"
