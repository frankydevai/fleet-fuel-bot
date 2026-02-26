"""
truck_stop_finder.py - Locate the best diesel stop for a truck.

Search logic (MOVING, speed > 5 mph):
  1. Pilot / Flying J within 50 mi
  2. Love's within 50 mi
  3. Pilot / Flying J within 80 mi
  4. Love's within 80 mi

PARKED (speed <= 5 mph):
  Nearest Pilot or Love's within 80 mi, no brand priority.

Heading filter removed entirely. It caused false "No stop found" alerts
on curved ramps and highways where a nearby stop's bearing appeared
slightly behind the truck's heading. Distance-only search is simpler
and more reliable.
"""

import math
import logging
from enum import Enum
from config import (
    PILOT_RADIUS_MILES,
    LOVES_RADIUS_MILES,
    EXTENDED_RADIUS_MILES,
)
from database import get_all_stops_with_diesel

log = logging.getLogger(__name__)

EARTH_RADIUS_MILES    = 3958.8
_PARKED_SPEED_MPH     = 5      # <= this = parked
_AT_STOP_RADIUS_MILES = 0.15   # ~250m - truck is in the lot


# -- Enum ---------------------------------------------------------------------

class StopType(Enum):
    AT_STOP  = "Already at a fuel stop"
    PILOT_50 = "Pilot/Flying J (within 50 mi)"
    LOVES_50 = "Love's (within 50 mi, no Pilot nearby)"
    PILOT_80 = "Pilot/Flying J (within 80 mi extended)"
    LOVES_80 = "Love's (within 80 mi extended)"
    NEAREST  = "Nearest stop (parked)"
    NONE     = "No stop found"


# -- Geo math -----------------------------------------------------------------

def haversine_miles(lat1, lng1, lat2, lng2):
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(a))


# -- Brand checks -------------------------------------------------------------

def _is_pilot(brand: str) -> bool:
    b = brand.lower()
    return "pilot" in b or "flying j" in b or "flyingj" in b or "one9" in b


def _is_loves(brand: str) -> bool:
    return "love" in brand.lower()


def _is_pilot_or_loves(brand: str) -> bool:
    return _is_pilot(brand) or _is_loves(brand)


# -- Core search --------------------------------------------------------------

def _search(stops, truck_lat, truck_lng, radius, brand_check):
    """
    Return all matching stops within radius sorted by distance (nearest first).
    No heading filter - distance only.
    """
    candidates = []

    for stop in stops:
        if not brand_check(stop.get("brand", "")):
            continue

        slat = float(stop["latitude"])
        slng = float(stop["longitude"])
        dist = haversine_miles(truck_lat, truck_lng, slat, slng)

        if dist > radius:
            continue

        candidates.append({
            **stop,
            "distance_miles":  round(dist, 2),
            "google_maps_url": f"https://maps.google.com/?q={slat},{slng}",
        })

    candidates.sort(key=lambda s: s["distance_miles"])
    return candidates


# -- Public API ---------------------------------------------------------------

def find_best_stop(truck_lat: float, truck_lng: float,
                   truck_heading: float,
                   speed_mph: float = 0) -> tuple[dict | None, StopType]:
    """
    Find the nearest diesel stop.

    PARKED : nearest Pilot or Love's wins (no brand priority).
    MOVING : Pilot preferred, Love's fallback, then extended 80 mi search.

    Returns (stop_dict, StopType).
    """
    all_stops = get_all_stops_with_diesel()
    parked    = speed_mph <= _PARKED_SPEED_MPH

    # Already at a stop?
    if parked:
        for stop in all_stops:
            if not _is_pilot_or_loves(stop.get("brand", "")):
                continue
            slat = float(stop["latitude"])
            slng = float(stop["longitude"])
            dist = haversine_miles(truck_lat, truck_lng, slat, slng)
            if dist <= _AT_STOP_RADIUS_MILES:
                log.info(
                    f"Stop finder: truck already at {stop['name']} "
                    f"({dist*5280:.0f} ft away) - no alert needed"
                )
                return {
                    **stop,
                    "distance_miles":  round(dist, 3),
                    "google_maps_url": f"https://maps.google.com/?q={slat},{slng}",
                }, StopType.AT_STOP

    if parked:
        results = _search(all_stops, truck_lat, truck_lng,
                          EXTENDED_RADIUS_MILES, _is_pilot_or_loves)
        if results:
            best = results[0]
            log.info(
                f"Stop finder [PARKED]: nearest={best['name']} "
                f"{best['distance_miles']:.1f} mi  brand={best.get('brand','?')}"
            )
            return best, StopType.NEAREST
        return None, StopType.NONE

    # Moving: brand priority chain

    # 1. Pilot / Flying J within 50 mi
    results = _search(all_stops, truck_lat, truck_lng, PILOT_RADIUS_MILES, _is_pilot)
    if results:
        log.info(f"Stop finder: Pilot {results[0]['distance_miles']:.1f} mi")
        return results[0], StopType.PILOT_50

    # 2. Love's within 50 mi
    results = _search(all_stops, truck_lat, truck_lng, LOVES_RADIUS_MILES, _is_loves)
    if results:
        log.info(f"Stop finder: Love's {results[0]['distance_miles']:.1f} mi (no Pilot within 50 mi)")
        return results[0], StopType.LOVES_50

    # 3. Pilot / Flying J within 80 mi
    results = _search(all_stops, truck_lat, truck_lng, EXTENDED_RADIUS_MILES, _is_pilot)
    if results:
        log.info(f"Stop finder: Pilot {results[0]['distance_miles']:.1f} mi (extended)")
        return results[0], StopType.PILOT_80

    # 4. Love's within 80 mi
    results = _search(all_stops, truck_lat, truck_lng, EXTENDED_RADIUS_MILES, _is_loves)
    if results:
        log.info(f"Stop finder: Love's {results[0]['distance_miles']:.1f} mi (extended)")
        return results[0], StopType.LOVES_80

    log.warning(f"Stop finder: nothing within {EXTENDED_RADIUS_MILES} mi")
    return None, StopType.NONE


def is_truck_near_stop(truck_lat, truck_lng, stop_lat, stop_lng, radius_miles):
    """True if truck is within radius_miles of the stop."""
    return haversine_miles(truck_lat, truck_lng, stop_lat, stop_lng) <= radius_miles

