"""
main.py  –  FleetFuel Bot entry point

Starts a blocking scheduler that runs fuel checks every POLL_INTERVAL_SECONDS.
Designed to run as a Google Cloud Run Job (continuous) or Cloud Run Service.
"""

import logging
import time
import signal
import sys

from config import POLL_INTERVAL_SECONDS
from database import init_db
from fuel_checker import run_fuel_check
from telegram_bot import send_startup_message

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── Graceful shutdown ─────────────────────────────────────────────────────────
_running = True

def _shutdown(signum, frame):
    global _running
    log.info("🛑  Shutdown signal received. Stopping after current cycle...")
    _running = False

signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT,  _shutdown)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    log.info("🚛  FleetFuel Bot starting up...")

    # Initialize DB schema (idempotent)
    log.info("🔧  Checking database schema...")
    init_db()

    # Notify Telegram group
    try:
        send_startup_message()
    except Exception as e:
        log.warning(f"⚠️  Could not send startup message: {e}")

    log.info(f"⏱  Poll interval: {POLL_INTERVAL_SECONDS}s  |  Starting main loop...")

    while _running:
        try:
            run_fuel_check()
        except Exception as e:
            log.error(f"💥  Unhandled error in poll cycle: {e}", exc_info=True)

        # Sleep in small increments so SIGTERM is caught quickly
        for _ in range(POLL_INTERVAL_SECONDS):
            if not _running:
                break
            time.sleep(1)

    log.info("👋  FleetFuel Bot stopped cleanly.")


if __name__ == "__main__":
    main()
