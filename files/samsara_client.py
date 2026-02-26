"""
samsara_client.py  –  Fetch vehicle list, locations, and fuel levels from Samsara API
Docs: https://developers.samsara.com/reference
"""

import requests
from config import SAMSARA_API_TOKEN, SAMSARA_BASE_URL


HEADERS = {
    "Authorization": f"Bearer {SAMSARA_API_TOKEN}",
    "Content-Type": "application/json",
}


def _get(endpoint: str, params: dict = None) -> dict:
    url = f"{SAMSARA_BASE_URL}{endpoint}"
    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_all_vehicles() -> list[dict]:
    """
    Returns list of vehicles with basic info.
    Each item: { id, name, externalIds, ... }
    """
    data = _get("/v1/fleet/list")
    return data.get("vehicles", [])


def get_vehicle_locations() -> list[dict]:
    """
    Returns current GPS + heading for all vehicles.
    Each item: { id, name, location: {lat, lng, heading, speed, time}, ... }
    Uses Samsara v2 vehicles/locations endpoint.
    """
    url = "https://api.samsara.com/fleet/vehicles/locations"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", [])


def get_vehicle_stats(vehicle_ids: list[str] = None) -> list[dict]:
    """
    Returns engine/fuel stats for vehicles.
    types: fuelPercents  (0-100 float)
    Each item: { id, name, fuelPercents: {value, time}, ... }
    """
    url = "https://api.samsara.com/fleet/vehicles/stats"
    params = {"types": "fuelPercents"}
    if vehicle_ids:
        params["vehicleIds"] = ",".join(vehicle_ids)
    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", [])


def get_driver_for_vehicle(vehicle_id: str) -> dict | None:
    """
    Returns current driver dispatched to a vehicle, or None.
    { id, name, phone, ... }
    """
    try:
        url = f"https://api.samsara.com/fleet/vehicles/{vehicle_id}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        driver = data.get("data", {}).get("currentDriver")
        return driver
    except Exception:
        return None


def get_combined_vehicle_data() -> list[dict]:
    """
    Merges locations + fuel stats into one list.
    Returns list of:
    {
        vehicle_id:   str,
        vehicle_name: str,
        driver_name:  str | None,
        lat:          float,
        lng:          float,
        heading:      float,   # 0-360 degrees
        speed_mph:    float,
        fuel_pct:     float,   # 0-100
    }
    """
    locations_raw = get_vehicle_locations()
    stats_raw     = get_vehicle_stats()

    # Index stats by vehicle id
    stats_map = {}
    for s in stats_raw:
        vid = s.get("id")
        fuel = s.get("fuelPercents", {})
        if vid and fuel:
            stats_map[vid] = float(fuel.get("value", 100))

    results = []
    for v in locations_raw:
        vid  = v.get("id")
        name = v.get("name", vid)
        loc  = v.get("location", {})
        lat  = loc.get("latitude")
        lng  = loc.get("longitude")
        heading   = loc.get("heading", 0)
        speed_mph = loc.get("speed", 0)

        if lat is None or lng is None:
            continue  # skip vehicles with no GPS fix

        fuel_pct = stats_map.get(vid, 100.0)

        # Try to get driver name (optional — adds an extra API call per vehicle)
        driver = get_driver_for_vehicle(vid)
        driver_name = driver.get("name") if driver else None

        results.append({
            "vehicle_id":   vid,
            "vehicle_name": name,
            "driver_name":  driver_name,
            "lat":          float(lat),
            "lng":          float(lng),
            "heading":      float(heading),
            "speed_mph":    float(speed_mph),
            "fuel_pct":     fuel_pct,
        })

    return results
