# One image, three entrypoints (PLAN.md §6): api · worker · scheduler.
# The compose file picks the command; the code and dependencies are identical.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# ffmpeg for the video pipeline; the rest is what Chromium needs, installed by playwright.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
# Dependencies first, so a code change does not reinstall the world.
COPY pyproject.toml uv.lock ./
COPY apps/api/jarvis/__init__.py apps/api/jarvis/__init__.py
COPY apps/mac-node/macnode/__init__.py apps/mac-node/macnode/__init__.py
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev \
    && uv run playwright install --with-deps chromium \
    && mkdir -p var/artifacts var/tts

ENV PATH="/app/.venv/bin:$PATH" JARVIS_ENV=cloud
EXPOSE 8000

# Default: the API. Compose overrides for the worker and the scheduler.
CMD ["uvicorn", "jarvis.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
