from django.db import migrations


def backfill(apps, schema_editor):
    """Snapshots can't be rebuilt, so stop the deploy rather than drop or misassign any."""
    League = apps.get_model("league", "League")
    models = [apps.get_model("nfl", "Projection"), apps.get_model("nfl", "ProjectionSnapshot")]

    seen = set()
    for league in League.objects.order_by("season"):
        if league.season in seen:
            raise RuntimeError(f"season {league.season} has more than one league, so rows can't be assigned safely")
        seen.add(league.season)
        for model in models:
            model.objects.filter(season=league.season).update(league=league)

    for model in models:
        orphans = model.objects.filter(league__isnull=True)
        if orphans.exists():
            seasons = sorted(set(orphans.values_list("season", flat=True)))
            raise RuntimeError(f"{model.__name__} rows in seasons {seasons} have no league to belong to")


class Migration(migrations.Migration):
    """Step 2 of 3: assign existing rows to their league."""

    dependencies = [
        ("nfl", "0004_league_scoped_add"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
