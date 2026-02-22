#!/bin/bash
# =============================================================================
# FleetFuel Bot — Google Cloud Run Deployment Script
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - Google Cloud project created
#   - Cloud SQL (MySQL) instance running
#   - Cloud SQL Auth Proxy or VPC connector configured
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
# =============================================================================

PROJECT_ID="your-gcp-project-id"        # ← paste your Project ID
CLOUD_SQL_INSTANCE="my-fleet-fuel:us-central1:fleetfuel-db"  # ← paste Connection name
set -e  # Exit on any error

# ── Configuration (edit these) ────────────────────────────────────────────────
PROJECT_ID="your-gcp-project-id"          # <-- set your GCP project ID
REGION="us-central1"                       # GCP region
SERVICE_NAME="fleetfuel-bot"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# Cloud SQL settings
CLOUD_SQL_INSTANCE="${PROJECT_ID}:${REGION}:fleetfuel-db"   # project:region:instance

# ── Colors ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚛 FleetFuel Bot — Cloud Run Deployment${NC}"
echo "Project: ${PROJECT_ID}"
echo "Region:  ${REGION}"
echo "Image:   ${IMAGE_NAME}"
echo ""

# ── Step 1: Ensure gcloud is configured ──────────────────────────────────────
echo -e "${YELLOW}[1/5] Setting GCP project...${NC}"
gcloud config set project "${PROJECT_ID}"

# ── Step 2: Enable required APIs ─────────────────────────────────────────────
echo -e "${YELLOW}[2/5] Enabling GCP APIs...${NC}"
gcloud services enable \
    run.googleapis.com \
    containerregistry.googleapis.com \
    cloudbuild.googleapis.com \
    sqladmin.googleapis.com \
    --quiet

# ── Step 3: Build and push Docker image ──────────────────────────────────────
echo -e "${YELLOW}[3/5] Building and pushing Docker image...${NC}"
docker build -t "${IMAGE_NAME}:latest" .
docker push "${IMAGE_NAME}:latest"

# ── Step 4: Load env vars from .env file ─────────────────────────────────────
echo -e "${YELLOW}[4/5] Reading .env for Cloud Run environment variables...${NC}"

# Parse .env file — skip comments and blank lines
ENV_VARS=""
while IFS='=' read -r key value; do
    # Skip comments and blank lines
    [[ "$key" =~ ^#.*$ ]] && continue
    [[ -z "$key" ]] && continue
    # Strip inline comments from value
    value="${value%%#*}"
    value="${value%"${value##*[![:space:]]}"}"  # trim trailing whitespace
    if [[ -n "$key" && -n "$value" ]]; then
        # Escape commas in value for gcloud (commas separate env vars)
        value_escaped="${value//,/\\,}"
        if [[ -z "$ENV_VARS" ]]; then
            ENV_VARS="${key}=${value_escaped}"
        else
            ENV_VARS="${ENV_VARS},${key}=${value_escaped}"
        fi
    fi
done < .env

# Override DB_HOST to use Cloud SQL Auth Proxy socket path
# When using Cloud SQL with Cloud Run, the proxy socket is at /cloudsql/INSTANCE
ENV_VARS="${ENV_VARS},DB_HOST=/cloudsql/${CLOUD_SQL_INSTANCE}"
ENV_VARS="${ENV_VARS},DB_PORT=3306"

echo "Environment variables configured."

# ── Step 5: Deploy to Cloud Run ───────────────────────────────────────────────
echo -e "${YELLOW}[5/5] Deploying to Cloud Run...${NC}"
gcloud run deploy "${SERVICE_NAME}" \
    --image="${IMAGE_NAME}:latest" \
    --region="${REGION}" \
    --platform=managed \
    --no-allow-unauthenticated \
    --set-env-vars="${ENV_VARS}" \
    --add-cloudsql-instances="${CLOUD_SQL_INSTANCE}" \
    --memory=512Mi \
    --cpu=1 \
    --min-instances=1 \
    --max-instances=1 \
    --timeout=3600 \
    --quiet

echo ""
echo -e "${GREEN}✅ Deployment complete!${NC}"
echo ""
echo "To check logs:"
echo "  gcloud run services logs read ${SERVICE_NAME} --region=${REGION} --tail=50"
echo ""
echo "To reset truck state history on next boot:"
echo "  gcloud run services update ${SERVICE_NAME} --region=${REGION} \\"
echo "    --set-env-vars RESET_DB=1"
echo "  # Then after one boot, set RESET_DB back to 0:"
echo "  gcloud run services update ${SERVICE_NAME} --region=${REGION} \\"
echo "    --update-env-vars RESET_DB=0"
