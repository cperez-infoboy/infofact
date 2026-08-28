"""Tests del modo streaming del modelo conversacional.

Regresión typewriter: ``_build_model`` usaba el default ``streaming=False``
de ``build_llm``, así que cada llamada devolvía el AIMessage completo de una
vez y el chat esperaba la respuesta entera antes de mostrar nada. El relay
ya toleraba chunks (tool_call_chunks + dedup por id), pero nunca llegaban.

Kill-switch: ``LLM_AGENT_STREAMING=false`` apaga el streaming por si el
proveedor OpenAI-compatible falla con SSE.
"""
from __future__ import annotations

import pytest

from backend.config import settings
from backend.services.agent_service import _build_model


@pytest.fixture
def _api_key(monkeypatch):
    """API key sintética: build_llm exige una al momento de construir."""
    monkeypatch.setattr(settings, "llm_api_key", "test-key")


def test_conversational_model_streams(_api_key):
    assert _build_model().streaming is True


def test_streaming_kill_switch(_api_key, monkeypatch):
    monkeypatch.setattr(settings, "llm_agent_streaming", False)
    assert _build_model().streaming is False
