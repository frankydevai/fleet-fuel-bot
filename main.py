"""
main.py  -  FleetFuel Bot with smart per-truck polling
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
    If pilot_stops table is empty, find and seed CSV files automatically.
    Searches multiple directories so it works both locally and in Docker.
    """
    stops = get_all_stops_with_diesel()
    if stops:
        log.info(f"   pilot_stops: {len(stops)} stops loaded — skipping seed")
        return

    log.info("   pilot_stops table is empty — searching for CSV files...")

    # All directories to search
    script_dir = os.path.dirname(os.path.abspath(__file__))
    search_dirs = list(dict.fromkeys([script_dir, "/app", ".", os.getcwd()]))

    # Log what files are available to help debug
    for d in search_dirs:
        try:
            files = [f for f in os.listdir(d) if f.endswith(".csv")]
            if files:
                log.info(f"   CSV files found in {d}: {files}")
        except Exception:
            pass

    # CSV filenames to look for and their brand
    csv_candidates = [
        ("all_stops.csv",    ""),
        ("pilot_stops.csv",  "pilot"),
        ("loves_stops.csv",  "loves"),
        ("pilot.csv",        "pilot"),
        ("loves.csv",        "loves"),
    ]

    seeded = False
    already_seeded = set()

    for search_dir in search_dirs:
        for filename, brand in csv_candidates:
            filepath = os.path.join(search_dir, filename)
            # avoid seeding same file twice from different dir references
            real = os.path.realpath(filepath)
            if real in already_seeded:
                continue
            if not os.path.exists(filepath):
                continue
            already_seeded.add(real)
            log.info(f"   Seeding from {filepath} (brand={brand or 'auto'})...")
            try:
                from seed_pilot_stops import seed
                seed(filepath=filepath, brand_override=brand,
                     dry_run=False, delimiter=",")
                seeded = True
            except Exception as e:
                log.error(f"   Seed failed for {filepath}: {e}", exc_info=True)

    if seeded:
        count = len(get_all_stops_with_diesel())
        log.info(f"   Seeding complete — {count} diesel stops now in DB")
    else:
        log.warning("   No CSV files found. Pilot/Love's stops not loaded — "
                    "alerts will show 'no stop found' until CSV is added to repo.")


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

    # Optional: reset truck states on startup
    if os.getenv("RESET_DB", "0") == "1":
        log.info("RESET_DB=1 — clearing all truck state history...")
        reset_truck_states()

    # Auto-seed CSV files if pilot_stops table is empty
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

            # Step 2: Batch-fetch ALL trucks from Samsara (1 API call)
            try:
                all_trucks = get_combined_vehicle_data()
            except Exception as e:
                log.error(f"Failed to fetch Samsara data: {e}")
                time.sleep(60)
                continue

            log.info(f"Poll #{poll_cycle}: {len(all_trucks)} trucks fetched, "
                     f"{len(due_trucks)} due for check")

            state_counts = {}
            for s in truck_states.values():
                st = s.get("state", "UNKNOWN")
                state_counts[st] = state_counts.get(st, 0) + 1
            if state_counts:
                log.info(f"   Fleet states: {state_counts}")

            # Step 3: Process due trucks
            for vid in due_trucks:
                current_data = next(
                    (t for t in all_trucks if t["vehicle_id"] == vid), None
                )
                if current_data is None:
                    log.warning(f"   Truck {vid} not found in Samsara (offline?)")
                    if vid in truck_states:
                        truck_states[vid]["next_poll"] = now + timedelta(minutes=30)
                    continue
                try:
                    process_truck(vid, truck_states.get(vid, {}), current_data, truck_states)
                except Exception as e:
                    log.error(f"Error processing truck {vid}: {e}", exc_info=True)

            # Step 4: Process new trucks not yet in state
            for truck in all_trucks:
                vid = truck["vehicle_id"]
                if vid not in truck_states:
                    log.info(f"   New truck discovered: {truck['vehicle_name']} ({vid})")
                    try:
                        process_truck(vid, {}, truck, truck_states)
                        time.sleep(1)
                    except Exception as e:
                        log.error(f"Error processing new truck {vid}: {e}", exc_info=True)

            # Step 5: Periodic DB save
            if (now - last_db_save).total_seconds() >= STATE_SAVE_INTERVAL_SECONDS:
                log.info(f"Saving {len(truck_states)} truck states to DB...")
                save_all_truck_states(truck_states)
                last_db_save = now

        except Exception as e:
            log.error(f"Unhandled error in poll cycle: {e}", exc_info=True)

        time.sleep(30)

    log.info("FleetFuel Bot stopped cleanly.")


if __name__ == "__main__":
    main()
