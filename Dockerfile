# FunForge. Two targets:
#   development - what docker-compose.yml builds on dockerhost; ./src is bind-mounted over the code.
#   production  - the baked image published to ghcr.io/bmbell23/funforge for k3s (#35): code in
#                 the image, no bind mounts, nothing installed at start. Everything the app
#                 writes (DB, cover art, family photos, APK) goes to /app/data, one volume.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

# ffmpeg and the image libs are for audio metadata and cover art; tini reaps the
# healthcheck's children (compose gets the same from `init: true`).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libjpeg-dev \
    libpng-dev \
    libfreetype6-dev \
    ffmpeg \
    tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so a code-only change reuses this layer.
COPY pyproject.toml ./
RUN mkdir -p src/fun_forge && touch src/fun_forge/__init__.py \
    && pip install --no-cache-dir -e . \
    && rm -rf src

COPY src ./src
COPY scripts ./scripts

EXPOSE 8006

# Development stage
FROM base AS development

RUN pip install --no-cache-dir -e ".[dev]"
RUN mkdir -p /app/data /app/logs

CMD ["python", "-m", "uvicorn", "fun_forge.main:app", "--host", "0.0.0.0", "--port", "8006", "--reload"]

# Production stage
FROM base AS production

COPY version.txt ./

# uid 1000 so a k3s fsGroup/securityContext can match it; /app/data is the PVC.
RUN useradd --uid 1000 --create-home --shell /bin/bash funforge \
    && mkdir -p /app/data /app/logs \
    && chown -R funforge:funforge /app/data /app/logs
USER funforge

VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8006/health')"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "uvicorn", "fun_forge.main:app", "--host", "0.0.0.0", "--port", "8006"]
