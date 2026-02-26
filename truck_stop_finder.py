"""
truck_stop_finder.py  -  Find the nearest diesel stop for a truck.

HOW IT WORKS:
  - All Pilot, Flying J, and Love's stops are loaded from the local SQLite DB
  - Distance is calculated using GPS coordinates (haversine formula)
  - No Google Maps API, no internet search needed

SEARCH PRIORITY (MOVING truck):
  1. Nearest Pilot / Flying J within 50 miles   -> best option
  2. Nearest Love's within 50 miles             -> no Pilot nearby
  3. Nearest Pilot / Flying J within 80 miles   -> extended search
  4. Nearest Love's within 80 miles             -> last resort
  5. Nothing found                              -> dispatcher alert

PARKED truck:
  - Find nearest stop of any brand within 80 miles (no brand priority)
  - If truck is already IN a stop lot (within 0.15 mi) -> no alert needed
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
_PARKED_SPEED_MPH     = 5     # truck is parked if speed <= this
_AT_STOP_RADIUS_MILES = 0.15  # 0.15 mi ~ 250m = truck is in the lot


# -- Result types -------------------------------------------------------------

class StopType(Enum):
    AT_STOP  = "Already at a fuel stop"
    PILOT_50 = "Pilot/Flying J within 50 miles"
    LOVES_50 = "Love's within 50 miles (no Pilot nearby)"
    PILOT_80 = "Pilot/Flying J within 80 miles (extended)"
    LOVES_80 = "Love's within 80 miles (extended)"
    NEAREST  = "Nearest stop (truck parked)"
    NONE     = "No stop found within 80 miles"


# -- Distance calculation -----------------------------------------------------

def haversine_miles(lat1, lng1, lat2, lng2):
    """
    Calculate straight-line distance in miles between two GPS coordinates.
    Uses the haversine formula — accurate for road distances up to ~100 miles.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(a))


# -- Brand detection ----------------------------------------------------------

def _is_pilot(brand: str) -> bool:
    """True if stop is a Pilot or Flying J location."""
    b = brand.lower()
    return "pilot" in b or "flying j" in b or "flyingj" in b or "one9" in b


def _is_loves(brand: str) -> bool:
    """True if stop is a Love's location."""
    return "love" in brand.lower()


def _is_any(brand: str) -> bool:
    """True if stop is Pilot, Flying J, or Love's."""
    return _is_pilot(brand) or _is_loves(brand)


# -- Core search --------------------------------------------------------------

def _find_nearest(stops, truck_lat, truck_lng, radius_miles, brand_check):
    """
    Search all stops and return those matching brand_check within radius_miles,
    sorted by distance (nearest first).

    Steps:
      1. Loop through every stop in the database
      2. Skip if brand doesn't match (e.g. skip Love's when looking for Pilot)
      3. Calculate distance from truck to stop using GPS coordinates
      4. Skip if distance is beyond the search radius
      5. Sort remaining candidates by distance
      6. Return the sorted list (first item = nearest)
    """
    candidates = []

    for stop in stops:
        brand = stop.get("brand", "")

        # Step 2: brand filter
        if not brand_check(brand):
            continue

        # Step 3: calculate distance
        stop_lat = float(stop["latitude"])
        stop_lng = float(stop["longitude"])
        dist     = haversine_miles(truck_lat, truck_lng, stop_lat, stop_lng)

        # Step 4: radius filter
        if dist > radius_miles:
            continue

        candidates.append({
            **stop,
            "distance_miles":  round(dist, 2),
            "google_maps_url": f"https://maps.google.com/?q={stop_lat},{stop_lng}",
        })

    # Step 5: sort by distance, nearest first
    candidates.sort(key=lambda s: s["distance_miles"])
    return candidates


# -- Public API ---------------------------------------------------------------

