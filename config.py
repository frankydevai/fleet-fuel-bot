"""
config.py  –  All configuration loaded from environment / .env file
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Samsara ───────────────────────────────────────────────────────────────────
SAMSARA_API_TOKEN = os.getenv("SAMSARA_API_TOKEN")
SAMSARA_BASE_URL  = "https://api.samsara.com"

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_GROUP_ID  = os.getenv("TELEGRAM_GROUP_ID", "").strip()

# ── MySQL ─────────────────────────────────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT     = int(os.getenv("DB_PORT", 3306))
DB_USER     = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME     = os.getenv("DB_NAME", "fleetfuel")

# ── Fuel threshold ────────────────────────────────────────────────────────────
# Alert fires when fuel drops at or below this percentage
FUEL_ALERT_THRESHOLD_PCT = float(os.getenv("FUEL_ALERT_THRESHOLD_PCT", 30))

# ── Stop search radii ─────────────────────────────────────────────────────────
# Priority order:
#   1. Pilot/Flying J within PILOT_RADIUS_MILES
#   2. Love's within LOVES_RADIUS_MILES
#   3. Any stop (Pilot or Love's) within EXTENDED_RADIUS_MILES
#   4. No stop found → critical alert, dispatcher notified
PILOT_RADIUS_MILES    = float(os.getenv("PILOT_RADIUS_MILES",    50))
LOVES_RADIUS_MILES    = float(os.getenv("LOVES_RADIUS_MILES",    50))
EXTENDED_RADIUS_MILES = float(os.getenv("EXTENDED_RADIUS_MILES", 80))

# Max heading deviation (degrees) to consider a stop "ahead" of the truck.
# 90 = anything within a 180-degree forward arc. Lower = stricter forward-only.
MAX_HEADING_DEVIATION_DEG = float(os.getenv("MAX_HEADING_DEVIATION_DEG", 90))

# ── Polling intervals (minutes) ───────────────────────────────────────────────
POLL_INTERVAL_HEALTHY         = int(os.getenv("POLL_INTERVAL_HEALTHY",         60))
POLL_INTERVAL_WATCH           = int(os.getenv("POLL_INTERVAL_WATCH",           20))
POLL_INTERVAL_CRITICAL_MOVING = int(os.getenv("POLL_INTERVAL_CRITICAL_MOVING", 10))
POLL_INTERVAL_CRITICAL_PARKED = int(os.getenv("POLL_INTERVAL_CRITICAL_PARKED", 60))

# ── Yard geofences ────────────────────────────────────────────────────────────
# Format in .env:  YARD_N=Yard Name:latitude:longitude:radius_miles
# Example:         YARD_1=Main Yard:28.4277:-81.3816:0.5
YARDS = []
for _i in range(1, 20):
    _val = os.getenv(f"YARD_{_i}", "").strip()
    if not _val:
        continue
    _parts = _val.split(":")
    if len(_parts) != 4:
        continue
    try:
        YARDS.append({
            "name":         _parts[0].strip(),
            "lat":          float(_parts[1]),
            "lng":          float(_parts[2]),
            "radius_miles": float(_parts[3]),
        })
    except ValueError:
        pass

# ── Skip / visit detection ────────────────────────────────────────────────────
# Hours after alert before flagging the truck as having skipped the stop
SKIP_DETECTION_HOURS = int(os.getenv("SKIP_DETECTION_HOURS", 10))

# Truck must be within this radius of the assigned stop to count as "visited"
VISIT_RADIUS_MILES = float(os.getenv("VISIT_RADIUS_MILES", 0.5))

# ── State persistence ─────────────────────────────────────────────────────────
STATE_SAVE_INTERVAL_SECONDS = int(os.getenv("STATE_SAVE_INTERVAL_SECONDS", 300))
