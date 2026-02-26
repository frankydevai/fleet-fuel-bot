"""
fuel_checker.py  –  Core orchestration logic

Called every poll cycle:
  1. Fetch all vehicle data from Samsara
  2. For each vehicle:
     a. If fuel >= threshold → check if open alert needs resolving → skip
     b. If fuel < threshold:
        - Check cooldown (don't re-alert too soon)
        - Find best stop ahead
        - Create DB records
        - Send Telegram alert
  3. Separately: check pending stop flags for skip detection
"""

import logging
from datetime import datetime, timezone

from samsara_client import get_combined_vehicle_data
from truck_stop_finder import get_best_stop, is_truck_near_stop
from telegram_bot import (
    send_low_fuel_alert,
    send_no_stop_found_alert,
    send_skip_alert,
    send_resolved_alert,
)
from database import (
    create_fuel_alert,
    create_stop_assignment,
    create_pending_flag,
    get_open_alert_for_vehicle,
    get_recent_alert_time,
    get_pending_flags_older_than,
    get_pending_flag_for_alert,
    resolve_alert,
    mark_flag_visited,
    mark_flag_skipped,
    update_alert_telegram_msg,
)
from config import (
    FUEL_ALERT_THRESHOLD_PCT,
    ALERT_COOLDOWN_MINUTES,
    SKIP_DETECTION_MINUTES,
    VISIT_DETECTION_RADIUS_MILES,
)

log = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc)


def _minutes_since(dt) -> float:
    if dt is None:
        return float("inf")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (_utcnow() - dt).total_seconds() / 60


# ── Main poll ─────────────────────────────────────────────────────────────────

def run_fuel_check():
    """Run one full poll cycle. Called by the scheduler."""
    log.info("🔄  Running fuel check poll...")

    try:
        vehicles = get_combined_vehicle_data()
    except Exception as e:
        log.error(f"❌  Failed to fetch Samsara data: {e}")
        return

    log.info(f"   Found {len(vehicles)} vehicles.")

    for v in vehicles:
        try:
            _process_vehicle(v)
        except Exception as e:
            log.error(f"❌  Error processing vehicle {v.get('vehicle_name')}: {e}")

    # After processing all vehicles, check for skipped stops
    try:
        _check_for_skipped_stops(vehicles)
    except Exception as e:
        log.error(f"❌  Error in skip detection: {e}")

    log.info("✅  Poll cycle complete.")


# ── Per-vehicle logic ─────────────────────────────────────────────────────────

def _process_vehicle(v: dict):
    vid          = v["vehicle_id"]
    vname        = v["vehicle_name"]
    driver       = v["driver_name"]
    fuel_pct     = v["fuel_pct"]
    lat, lng     = v["lat"], v["lng"]
    heading      = v["heading"]
    speed_mph    = v["speed_mph"]

    log.debug(f"   {vname}: fuel={fuel_pct:.1f}%  heading={heading:.0f}°")

    # ── Case 1: Fuel is fine ────────────────────────────────────────────────
    if fuel_pct >= FUEL_ALERT_THRESHOLD_PCT:
        # Check if we need to auto-resolve an open alert
        open_alert = get_open_alert_for_vehicle(vid)
        if open_alert:
            log.info(f"   {vname}: Fuel restored to {fuel_pct:.1f}% — resolving alert.")
            _auto_resolve(open_alert, vname, driver, fuel_pct)
        return

    # ── Case 2: Fuel is LOW ─────────────────────────────────────────────────
    open_alert = get_open_alert_for_vehicle(vid)

    # Check if truck is near assigned stop → mark visited → resolve
    if open_alert:
        flag = get_pending_flag_for_alert(open_alert["id"])
        if flag:
            near = is_truck_near_stop(
                lat, lng,
                float(flag["stop_lat"]), float(flag["stop_lng"]),
                VISIT_DETECTION_RADIUS_MILES,
            )
            if near:
                log.info(f"   {vname}: Arrived at stop '{flag['stop_name']}' — marking visited.")
                mark_flag_visited(flag["id"])
                resolve_alert(open_alert["id"])
                send_resolved_alert(vname, driver, flag["stop_name"], fuel_pct)
                return

        # Alert already exists and truck hasn't stopped yet — nothing new to do
        log.debug(f"   {vname}: Open alert exists, waiting for truck to reach stop.")
        return

    # Check cooldown before sending a new alert
    last_alert_time = get_recent_alert_time(vid)
    mins_since_last = _minutes_since(last_alert_time)
    if mins_since_last < ALERT_COOLDOWN_MINUTES:
        log.debug(f"   {vname}: Cooldown active ({mins_since_last:.0f}/{ALERT_COOLDOWN_MINUTES} min).")
        return

    # New low-fuel event — find best stop and alert
    log.info(f"   🚨 {vname}: Fuel at {fuel_pct:.1f}% — sending alert.")
    _handle_new_low_fuel(v)


