"""Contract: the vision LLM uses its own OpenAI-compatible endpoint and API key,
separate from the text agent's, so any vision provider can be swapped in without
touching DeepAgents.

Regression motivation: glm-4.6v hallucinated fields on a dense ER diagram
(sigsa_srs.pdf); the team needs to point vision at a different OpenAI-compatible
provider (model + base_url + key) independently of the chat agent. Both new
fields default to empty and fall back to the shared llm_* values, so existing
deployments (z.ai/glm-4.6v) keep working unchanged.
"""
import pytest

from backend.agents.vision import _vision_llm
from backend.config import settings


def _reset_vision(monkeypatch):
    """Neutralize every vision/llm auth field so each test sets its own."""
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "llm_vision_api_key", "")
    monkeypatch.setattr(settings, "llm_vision_base_url", "")
    monkeypatch.setattr(settings, "llm_vision_model", "vision-model")


def test_default_falls_back_to_shared_key_and_url(monkeypatch):
    _reset_vision(monkeypatch)
    monkeypatch.setattr(settings, "llm_api_key", "shared-key")
    monkeypatch.setattr(settings, "llm_base_url", "https://shared.example/v1")

    llm = _vision_llm("vision-model")

    assert llm.openai_api_key.get_secret_value() == "shared-key"
    assert llm.openai_api_base == "https://shared.example/v1"
    assert llm.model_name == "vision-model"


def test_vision_overrides_take_precedence(monkeypatch):
    _reset_vision(monkeypatch)
    monkeypatch.setattr(settings, "llm_api_key", "shared-key")
    monkeypatch.setattr(settings, "llm_base_url", "https://shared.example/v1")
    monkeypatch.setattr(settings, "llm_vision_api_key", "vision-key")
    monkeypatch.setattr(settings, "llm_vision_base_url", "https://vision.example/v1")

    llm = _vision_llm("vision-model")

    assert llm.openai_api_key.get_secret_value() == "vision-key"
    assert llm.openai_api_base == "https://vision.example/v1"


def test_vision_without_any_key_raises(monkeypatch):
    _reset_vision(monkeypatch)
    with pytest.raises(RuntimeError, match="API key"):
        _vision_llm("vision-model")


def test_supports_vision_uses_vision_key_or_shared(monkeypatch):
    _reset_vision(monkeypatch)
    monkeypatch.setattr(settings, "llm_vision_api_key", "vision-key")
    assert settings.supports_vision is True


def test_supports_vision_uses_shared_key_when_no_vision_key(monkeypatch):
    _reset_vision(monkeypatch)
    monkeypatch.setattr(settings, "llm_api_key", "shared-key")
    assert settings.supports_vision is True


def test_supports_vision_false_without_any_key(monkeypatch):
    _reset_vision(monkeypatch)
    assert settings.supports_vision is False


def test_supports_vision_false_without_model(monkeypatch):
    _reset_vision(monkeypatch)
    monkeypatch.setattr(settings, "llm_vision_api_key", "vision-key")
    monkeypatch.setattr(settings, "llm_vision_model", "")
    assert settings.supports_vision is False


def test_backward_compat_no_new_env_uses_shared(monkeypatch):
    """No vision-specific overrides -> identical to today (shared key + url)."""
    _reset_vision(monkeypatch)
    monkeypatch.setattr(settings, "llm_api_key", "shared-key")
    monkeypatch.setattr(settings, "llm_base_url", "https://api.z.ai/api/coding/paas/v4")

    llm = _vision_llm("glm-4.6v")

    assert llm.openai_api_key.get_secret_value() == "shared-key"
    assert llm.openai_api_base == "https://api.z.ai/api/coding/paas/v4"
    assert settings.supports_vision is True
