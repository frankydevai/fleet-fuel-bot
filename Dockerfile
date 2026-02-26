# FleetFuel Bot - Railway Dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

# DATA_DIR will be set to /data in Railway (persistent volume)
ENV DATA_DIR=/data

CMD ["python", "main.py"]
