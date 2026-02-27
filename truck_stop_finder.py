"""
truck_stop_finder.py  -  Find the nearest diesel stop for a truck.

HOW IT WORKS:
  - All Pilot, Flying J, and Love's stops loaded from SQLite DB
  - Distance calculated using GPS coordinates (haversine formula)
  - For MOVING trucks: prefer stops ahead of the truck (within 120 degree arc)
    If nothing found ahead, fall back to any direction (truck may need to turn around)
  - For PARKED trucks: pure distance, no direction filter

SEARCH PRIORITY (MOVING truck):
  1. Nearest Pilot / Flying J within 50 miles AHEAD
  2. Nearest Love's within 50 miles AHEAD
  3. Nearest Pilot / Flying J within 80 miles AHEAD
  4. Nearest Love's within 80 miles AHEAD
  -- fallback if nothing ahead --
  5. Nearest Pilot / Flying J within 80 miles ANY direction
  6. Nearest Love's within 80 miles ANY direction

PARKED truck:
  - Nearest stop any brand within 80 miles
  - If already IN a stop lot (within 0.15 mi) -> no alert needed
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
_PARKED_SPEED_MPH     = 5      # truck is parked if speed <= this
_AT_STOP_RADIUS_MILES = 0.15   # 0.15 mi ~ 250m = truck is in the lot
_AHEAD_MAX_DEGREES    = 120    # stops within this arc are considered "ahead"
                                # 120 = 60 degrees left and right of heading


# -- Result types -------------------------------------------------------------

class StopType(Enum):
    AT_STOP   = "Already at a fuel stop"
    PILOT_50  = "Pilot/Flying J within 50 miles"
    LOVES_50  = "Love's within 50 miles (no Pilot nearby)"
    PILOT_80  = "Pilot/Flying J within 80 miles (extended)"
    LOVES_80  = "Love's within 80 miles (extended)"
    NEAREST   = "Nearest stop (truck parked)"
    NONE      = "No stop found within 80 miles"


# -- Geo math -----------------------------------------------------------------

def haversine_miles(lat1, lng1, lat2, lng2):
    """Straight-line distance in miles between two GPS coordinates."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(a))


