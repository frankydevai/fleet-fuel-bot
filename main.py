"""
main.py  -  FleetFuel Bot with smart per-truck polling
"""
"""
main.py  -  FleetFuel Bot with smart per-truck polling
Revised for Railway/Docker CSV Seeding
"""

import logging
import time
import signal
import sys
import os
from datetime import datetime, timedelta, timezone

from config import STATE_SAVE_INTERVAL_SECONDS
from database import init_db, load_all_truck_states, save_all_truck_states, reset_truck_states, get_all_stops_with_diesel
from samsara_client import get_combined_vehicle_data
from state_machine import process_truck
from telegram_bot import send_startup_message

# -- Logging ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# -- Global state -------------------------------------------------------------
truck_states = {}

# -- Graceful shutdown --------------------------------------------------------
_running = True

def _shutdown(signum, frame):
    global _running
    log.info("Shutdown signal received. Saving state and stopping...")
    save_all_truck_states(truck_states)
    _running = False

signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT,  _shutdown)


# -- Auto seed ----------------------------------------------------------------

def _auto_seed():
    """
    Revised seeding logic:
    - Checks /app (Docker WORKDIR) and script directory.
    - Matches filenames: pilot.csv and loves.csv.
    """
    stops = get_all_stops_with_diesel()
    if stops:
        log.info(f"   pilot_stops: {len(stops)} stops loaded — skipping seed")
        return

    log.info("   pilot_stops table is empty — searching for CSV files...")

    # Define paths based on your Dockerfile WORKDIR
    script_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = "/app"
    search_dirs = list(dict.fromkeys([script_dir, app_dir, ".", os.getcwd()]))

    # DEBUG: Log what the container actually sees
    for d in search_dirs:
        if os.path.exists(d):
            try:
                found_files = os.listdir(d)
                log.info(f"   Directory check: {d} contains {found_files}")
            except Exception as e:
                log.error(f"   Could not list directory {d}: {e}")

    # Updated filenames to match your current setup
    csv_candidates = [
        ("pilot.csv", "pilot"),
        ("loves.csv", "loves"),
    ]

    seeded = False
    already_seeded = set()

    for search_dir in search_dirs:
        for filename, brand in csv_candidates:
            filepath = os.path.join(search_dir, filename)
            
            # Case-sensitive check for Linux/Docker environment
            if not os.path.exists(filepath):
                continue
                
            real = os.path.realpath(filepath)
            if real in already_seeded:
                continue
                
            already_seeded.add(real)
            log.info(f"   Seeding from {filepath} (brand={brand})...")
            try:
                from seed_pilot_stops import seed
                # Note: using brand_override=brand so the seeder knows which logic to use
                seed(filepath=filepath, brand_override=brand,
                     dry_run=False, delimiter=",")
                seeded = True
            except Exception as e:
                log.error(f"   Seed failed for {filepath}: {e}", exc_info=True)

    if seeded:
        count = len(get_all_stops_with_diesel())
        log.info(f"   Seeding complete — {count} diesel stops now in DB")
    else:
        log.warning("   No CSV files found in search paths. Stops not loaded.")


# -- Helpers ------------------------------------------------------------------

def _utcnow():
    return datetime.now(timezone.utc)


# -- Main loop ----------------------------------------------------------------

def main():
    global truck_states

    log.info("FleetFuel Bot starting up...")

    # Initialize DB schema
    log.info("Checking database schema...")
    init_db()

    # CRITICAL: If table is "empty" but seeding failed before, 
    # setting RESET_DB=1 in Railway will fix it.
    if os.getenv("RESET_DB", "0") == "1":
        log.info("RESET_DB=1 — clearing all truck state history and stop data...")
        reset_truck_states()

    # Auto-seed CSV files
    _auto_seed()

    # Load saved state from DB
    log.info("Loading truck states from database...")
    truck_states = load_all_truck_states()
    log.info(f"   Loaded {len(truck_states)} truck states")

    # Notify Telegram
    try:
        send_startup_message()
    except Exception as e:
        log.warning(f"Could not send startup message: {e}")

    log.info("Starting polling loop...")

    last_db_save = _utcnow()
    poll_cycle   = 0

    while _running:
        try:
            poll_cycle += 1
            now = _utcnow()

            # Step 1: Find trucks due for polling
            due_trucks = []
            for vid, state in truck_states.items():
                next_poll = state.get("next_poll")
                if next_poll is None:
                    due_trucks.append(vid)
                elif isinstance(next_poll, datetime):
                    if next_poll.tzinfo is None:
                        next_poll = next_poll.replace(tzinfo=timezone.utc)
                    if next_poll <= now:
                        due_trucks.append(vid)

            # Step 2: Fetch data from Samsara
            try:
                all_trucks = get_combined_vehicle_data()
            except Exception as e:
                log.error(f"Failed to fetch Samsara data: {e}")
                time.sleep(60)
                continue

            log.info(f"Poll #{poll_cycle}: {len(all_trucks)} trucks fetched, "
                     f"{len(due_trucks)} due for check")

            # Step 3: Process due trucks
            for vid in due_trucks:
                current_data = next((t for t in all_trucks if t["vehicle_id"] == vid), None)
                if current_data is None:
                    if vid in truck_states:
                        truck_states[vid]["next_poll"] = now + timedelta(minutes=30)
                    continue
                try:
                    process_truck(vid, truck_states.get(vid, {}), current_data, truck_states)
                except Exception as e:
                    log.error(f"Error processing truck {vid}: {e}")

            # Step 4: Add new trucks
            for truck in all_trucks:
                vid = truck["vehicle_id"]
                if vid not in truck_states:
                    log.info(f"   New truck discovered: {truck['vehicle_name']} ({vid})")
                    process_truck(vid, {}, truck, truck_states)

            # Step 5: Periodic DB save
            if (now - last_db_save).total_seconds() >= STATE_SAVE_INTERVAL_SECONDS:
                save_all_truck_states(truck_states)
                last_db_save = now

        except Exception as e:
            log.error(f"Unhandled error in poll cycle: {e}", exc_info=True)

        time.sleep(30)

    log.info("FleetFuel Bot stopped cleanly.")


if __name__ == "__main__":
    main()
