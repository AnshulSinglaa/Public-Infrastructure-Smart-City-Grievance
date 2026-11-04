# The API image for Render. Build from the repo root:
#   docker build -f infra/api/Dockerfile -t civic-api .
# Base images are pinned by digest (SEC-009): the same bytes on every build; Dependabot bumps them.
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
COPY --from=ghcr.io/astral-sh/uv:0.9.25@sha256:13e233d08517abdafac4ead26c16d881cd77504a2c40c38c905cf3a0d70131a6 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first (exact versions from uv.lock, no dev tools), so code changes reuse this layer.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend backend
COPY data/*.csv data/

# Never run as root: a bug in the app then can't change the image.
RUN useradd --create-home app && mkdir media .cache && chown app media .cache
USER app

ENV PATH="/app/.venv/bin:$PATH" APP_ENV=prod
EXPOSE 8010
# Render sets $PORT. No X-Forwarded-* trust (SEC-019): nothing in prod reads the client IP
# (only the local dev login does), and every caller is the Streamlit server anyway.
CMD ["sh", "-c", "exec uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8010}"]
