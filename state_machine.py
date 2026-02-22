"""
state_machine.py

CORRECT ALERT LOGIC
───────────────────

MOVING + low fuel:
  → Send ONE alert with nearest stop (Pilot first, Love's fallback)
  → Create PENDING flag
  → Poll every 10 min

PARKED + low fuel (sleeping at a stop):
  → Send ONE alert so dispatcher knows truck stopped with low fuel
  → Mark as SLEEPING — do NOT send "Refueled" just because truck is near a stop
  → Poll every 60 min, wait

TRUCK WAKES UP (was parked, now moving again):
  → NOW check fuel:
      • Fuel went UP  → driver refueled — send "Refueled", close alert
      • Fuel still low → send fresh alert with nearest stop ahead
  → Clear SLEEPING state

YARD trucks: zero alerts, zero checks, always ignored.
"""

import logging
from datetime import datetime, timedelta, timezone

from config import (
    FUEL_ALERT_THRESHOLD_PCT,
    POLL_INTERVAL_HEALTHY,
    POLL_INTERVAL_WATCH,
    POLL_INTERVAL_CRITICAL_MOVING,
    POLL_INTERVAL_CRITICAL_PARKED,
    SKIP_DETECTION_HOURS,
    VISIT_RADIUS_MILES,
)
from yard_geofence import is_in_yard, get_yard_name
from truck_stop_finder import find_best_stop, is_truck_near_stop, StopType
from telegram_bot import (
    send_low_fuel_alert,
    send_no_stop_alert,
    send_refueled_alert,
    send_flagged_alert,
    send_left_yard_low_fuel,
)
from database import (
    create_fuel_alert,
    create_stop_assignment,
    create_pending_flag,
    get_pending_flags_for_vehicle,
    mark_flag_visited,
    mark_flag_skipped,
    resolve_alert,
    mark_alert_skipped,
    update_alert_telegram_msg,
)

log = logging.getLogger(__name__)

_MOVING_MPH = 5   # above this = truck is moving


def _utcnow():
    return datetime.now(timezone.utc)


