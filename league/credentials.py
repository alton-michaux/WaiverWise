"""Encrypting stored ESPN cookies. CREDENTIAL_KEY may list several keys, newest first, so one can be rotated."""

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet() -> MultiFernet:
    if not settings.CREDENTIAL_KEYS:
        raise ImproperlyConfigured("Set CREDENTIAL_KEY to store ESPN cookies")
    return MultiFernet([Fernet(key) for key in settings.CREDENTIAL_KEYS])


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
