"""Claude-written explanations of the numbers each page already computed.

Claude only narrates: every number comes from the analysis layer and is passed in as JSON.
Responses are cached by payload hash so re-clicking "Explain" on unchanged data is free.
"""

import hashlib
import json

import anthropic
from django.conf import settings

from analysis.matchups import NEUTRAL
from web.models import Explanation

MAX_TOKENS = 16000
# models that accept server-side refusal fallback; Haiku has none, so don't send it there
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

SYSTEM = """You are the analyst inside WaiverWise, a fantasy football tool for a 10-team ESPN redraft league \
(0.75 PPR, 5-point passing TDs). The tool has already computed every number you see; your job is to explain \
what they mean for the user's decision, in the voice of a sharp, friendly league-mate.

Rules:
- Use only the data provided. Don't invent injuries, news, matchups, or stats that aren't in it. \
If the data can't answer something, say so briefly.
- Lead with the decision (start X, claim Y, accept/decline), then the 2-4 reasons that matter most, \
citing the specific numbers.
- Call out real risk: injury tags, wide floor/ceiling ranges, small edges that are basically coin flips, \
pending moves.
- For trades, the user is the proposer: say whether to offer it, and how the partner's own gain or loss \
affects the odds they accept. Don't tell the user to "accept" a trade they would be sending.
- Projections already account for the opponent. Use matchup grades and betting lines to explain a projection, \
never as a reason to expect more or less than it.
- Keep it under 180 words. Plain text: short paragraphs and "- " bullets, no headings, no markdown emphasis.

Glossary: proj/floor/ceiling = projected points and 10th/90th percentile outcomes. ROS = rest of season. \
VORP = rest-of-season points over a replacement-level player at the position. "ROS gain" = how many more points \
the user's optimal lineup scores over the rest of the season with a move, compared against streaming the \
3rd-best free agent each week. Win % comes from 10,000 simulated matchups against the opponent's best lineup. \
matchup = Good or Tough when the opponent is among the 8 softest or toughest defenses against that position, adjusted \
for offenses faced; it's noisy, and missing means unremarkable or too few games. team_implied_total = points the betting \
lines imply for the player's team. team_spread = negative when that team is favored. ros_schedule and playoff_schedule = Good or Tough when the player's \
team has one of the 8 softest or toughest remaining schedules at that position, or in the fantasy playoff weeks; missing \
means unremarkable. ROS points already reflect these, so they explain the number rather than add to it."""


class NarrationError(Exception):
    """User-facing failure; the message is safe to show on the page."""


def explain(kind: str, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, default=str)
    key = hashlib.sha256(f"{settings.CLAUDE_MODEL}:{kind}:{body}".encode()).hexdigest()
    if cached := Explanation.objects.filter(key=key).first():
        return cached.text

    text = _ask_claude(kind, body)
    Explanation.objects.update_or_create(key=key, defaults={"kind": kind, "text": text})
    return text


def _ask_claude(kind: str, body: str) -> str:
    if not settings.ANTHROPIC_API_KEY:
        raise NarrationError("Set ANTHROPIC_API_KEY in .env (then `docker compose up -d`) to enable explanations.")
    # user-level keys (sk-ant-usr-...) must name a workspace on every request
    headers = {"anthropic-workspace-id": settings.ANTHROPIC_WORKSPACE_ID} if settings.ANTHROPIC_WORKSPACE_ID else None
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, default_headers=headers)
    model = settings.CLAUDE_MODEL
    extra = {}
    if model in FALLBACK_MODELS:
        # on a safety-classifier decline, retry server-side on Anthropic's recommended model
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            # narration of precomputed numbers doesn't need deep reasoning
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": f"Explain this {kind} analysis:\n\n{body}"}],
            **extra,
        )
    except anthropic.AuthenticationError:
        raise NarrationError("ANTHROPIC_API_KEY was rejected. Check the key in .env.")
    except anthropic.RateLimitError:
        raise NarrationError("Rate limited by the Claude API. Try again in a minute.")
    except anthropic.APIStatusError as e:
        # surface the API's own message (e.g. "credit balance is too low") rather than just the code
        detail = (e.body or {}).get("error", {}).get("message") if isinstance(e.body, dict) else None
        raise NarrationError(f"Claude API error ({e.status_code}): {detail or 'try again later.'}")
    except anthropic.APIConnectionError:
        raise NarrationError("Couldn't reach the Claude API. Check your connection.")

    if response.stop_reason == "refusal":
        raise NarrationError("Claude declined to explain this one.")
    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise NarrationError("Claude returned an empty explanation.")
    return text


