"""
samsara_client.py  –  Fetch vehicle list, locations, and fuel levels from Samsara API
Docs: https://developers.samsara.com/reference

FIX (2026-02-25):
  get_driver_for_vehicle() was called once per vehicle inside get_combined_vehicle_data(),
  causing N+1 API calls (e.g. 40 trucks = 40 extra requests every poll cycle).
  Fixed by fetching all active drivers in one call and building a lookup map.

  Also fixed: get_vehicle_stats() used the /feed endpoint which returns a stream
  of events, not current snapshots. Switched to /history endpoint with a short
  lookback window to get the latest fuel reading reliably.
"""

import requests
import logging
from datetime import datetime, timedelta, timezone
from config import SAMSARA_API_TOKEN, SAMSARA_BASE_URL

log = logging.getLogger(__name__)

HEADERS = {
    "Authorization": f"Bearer {SAMSARA_API_TOKEN}",
    "Content-Type":  "application/json",
}


def _get(url: str, params: dict = None) -> dict:
    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ── Locations ─────────────────────────────────────────────────────────────────

def get_vehicle_locations() -> list[dict]:
    """
    Returns current GPS + heading for all vehicles.
    Each item: { id, name, location: {latitude, longitude, heading, speed}, ... }
    """
    data = _get("https://api.samsara.com/fleet/vehicles/locations")
    return data.get("data", [])


# ── Fuel stats ────────────────────────────────────────────────────────────────

def get_vehicle_stats() -> list[dict]:
    """
    Returns latest fuel percent for all vehicles using the history endpoint.
    The /feed endpoint returns a stream and may miss vehicles that haven't
    reported recently. History with a short lookback is more reliable.

    Returns list of: { id, name, fuelPercents: [{value, time}] }
    """
    end_time   = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=2)

    params = {
        "types":     "fuelPercents",
        "startTime": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endTime":   end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        data = _get("https://api.samsara.com/fleet/vehicles/stats/history", params)
        return data.get("data", [])
    except Exception as e:
        log.warning(f"Stats history failed, falling back to feed: {e}")
        data = _get("https://api.samsara.com/fleet/vehicles/stats/feed",
                    {"types": "fuelPercents"})
        return data.get("data", [])


# ── Drivers ───────────────────────────────────────────────────────────────────

def get_all_driver_assignments() -> dict[str, str]:
    """
    Returns a map of {vehicle_id: driver_name} for all currently active drivers.
    One API call instead of one per vehicle.
    """
    try:
        data = _get("https://api.samsara.com/fleet/drivers",
                    {"driverActivationStatus": "active"})
        drivers = data.get("data", [])
        assignment_map = {}
        for driver in drivers:
            vehicle = driver.get("currentVehicle")
            if vehicle and vehicle.get("id"):
                assignment_map[vehicle["id"]] = driver.get("name", "")
        return assignment_map
    except Exception as e:
        log.warning(f"Could not fetch driver assignments: {e}")
        return {}


# ── Combined ──────────────────────────────────────────────────────────────────

def get_combined_vehicle_data() -> list[dict]:
    """
    Merges locations + fuel stats + driver assignments into one list.
    Uses 3 API calls total regardless of fleet size (was N+2 before fix).

    Returns list of:
    {
        vehicle_id:   str,
        vehicle_name: str,
        driver_name:  str | None,
        lat:          float,
        lng:          float,
        heading:      float,
        speed_mph:    float,
        fuel_pct:     float,
    }
    """
    locations_raw = get_vehicle_locations()
    stats_raw     = get_vehicle_stats()
    driver_map    = get_all_driver_assignments()

    # Build fuel map: vehicle_id → latest fuel percent
    stats_map: dict[str, float] = {}
    for s in stats_raw:
        vid         = s.get("id")
        fuel_events = s.get("fuelPercents", [])
        if vid and fuel_events:
            latest = max(fuel_events, key=lambda x: x.get("time", ""))
            stats_map[vid] = float(latest.get("value", 100))
        elif vid:
            stats_map[vid] = 100.0

    results = []
    for v in locations_raw:
        vid  = v.get("id")
        name = v.get("name", vid)
        loc  = v.get("location", {})
        lat  = loc.get("latitude")
        lng  = loc.get("longitude")

        if lat is None or lng is None:
            continue

        heading   = float(loc.get("heading", 0))
        speed_mph = float(loc.get("speed",   0))
        fuel_pct  = stats_map.get(vid, 100.0)
        driver_name = driver_map.get(vid)  # FIX: single batch lookup

        results.append({
            "vehicle_id":   vid,
            "vehicle_name": name,
            "driver_name":  driver_name,
            "lat":          float(lat),
            "lng":          float(lng),
            "heading":      heading,
            "speed_mph":    speed_mph,
            "fuel_pct":     fuel_pct,
        })

    return results
