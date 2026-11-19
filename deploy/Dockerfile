# Production image for Google Cloud Run. ONE image, two services (see deploy/cloudrun/):
#   veloxa-api     -> `veloxa serve` (HTTP, scales with requests)
#   veloxa-worker  -> `veloxa intake-work` + DBOS workflow recovery (always-on, no public ingress)
# The development Dockerfile at the repository root keeps tests and dev tools; this one does not.
# Build:  docker build -f deploy/Dockerfile -t veloxa:<git-sha> .

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
RUN pip wheel --constraint requirements.lock --wheel-dir /wheels .

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    VELOXA_CONFIG_DIR=/app/config PORT=8080
WORKDIR /app
COPY --from=build /wheels /wheels
RUN pip install --no-index --find-links /wheels veloxa-cockpit && rm -rf /wheels
COPY config ./config
RUN useradd --create-home --uid 10001 veloxa
USER veloxa
EXPOSE 8080
# Cloud Run provides $PORT. The worker service overrides the command (see worker.service.yaml).
CMD ["sh", "-c", "exec veloxa serve --host 0.0.0.0 --port ${PORT}"]
