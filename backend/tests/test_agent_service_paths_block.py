"""Tests del bloque de convención de rutas anexado al prompt del orquestador.

Regresión de la sesión 50 (planitrack2-0): sin el bloque, el modelo llamaba
`ls("/workspace/Observaciones")` — prefijo absoluto inventado — y perdía un
ciclo por "path escapes project workspace" antes de corregirse con la pista
del mensaje de error.
"""
from __future__ import annotations

from backend.services.agent_service import _workspace_paths_block


def test_block_declares_relative_root_and_slug():
    block = _workspace_paths_block("mi-proyecto")
    # Enseña las formas relativas correctas (que funcionan desde ya).
    assert 'ls(".")' in block
    assert 'ls("Observaciones")' in block
    # Advierte exactamente los prefijos que el modelo inventaba.
    assert "`/workspace`" in block
    assert "raíz del container" in block
    # Y da la ruta absoluta válida por si la necesita.
    assert "/workspaces/mi-proyecto/" in block


def test_block_interpolates_project_slug():
    assert _workspace_paths_block("otro-slug").find("otro-slug") > 0
