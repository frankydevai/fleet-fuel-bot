"""
database.py  -  SQLite connection + schema management

Database file is stored at DATA_DIR/fleet.db
Set DATA_DIR environment variable to persist across restarts (e.g. /data on Railway).
Defaults to current directory if not set.

Tables:
  pilot_stops      - seeded from your CSV
  fuel_alerts      - one row per low-fuel event
  stop_assignments - which stop was assigned to which alert
  stop_flags       - VISITED / SKIPPED / PENDING tracking
  truck_states     - current state of each truck (for persistence)
"""

import sqlite3
import os
from config import DATA_DIR
import logging
from contextlib import contextmanager
from datetime import datetime

log = logging.getLogger(__name__)

# Store DB file in DATA_DIR (Railway persistent volume) or current dir

DB_PATH  = os.path.join(DATA_DIR, "fleet.db")


def get_connection():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row   # rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_cursor():
    """Yields a cursor; commits on success, rolls back on error."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# -- Schema -------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pilot_stops (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    brand       TEXT    NOT NULL DEFAULT 'Pilot',
    address     TEXT,
    city        TEXT,
    state       TEXT,
    zip         TEXT,
    latitude    REAL    NOT NULL,
    longitude   REAL    NOT NULL,
    phone       TEXT,
    has_diesel  INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pilot_stops_lat_lng ON pilot_stops (latitude, longitude);

CREATE TABLE IF NOT EXISTS fuel_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id      TEXT    NOT NULL,
    vehicle_name    TEXT,
    driver_name     TEXT,
    fuel_pct        REAL    NOT NULL,
    latitude        REAL    NOT NULL,
    longitude       REAL    NOT NULL,
    heading         REAL,
    speed_mph       REAL,
    alert_sent_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    telegram_msg_id INTEGER,
    status          TEXT    NOT NULL DEFAULT 'open'
);

CREATE INDEX IF NOT EXISTS idx_fuel_alerts_vehicle ON fuel_alerts (vehicle_id);
CREATE INDEX IF NOT EXISTS idx_fuel_alerts_status  ON fuel_alerts (status);

CREATE TABLE IF NOT EXISTS stop_assignments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id        INTEGER NOT NULL,
    stop_id         INTEGER NOT NULL,
    distance_miles  REAL    NOT NULL,
    assigned_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (alert_id) REFERENCES fuel_alerts(id),
    FOREIGN KEY (stop_id)  REFERENCES pilot_stops(id)
);

CREATE TABLE IF NOT EXISTS stop_flags (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id          INTEGER NOT NULL,
    vehicle_id        TEXT    NOT NULL,
    stop_id           INTEGER NOT NULL,
    flag              TEXT    NOT NULL DEFAULT 'pending',
    flagged_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    skip_alert_msg_id INTEGER,
    FOREIGN KEY (alert_id) REFERENCES fuel_alerts(id),
    FOREIGN KEY (stop_id)  REFERENCES pilot_stops(id)
);

CREATE INDEX IF NOT EXISTS idx_stop_flags_vehicle ON stop_flags (vehicle_id, flag);

CREATE TABLE IF NOT EXISTS truck_states (
    vehicle_id              TEXT PRIMARY KEY,
    vehicle_name            TEXT,
    driver_name             TEXT,
    state                   TEXT    NOT NULL DEFAULT 'UNKNOWN',
    fuel_pct                REAL,
    latitude                REAL,
    longitude               REAL,
    speed_mph               REAL,
    heading                 REAL,
    next_poll               TEXT    NOT NULL,
    parked_since            TEXT,
    alert_sent              INTEGER DEFAULT 0,
    overnight_alert_sent    INTEGER DEFAULT 0,
    open_alert_id           INTEGER,
    assigned_stop_id        INTEGER,
    assigned_stop_name      TEXT,
    assigned_stop_lat       REAL,
    assigned_stop_lng       REAL,
    assignment_time         TEXT,
    in_yard                 INTEGER DEFAULT 0,
    yard_name               TEXT,
    sleeping                INTEGER DEFAULT 0,
    fuel_when_parked        REAL,
    last_updated            TEXT    DEFAULT (datetime('now'))
);
"""


def init_db():
    """Create all tables if they don't exist."""
    log.info(f"Using database: {DB_PATH}")
    conn = get_connection()
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    print("✅  Database schema ready.")


# -- Helpers ------------------------------------------------------------------

def _row_to_dict(row):
    """Convert sqlite3.Row to plain dict."""
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows):
    return [dict(r) for r in rows]


def _dt(val):
    """Parse datetime string from SQLite back to datetime object."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(val)
    except Exception:
        return None


def _str_dt(val):
    """Convert datetime to ISO string for storage."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


# -- pilot_stops --------------------------------------------------------------

