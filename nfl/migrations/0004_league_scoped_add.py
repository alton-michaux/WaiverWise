import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 1 of 3: add the league columns as nullable. See league/migrations/0008."""

    dependencies = [
        ("league", "0008_league_scoped_add"),
        ("nfl", "0003_game_lines"),
    ]

    operations = [
        migrations.AddField(
            model_name="projection",
            name="league",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE, related_name="projections", to="league.league"
            ),
        ),
        migrations.AddField(
            model_name="projectionsnapshot",
            name="league",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="projection_snapshots",
                to="league.league",
            ),
        ),
    ]
