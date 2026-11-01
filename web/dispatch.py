"""Asking GitHub Actions to run a new league's first sync, so nobody waits for the next scheduled one."""

import logging

import httpx
from django.conf import settings

log = logging.getLogger(__name__)


def request_first_sync(league_pk: int) -> bool:
    """False means it didn't start; the next scheduled sync still picks the league up."""
    if not settings.GITHUB_DISPATCH_TOKEN:
        return False
    try:
        response = httpx.post(
            f"https://api.github.com/repos/{settings.GITHUB_REPO}/actions/workflows/sync.yml/dispatches",
            headers={
                "Authorization": f"Bearer {settings.GITHUB_DISPATCH_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
            json={"ref": "main", "inputs": {"league": str(league_pk)}},
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("couldn't start the first sync for league %s: %s", league_pk, type(e).__name__)
        return False
    return True
