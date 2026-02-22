"""
seed_pilot_stops.py  –  One-time script to load your Pilot/Flying J CSV into MySQL.

Expected CSV columns (flexible — mapped in COLUMN_MAP below):
  name, brand, address, city, state, zip, latitude, longitude, phone, has_diesel

Run once:
  python seed_pilot_stops.py --file pilot_stops.csv

Options:
  --file    Path to your CSV file (default: pilot_stops.csv)
  --dry-run  Parse and show first 5 rows without inserting
  --delimiter  CSV delimiter (default: comma)
"""

import argparse
import csv
import sys
from database import init_db, upsert_pilot_stop

# ── Map your CSV column names to our DB column names ─────────────────────────
# Left side = your CSV header, Right side = our DB field
# Adjust left side to match your actual CSV headers
COLUMN_MAP = {
    # CSV header           : DB field
    "name":                 "name",
    "store_name":           "name",        # alternate
    "location_name":        "name",        # alternate
    "brand":                "brand",
    "chain":                "brand",       # alternate
    "address":              "address",
    "street":               "address",     # alternate
    "street_address":       "address",     # alternate
    "city":                 "city",
    "state":                "state",
    "st":                   "state",       # alternate
    "zip":                  "zip",
    "zip_code":             "zip",         # alternate
    "postal_code":          "zip",         # alternate
    "latitude":             "latitude",
    "lat":                  "latitude",    # alternate
    "longitude":            "longitude",
    "lng":                  "longitude",   # alternate
    "lon":                  "longitude",   # alternate
    "long":                 "longitude",   # alternate
    "phone":                "phone",
    "phone_number":         "phone",       # alternate
    "has_diesel":           "has_diesel",
    "diesel":               "has_diesel",  # alternate
}

REQUIRED_DB_FIELDS = {"name", "latitude", "longitude"}

DEFAULTS = {
    "brand":      "Pilot",
    "address":    "",
    "city":       "",
    "state":      "",
    "zip":        "",
    "phone":      "",
    "has_diesel": 1,
}


def parse_bool(val: str) -> int:
    """Convert various truthy strings to 1/0 for has_diesel."""
    if isinstance(val, (int, float)):
        return 1 if val else 0
    v = str(val).strip().lower()
    return 1 if v in ("1", "true", "yes", "y", "t") else 0


def map_row(raw_row: dict) -> dict | None:
    """Map a raw CSV row dict to DB fields. Returns None if required fields missing."""
    mapped = dict(DEFAULTS)
    for csv_col, value in raw_row.items():
        db_field = COLUMN_MAP.get(csv_col.strip().lower())
        if db_field and value is not None:
            mapped[db_field] = value.strip() if isinstance(value, str) else value

    # Validate required fields
    for field in REQUIRED_DB_FIELDS:
        if not mapped.get(field):
            return None

    # Type coerce
    try:
        mapped["latitude"]  = float(mapped["latitude"])
        mapped["longitude"] = float(mapped["longitude"])
    except (ValueError, TypeError):
        return None

    mapped["has_diesel"] = parse_bool(mapped.get("has_diesel", 1))

    return mapped


def seed(filepath: str, dry_run: bool, delimiter: str):
    if not dry_run:
        print("🔧  Initializing database...")
        init_db()

    inserted = 0
    skipped  = 0
    errors   = 0

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        rows   = list(reader)

    print(f"📄  Read {len(rows)} rows from {filepath}")
    if rows:
        print(f"   CSV headers: {list(rows[0].keys())}")

    if dry_run:
        print("\n🔍  DRY RUN — first 5 mapped rows:")
        for raw in rows[:5]:
            mapped = map_row(raw)
            print(f"   {'✅' if mapped else '❌'}  {mapped or raw}")
        return

    for i, raw in enumerate(rows, 1):
        mapped = map_row(raw)
        if mapped is None:
            skipped += 1
            if skipped <= 5:
                print(f"⚠️   Row {i} skipped (missing required fields): {raw}")
            continue
        try:
            upsert_pilot_stop(mapped)
            inserted += 1
        except Exception as e:
            errors += 1
            print(f"❌   Row {i} error: {e}  →  {mapped}")

        if i % 100 == 0:
            print(f"   ... {i}/{len(rows)} processed")

    print(f"\n✅  Done!  Inserted/updated: {inserted}  |  Skipped: {skipped}  |  Errors: {errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Pilot/Flying J stops from CSV")
    parser.add_argument("--file",      default="pilot_stops.csv", help="Path to CSV file")
    parser.add_argument("--dry-run",   action="store_true",       help="Parse only, no DB writes")
    parser.add_argument("--delimiter", default=",",               help="CSV delimiter (default: comma)")
    args = parser.parse_args()

    try:
        seed(args.file, args.dry_run, args.delimiter)
    except FileNotFoundError:
        print(f"❌  File not found: {args.file}")
        sys.exit(1)
