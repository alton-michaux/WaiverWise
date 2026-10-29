"""Batched upserts. Syncs run against a remote database, so per-row round trips dominate."""

from django.db import models

BATCH_SIZE = 500


def upsert(model: type[models.Model], objs: list, unique_fields: list[str], update_fields: list[str]) -> None:
    """INSERT ... ON CONFLICT (unique_fields) DO UPDATE SET update_fields, in batches."""
    if objs:
        model.objects.bulk_create(
            objs,
            batch_size=BATCH_SIZE,
            update_conflicts=True,
            unique_fields=unique_fields,
            update_fields=update_fields,
        )
