from datetime import timedelta
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

DEV_SECRET_KEY = "dev-insecure-change-me"
SECRET_KEY = env("SECRET_KEY", default=DEV_SECRET_KEY)
DEBUG = env("DEBUG")
if not DEBUG and SECRET_KEY == DEV_SECRET_KEY:
    raise ImproperlyConfigured("Set SECRET_KEY when DEBUG is off")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
# Render sets this to the service's public hostname (e.g. waiverwise.onrender.com)
if RENDER_HOST := env("RENDER_EXTERNAL_HOSTNAME", default=None):
    ALLOWED_HOSTS.append(RENDER_HOST)
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_HOST}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "axes",
    "league",
    "nfl",
    "web",
]

MIDDLEWARE = [
    "web.middleware.HealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "web.middleware.LeagueScopeMiddleware",
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "web.context_processors.sync_status",
                "web.context_processors.team_picker",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# bump to make installed PWAs drop their cached assets (Render sets RENDER_GIT_COMMIT per deploy)
APP_VERSION = env("APP_VERSION", default=env("RENDER_GIT_COMMIT", default="dev"))

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
# installed PWAs shouldn't nag for a login; 90 days, renewed on each visit
SESSION_COOKIE_AGE = 60 * 60 * 24 * 90
SESSION_SAVE_EVERY_REQUEST = True

AUTHENTICATION_BACKENDS = ["axes.backends.AxesStandaloneBackend", "django.contrib.auth.backends.ModelBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
# by username alone: behind Render's proxy every request has the same address, and X-Forwarded-For can be forged
AXES_LOCKOUT_PARAMETERS = ["username"]
SILENCED_SYSTEM_CHECKS = ["axes.W006"]  # asks for ip_address too, which here would let a forged header dodge the lockout
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_RESET_ON_SUCCESS = True
AXES_DISABLE_ACCESS_LOG = True  # no table of every sign-in to store on Neon's free plan
AXES_LOCKOUT_TEMPLATE = "web/locked_out.html"

if not DEBUG:
    # the platform terminates TLS and forwards plain HTTP with X-Forwarded-Proto
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_CONTENT_TYPE_NOSNIFF = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    # at INFO these print every request's address, which includes people's ESPN league ids
    # and axes prints the request's path at INFO, which on a signup includes the invite's token
    "loggers": {"httpx": {"level": "WARNING"}, "httpcore": {"level": "WARNING"}, "axes": {"level": "WARNING"}},
}

# ESPN league access (private leagues need espn_s2 + SWID cookies)
ESPN_SEASON = env.int("ESPN_SEASON", default=2026)
# only for bootstrap_owner, to attach the first account's cookies. Everyone else connects in the app.
ESPN_S2 = env("ESPN_S2", default=None)
ESPN_SWID = env("ESPN_SWID", default=None)
# encrypts stored ESPN cookies: Fernet keys, newest first, comma-separated so one can be rotated
CREDENTIAL_KEYS = env.list("CREDENTIAL_KEY", default=[])
# starts a new league's first sync; a fine-grained token limited to Actions on this repo
GITHUB_DISPATCH_TOKEN = env("GITHUB_DISPATCH_TOKEN", default=None)
GITHUB_REPO = env("GITHUB_REPO", default="alton-michaux/WaiverWise")
# syncs run one league at a time, and much past this they'd overrun the 30-minute game-day schedule
MAX_LEAGUES = env.int("MAX_LEAGUES", default=10)

# Claude explanations (optional)
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default=None)
ANTHROPIC_WORKSPACE_ID = env("ANTHROPIC_WORKSPACE_ID", default=None)  # only for keys not scoped to a workspace
# cheapest current model by default; e.g. claude-sonnet-5-5 or claude-opus-5-5 for richer write-ups
CLAUDE_MODEL = env("CLAUDE_MODEL", default="claude-haiku-5-5")
EXPLAIN_DAILY_CAP = env.int("EXPLAIN_DAILY_CAP", default=25)  # explanations Claude writes per user, per day