def find_best_stop(truck_lat: float, truck_lng: float,
                   truck_heading: float,
                   speed_mph: float = 0) -> tuple[dict | None, StopType]:
    """
    Find the best diesel stop for a truck based on its current position.

    Args:
        truck_lat:     Truck's current latitude
        truck_lng:     Truck's current longitude
        truck_heading: Truck's current heading in degrees (not used for filtering,
                       kept for API compatibility)
        speed_mph:     Truck's current speed

    Returns:
        (stop_dict, StopType) where stop_dict contains stop details + distance_miles
        or (None, StopType.NONE) if no stop found
    """
    all_stops = get_all_stops_with_diesel()
    parked    = speed_mph <= _PARKED_SPEED_MPH

    log.debug(f"Stop finder: {len(all_stops)} stops in DB, "
              f"truck at ({truck_lat:.4f}, {truck_lng:.4f}), "
              f"speed={speed_mph:.0f} mph, parked={parked}")

    # -------------------------------------------------------------------------
    # Check: is truck already parked IN a stop lot?
    # If within 0.15 miles of any stop -> no alert needed
    # -------------------------------------------------------------------------
    if parked:
        for stop in all_stops:
            if not _is_any(stop.get("brand", "")):
                continue
            slat = float(stop["latitude"])
            slng = float(stop["longitude"])
            dist = haversine_miles(truck_lat, truck_lng, slat, slng)
            if dist <= _AT_STOP_RADIUS_MILES:
                log.info(
                    f"Stop finder: truck already at {stop['name']} "
                    f"({dist * 5280:.0f} ft away) - no alert needed"
                )
                return {
                    **stop,
                    "distance_miles":  round(dist, 3),
                    "google_maps_url": f"https://maps.google.com/?q={slat},{slng}",
                }, StopType.AT_STOP

    # -------------------------------------------------------------------------
    # PARKED truck: find nearest stop of any brand, no priority
    # -------------------------------------------------------------------------
    if parked:
        results = _find_nearest(all_stops, truck_lat, truck_lng,
                                EXTENDED_RADIUS_MILES, _is_any)
        if results:
            best = results[0]
            log.info(
                f"Stop finder [PARKED]: {best['name']} "
                f"{best['distance_miles']:.1f} mi "
                f"brand={best.get('brand', '?')}"
            )
            return best, StopType.NEAREST
        log.warning("Stop finder [PARKED]: nothing within 80 miles")
        return None, StopType.NONE

    # -------------------------------------------------------------------------
    # MOVING truck: brand priority chain
    # -------------------------------------------------------------------------

    # Priority 1: Pilot / Flying J within 50 miles
    results = _find_nearest(all_stops, truck_lat, truck_lng,
                            PILOT_RADIUS_MILES, _is_pilot)
    if results:
        best = results[0]
        log.info(f"Stop finder: Pilot/Flying J '{best['name']}' "
                 f"{best['distance_miles']:.1f} mi")
        return best, StopType.PILOT_50

    # Priority 2: Love's within 50 miles (no Pilot found)
    results = _find_nearest(all_stops, truck_lat, truck_lng,
                            LOVES_RADIUS_MILES, _is_loves)
    if results:
        best = results[0]
        log.info(f"Stop finder: Love's '{best['name']}' "
                 f"{best['distance_miles']:.1f} mi (no Pilot within 50 mi)")
        return best, StopType.LOVES_50

    # Priority 3: Pilot / Flying J within 80 miles (extended search)
    results = _find_nearest(all_stops, truck_lat, truck_lng,
                            EXTENDED_RADIUS_MILES, _is_pilot)
    if results:
        best = results[0]
        log.info(f"Stop finder: Pilot/Flying J '{best['name']}' "
                 f"{best['distance_miles']:.1f} mi (extended 80 mi search)")
        return best, StopType.PILOT_80

    # Priority 4: Love's within 80 miles (last resort)
    results = _find_nearest(all_stops, truck_lat, truck_lng,
                            EXTENDED_RADIUS_MILES, _is_loves)
    if results:
        best = results[0]
        log.info(f"Stop finder: Love's '{best['name']}' "
                 f"{best['distance_miles']:.1f} mi (extended 80 mi search)")
        return best, StopType.LOVES_80

    # Nothing found within 80 miles
    log.warning(f"Stop finder: no Pilot or Love's within {EXTENDED_RADIUS_MILES} miles")
    return None, StopType.NONE


def is_truck_near_stop(truck_lat, truck_lng, stop_lat, stop_lng, radius_miles):
    """Return True if truck is within radius_miles of the given stop coordinates."""
    return haversine_miles(truck_lat, truck_lng, stop_lat, stop_lng) <= radius_miles
