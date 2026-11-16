FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VELOXA_CONFIG_DIR=/app/config

WORKDIR /app

# Dependencies first (pinned by requirements.lock) for layer caching.
COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
RUN pip install --constraint requirements.lock ".[dev]"

COPY config ./config
COPY tests ./tests

# Run as an unprivileged user. The release packs are mounted read-only at /data at runtime.
RUN useradd --create-home --uid 10001 veloxa
USER veloxa

EXPOSE 8000
CMD ["veloxa", "serve"]