def _bearing(lat1, lng1, lat2, lng2):
    """Compass bearing in degrees from point 1 to point 2."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlam = math.radians(lng2 - lng1)
    x = math.sin(dlam) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _angle_diff(a, b):
    """Smallest angle between two bearings (0-180)."""
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


def _is_ahead(truck_heading, truck_lat, truck_lng, stop_lat, stop_lng):
    """Return True if stop is within _AHEAD_MAX_DEGREES arc of truck heading."""
    bear = _bearing(truck_lat, truck_lng, stop_lat, stop_lng)
    return _angle_diff(truck_heading, bear) <= _AHEAD_MAX_DEGREES


# -- Brand detection ----------------------------------------------------------

def _is_pilot(brand: str) -> bool:
    b = brand.lower()
    return "pilot" in b or "flying j" in b or "flyingj" in b or "one9" in b


def _is_loves(brand: str) -> bool:
    return "love" in brand.lower()


def _is_any(brand: str) -> bool:
    return _is_pilot(brand) or _is_loves(brand)


# -- Core search --------------------------------------------------------------

def _find_nearest(stops, truck_lat, truck_lng, radius_miles,
                  brand_check, truck_heading=None):
    """
    Find matching stops within radius, sorted by distance.

    If truck_heading is provided, only returns stops AHEAD of the truck
    (within _AHEAD_MAX_DEGREES arc). If no stops found ahead, returns empty list
    so caller can fall back to any-direction search.
    """
    candidates = []

    for stop in stops:
        brand = stop.get("brand", "")
        if not brand_check(brand):
            continue

        slat = float(stop["latitude"])
        slng = float(stop["longitude"])
        dist = haversine_miles(truck_lat, truck_lng, slat, slng)

        if dist > radius_miles:
            continue

        # If heading provided, skip stops behind the truck
        if truck_heading is not None:
            if not _is_ahead(truck_heading, truck_lat, truck_lng, slat, slng):
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
    Find the best diesel stop for a truck.

    For MOVING trucks: prefer stops ahead, fall back to any direction.
    For PARKED trucks: nearest stop any direction.

    Returns (stop_dict, StopType) or (None, StopType.NONE).
    """
    all_stops = get_all_stops_with_diesel()
    parked    = speed_mph <= _PARKED_SPEED_MPH

    # -------------------------------------------------------------------------
    # Already at a stop? (parked within 0.15 mi of any stop)
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
    # PARKED: nearest stop any brand, any direction
    # -------------------------------------------------------------------------
    if parked:
        results = _find_nearest(all_stops, truck_lat, truck_lng,
                                EXTENDED_RADIUS_MILES, _is_any)
        if results:
            best = results[0]
            log.info(f"Stop finder [PARKED]: {best['name']} "
                     f"{best['distance_miles']:.1f} mi "
                     f"brand={best.get('brand', '?')}")
            return best, StopType.NEAREST
        log.warning("Stop finder [PARKED]: nothing within 80 miles")
        return None, StopType.NONE

    # -------------------------------------------------------------------------
    # MOVING: brand priority, prefer ahead, fallback to any direction
    # -------------------------------------------------------------------------

    # 1. Pilot within 50 mi AHEAD
    results = _find_nearest(all_stops, truck_lat, truck_lng,
                            PILOT_RADIUS_MILES, _is_pilot, truck_heading)
    if results:
        best = results[0]
        log.info(f"Stop finder: Pilot '{best['name']}' {best['distance_miles']:.1f} mi ahead")
        return best, StopType.PILOT_50

    # 2. Love's within 50 mi AHEAD
    results = _find_nearest(all_stops, truck_lat, truck_lng,
                            LOVES_RADIUS_MILES, _is_loves, truck_heading)
    if results:
        best = results[0]
        log.info(f"Stop finder: Love's '{best['name']}' {best['distance_miles']:.1f} mi ahead")
        return best, StopType.LOVES_50

    # 3. Pilot within 80 mi AHEAD
    results = _find_nearest(all_stops, truck_lat, truck_lng,
                            EXTENDED_RADIUS_MILES, _is_pilot, truck_heading)
    if results:
        best = results[0]
        log.info(f"Stop finder: Pilot '{best['name']}' {best['distance_miles']:.1f} mi ahead (extended)")
        return best, StopType.PILOT_80

    # 4. Love's within 80 mi AHEAD
    results = _find_nearest(all_stops, truck_lat, truck_lng,
                            EXTENDED_RADIUS_MILES, _is_loves, truck_heading)
    if results:
        best = results[0]
        log.info(f"Stop finder: Love's '{best['name']}' {best['distance_miles']:.1f} mi ahead (extended)")
        return best, StopType.LOVES_80

    # -------------------------------------------------------------------------
    # FALLBACK: nothing ahead — search any direction
    # (truck may be in remote area, turning around may be necessary)
    # -------------------------------------------------------------------------
    log.warning("Stop finder: nothing ahead within 80 mi — searching any direction")

    results = _find_nearest(all_stops, truck_lat, truck_lng,
                            EXTENDED_RADIUS_MILES, _is_pilot)
    if results:
        best = results[0]
        log.info(f"Stop finder: Pilot '{best['name']}' {best['distance_miles']:.1f} mi (any direction)")
        return best, StopType.PILOT_80

    results = _find_nearest(all_stops, truck_lat, truck_lng,
                            EXTENDED_RADIUS_MILES, _is_loves)
    if results:
        best = results[0]
        log.info(f"Stop finder: Love's '{best['name']}' {best['distance_miles']:.1f} mi (any direction)")
        return best, StopType.LOVES_80

    log.warning(f"Stop finder: no Pilot or Love's within {EXTENDED_RADIUS_MILES} miles")
    return None, StopType.NONE


def is_truck_near_stop(truck_lat, truck_lng, stop_lat, stop_lng, radius_miles):
    """Return True if truck is within radius_miles of the given stop."""
    return haversine_miles(truck_lat, truck_lng, stop_lat, stop_lng) <= radius_miles
