# syntax=docker/dockerfile:1

# Build stage: resolve dependencies from uv.lock into a virtualenv, then
# install Maya itself into it (non-editable, so the runtime stage needs only
# the venv, not the source tree). Follows uv's documented Docker pattern.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first, in their own layer, so editing Maya's code doesn't
# re-download them.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


# Runtime stage: plain Python, the venv, a non-root user, and a volume for
# the one piece of real state -- the SQLite database.
FROM python:3.13-slim-bookworm

LABEL org.opencontainers.image.title="maya" \
      org.opencontainers.image.description="An API simulator for AI agents: a fictional world of everyday services -- flights, hotels, car rental and food delivery -- exposed as MCP tools." \
      org.opencontainers.image.source="https://github.com/shvinn/maya" \
      org.opencontainers.image.licenses="MIT"

RUN useradd --create-home --uid 10001 maya \
    && mkdir /data \
    && chown maya:maya /data

COPY --from=builder --chown=maya:maya /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MAYA_DB_PATH=/data/maya.db \
    MAYA_HOST=0.0.0.0

USER maya
WORKDIR /data
VOLUME /data
EXPOSE 6292

ENTRYPOINT ["maya"]
CMD ["serve", "--domain", "all"]
