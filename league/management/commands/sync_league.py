from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from league import espn_client
from league.bulk import upsert
from league.models import League, Matchup, PendingMove, Player, PlayerWeek, ProGame, RosterEntry, Team


class Command(BaseCommand):
    help = "Pull league settings, teams, rosters, matchups and player weeks from ESPN."

    def handle(self, *args, **opts):
        if not settings.ESPN_LEAGUE_ID:
            raise CommandError("ESPN_LEAGUE_ID is not set")
        data = espn_client.fetch_league(
            settings.ESPN_LEAGUE_ID, settings.ESPN_SEASON, settings.ESPN_S2, settings.ESPN_SWID
        )
        with transaction.atomic():
            league = self._save(data)
        self.stdout.write(
            self.style.SUCCESS(
                f"Synced {league}: week {league.current_week}, {len(data.teams)} teams, "
                f"{len(data.players)} players, {len(data.pending)} pending moves"
            )
        )

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
                    season_projection=p.season_projection,
                    fa_status=p.fa_status,
                    waiver_clears=p.waiver_clears,
                )
                for p in data.players
            ],
            unique_fields=["espn_id"],
            update_fields=[
                "name", "position", "pro_team", "eligible_slots", "injury_status",
                "percent_owned", "season_projection", "fa_status", "waiver_clears",
            ],
        )
        players = dict(Player.objects.filter(espn_id__in=[p.espn_id for p in data.players]).values_list("espn_id", "pk"))
        upsert(
            PlayerWeek,
            [
                PlayerWeek(player_id=players[p.espn_id], season=data.season, week=week, **w)
                for p in data.players
                for week, w in p.weeks.items()
            ],
            unique_fields=["player", "season", "week"],
            update_fields=["points", "projected_points", "breakdown"],
        )

        # players outside this sync's pool have unknown availability
        Player.objects.exclude(espn_id__in=players.keys()).update(fa_status="", waiver_clears=None)

        upsert(
            Team,
            [
                Team(
                    league=league,
                    espn_id=t.espn_id,
                    name=t.name,
                    abbrev=t.abbrev,
                    owners=t.owners,
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
                "name", "abbrev", "owners", "wins", "losses", "ties",
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

        PendingMove.objects.filter(league=league).delete()
        PendingMove.objects.bulk_create(
            PendingMove(league=league, espn_id=m.espn_id, kind=m.kind, team_espn_id=m.team_id, items=m.items)
            for m in data.pending
        )
        return league
