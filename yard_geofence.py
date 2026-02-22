"""
yard_geofence.py  –  Detect whether a truck is inside a company yard.

Yards are configured in .env as YARD_N=Name:lat:lng:radius_miles.
Any truck inside a yard geofence is completely silenced — no fuel alerts.
"""

import math
import logging
from config import YARDS

log = logging.getLogger(__name__)

EARTH_RADIUS_MILES = 3958.8


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return great-circle distance in miles between two GPS coordinates."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(a))


def is_in_yard(lat: float, lng: float) -> bool:
    """Return True if the given GPS coordinate falls inside any configured yard."""
    if not YARDS:
        return False
    for yard in YARDS:
        dist = _haversine(lat, lng, yard["lat"], yard["lng"])
        if dist <= yard["radius_miles"]:
            return True
    return False


def get_yard_name(lat: float, lng: float) -> str | None:
    """Return the name of the yard the truck is in, or None."""
    for yard in YARDS:
        dist = _haversine(lat, lng, yard["lat"], yard["lng"])
        if dist <= yard["radius_miles"]:
            return yard["name"]
    return None


def yard_summary() -> str:
    """Human-readable summary of configured yards (for startup log)."""
    if not YARDS:
        return "No yards configured"
    lines = [f"  • {y['name']}  ({y['lat']:.4f}, {y['lng']:.4f})  r={y['radius_miles']} mi"
             for y in YARDS]
    return "\n".join(lines)
