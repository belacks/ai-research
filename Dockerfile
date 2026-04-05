FROM python:3.11-slim

# env containerize
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# system deps (curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# working directory
WORKDIR /app

# dependencies (cached layer — only rebuilds when requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# configure Playwright to install browsers outside the home directory
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# browser
RUN playwright install chromium \
    && playwright install-deps chromium \
    && chmod -R 755 /ms-playwright
# ---------------------------------------------------------------------------
# Security: run as non-root user
# ---------------------------------------------------------------------------
RUN useradd --create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

# source code (copied as appuser so file ownership is correct)
COPY --chown=appuser:appuser . .

# CMD overridden by docker-compose.yml per service