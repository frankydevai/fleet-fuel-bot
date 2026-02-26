# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

# Prevent .pyc files and enable stdout logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# ── Runtime ───────────────────────────────────────────────────────────────────
# Cloud Run will set PORT env var, but we don't listen on a port (long-running job)
# Health: Cloud Run Jobs don't need HTTP health checks

CMD ["python", "main.py"]
