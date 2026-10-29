from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from league import espn_client
from league.models import League, Matchup, Player, PlayerWeek, ProGame, RosterEntry, Team


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
                f"{len(data.players)} players"
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

        for g in data.pro_games:
            ProGame.objects.update_or_create(
                season=data.season,
                week=g.week,
                team=g.team,
                defaults=dict(opponent=g.opponent, kickoff=g.kickoff),
            )

        players = {}
        for p in data.players:
            players[p.espn_id], _ = Player.objects.update_or_create(
                espn_id=p.espn_id,
                defaults=dict(
                    name=p.name,
                    position=p.position,
                    pro_team=p.pro_team,
                    eligible_slots=p.eligible_slots,
                    injury_status=p.injury_status,
                    percent_owned=p.percent_owned,
                    season_projection=p.season_projection,
                ),
            )
            for week, w in p.weeks.items():
                PlayerWeek.objects.update_or_create(
                    player=players[p.espn_id], season=data.season, week=week, defaults=w
                )

        teams = {}
        for t in data.teams:
            teams[t.espn_id], _ = Team.objects.update_or_create(
                league=league,
                espn_id=t.espn_id,
                defaults=dict(
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
                ),
            )

        RosterEntry.objects.filter(team__league=league).delete()
        RosterEntry.objects.bulk_create(
            RosterEntry(team=teams[t.espn_id], player=players[pid], lineup_slot=slot)
            for t in data.teams
            for pid, slot in t.roster
            if pid in players
        )

        scores = {(t.espn_id, week): score for t in data.teams for week, (_, score) in t.schedule.items()}
        for t in data.teams:
            for week, (opp_id, score) in t.schedule.items():
                if t.espn_id > opp_id:
                    continue  # each pairing saved once, from the lower team id
                Matchup.objects.update_or_create(
                    league=league,
                    week=week,
                    team_a=teams[t.espn_id],
                    defaults=dict(team_b=teams[opp_id], score_a=score, score_b=scores[(opp_id, week)]),
                )
        return league
