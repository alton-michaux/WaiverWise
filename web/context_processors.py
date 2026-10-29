from league.models import League


def sync_status(request):
    return {"last_synced": League.objects.order_by("-synced_at").values_list("synced_at", flat=True).first()}
