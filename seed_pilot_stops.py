"""
seed_pilot_stops.py  -  Load Pilot/Flying J and Love's stops into SQLite DB.

Supports both CSV formats:

PILOT / FLYING J headers:
  Store #, Name, Address, City, State, Zip Code, Interstate,
  Latitude, Longitude, Phone Number, Parking Spaces Count, ...

LOVE'S headers:
  store_name, StoreNumber, State, City, Address, HighwayOrExit,
  Zip, Latitude, Longitude, Diesel, Phone, ...

Run:
  python seed_pilot_stops.py --file pilot_stops.csv --brand Pilot
  python seed_pilot_stops.py --file loves_stops.csv --brand Loves
  python seed_pilot_stops.py --file all_stops.csv          (auto-detect)

Options:
  --file      Path to CSV file (required)
  --brand     Force brand: Pilot or Loves (optional, auto-detected if not set)
  --dry-run   Preview first 5 rows without inserting
  --delimiter CSV delimiter (default: comma)
"""

import argparse
import csv
import sys
from database import init_db, upsert_pilot_stop


# -- Column maps --------------------------------------------------------------

# Pilot / Flying J CSV column -> DB field
PILOT_COLUMN_MAP = {
    "store #":              "name",       # will combine with Name below
    "name":                 "name",
    "address":              "address",
    "city":                 "city",
    "state":                "state",
    "zip code":             "zip",
    "zip":                  "zip",
    "latitude":             "latitude",
    "longitude":            "longitude",
    "phone number":         "phone",
    "phone":                "phone",
}

# Love's CSV column -> DB field
LOVES_COLUMN_MAP = {
    "store_name":           "name",
    "storenumber":          "store_number",
    "address":              "address",
    "city":                 "city",
    "state":                "state",
    "zip":                  "zip",
    "latitude":             "latitude",
    "longitude":            "longitude",
    "phone":                "phone",
    "diesel":               "has_diesel",  # 1/0 or Y/N
}

# Generic fallback (works for any CSV with standard headers)
GENERIC_COLUMN_MAP = {
    "name":                 "name",
    "store_name":           "name",
    "location_name":        "name",
    "brand":                "brand",
    "chain":                "brand",
    "address":              "address",
    "street":               "address",
    "city":                 "city",
    "state":                "state",
    "st":                   "state",
    "zip":                  "zip",
    "zip code":             "zip",
    "zip_code":             "zip",
    "postal_code":          "zip",
    "latitude":             "latitude",
    "lat":                  "latitude",
    "longitude":            "longitude",
    "lng":                  "longitude",
    "lon":                  "longitude",
    "phone":                "phone",
    "phone number":         "phone",
    "phone_number":         "phone",
    "has_diesel":           "has_diesel",
    "diesel":               "has_diesel",
}

REQUIRED = {"name", "latitude", "longitude"}

DEFAULTS = {
    "brand":      "Pilot",
    "address":    "",
    "city":       "",
    "state":      "",
    "zip":        "",
    "phone":      "",
    "has_diesel": 1,
}


# -- Helpers ------------------------------------------------------------------

def _parse_bool(val) -> int:
    if isinstance(val, (int, float)):
        return 1 if val else 0
    v = str(val).strip().lower()
    return 1 if v in ("1", "true", "yes", "y", "t", "x") else 0


def _detect_format(headers):
    """Auto-detect CSV format from headers."""
    lowered = [h.strip().lower() for h in headers]
    if "store_name" in lowered or "storenumber" in lowered:
        return "loves"
    if "store #" in lowered or "fuel lane count" in lowered:
        return "pilot"
    return "generic"


def _map_pilot_row(raw: dict, store_number: str = "") -> dict | None:
    """Map a Pilot/Flying J CSV row to DB fields."""
    mapped = dict(DEFAULTS)
    mapped["brand"] = "Pilot"

    for col, val in raw.items():
        key = col.strip().lower()
        db_field = PILOT_COLUMN_MAP.get(key)
        if db_field and val:
            mapped[db_field] = str(val).strip()

    # Build name from Store # + Name
    store_num = str(raw.get("Store #", "") or "").strip()
    store_name = str(raw.get("Name", "") or "").strip()
    if store_num and store_name:
        mapped["name"] = f"{store_name} #{store_num}"
    elif store_name:
        mapped["name"] = store_name
    elif store_num:
        mapped["name"] = f"Pilot #{store_num}"

    return _validate_and_coerce(mapped)