def _handle_new_low_fuel(v: dict):
    vid       = v["vehicle_id"]
    vname     = v["vehicle_name"]
    driver    = v["driver_name"]
    fuel_pct  = v["fuel_pct"]
    lat, lng  = v["lat"], v["lng"]
    heading   = v["heading"]
    speed_mph = v["speed_mph"]

    # Create DB alert record
    alert_id = create_fuel_alert(vid, vname, driver, fuel_pct, lat, lng, heading, speed_mph)

    # Find best stop ahead
    best_stop = get_best_stop(lat, lng, heading)

    if best_stop is None:
        log.warning(f"   {vname}: No stop found ahead — sending critical alert.")
        msg_id = send_no_stop_found_alert(vname, driver, fuel_pct, heading)
        update_alert_telegram_msg(alert_id, msg_id)
        return

    # Save assignment + pending flag
    create_stop_assignment(alert_id, best_stop["id"], best_stop["distance_miles"])
    create_pending_flag(alert_id, vid, best_stop["id"])

    # Send Telegram alert
    msg_id = send_low_fuel_alert(vname, driver, fuel_pct, best_stop, heading, speed_mph)
    if msg_id:
        update_alert_telegram_msg(alert_id, msg_id)

    log.info(
        f"   ✅ Alert sent for {vname} → {best_stop['name']} "
        f"({best_stop['distance_miles']:.1f} mi ahead)"
    )


def _auto_resolve(open_alert: dict, vname: str, driver: str, fuel_pct: float):
    """Fuel went back above threshold — close the alert."""
    flag = get_pending_flag_for_alert(open_alert["id"])
    stop_name = flag["stop_name"] if flag else "Unknown stop"
    resolve_alert(open_alert["id"])
    if flag:
        mark_flag_visited(flag["id"])
    send_resolved_alert(vname, driver, stop_name, fuel_pct)


# ── Skip detection ────────────────────────────────────────────────────────────

def _check_for_skipped_stops(current_vehicles: list[dict]):
    """
    Looks for pending stop flags older than SKIP_DETECTION_MINUTES.
    If the truck is no longer near (or heading toward) the assigned stop
    and the flag is still pending → mark as SKIPPED and send flag alert.
    """
    old_flags = get_pending_flags_older_than(SKIP_DETECTION_MINUTES)
    if not old_flags:
        return

    # Build quick lookup of current truck positions
    truck_pos = {v["vehicle_id"]: v for v in current_vehicles}

    for flag in old_flags:
        vid = flag["vehicle_id"]
        current = truck_pos.get(vid)

        if current is None:
            continue  # vehicle not in this poll (offline?)

        # Check if truck is near the assigned stop
        near = is_truck_near_stop(
            current["lat"], current["lng"],
            float(flag["stop_lat"]), float(flag["stop_lng"]),
            VISIT_DETECTION_RADIUS_MILES,
        )

        if near:
            # Arrived — mark visited (may have been slow)
            log.info(f"   {flag['vehicle_name']}: Late arrival at '{flag['stop_name']}' detected.")
            mark_flag_visited(flag["id"])
            resolve_alert(flag["alert_id"])
            send_resolved_alert(
                flag["vehicle_name"], flag["driver_name"],
                flag["stop_name"], current["fuel_pct"]
            )
        else:
            # Truck has passed the stop window — SKIPPED
            log.warning(
                f"   🚩 {flag['vehicle_name']}: Skipped stop '{flag['stop_name']}'!"
            )
            skip_msg_id = send_skip_alert(
                flag["vehicle_name"],
                flag["driver_name"],
                flag["stop_name"],
                flag.get("telegram_msg_id"),
            )
            mark_flag_skipped(flag["id"], skip_msg_id)
            # Close the original alert as 'skipped'
            from database import db_cursor
            with db_cursor() as cur:
                cur.execute(
                    "UPDATE fuel_alerts SET status='skipped' WHERE id=%s",
                    (flag["alert_id"],)
                )