def upsert_pilot_stop(row: dict) -> int:
    """Insert or update a stop from CSV seed. Returns stop id."""
    sql = """
        INSERT INTO pilot_stops
            (name, brand, address, city, state, zip, latitude, longitude, phone, has_diesel)
        VALUES
            (:name, :brand, :address, :city, :state, :zip,
             :latitude, :longitude, :phone, :has_diesel)
        ON CONFLICT(rowid) DO UPDATE SET
            name=excluded.name, address=excluded.address,
            latitude=excluded.latitude, longitude=excluded.longitude
    """
    with db_cursor() as cur:
        cur.execute(sql, row)
        return cur.lastrowid


def get_all_stops_with_diesel() -> list:
    """Return all stops that have diesel fuel."""
    with db_cursor() as cur:
        cur.execute("SELECT * FROM pilot_stops WHERE has_diesel = 1")
        return _rows_to_dicts(cur.fetchall())


def get_stop_by_id(stop_id: int) -> dict | None:
    """Get stop details by ID."""
    with db_cursor() as cur:
        cur.execute("SELECT * FROM pilot_stops WHERE id = ?", (stop_id,))
        return _row_to_dict(cur.fetchone())


# -- fuel_alerts --------------------------------------------------------------

def create_fuel_alert(vehicle_id, vehicle_name, driver_name,
                      fuel_pct, lat, lng, heading, speed_mph) -> int:
    sql = """
        INSERT INTO fuel_alerts
            (vehicle_id, vehicle_name, driver_name, fuel_pct,
             latitude, longitude, heading, speed_mph)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    with db_cursor() as cur:
        cur.execute(sql, (vehicle_id, vehicle_name, driver_name,
                          fuel_pct, lat, lng, heading, speed_mph))
        return cur.lastrowid


def update_alert_telegram_msg(alert_id: int, msg_id: int):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE fuel_alerts SET telegram_msg_id=? WHERE id=?",
            (msg_id, alert_id)
        )


def resolve_alert(alert_id: int):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE fuel_alerts SET status='resolved' WHERE id=?",
            (alert_id,)
        )


def mark_alert_skipped(alert_id: int):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE fuel_alerts SET status='skipped' WHERE id=?",
            (alert_id,)
        )


# -- stop_assignments ---------------------------------------------------------

def create_stop_assignment(alert_id: int, stop_id: int, distance_miles: float) -> int:
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO stop_assignments (alert_id, stop_id, distance_miles) VALUES (?, ?, ?)",
            (alert_id, stop_id, distance_miles)
        )
        return cur.lastrowid


# -- stop_flags ---------------------------------------------------------------

def create_pending_flag(alert_id: int, vehicle_id: str, stop_id: int) -> int:
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO stop_flags (alert_id, vehicle_id, stop_id, flag) VALUES (?, ?, ?, 'pending')",
            (alert_id, vehicle_id, stop_id)
        )
        return cur.lastrowid


def mark_flag_visited(flag_id: int):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE stop_flags SET flag='visited', flagged_at=datetime('now') WHERE id=?",
            (flag_id,)
        )


def mark_flag_skipped(flag_id: int, skip_msg_id: int = None):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE stop_flags SET flag='skipped', flagged_at=datetime('now'), skip_alert_msg_id=? WHERE id=?",
            (skip_msg_id, flag_id)
        )


def get_pending_flag_by_alert(alert_id: int) -> dict | None:
    with db_cursor() as cur:
        cur.execute(
            """SELECT sf.*, ps.name AS stop_name,
                      ps.latitude AS stop_lat, ps.longitude AS stop_lng
               FROM stop_flags sf
               JOIN pilot_stops ps ON sf.stop_id = ps.id
               WHERE sf.alert_id=? AND sf.flag='pending'
               LIMIT 1""",
            (alert_id,)
        )
        return _row_to_dict(cur.fetchone())


def get_pending_flags_for_vehicle(vehicle_id: str) -> list:
    with db_cursor() as cur:
        cur.execute(
            """SELECT sf.*, ps.name AS stop_name,
                      ps.latitude AS stop_lat, ps.longitude AS stop_lng,
                      fa.telegram_msg_id
               FROM stop_flags sf
               JOIN pilot_stops ps ON sf.stop_id = ps.id
               JOIN fuel_alerts fa ON sf.alert_id = fa.id
               WHERE sf.vehicle_id=? AND sf.flag='pending'
               ORDER BY sf.flagged_at ASC""",
            (vehicle_id,)
        )
        return _rows_to_dicts(cur.fetchall())


# -- truck_states -------------------------------------------------------------

def load_all_truck_states() -> dict:
    """Load all truck states from DB. Returns {vehicle_id: state_dict}"""
    with db_cursor() as cur:
        cur.execute("SELECT * FROM truck_states")
        rows = cur.fetchall()

    states = {}
    for row in rows:
        r = dict(row)
        vid = r["vehicle_id"]
        states[vid] = {
            "vehicle_id":           vid,
            "vehicle_name":         r["vehicle_name"],
            "driver_name":          r["driver_name"],
            "state":                r["state"],
            "fuel_pct":             r["fuel_pct"],
            "lat":                  r["latitude"],
            "lng":                  r["longitude"],
            "speed_mph":            r["speed_mph"],
            "heading":              r["heading"],
            "next_poll":            _dt(r["next_poll"]),
            "parked_since":         _dt(r["parked_since"]),
            "alert_sent":           bool(r["alert_sent"]),
            "overnight_alert_sent": bool(r["overnight_alert_sent"]),
            "open_alert_id":        r["open_alert_id"],
            "assigned_stop_id":     r["assigned_stop_id"],
            "assigned_stop_name":   r["assigned_stop_name"],
            "assigned_stop_lat":    r["assigned_stop_lat"],
            "assigned_stop_lng":    r["assigned_stop_lng"],
            "assignment_time":      _dt(r["assignment_time"]),
            "in_yard":              bool(r["in_yard"]),
            "yard_name":            r["yard_name"],
            "sleeping":             bool(r.get("sleeping", 0)),
            "fuel_when_parked":     r.get("fuel_when_parked"),
        }
    return states


def save_truck_state(state: dict):
    """Upsert a single truck state to DB."""
    sql = """
        INSERT INTO truck_states
            (vehicle_id, vehicle_name, driver_name, state, fuel_pct,
             latitude, longitude, speed_mph, heading, next_poll, parked_since,
             alert_sent, overnight_alert_sent, open_alert_id,
             assigned_stop_id, assigned_stop_name, assigned_stop_lat, assigned_stop_lng,
             assignment_time, in_yard, yard_name, sleeping, fuel_when_parked,
             last_updated)
        VALUES
            (?, ?, ?, ?, ?,
             ?, ?, ?, ?, ?, ?,
             ?, ?, ?,
             ?, ?, ?, ?,
             ?, ?, ?, ?, ?,
             datetime('now'))
        ON CONFLICT(vehicle_id) DO UPDATE SET
            vehicle_name=excluded.vehicle_name,
            driver_name=excluded.driver_name,
            state=excluded.state,
            fuel_pct=excluded.fuel_pct,
            latitude=excluded.latitude,
            longitude=excluded.longitude,
            speed_mph=excluded.speed_mph,
            heading=excluded.heading,
            next_poll=excluded.next_poll,
            parked_since=excluded.parked_since,
            alert_sent=excluded.alert_sent,
            overnight_alert_sent=excluded.overnight_alert_sent,
            open_alert_id=excluded.open_alert_id,
            assigned_stop_id=excluded.assigned_stop_id,
            assigned_stop_name=excluded.assigned_stop_name,
            assigned_stop_lat=excluded.assigned_stop_lat,
            assigned_stop_lng=excluded.assigned_stop_lng,
            assignment_time=excluded.assignment_time,
            in_yard=excluded.in_yard,
            yard_name=excluded.yard_name,
            sleeping=excluded.sleeping,
            fuel_when_parked=excluded.fuel_when_parked,
            last_updated=datetime('now')
    """
    with db_cursor() as cur:
        cur.execute(sql, (
            state["vehicle_id"], state["vehicle_name"], state["driver_name"],
            state["state"], state["fuel_pct"],
            state["lat"], state["lng"], state["speed_mph"], state["heading"],
            _str_dt(state["next_poll"]), _str_dt(state.get("parked_since")),
            int(state["alert_sent"]), int(state["overnight_alert_sent"]),
            state["open_alert_id"],
            state["assigned_stop_id"], state["assigned_stop_name"],
            state["assigned_stop_lat"], state["assigned_stop_lng"],
            _str_dt(state.get("assignment_time")),
            int(state["in_yard"]), state["yard_name"],
            int(state.get("sleeping", False)),
            state.get("fuel_when_parked"),
        ))


def save_all_truck_states(states: dict):
    """Batch save all truck states to DB."""
    for state in states.values():
        save_truck_state(state)


def reset_truck_states():
    """Clear all truck states for a fresh start."""
    with db_cursor() as cur:
        cur.execute("DELETE FROM truck_states")
    print("✅  Truck states reset.")


def get_open_alert_for_vehicle(vehicle_id: str) -> dict | None:
    with db_cursor() as cur:
        cur.execute(
            """SELECT * FROM fuel_alerts
               WHERE vehicle_id=? AND status='open'
               ORDER BY alert_sent_at DESC LIMIT 1""",
            (vehicle_id,)
        )
        return _row_to_dict(cur.fetchone())
