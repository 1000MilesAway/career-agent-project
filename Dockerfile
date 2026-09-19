# One image for both the API and the worker: they import the same code and
# differ only in the command, so a second image would just be the same layers
# under another name.
FROM python:3.12-slim

# uv, pinned by digest-free tag on purpose: the lockfile is what pins our
# dependencies, and uv itself only needs to be recent enough to read it.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/srv/.venv \
    PATH="/srv/.venv/bin:$PATH"

# Dependencies first, in their own layer: application code changes on every
# deploy, the lockfile rarely, so this layer survives most rebuilds.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY core/ ./core/
# app/ carries app/static/index.html, the cabinet itself — there is no build
# step and no second container to serve one file.
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini ./
# Channel lists and stop-words are data, not code — the scan reads them at
# runtime and they are expected to be updated without touching a module.
COPY data/sources/ ./data/sources/

# Non-root: a container that runs as root turns a bug in a request handler
# into root on the host's mounted volumes.
RUN useradd --system --create-home --uid 10001 career \
    && chown -R career:career /srv
USER career

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