# Payload builders: turn page contexts into compact, model-friendly JSON.


def _proj(p):
    # None only happens when the player's team has no game that week
    return "bye" if p is None else {"mean": p.mean, "floor": p.floor, "ceiling": p.ceiling}


def _player(p):
    out = {"name": p.name, "pos": p.position, "nfl_team": p.pro_team}
    if p.injury_status and p.injury_status != "ACTIVE":
        out["injury"] = p.injury_status
    return out


def _game_context(row) -> dict:
    out = {}
    if row.matchup and row.matchup.grade != NEUTRAL:
        out["matchup"] = row.matchup.grade
    if row.line and row.line.implied_total is not None:
        out["team_implied_total"] = row.line.implied_total
        out["team_spread"] = row.line.spread
    return out


def _schedules(row) -> dict:
    out = {}
    if row.ros_schedule and row.ros_schedule.grade != NEUTRAL:
        out["ros_schedule"] = row.ros_schedule.grade
    if row.playoff_schedule and row.playoff_schedule.grade != NEUTRAL:
        out["playoff_schedule"] = row.playoff_schedule.grade
    return out


def startsit_payload(ctx: dict) -> dict:
    rec = ctx["rec"]
    return {
        "team": ctx["team"].name,
        "opponent": ctx["opponent"].name if ctx["opponent"] else None,
        "week": ctx["week"],
        "optimized_for": ctx["mode"],
        "current_lineup": {"points": rec.current_points, "win_pct": rec.current_win_prob},
        "best_lineup": {"points": rec.best_points, "win_pct": rec.best_win_prob},
        "recommended_moves": [
            {"start": i.player.name, "bench": o.player.name if o else None, "slot": o.slot if o else "empty slot"}
            for i, o in ctx["moves"]
        ],
        "roster": [
            {
                **_player(row.player),
                "slot": row.slot,
                "best_slot": best,
                "opponent": row.opponent,
                **_game_context(row),
                "projection": _proj(row.projection),
                **({"game_started": True} if row.locked else {}),
                **({"pending_move": row.pending} if row.pending else {}),
            }
            for row, best in ctx["rows"]
        ],
    }


WAIVER_ROWS = 15


def waivers_payload(ctx: dict) -> dict:
    return {
        "team": ctx["team"].name,
        "weeks": f"{ctx['weeks'].start}-{ctx['weeks'].stop - 1}",
        "position_filter": ctx["position"],
        "most_expendable_player": _player(ctx["drop"]) if ctx["drop"] else None,
        "most_expendable_drop_cost_ros": ctx["drop_cost"],
        "targets": [
            {
                **_player(r.player),
                "status": r.player.fa_status,
                **({"waiver_clears": r.player.waiver_clears} if r.player.waiver_clears else {}),
                "espn_pct_owned": r.player.percent_owned,
                "this_week": _proj(r.week_proj),
                "ros_points": r.ros_points,
                "vorp": r.vorp,
                "this_week_gain": r.next_week_gain,
                "ros_gain": r.ros_gain,
                "drop_for_this_add": r.drop.name if r.drop else "open roster spot",
                **_schedules(r),
                **(
                    {
                        "opportunities_per_game_last3": r.trend.recent_opportunities,
                        "opportunity_change": r.trend.opportunity_change,
                        "snap_pct_last3": r.trend.recent_snap_pct,
                    }
                    if r.trend
                    else {}
                ),
                **({"pending_move": r.pending} if r.pending else {}),
            }
            for r in ctx["rows"][:WAIVER_ROWS]
        ],
    }


def trade_payload(ctx: dict) -> dict:
    result = ctx["result"]
    t = result["trade"]

    def rows(trade_rows):
        return [{**_player(r.player), "ros_points": r.ros_points, **_schedules(r)} for r in trade_rows]

    return {
        "situation": (
            "The user is considering proposing this trade. The partner team has to accept it, "
            "so judge both whether it helps the user and whether the partner is likely to say yes."
        ),
        "weeks": f"{ctx['weeks'].start}-{ctx['weeks'].stop - 1}",
        "user_team": {
            "name": ctx["team"].name,
            "gives": rows(result["a_gives"]),
            "ros_lineup_points_before": t.a.before,
            "ros_lineup_points_after": t.a.after,
            "gain": t.a.gain,
            "must_drop": rows(result["a_dropped"]),
        },
        "partner_team": {
            "name": ctx["partner"].name,
            "gives": rows(result["a_gets"]),
            "ros_lineup_points_before": t.b.before,
            "ros_lineup_points_after": t.b.after,
            "gain": t.b.gain,
            "must_drop": rows(result["b_dropped"]),
        },
    }