def _tz(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _next_poll(minutes):
    return _utcnow() + timedelta(minutes=minutes)


# ── State skeleton ────────────────────────────────────────────────────────────

def _new_state(vid, data):
    return {
        "vehicle_id":           vid,
        "vehicle_name":         data["vehicle_name"],
        "driver_name":          data["driver_name"],
        "state":                "UNKNOWN",
        "fuel_pct":             data["fuel_pct"],
        "lat":                  data["lat"],
        "lng":                  data["lng"],
        "speed_mph":            data["speed_mph"],
        "heading":              data["heading"],
        "next_poll":            _utcnow(),
        "parked_since":         None,
        "alert_sent":           False,
        "overnight_alert_sent": False,
        "open_alert_id":        None,
        "assigned_stop_id":     None,
        "assigned_stop_name":   None,
        "assigned_stop_lat":    None,
        "assigned_stop_lng":    None,
        "assignment_time":      None,
        "in_yard":              False,
        "yard_name":            None,
        "fuel_when_parked":     None,   # fuel level recorded when truck stopped
        "sleeping":             False,  # True while parked with low fuel
    }


def _clear_alert(state):
    state["open_alert_id"]        = None
    state["assigned_stop_id"]     = None
    state["assigned_stop_name"]   = None
    state["assigned_stop_lat"]    = None
    state["assigned_stop_lng"]    = None
    state["assignment_time"]      = None
    state["alert_sent"]           = False
    state["overnight_alert_sent"] = False
    state["fuel_when_parked"]     = None
    state["sleeping"]             = False


# ── Main entry point ──────────────────────────────────────────────────────────

def process_truck(vid, prev_state, current_data, truck_states):

    fuel    = current_data["fuel_pct"]
    speed   = current_data["speed_mph"]
    lat     = current_data["lat"]
    lng     = current_data["lng"]
    heading = current_data["heading"]
    vname   = current_data["vehicle_name"]
    driver  = current_data["driver_name"]

    if vid not in truck_states:
        truck_states[vid] = _new_state(vid, current_data)

    state = truck_states[vid]

    state["vehicle_name"] = vname
    state["driver_name"]  = driver
    state["fuel_pct"]     = fuel
    state["lat"]          = lat
    state["lng"]          = lng
    state["speed_mph"]    = speed
    state["heading"]      = heading

    moving = speed > _MOVING_MPH

    log.info(f"   {vname}: fuel={fuel:.1f}%  speed={speed:.0f}mph  "
             f"state={state.get('state','NEW')}  sleeping={state.get('sleeping',False)}")

    # ══════════════════════════════════════════════════════════════════════════
    # 1. YARD CHECK — always first, always silences everything
    # ══════════════════════════════════════════════════════════════════════════
    in_yard_now = is_in_yard(lat, lng)
    was_in_yard = state.get("in_yard", False)

    if in_yard_now:
        yard_name = get_yard_name(lat, lng)
        if not was_in_yard:
            log.info(f"   {vname} entered yard: {yard_name}")
        state["in_yard"]   = True
        state["yard_name"] = yard_name
        state["state"]     = "IN_YARD"
        state["next_poll"] = _next_poll(30)
        return  # zero alerts, zero checks

    if was_in_yard and not in_yard_now:
        yard_name = state.get("yard_name", "yard")
        log.info(f"   {vname} left {yard_name} with {fuel:.1f}% fuel")
        state["in_yard"]   = False
        state["yard_name"] = None
        if fuel <= FUEL_ALERT_THRESHOLD_PCT:
            send_left_yard_low_fuel(vname, driver, fuel, yard_name)
            _fire_alert(vid, state, current_data)
            return

    # ══════════════════════════════════════════════════════════════════════════
    # 2. FUEL IS FINE
    # ══════════════════════════════════════════════════════════════════════════
    if fuel > FUEL_ALERT_THRESHOLD_PCT:
        if state.get("open_alert_id"):
            log.info(f"   {vname}: fuel recovered to {fuel:.1f}% — closing alert")
            resolve_alert(state["open_alert_id"])
            _clear_alert(state)

        if fuel > 50:
            state["state"]     = "HEALTHY"
            state["next_poll"] = _next_poll(POLL_INTERVAL_HEALTHY)
        else:
            state["state"]     = "WATCH"
            state["next_poll"] = _next_poll(
                POLL_INTERVAL_WATCH if moving else POLL_INTERVAL_HEALTHY
            )

        state["parked_since"]     = None
        state["sleeping"]         = False
        state["fuel_when_parked"] = None
        return

    # ══════════════════════════════════════════════════════════════════════════
    # 3. FUEL IS LOW
    # ══════════════════════════════════════════════════════════════════════════

    was_sleeping = state.get("sleeping", False)

    # ── 3a. TRUCK JUST WOKE UP (was sleeping, now moving) ────────────────────
    if was_sleeping and moving:
        fuel_when_parked = state.get("fuel_when_parked") or fuel
        log.info(f"   {vname}: woke up — was {fuel_when_parked:.1f}%, now {fuel:.1f}%")

        # Clear sleep state first
        state["sleeping"]         = False
        state["fuel_when_parked"] = None
        state["parked_since"]     = None

        if fuel > fuel_when_parked + 5:
            # Fuel went up 5%+ → refueled during sleep
            stop_name = state.get("assigned_stop_name") or "a fuel stop"
            log.info(f"   {vname}: refueled during sleep (+{fuel - fuel_when_parked:.1f}%)")
            if state.get("open_alert_id"):
                resolve_alert(state["open_alert_id"])
            send_refueled_alert(vname, driver, stop_name, fuel)
            _clear_alert(state)
            state["state"]     = "CRITICAL_MOVING" if fuel <= FUEL_ALERT_THRESHOLD_PCT else "HEALTHY"
            state["next_poll"] = _next_poll(POLL_INTERVAL_CRITICAL_MOVING)
        else:
            # Still low — fire a fresh alert with current heading
            log.info(f"   {vname}: woke up still low — fresh alert")
            if state.get("open_alert_id"):
                resolve_alert(state["open_alert_id"])
            _clear_alert(state)
            state["state"]     = "CRITICAL_MOVING"
            state["next_poll"] = _next_poll(POLL_INTERVAL_CRITICAL_MOVING)
            _fire_alert(vid, state, current_data)

        return

    # ── 3b. MOVING + LOW FUEL ────────────────────────────────────────────────
    if moving:
        state["state"]        = "CRITICAL_MOVING"
        state["next_poll"]    = _next_poll(POLL_INTERVAL_CRITICAL_MOVING)
        state["parked_since"] = None

        # Check flags (near stop = visited while moving, old flag = skipped)
        _check_flags(vid, state, lat, lng, fuel, vname, driver)

        # Fire alert only once per trip leg
        if not state.get("alert_sent"):
            _fire_alert(vid, state, current_data)
        return

    # ── 3c. PARKED + LOW FUEL (sleeping) ─────────────────────────────────────
    if not state.get("parked_since"):
        state["parked_since"]     = _utcnow()
        state["fuel_when_parked"] = fuel
        log.info(f"   {vname}: parked at {fuel:.1f}% — entering sleep mode")

    state["state"]     = "CRITICAL_PARKED"
    state["next_poll"] = _next_poll(POLL_INTERVAL_CRITICAL_PARKED)
    state["sleeping"]  = True

    # ONE alert to notify dispatcher the truck is parked with low fuel.
    # We do NOT resolve or "refuel" based on proximity while sleeping.
    if not state.get("overnight_alert_sent"):
        _fire_alert(vid, state, current_data)
        state["overnight_alert_sent"] = True


# ── Alert firing ──────────────────────────────────────────────────────────────

def _fire_alert(vid, state, data):
    """Find nearest stop and send Telegram alert. Creates DB records."""
    vname   = data["vehicle_name"]
    driver  = data["driver_name"]
    fuel    = data["fuel_pct"]
    lat     = data["lat"]
    lng     = data["lng"]
    heading = data["heading"]
    speed   = data["speed_mph"]

    log.info(f"   {vname}: firing alert — fuel={fuel:.1f}%")

    alert_id = create_fuel_alert(vid, vname, driver, fuel, lat, lng, heading, speed)
    state["open_alert_id"] = alert_id

    stop, stop_type = find_best_stop(lat, lng, heading, speed)

    if stop_type == StopType.AT_STOP:
        # Truck is physically in a stop's parking lot right now
        log.info(f"   {vname}: already in lot of {stop['name']} — no alert")
        resolve_alert(alert_id)
        state["open_alert_id"] = None
        state["alert_sent"]    = True
        return

    if stop is None or stop_type == StopType.NONE:
        log.warning(f"   {vname}: no stop within 80 miles")
        msg_id = send_no_stop_alert(vname, driver, fuel, lat, lng, heading, speed)
        if msg_id:
            update_alert_telegram_msg(alert_id, msg_id)
        state["alert_sent"] = True
        return

    log.info(f"   {vname}: -> {stop['name']} {stop['distance_miles']:.1f} mi [{stop_type.value}]")

    create_stop_assignment(alert_id, stop["id"], stop["distance_miles"])
    create_pending_flag(alert_id, vid, stop["id"])

    state["assigned_stop_id"]   = stop["id"]
    state["assigned_stop_name"] = stop["name"]
    state["assigned_stop_lat"]  = float(stop["latitude"])
    state["assigned_stop_lng"]  = float(stop["longitude"])
    state["assignment_time"]    = _utcnow()

    msg_id = send_low_fuel_alert(vname, driver, fuel, lat, lng, stop, heading, speed, stop_type)
    if msg_id:
        update_alert_telegram_msg(alert_id, msg_id)

    state["alert_sent"] = True


# ── Flag check (moving trucks only) ──────────────────────────────────────────

def _check_flags(vid, state, lat, lng, fuel, vname, driver):
    """
    Near the assigned stop while moving = Refueled.
    Flag older than SKIP_DETECTION_HOURS = Flagged/skipped.
    NOT called while truck is sleeping.
    """
    flags = get_pending_flags_for_vehicle(vid)
    if not flags:
        return

    for flag in flags:
        stop_lat  = float(flag["stop_lat"])
        stop_lng  = float(flag["stop_lng"])
        stop_name = flag["stop_name"]
        flag_at   = _tz(flag["flagged_at"])
        alert_id  = flag["alert_id"]

        if is_truck_near_stop(lat, lng, stop_lat, stop_lng, VISIT_RADIUS_MILES):
            log.info(f"   {vname}: at '{stop_name}' while moving — Refueled")
            mark_flag_visited(flag["id"])
            resolve_alert(alert_id)
            send_refueled_alert(vname, driver, stop_name, fuel)
            _clear_alert(state)
            continue

        hours = (_utcnow() - flag_at).total_seconds() / 3600
        if hours >= SKIP_DETECTION_HOURS:
            log.warning(f"   {vname}: skipped '{stop_name}' after {hours:.1f}h")
            msg_id = send_flagged_alert(vname, driver, stop_name, fuel,
                                        flag.get("telegram_msg_id"))
            mark_flag_skipped(flag["id"], msg_id)
            mark_alert_skipped(alert_id)
            _clear_alert(state)
