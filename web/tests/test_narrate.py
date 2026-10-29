from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from web import narrate
from web.models import Explanation

pytestmark = pytest.mark.django_db


def response(text="Start Kraft.", stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


@pytest.fixture
def fake_claude(monkeypatch, settings):
    settings.ANTHROPIC_API_KEY = "test-key"
    calls = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return fake_claude.reply

    class FakeClient:
        def __init__(self, **kwargs):
            fake_claude.client_kwargs = kwargs
            self.beta = SimpleNamespace(messages=FakeMessages())

    monkeypatch.setattr(narrate.anthropic, "Anthropic", FakeClient)
    fake_claude.reply = response()
    fake_claude.calls = calls
    return fake_claude


def test_caches_by_payload(fake_claude):
    assert narrate.explain("startsit", {"week": 8}) == "Start Kraft."
    assert narrate.explain("startsit", {"week": 8}) == "Start Kraft."
    assert len(fake_claude.calls) == 1
    narrate.explain("startsit", {"week": 9})
    assert len(fake_claude.calls) == 2
    assert Explanation.objects.count() == 2


def test_default_model_is_haiku_without_fallbacks(fake_claude):
    narrate.explain("waivers", {"x": 1})
    req = fake_claude.calls[0]
    assert req["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in req and "betas" not in req
    assert req["output_config"] == {"effort": "low"}


def test_opus_gets_server_side_fallback(fake_claude, settings):
    settings.CLAUDE_MODEL = "claude-opus-5-5"
    narrate.explain("waivers", {"x": 1})
    req = fake_claude.calls[0]
    assert req["fallbacks"] == "default"
    assert req["betas"] == ["server-side-fallback-2026-07-01"]


def test_cache_is_per_model(fake_claude, settings):
    narrate.explain("waivers", {"x": 1})
    settings.CLAUDE_MODEL = "claude-sonnet-5-5"
    narrate.explain("waivers", {"x": 1})
    assert len(fake_claude.calls) == 2


def test_refusal_is_not_cached(fake_claude):
    fake_claude.reply = response(text="", stop_reason="refusal")
    with pytest.raises(narrate.NarrationError, match="declined"):
        narrate.explain("trade", {"x": 1})
    assert Explanation.objects.count() == 0


def test_missing_key(settings):
    settings.ANTHROPIC_API_KEY = None
    with pytest.raises(narrate.NarrationError, match="ANTHROPIC_API_KEY"):
        narrate.explain("trade", {"x": 1})


def test_api_errors_become_friendly(fake_claude, monkeypatch):
    def boom(**kwargs):
        raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))

    monkeypatch.setattr(narrate.anthropic, "Anthropic", lambda **kw: SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=boom))))
    with pytest.raises(narrate.NarrationError, match="reach"):
        narrate.explain("trade", {"x": 1})


def test_workspace_header_only_when_configured(fake_claude, settings):
    settings.ANTHROPIC_WORKSPACE_ID = None
    narrate.explain("trade", {"x": 1})
    assert fake_claude.client_kwargs["default_headers"] is None
    settings.ANTHROPIC_WORKSPACE_ID = "wrkspc_123"
    narrate.explain("trade", {"x": 2})
    assert fake_claude.client_kwargs["default_headers"] == {"anthropic-workspace-id": "wrkspc_123"}
