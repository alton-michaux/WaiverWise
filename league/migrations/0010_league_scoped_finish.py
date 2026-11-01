import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 3 of 3: require the league, include it in uniqueness, and drop the old columns."""

    dependencies = [
        ("league", "0009_league_scoped_backfill"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="playerweek", name="uniq_player_week"),
        migrations.AlterField(
            model_name="playerweek",
            name="league",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="player_weeks", to="league.league"
            ),
        ),
        migrations.AddConstraint(
            model_name="playerweek",
            constraint=models.UniqueConstraint(fields=("league", "player", "season", "week"), name="uniq_player_week"),
        ),
        migrations.RemoveField(model_name="player", name="season_projection"),
        migrations.RemoveField(model_name="player", name="fa_status"),
        migrations.RemoveField(model_name="player", name="waiver_clears"),
    ]
