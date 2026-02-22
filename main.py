"""
main.py  –  FleetFuel Bot with smart per-truck polling

Polling strategy:
  - Batch-fetch all trucks from Samsara (1 API call)
  - Check which trucks are due for polling based on their state
  - Process only the due trucks
  - Save state to DB every 5 minutes

Yard trucks: always ignored — no alerts fire while in yard
Other trucks: dynamic intervals based on fuel + movement
"""

import logging
import time
import signal
import sys
import os
from datetime import datetime, timedelta, timezone

from config import STATE_SAVE_INTERVAL_SECONDS
from database import init_db, load_all_truck_states, save_all_truck_states, reset_truck_states
from samsara_client import get_combined_vehicle_data
from state_machine import process_truck
from telegram_bot import send_startup_message

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── Global state (in-memory) ──────────────────────────────────────────────────
truck_states = {}  # {vehicle_id: state_dict}

# ── Graceful shutdown ─────────────────────────────────────────────────────────
_running = True

def _shutdown(signum, frame):
    global _running
    log.info("🛑  Shutdown signal received. Saving state and stopping...")
    save_all_truck_states(truck_states)
    _running = False

signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT,  _shutdown)


# ── Main loop ─────────────────────────────────────────────────────────────────

def _utcnow():
    return datetime.now(timezone.utc)


def main():
    global truck_states

    log.info("🚛  FleetFuel Bot starting up...")

    # Initialize DB schema
    log.info("🔧  Checking database schema...")
    init_db()

    # Optional: reset truck states on startup (set RESET_DB=1 env var)
    if os.getenv("RESET_DB", "0") == "1":
        log.info("🗑️  RESET_DB=1 detected — clearing all truck state history...")
        reset_truck_states()

    # Load saved state from DB
    log.info("📂  Loading truck states from database...")
    truck_states = load_all_truck_states()
    log.info(f"   Loaded {len(truck_states)} truck states")
    
    # Notify Telegram group
    try:
        send_startup_message()
    except Exception as e:
        log.warning(f"⚠️  Could not send startup message: {e}")
    
    log.info("⏱  Starting smart polling loop...")
    
    last_db_save = _utcnow()
    poll_cycle = 0
    
    while _running:
        try:
            poll_cycle += 1
            now = _utcnow()
            
            # ── Step 1: Find trucks due for polling ──────────────────────────
            due_trucks = []
            for vid, state in truck_states.items():
                next_poll = state.get('next_poll')
                if next_poll is None:
                    # New truck, never polled
                    due_trucks.append(vid)
                elif isinstance(next_poll, datetime):
                    if next_poll.tzinfo is None:
                        next_poll = next_poll.replace(tzinfo=timezone.utc)
                    if next_poll <= now:
                        due_trucks.append(vid)
            
            # ── Step 2: Batch-fetch ALL trucks from Samsara (1 API call) ─────
            try:
                all_trucks = get_combined_vehicle_data()
            except Exception as e:
                log.error(f"❌  Failed to fetch Samsara data: {e}")
                time.sleep(60)  # Wait 1 min before retry
                continue

            log.info(f"🔄  Poll #{poll_cycle}: {len(all_trucks)} trucks fetched, {len(due_trucks)} due for check")
            
            # Log summary of current states
            state_counts = {}
            for s in truck_states.values():
                st = s.get('state', 'UNKNOWN')
                state_counts[st] = state_counts.get(st, 0) + 1
            if state_counts:
                log.info(f"   Fleet states: {state_counts}")
            
            # ── Step 3: Process only the due trucks ──────────────────────────
            for vid in due_trucks:
                # Find current data for this truck
                current_data = None
                for truck in all_trucks:
                    if truck['vehicle_id'] == vid:
                        current_data = truck
                        break
                
                if current_data is None:
                    log.warning(f"   ⚠️  Truck {vid} not found in Samsara data (offline?)")
                    # Schedule retry in 30 min
                    if vid in truck_states:
                        truck_states[vid]['next_poll'] = now + timedelta(minutes=30)
                    continue
                
                try:
                    prev_state = truck_states.get(vid, {})
                    process_truck(vid, prev_state, current_data, truck_states)
                except Exception as e:
                    log.error(f"❌  Error processing truck {vid}: {e}", exc_info=True)
            
            # ── Step 4: Add and PROCESS any new trucks not yet in state ──────
            for truck in all_trucks:
                vid = truck['vehicle_id']
                if vid not in truck_states:
                    log.info(f"   🆕 New truck discovered: {truck['vehicle_name']} ({vid})")
                    # Process immediately on this cycle
                    try:
                        process_truck(vid, {}, truck, truck_states)
                        time.sleep(1)  # Small gap between trucks to avoid Telegram 429
                    except Exception as e:
                        log.error(f"❌  Error processing new truck {vid}: {e}", exc_info=True)
            
            # ── Step 5: Periodic DB save ─────────────────────────────────────
            if (now - last_db_save).total_seconds() >= STATE_SAVE_INTERVAL_SECONDS:
                log.info(f"💾  Saving {len(truck_states)} truck states to DB...")
                save_all_truck_states(truck_states)
                last_db_save = now
            
        except Exception as e:
            log.error(f"💥  Unhandled error in poll cycle: {e}", exc_info=True)
        
        # Sleep 30 seconds between checks
        time.sleep(30)
    
    log.info("👋  FleetFuel Bot stopped cleanly.")


if __name__ == "__main__":
    main()
