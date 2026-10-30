"""League-wide trade deals: searched once per version of the data, then served from the database."""

import time
from urllib.parse import urlencode

from league.models import SyncRun, Team
from web.models import TradeDeals
from web.services import TradeContext, trade_rows

BUDGET_SECONDS = 20  # stop starting new partners after this; the page asks again for the rest
STORED_PER_PARTNER = 20
SHOWN = 20
SHOWN_PER_TARGET = 2  # keep the league-wide list from being twenty ways to get one player


def data_version(league) -> str:
    """Changes whenever a sync could have changed the answer, including a sync run by hand."""
    run = SyncRun.objects.order_by("-pk").values_list("pk", flat=True).first()
    return f"{run or 0}-{league.synced_at.isoformat()}"


def _players(ctx: TradeContext, ids: list[int]) -> list[dict]:
    return [
        {"id": r.player.pk, "name": r.player.name, "position": r.player.position, "ros": r.ros_points}
        for r in trade_rows(ctx, ids)
    ]


def _display(ctx: TradeContext, trade) -> dict:
    return {
        "gives": _players(ctx, trade.a.gives),
        "gets": _players(ctx, trade.a.gets),
        "you": trade.a.gain,
        "you_before": trade.a.before,
        "you_after": trade.a.after,
        "you_drop": _players(ctx, trade.a.dropped),
        "them": trade.b.gain,
        "them_before": trade.b.before,
        "them_after": trade.b.after,
        "them_drop": _players(ctx, trade.b.dropped),
    }


def _search(ctx: TradeContext, team: Team, partner: Team, version: str) -> TradeDeals:
    found = ctx.find(team, partner)
    row, _ = TradeDeals.objects.update_or_create(
        team=team,
        partner=partner,
        version=version,
        defaults={"total": len(found), "deals": [_display(ctx, t) for t in found[:STORED_PER_PARTNER]]},
    )
    return row


def _rank(team: Team, rows: list[TradeDeals], cap_targets: bool) -> list[dict]:
    deals = [(row.partner, deal) for row in rows for deal in row.deals]
    deals.sort(key=lambda pair: (pair[1]["you"], pair[1]["them"]), reverse=True)
    shown, per_target = [], {}
    for partner, deal in deals:
        target = tuple(p["id"] for p in deal["gets"])
        per_target[target] = per_target.get(target, 0) + 1
        if cap_targets and per_target[target] > SHOWN_PER_TARGET:
            continue
        query = urlencode(
            {
                "team": team.espn_id,
                "partner": partner.espn_id,
                "give": [p["id"] for p in deal["gives"]],
                "get": [p["id"] for p in deal["gets"]],
            },
            doseq=True,
        )
        shown.append({**deal, "partner": partner, "query": query})
        if len(shown) == SHOWN:
            break
    return shown


def best_deals(team: Team, partner: Team | None = None, search: bool = True, budget: float | None = None) -> dict:
    """Ranked deals for `team`, searching partners not stored yet until the budget runs out.

    Partial results are fine: `done` is False and the page asks again, so no single request
    has to outlast a slow server's timeout. `search=False` only reads what's stored.
    """
    league = team.league
    version = data_version(league)
    partners = list(league.teams.exclude(pk=team.pk))
    rows = {r.partner_id: r for r in TradeDeals.objects.filter(team=team, version=version).select_related("partner")}
    missing = [p for p in partners if p.pk not in rows]

    if missing and search:
        TradeDeals.objects.exclude(version=version).delete()
        started = time.monotonic()
        ctx = TradeContext(league)
        for other in missing:
            rows[other.pk] = _search(ctx, team, other, version)
            if time.monotonic() - started >= (BUDGET_SECONDS if budget is None else budget):
                break

    done = len(rows) == len(partners)
    chosen = [rows[partner.pk]] if partner and partner.pk in rows else list(rows.values())
    return {
        "team": team,
        "deals": _rank(team, chosen, cap_targets=partner is None),
        "total": sum(r.total for r in chosen),
        "checked": len(rows),
        "of": len(partners),
        "done": done,
        "partner_filter": partner,
        "partners": sorted(partners, key=lambda p: p.name.lower()),
    }
