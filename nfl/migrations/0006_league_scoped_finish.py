import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 3 of 3: require the league and include it in uniqueness."""

    dependencies = [
        ("nfl", "0005_league_scoped_backfill"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="projection", name="uniq_projection"),
        migrations.RemoveConstraint(model_name="projectionsnapshot", name="uniq_projection_snapshot"),
        migrations.AlterField(
            model_name="projection",
            name="league",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="projections", to="league.league"
            ),
        ),
        migrations.AlterField(
            model_name="projectionsnapshot",
            name="league",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="projection_snapshots", to="league.league"
            ),
        ),
        migrations.AddConstraint(
            model_name="projection",
            constraint=models.UniqueConstraint(
                fields=("league", "player", "season", "week", "source"), name="uniq_projection"
            ),
        ),
        migrations.AddConstraint(
            model_name="projectionsnapshot",
            constraint=models.UniqueConstraint(
                fields=("league", "player", "season", "week"), name="uniq_projection_snapshot"
            ),
        ),
    ]
