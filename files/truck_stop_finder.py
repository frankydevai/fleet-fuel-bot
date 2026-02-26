"""
truck_stop_finder.py  –  Find the best Pilot/Flying J stop ahead of the truck

Algorithm:
  1. Pull all diesel stops from DB
  2. Calculate distance (haversine) from truck → each stop
  3. Filter out stops > 50 miles away
  4. Calculate bearing from truck → each stop
  5. Filter out stops where bearing deviation from truck heading > MAX_HEADING_DEVIATION_DEG
     (these are stops the truck would have to turn around to reach)
  6. Score remaining stops by distance (closest wins)
  7. Return ranked list
"""

import math
from database import get_all_stops_with_diesel
from config import SEARCH_RADIUS_MILES, MAX_HEADING_DEVIATION_DEG


# ── Geo math ──────────────────────────────────────────────────────────────────

EARTH_RADIUS_MILES = 3958.8


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two GPS points in miles."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi  = math.radians(lat2 - lat1)
    d_lam  = math.radians(lng2 - lng1)

    a = math.sin(d_phi / 2) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(a))


def bearing_degrees(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Forward bearing (azimuth) from point1 to point2.
    Returns 0-360 degrees (0=North, 90=East, 180=South, 270=West).
    """
    phi1  = math.radians(lat1)
    phi2  = math.radians(lat2)
    d_lam = math.radians(lng2 - lng1)

    x = math.sin(d_lam) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - \
        math.sin(phi1) * math.cos(phi2) * math.cos(d_lam)

    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def angular_difference(a: float, b: float) -> float:
    """
    Smallest angle between two headings (0-180°).
    e.g. difference between 350° and 10° is 20°, not 340°.
    """
    diff = abs(a - b) % 360
    return diff if diff <= 180 else 360 - diff


# ── Main finder ───────────────────────────────────────────────────────────────

def find_best_stop(truck_lat: float, truck_lng: float,
                   truck_heading: float,
                   radius_miles: float = None,
                   max_heading_dev: float = None) -> list[dict]:
    """
    Find and rank truck stops ahead of the truck.

    Returns list of stop dicts sorted by distance, each augmented with:
      - distance_miles
      - bearing_to_stop
      - heading_deviation
      - google_maps_url

    Returns empty list if no suitable stops found.
    """
    radius      = radius_miles   or SEARCH_RADIUS_MILES
    max_dev     = max_heading_dev or MAX_HEADING_DEVIATION_DEG

    all_stops   = get_all_stops_with_diesel()
    candidates  = []

    for stop in all_stops:
        slat = float(stop["latitude"])
        slng = float(stop["longitude"])

        # Step 1: distance filter
        dist = haversine_miles(truck_lat, truck_lng, slat, slng)
        if dist > radius:
            continue

        # Step 2: heading filter — skip stops that are behind the truck
        bear = bearing_degrees(truck_lat, truck_lng, slat, slng)
        dev  = angular_difference(truck_heading, bear)

        if dev > max_dev:
            continue  # stop is behind or sideways — would require a U-turn

        candidates.append({
            **stop,
            "distance_miles":   round(dist, 2),
            "bearing_to_stop":  round(bear, 1),
            "heading_deviation": round(dev, 1),
            "google_maps_url":  f"https://maps.google.com/?q={slat},{slng}",
        })

    # Sort by distance (closest first)
    candidates.sort(key=lambda s: s["distance_miles"])
    return candidates


def get_best_stop(truck_lat: float, truck_lng: float,
                  truck_heading: float) -> dict | None:
    """Convenience wrapper — returns only the single best (closest ahead) stop."""
    results = find_best_stop(truck_lat, truck_lng, truck_heading)
    return results[0] if results else None


def is_truck_near_stop(truck_lat: float, truck_lng: float,
                        stop_lat: float, stop_lng: float,
                        radius_miles: float = 0.5) -> bool:
    """Returns True if truck is within radius_miles of a stop (visit detection)."""
    return haversine_miles(truck_lat, truck_lng, stop_lat, stop_lng) <= radius_miles
