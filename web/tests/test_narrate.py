from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from web import narrate
from web.models import Explanation

pytestmark = pytest.mark.django_db

LEAGUE = SimpleNamespace(
    team_count=10,
    scoring={"4": {"points": 5.0, "overrides": {}}, "53": {"points": 0.75, "overrides": {}}},
    lineup_slots={"QB": 1, "RB": 2, "BE": 7},
)


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
    assert narrate.explain("startsit", {"week": 8}, LEAGUE) == "Start Kraft."
    assert narrate.explain("startsit", {"week": 8}, LEAGUE) == "Start Kraft."
    assert len(fake_claude.calls) == 1
    narrate.explain("startsit", {"week": 9}, LEAGUE)
    assert len(fake_claude.calls) == 2
    assert Explanation.objects.count() == 2


def test_default_model_is_haiku_without_fallbacks(fake_claude):
    narrate.explain("waivers", {"x": 1}, LEAGUE)
    req = fake_claude.calls[0]
    assert req["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in req and "betas" not in req
    assert req["output_config"] == {"effort": "low"}


def test_opus_gets_server_side_fallback(fake_claude, settings):
    settings.CLAUDE_MODEL = "claude-opus-5-5"
    narrate.explain("waivers", {"x": 1}, LEAGUE)
    req = fake_claude.calls[0]
    assert req["fallbacks"] == "default"
    assert req["betas"] == ["server-side-fallback-2026-07-01"]


def test_cache_is_per_model(fake_claude, settings):
    narrate.explain("waivers", {"x": 1}, LEAGUE)
    settings.CLAUDE_MODEL = "claude-sonnet-5-5"
    narrate.explain("waivers", {"x": 1}, LEAGUE)
    assert len(fake_claude.calls) == 2


def test_refusal_is_not_cached(fake_claude):
    fake_claude.reply = response(text="", stop_reason="refusal")
    with pytest.raises(narrate.NarrationError, match="declined"):
        narrate.explain("trade", {"x": 1}, LEAGUE)
    assert Explanation.objects.count() == 0


def test_missing_key(settings):
    settings.ANTHROPIC_API_KEY = None
    with pytest.raises(narrate.NarrationError, match="ANTHROPIC_API_KEY"):
        narrate.explain("trade", {"x": 1}, LEAGUE)


def test_api_errors_become_friendly(fake_claude, monkeypatch):
    def boom(**kwargs):
        raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))

    monkeypatch.setattr(narrate.anthropic, "Anthropic", lambda **kw: SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=boom))))
    with pytest.raises(narrate.NarrationError, match="reach"):
        narrate.explain("trade", {"x": 1}, LEAGUE)


def test_workspace_header_only_when_configured(fake_claude, settings):
    settings.ANTHROPIC_WORKSPACE_ID = None
    narrate.explain("trade", {"x": 1}, LEAGUE)
    assert fake_claude.client_kwargs["default_headers"] is None
    settings.ANTHROPIC_WORKSPACE_ID = "wrkspc_123"
    narrate.explain("trade", {"x": 2}, LEAGUE)
    assert fake_claude.client_kwargs["default_headers"] == {"anthropic-workspace-id": "wrkspc_123"}


def test_prompt_describes_this_league(fake_claude):
    narrate.explain("startsit", {"week": 8}, LEAGUE)
    system = fake_claude.calls[0]["system"]
    assert "10-team ESPN league (0.75 PPR, 5-point passing TDs) starting 1 QB, 2 RB" in system
    assert "BE" not in system.split("starting")[1].split(".")[0]


def test_prompt_names_superflex_and_te_premium():
    league = SimpleNamespace(
        team_count=12,
        scoring={"4": {"points": 4.0, "overrides": {}}, "53": {"points": 1.0, "overrides": {"6": 1.5}}},
        lineup_slots={"QB": 1, "OP": 1},
    )
    text = narrate.league_description(league)
    assert "12-team" in text and "1 PPR" in text and "4-point passing TDs" in text
    assert "1.5 points per TE catch" in text
    assert "superflex" in text


def test_other_rules_do_not_reuse_an_explanation(fake_claude):
    other = SimpleNamespace(team_count=12, scoring=LEAGUE.scoring, lineup_slots=LEAGUE.lineup_slots)
    narrate.explain("startsit", {"week": 8}, LEAGUE)
    narrate.explain("startsit", {"week": 8}, other)
    assert len(fake_claude.calls) == 2
