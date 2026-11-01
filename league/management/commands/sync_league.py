import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from espn_api.requests.espn_requests import ESPNAccessDenied

from analysis.scoring import unscored_stats
from league import espn_client
from league.bulk import upsert
from league.models import EspnCredential, League, LeaguePlayer, Matchup, Membership, PendingMove, Player, PlayerWeek, ProGame, RosterEntry, Team
from league.support import check_supported

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Pull one league's settings, teams, rosters, matchups and player weeks from ESPN."

    def add_arguments(self, parser):
        parser.add_argument("league", type=int, help="the league's id in this database")

    def handle(self, *args, league, **opts):
        league = League.objects.get(pk=league)
        data = self._fetch(league)
        if problems := check_supported(data):
            raise CommandError(" ".join(problems))
        with transaction.atomic():
            league = self._save(data)
        claims = self._save_claims(league)
        # ids and counts only: Actions logs are public
        self.stdout.write(
            self.style.SUCCESS(
                f"Synced league {league.pk}: week {league.current_week}, {len(data.teams)} teams, "
                f"{len(data.players)} players, {claims} waiver claims"
            )
        )
        if missing := unscored_stats(data.scoring):
            self.stdout.write(f"{len(missing)} scoring rules have no Sleeper projection")

    def _fetch(self, league: League) -> espn_client.LeagueData:
        """Without cookies if the league is public, else with each member's until one works.

        Expired cookies are tried last rather than never, so a false alarm from ESPN heals itself.
        """
        if league.is_public:
            try:
                return espn_client.fetch_league(league.espn_id, league.season, None, None)
            except ESPNAccessDenied:
                league.is_public = False
                league.save(update_fields=["is_public"])
        credentials = sorted(league.espn_credentials.all(), key=lambda c: (c.status != EspnCredential.OK, c.pk))
        for credential in credentials:
            espn_s2, swid = credential.cookies()
            try:
                data = espn_client.fetch_league(league.espn_id, league.season, espn_s2, swid)
            except ESPNAccessDenied:
                credential.mark_expired()
                continue
            credential.mark_ok()
            return data
        # everyone's cookies failed, or the league went public after connecting
        data = espn_client.fetch_league(league.espn_id, league.season, None, None)
        league.is_public = True
        league.save(update_fields=["is_public"])
        return data

    def _save_claims(self, league: League) -> int:
        """ESPN shows waiver claims only to their owner, so each member's come from their own cookies."""
        members = dict(Membership.objects.filter(league=league).values_list("user_id", "team__espn_id"))
        total = 0
        for credential in league.espn_credentials.filter(status=EspnCredential.OK):
            team = members.get(credential.user_id)
            if team is None:
                continue
            espn_s2, swid = credential.cookies()
            try:
                moves = espn_client.fetch_claims(league.espn_id, league.season, league.current_week, espn_s2, swid)
            except ESPNAccessDenied:
                credential.mark_expired()
                continue
            except Exception as e:
                # keep their last known claims; one member's hiccup isn't the league's
                log.warning("claims for league %s failed: %s", league.pk, type(e).__name__)
                continue
            # a commissioner may see more than their own, and those aren't theirs to see here
            mine = [m for m in moves if m.team_id == team]
            with transaction.atomic():
                PendingMove.objects.filter(league=league, owner_id=credential.user_id).delete()
                PendingMove.objects.bulk_create(
                    PendingMove(league=league, espn_id=m.espn_id, kind=m.kind, team_espn_id=m.team_id, items=m.items, owner_id=credential.user_id)
                    for m in mine
                )
            total += len(mine)
        return total

    def _save(self, data: espn_client.LeagueData) -> League:
        league, _ = League.objects.update_or_create(
            espn_id=data.espn_id,
            season=data.season,
            defaults=dict(
                name=data.name,
                team_count=data.team_count,
                current_week=data.current_week,
                reg_season_weeks=data.reg_season_weeks,
                final_week=data.final_week,
                lineup_slots=data.lineup_slots,
                scoring=data.scoring,
                has_data=True,
            ),
        )

        upsert(
            ProGame,
            [ProGame(season=data.season, week=g.week, team=g.team, opponent=g.opponent, kickoff=g.kickoff) for g in data.pro_games],
            unique_fields=["season", "week", "team"],
            update_fields=["opponent", "kickoff"],
        )

        upsert(
            Player,
            [
                Player(
                    espn_id=p.espn_id,
                    name=p.name,
                    position=p.position,
                    pro_team=p.pro_team,
                    eligible_slots=p.eligible_slots,
                    injury_status=p.injury_status,
                    percent_owned=p.percent_owned,
                )
                for p in data.players
            ],
            unique_fields=["espn_id"],
            update_fields=["name", "position", "pro_team", "eligible_slots", "injury_status", "percent_owned"],
        )
        players = dict(Player.objects.filter(espn_id__in=[p.espn_id for p in data.players]).values_list("espn_id", "pk"))
        upsert(
            LeaguePlayer,
            [
                LeaguePlayer(
                    league=league,
                    player_id=players[p.espn_id],
                    season_projection=p.season_projection,
                    fa_status=p.fa_status,
                    waiver_clears=p.waiver_clears,
                )
                for p in data.players
            ],
            unique_fields=["league", "player"],
            update_fields=["season_projection", "fa_status", "waiver_clears"],
        )
        upsert(
            PlayerWeek,
            [
                PlayerWeek(league=league, player_id=players[p.espn_id], season=data.season, week=week, **w)
                for p in data.players
                for week, w in p.weeks.items()
            ],
            unique_fields=["league", "player", "season", "week"],
            update_fields=["points", "projected_points", "breakdown"],
        )

        # players outside this sync's pool have unknown availability
        LeaguePlayer.objects.filter(league=league).exclude(player_id__in=players.values()).update(
            fa_status="", waiver_clears=None
        )

        upsert(
            Team,
            [
                Team(
                    league=league,
                    espn_id=t.espn_id,
                    name=t.name,
                    abbrev=t.abbrev,
                    owners=t.owners,
                    owner_ids=t.owner_ids,
                    wins=t.wins,
                    losses=t.losses,
                    ties=t.ties,
                    points_for=t.points_for,
                    points_against=t.points_against,
                    standing=t.standing,
                    waiver_rank=t.waiver_rank,
                )
                for t in data.teams
            ],
            unique_fields=["league", "espn_id"],
            update_fields=[
                "name", "abbrev", "owners", "owner_ids", "wins", "losses", "ties",
                "points_for", "points_against", "standing", "waiver_rank",
            ],
        )
        teams = dict(league.teams.values_list("espn_id", "pk"))

        RosterEntry.objects.filter(team__league=league).delete()
        RosterEntry.objects.bulk_create(
            RosterEntry(team_id=teams[t.espn_id], player_id=players[pid], lineup_slot=slot)
            for t in data.teams
            for pid, slot in t.roster
            if pid in players
        )

        scores = {(t.espn_id, week): score for t in data.teams for week, (_, score) in t.schedule.items()}
        upsert(
            Matchup,
            [
                Matchup(
                    league=league,
                    week=week,
                    team_a_id=teams[t.espn_id],
                    team_b_id=teams[opp_id],
                    score_a=score,
                    score_b=scores[(opp_id, week)],
                )
                for t in data.teams
                for week, (opp_id, score) in t.schedule.items()
                if t.espn_id < opp_id  # each pairing saved once, from the lower team id
            ],
            unique_fields=["league", "week", "team_a"],
            update_fields=["team_b", "score_a", "score_b"],
        )

        # trades are the league's business. Claims in `data` came from one member's cookies, so _save_claims handles them.
        PendingMove.objects.filter(league=league, owner__isnull=True).delete()
        PendingMove.objects.bulk_create(
            PendingMove(league=league, espn_id=m.espn_id, kind=m.kind, team_espn_id=m.team_id, items=m.items)
            for m in data.pending
            if m.kind == PendingMove.TRADE
        )
        return league
