# FleetFuel Bot — Google Cloud Run Deployment Guide

## Prerequisites
- Google Cloud account with billing enabled
- `gcloud` CLI installed and authenticated
- Docker installed locally
- Your MySQL instance (Cloud SQL recommended)

---

## Step 1 — Create Cloud SQL (MySQL) Instance

```bash
# Create MySQL 8.0 instance
gcloud sql instances create fleetfuel-db \
  --database-version=MYSQL_8_0 \
  --tier=db-f1-micro \
  --region=us-central1

# Create database
gcloud sql databases create fleetfuel \
  --instance=fleetfuel-db

# Create user
gcloud sql users create fleetfuel_user \
  --instance=fleetfuel-db \
  --password=YOUR_STRONG_PASSWORD
```

---

## Step 2 — Store Secrets in Secret Manager

```bash
# Enable Secret Manager
gcloud services enable secretmanager.googleapis.com

# Store each secret
echo -n "your_samsara_token"    | gcloud secrets create SAMSARA_API_TOKEN    --data-file=-
echo -n "your_telegram_token"   | gcloud secrets create TELEGRAM_BOT_TOKEN   --data-file=-
echo -n "-1001234567890"        | gcloud secrets create TELEGRAM_GROUP_ID     --data-file=-
echo -n "YOUR_STRONG_PASSWORD"  | gcloud secrets create DB_PASSWORD           --data-file=-
```

---

## Step 3 — Build and Push Docker Image

```bash
# Set your project ID
PROJECT_ID=$(gcloud config get-value project)

# Enable required APIs
gcloud services enable run.googleapis.com cloudbuild.googleapis.com

# Build and push
gcloud builds submit \
  --tag gcr.io/$PROJECT_ID/fleetfuel-bot .
```

---

## Step 4 — Deploy to Cloud Run

```bash
# Get Cloud SQL connection name
SQL_CONN=$(gcloud sql instances describe fleetfuel-db \
  --format="value(connectionName)")

# Deploy
gcloud run deploy fleetfuel-bot \
  --image gcr.io/$PROJECT_ID/fleetfuel-bot \
  --region us-central1 \
  --platform managed \
  --no-allow-unauthenticated \
  --add-cloudsql-instances $SQL_CONN \
  --set-env-vars DB_HOST=/cloudsql/$SQL_CONN \
  --set-env-vars DB_NAME=fleetfuel \
  --set-env-vars DB_USER=fleetfuel_user \
  --set-env-vars DB_PORT=3306 \
  --set-secrets SAMSARA_API_TOKEN=SAMSARA_API_TOKEN:latest \
  --set-secrets TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest \
  --set-secrets TELEGRAM_GROUP_ID=TELEGRAM_GROUP_ID:latest \
  --set-secrets DB_PASSWORD=DB_PASSWORD:latest \
  --min-instances 1 \
  --max-instances 1 \
  --timeout 3600
```

> **Note:** `--min-instances 1` keeps the container always running (required for
> the polling loop). This costs ~$7-10/month for a small instance.

---

## Step 5 — Seed Pilot Stops from CSV

Run the seed script locally (connecting to Cloud SQL via proxy):

```bash
# Install Cloud SQL Auth Proxy
curl -o cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.6.0/cloud-sql-proxy.linux.amd64
chmod +x cloud-sql-proxy

# Start proxy in background
./cloud-sql-proxy $SQL_CONN &

# Run seed script
DB_HOST=127.0.0.1 DB_PASSWORD=YOUR_STRONG_PASSWORD \
  python seed_pilot_stops.py --file pilot_stops.csv --dry-run  # preview first

python seed_pilot_stops.py --file pilot_stops.csv              # then import
```

---

## Step 6 — View Logs

```bash
gcloud run services logs read fleetfuel-bot \
  --region us-central1 \
  --limit 100
```

---

## Environment Variables Reference

| Variable                   | Default | Description                                    |
|----------------------------|---------|------------------------------------------------|
| SAMSARA_API_TOKEN          | —       | Required. Samsara API bearer token             |
| TELEGRAM_BOT_TOKEN         | —       | Required. BotFather token                      |
| TELEGRAM_GROUP_ID          | —       | Required. Negative number for groups           |
| DB_HOST                    | 127.0.0.1 | Cloud SQL socket path on GCP                 |
| DB_PASSWORD                | —       | Required. MySQL password                       |
| FUEL_ALERT_THRESHOLD_PCT   | 30      | Alert trigger fuel level (%)                   |
| SEARCH_RADIUS_MILES        | 50      | Max miles to search for stops                  |
| MAX_HEADING_DEVIATION_DEG  | 90      | Max degrees off heading to include a stop      |
| ALERT_COOLDOWN_MINUTES     | 60      | Minutes between repeat alerts for same truck   |
| SKIP_DETECTION_MINUTES     | 30      | Minutes before flagging a stop as skipped      |
| POLL_INTERVAL_SECONDS      | 300     | Samsara poll frequency (5 min)                 |

---

## Monitoring

Cloud Run logs all stdout. You can set up **Cloud Monitoring alerts** on:
- Container instance count (should always be 1)
- Error log rate
- Memory usage

```bash
# Quick health check — see last poll result
gcloud run services logs read fleetfuel-bot --region us-central1 | grep "Poll cycle"
```
