# ── FleetFuel Bot – Google Cloud Run Dockerfile ──────────────────────────────
FROM python:3.11-slim

# Prevent .pyc files and enable stdout logging (Cloud Run reads stdout/stderr)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source (exclude .venv and __pycache__)
COPY *.py ./
COPY *.csv ./
COPY .env ./

# Cloud Run Jobs: just run the bot (no HTTP server needed)
CMD ["python", "main.py"]
