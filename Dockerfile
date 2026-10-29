FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .

# hashed + compressed static files for whitenoise; the key is only needed to load settings
RUN SECRET_KEY=collectstatic-only DEBUG=false python manage.py collectstatic --noinput

EXPOSE 8000
# Render's free tier has no pre-deploy step, so migrate on start (one instance, so no race).
# Render injects $PORT; threads let the trade finder's parallel HTMX requests share a worker.
CMD python manage.py migrate --noinput && \
    exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers 2 --threads 4 --timeout 60
