import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 1 of 3: add the league columns as nullable.

    Separate from the backfill because Postgres can't alter a table with pending deferred FK
    checks, which is what writing rows and changing the schema in one transaction leaves behind.
    """

    dependencies = [
        ("league", "0007_sync_run"),
    ]

    operations = [
        migrations.CreateModel(
            name="LeaguePlayer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("season_projection", models.FloatField(default=0)),
                ("fa_status", models.CharField(blank=True, max_length=10)),
                ("waiver_clears", models.DateTimeField(blank=True, null=True)),
                (
                    "league",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="league_players", to="league.league"
                    ),
                ),
                (
                    "player",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="league_entries", to="league.player"
                    ),
                ),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("league", "player"), name="uniq_league_player")],
            },
        ),
        migrations.AddField(
            model_name="playerweek",
            name="league",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE, related_name="player_weeks", to="league.league"
            ),
        ),
        migrations.AddField(
            model_name="syncrun",
            name="league",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="sync_runs",
                to="league.league",
            ),
        ),
    ]
