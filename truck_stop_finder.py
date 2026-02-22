"""
truck_stop_finder.py  –  Locate the best diesel stop for a truck.

Search logic
────────────
MOVING (speed > 5 mph):
  Priority order ahead of the truck:
    1. Pilot / Flying J  ≤ 50 mi
    2. Love's            ≤ 50 mi
    3. Pilot / Flying J  ≤ 80 mi
    4. Love's            ≤ 80 mi

PARKED (speed ≤ 5 mph):
  Ignore brand priority — just find the single nearest stop (Pilot OR Love's).
  Heading filter is also off because a parked truck has no meaningful direction.
  This prevents sending a driver 36 miles to a Pilot when a Love's is 0.1 miles away.
"""

import math
import logging
from enum import Enum
from config import (
    MAX_HEADING_DEVIATION_DEG,
    PILOT_RADIUS_MILES,
    LOVES_RADIUS_MILES,
    EXTENDED_RADIUS_MILES,
)
from database import get_all_stops_with_diesel

log = logging.getLogger(__name__)

EARTH_RADIUS_MILES   = 3958.8
_PARKED_SPEED_MPH    = 5    # ≤ this → treat as parked
_NEARBY_ALWAYS_MILES = 5    # stops this close bypass heading filter even when moving


# ── Enum ──────────────────────────────────────────────────────────────────────

class StopType(Enum):
    AT_STOP    = "Already at a fuel stop"
    PILOT_50   = "Pilot/Flying J (within 50 mi)"
    LOVES_50   = "Love's (within 50 mi, no Pilot nearby)"
    PILOT_80   = "Pilot/Flying J (within 80 mi extended)"
    LOVES_80   = "Love's (within 80 mi extended)"
    NEAREST    = "Nearest stop (parked)"
    NONE       = "No stop found"

# Truck within this radius = it is in the stop lot, not just across the highway.
# 0.15 mi ~ 250 metres.
_AT_STOP_RADIUS_MILES = 0.15


# ── Geo math ──────────────────────────────────────────────────────────────────

def haversine_miles(lat1, lng1, lat2, lng2):
    phi1 = math.radians(lat1);  phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1);  dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(a))


def _bearing(lat1, lng1, lat2, lng2):
    phi1 = math.radians(lat1);  phi2 = math.radians(lat2)
    dlam = math.radians(lng2 - lng1)
    x = math.sin(dlam) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _angle_diff(a, b):
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


# ── Brand checks ──────────────────────────────────────────────────────────────

def _is_pilot(brand: str) -> bool:
    b = brand.lower()
    return "pilot" in b or "flying j" in b or "flyingj" in b or "one9" in b


def _is_loves(brand: str) -> bool:
    return "love" in brand.lower()


def _is_pilot_or_loves(brand: str) -> bool:
    return _is_pilot(brand) or _is_loves(brand)


# ── Core search ───────────────────────────────────────────────────────────────

def _search(stops, truck_lat, truck_lng, truck_heading, speed_mph,
            radius, brand_check):
    """
    Return matching stops within radius sorted by distance.
    Heading filter is skipped for parked trucks and very nearby stops.
    """
    parked = speed_mph <= _PARKED_SPEED_MPH
    candidates = []

    for stop in stops:
        if not brand_check(stop.get("brand", "")):
            continue

        slat = float(stop["latitude"])
        slng = float(stop["longitude"])

        dist = haversine_miles(truck_lat, truck_lng, slat, slng)
        if dist > radius:
            continue

        bear = _bearing(truck_lat, truck_lng, slat, slng)
        dev  = _angle_diff(truck_heading, bear)

        if not parked and dist > _NEARBY_ALWAYS_MILES:
            if dev > MAX_HEADING_DEVIATION_DEG:
                continue

        candidates.append({
            **stop,
            "distance_miles":    round(dist, 2),
            "bearing_to_stop":   round(bear, 1),
            "heading_deviation": round(dev, 1),
            "google_maps_url":   f"https://maps.google.com/?q={slat},{slng}",
        })

    candidates.sort(key=lambda s: s["distance_miles"])
    return candidates


# ── Public API ────────────────────────────────────────────────────────────────

def find_best_stop(truck_lat: float, truck_lng: float,
                   truck_heading: float,
                   speed_mph: float = 0) -> tuple[dict | None, StopType]:
    """
    Find the nearest diesel stop.

    PARKED: nearest Pilot or Love's wins (no brand priority, no heading filter).
    MOVING: Pilot preferred, Love's fallback, then extended 80 mi search.

    Returns (stop_dict, StopType).
    """
    all_stops = get_all_stops_with_diesel()
    parked    = speed_mph <= _PARKED_SPEED_MPH

    # ── Already at a stop? ────────────────────────────────────────────────────
    # If the truck is parked within 0.15 mi of ANY fuel stop, it is already there.
    # Don't send an alert and don't direct the driver somewhere else.
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
                    f"({dist*5280:.0f} ft away) — no alert needed"
                )
                return {
                    **stop,
                    "distance_miles":  round(dist, 3),
                    "google_maps_url": f"https://maps.google.com/?q={slat},{slng}",
                }, StopType.AT_STOP

    if parked:
        # ── Parked: pure distance wins, any brand ─────────────────────────────
        results = _search(all_stops, truck_lat, truck_lng, truck_heading,
                          speed_mph, EXTENDED_RADIUS_MILES, _is_pilot_or_loves)
        if results:
            best = results[0]
            log.info(
                f"Stop finder [PARKED]: nearest={best['name']} "
                f"{best['distance_miles']:.1f} mi  brand={best.get('brand','?')}"
            )
            return best, StopType.NEAREST
        return None, StopType.NONE

    # ── Moving: brand priority chain ──────────────────────────────────────────

    # 1. Pilot / Flying J ≤ 50 mi ahead
    results = _search(all_stops, truck_lat, truck_lng, truck_heading,
                      speed_mph, PILOT_RADIUS_MILES, _is_pilot)
    if results:
        log.info(f"Stop finder: Pilot {results[0]['distance_miles']:.1f} mi ahead")
        return results[0], StopType.PILOT_50

    # 2. Love's ≤ 50 mi ahead
    results = _search(all_stops, truck_lat, truck_lng, truck_heading,
                      speed_mph, LOVES_RADIUS_MILES, _is_loves)
    if results:
        log.info(f"Stop finder: Love's {results[0]['distance_miles']:.1f} mi ahead (no Pilot ≤50 mi)")
        return results[0], StopType.LOVES_50

    # 3. Pilot / Flying J ≤ 80 mi ahead
    results = _search(all_stops, truck_lat, truck_lng, truck_heading,
                      speed_mph, EXTENDED_RADIUS_MILES, _is_pilot)
    if results:
        log.info(f"Stop finder: Pilot {results[0]['distance_miles']:.1f} mi ahead (extended)")
        return results[0], StopType.PILOT_80

    # 4. Love's ≤ 80 mi ahead
    results = _search(all_stops, truck_lat, truck_lng, truck_heading,
                      speed_mph, EXTENDED_RADIUS_MILES, _is_loves)
    if results:
        log.info(f"Stop finder: Love's {results[0]['distance_miles']:.1f} mi ahead (extended)")
        return results[0], StopType.LOVES_80

    log.warning(f"Stop finder: nothing within {EXTENDED_RADIUS_MILES} mi")
    return None, StopType.NONE


def is_truck_near_stop(truck_lat, truck_lng, stop_lat, stop_lng, radius_miles):
    """True if truck is within radius_miles of the stop."""
    return haversine_miles(truck_lat, truck_lng, stop_lat, stop_lng) <= radius_miles
