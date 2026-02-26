import os
from dotenv import load_dotenv

load_dotenv()

# ── Samsara ──────────────────────────────────────────────
SAMSARA_API_TOKEN = os.getenv("SAMSARA_API_TOKEN")
SAMSARA_BASE_URL  = "https://api.samsara.com/fleet"

# ── Telegram ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_GROUP_ID  = os.getenv("TELEGRAM_GROUP_ID")   # negative number e.g. -1001234567890

# ── MySQL ─────────────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT     = int(os.getenv("DB_PORT", 3306))
DB_USER     = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME     = os.getenv("DB_NAME", "fleetfuel")

# ── Business logic ────────────────────────────────────────
FUEL_ALERT_THRESHOLD_PCT = float(os.getenv("FUEL_ALERT_THRESHOLD_PCT", 30))  # %
SEARCH_RADIUS_MILES      = float(os.getenv("SEARCH_RADIUS_MILES", 50))
# Max heading deviation to consider a stop "ahead" of the truck (degrees)
MAX_HEADING_DEVIATION_DEG = float(os.getenv("MAX_HEADING_DEVIATION_DEG", 90))
# Re-alert cooldown: don't alert same truck again for X minutes
ALERT_COOLDOWN_MINUTES   = int(os.getenv("ALERT_COOLDOWN_MINUTES", 60))
# How many minutes before we mark a stop as "skipped" (truck passed without stopping)
SKIP_DETECTION_MINUTES   = int(os.getenv("SKIP_DETECTION_MINUTES", 30))
# Radius in miles to detect if truck actually visited a stop
VISIT_DETECTION_RADIUS_MILES = float(os.getenv("VISIT_DETECTION_RADIUS_MILES", 0.5))
# Poll interval in seconds
POLL_INTERVAL_SECONDS    = int(os.getenv("POLL_INTERVAL_SECONDS", 300))  # 5 min