def _map_loves_row(raw: dict) -> dict | None:
    """Map a Love's CSV row to DB fields."""
    mapped = dict(DEFAULTS)
    mapped["brand"] = "Love's"

    for col, val in raw.items():
        key = col.strip().lower()
        db_field = LOVES_COLUMN_MAP.get(key)
        if db_field and val is not None and str(val).strip():
            mapped[db_field] = str(val).strip()

    # Build name from store_name + StoreNumber
    store_name = str(raw.get("store_name", "") or "").strip()
    store_num  = str(raw.get("StoreNumber", "") or "").strip()
    if store_name and store_num:
        mapped["name"] = f"{store_name} #{store_num}"
    elif store_name:
        mapped["name"] = store_name
    elif store_num:
        mapped["name"] = f"Love's #{store_num}"

    # Love's has a Diesel column - check if this stop has diesel
    diesel_val = raw.get("Diesel", "1")
    mapped["has_diesel"] = _parse_bool(diesel_val) if diesel_val else 1

    return _validate_and_coerce(mapped)


def _map_generic_row(raw: dict, default_brand: str = "Pilot") -> dict | None:
    """Map a generic CSV row using flexible column name matching."""
    mapped = dict(DEFAULTS)
    mapped["brand"] = default_brand

    for col, val in raw.items():
        key = col.strip().lower()
        db_field = GENERIC_COLUMN_MAP.get(key)
        if db_field and val is not None and str(val).strip():
            mapped[db_field] = str(val).strip()

    return _validate_and_coerce(mapped)


def _validate_and_coerce(mapped: dict) -> dict | None:
    """Validate required fields and coerce types. Returns None if invalid."""
    for field in REQUIRED:
        if not mapped.get(field):
            return None
    try:
        mapped["latitude"]  = float(mapped["latitude"])
        mapped["longitude"] = float(mapped["longitude"])
    except (ValueError, TypeError):
        return None

    if not isinstance(mapped.get("has_diesel"), int):
        mapped["has_diesel"] = _parse_bool(mapped.get("has_diesel", 1))

    return mapped


# -- Main seeder --------------------------------------------------------------

def seed(filepath: str, brand_override: str, dry_run: bool, delimiter: str):
    if not dry_run:
        print("Initializing database...")
        init_db()

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        rows   = list(reader)

    if not rows:
        print("No rows found in CSV.")
        return

    headers    = list(rows[0].keys())
    fmt        = _detect_format(headers) if not brand_override else brand_override.lower()
    print(f"CSV file:  {filepath}")
    print(f"Format:    {fmt}")
    print(f"Rows:      {len(rows)}")
    print(f"Headers:   {headers}")

    if dry_run:
        print("\nDRY RUN - first 5 rows:")
        for raw in rows[:5]:
            if fmt == "loves":
                mapped = _map_loves_row(raw)
            elif fmt in ("pilot", "flying j"):
                mapped = _map_pilot_row(raw)
            else:
                mapped = _map_generic_row(raw, brand_override or "Pilot")
            status = "OK" if mapped else "SKIP"
            print(f"  [{status}] {mapped or raw}")
        return

    inserted = 0
    skipped  = 0
    errors   = 0

    for i, raw in enumerate(rows, 1):
        try:
            if fmt == "loves":
                mapped = _map_loves_row(raw)
            elif fmt in ("pilot", "flying j"):
                mapped = _map_pilot_row(raw)
            else:
                mapped = _map_generic_row(raw, brand_override or "Pilot")

            if mapped is None:
                skipped += 1
                if skipped <= 3:
                    print(f"  Row {i} skipped (missing name/lat/lng): {raw}")
                continue

            upsert_pilot_stop(mapped)
            inserted += 1

        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  Row {i} error: {e}")

        if i % 200 == 0:
            print(f"  ... {i}/{len(rows)} processed")

    print(f"\nDone!")
    print(f"  Inserted/updated : {inserted}")
    print(f"  Skipped          : {skipped}")
    print(f"  Errors           : {errors}")


# -- Entry point --------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed truck stops from CSV into SQLite")
    parser.add_argument("--file",      required=True,  help="Path to CSV file")
    parser.add_argument("--brand",     default="",     help="Force brand: Pilot or Loves (auto-detected if not set)")
    parser.add_argument("--dry-run",   action="store_true", help="Preview only, no DB writes")
    parser.add_argument("--delimiter", default=",",    help="CSV delimiter (default: comma)")
    args = parser.parse_args()

    try:
        seed(args.file, args.brand, args.dry_run, args.delimiter)
    except FileNotFoundError:
        print(f"File not found: {args.file}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
