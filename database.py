"""
database.py  –  MySQL connection + schema management
Tables:
  pilot_stops       – seeded from your CSV
  fuel_alerts       – one row per low-fuel event
  stop_assignments  – which stop was assigned to which alert
  stop_flags        – VISITED / SKIPPED / PENDING tracking
  truck_states      – current state of each truck (for persistence)

FIX (2026-02-25):
  truck_states table was missing 'sleeping' and 'fuel_when_parked' columns.
  These are used by state_machine.py to track parked/sleeping trucks and detect
  refueling. Without them, every Cloud Run restart would lose sleeping state,
  causing duplicate alerts on parked trucks.

  load_all_truck_states() and save_truck_state() updated to include these fields.
"""

import pymysql
import pymysql.cursors
from contextlib import contextmanager
from datetime import datetime
import json
from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME


# ── Connection pool (simple) ──────────────────────────────────────────────────

def get_connection():
    ssl = {"ssl": {"ssl_disabled": False}} if not DB_HOST.startswith("/") else {}
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        ssl_verify_cert=False,
        **ssl,
    )


@contextmanager
def db_cursor():
    """Yields a cursor; commits on success, rolls back on error."""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema creation ───────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pilot_stops (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(255)   NOT NULL,
    brand       VARCHAR(50)    NOT NULL DEFAULT 'Pilot',
    address     VARCHAR(512),
    city        VARCHAR(100),
    state       CHAR(50),
    zip         VARCHAR(10),
    latitude    DOUBLE         NOT NULL,
    longitude   DOUBLE         NOT NULL,
    phone       VARCHAR(20),
    has_diesel  TINYINT(1)     NOT NULL DEFAULT 1,
    created_at  DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_lat_lng (latitude, longitude)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS fuel_alerts (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    vehicle_id      VARCHAR(100)   NOT NULL,
    vehicle_name    VARCHAR(255),
    driver_name     VARCHAR(255),
    fuel_pct        FLOAT          NOT NULL,
    latitude        DOUBLE         NOT NULL,
    longitude       DOUBLE         NOT NULL,
    heading         FLOAT,
    speed_mph       FLOAT,
    alert_sent_at   DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    telegram_msg_id BIGINT,
    status          ENUM('open','resolved','skipped') NOT NULL DEFAULT 'open',
    INDEX idx_vehicle (vehicle_id),
    INDEX idx_status  (status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS stop_assignments (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    alert_id        INT            NOT NULL,
    stop_id         INT            NOT NULL,
    distance_miles  FLOAT          NOT NULL,
    assigned_at     DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (alert_id) REFERENCES fuel_alerts(id),
    FOREIGN KEY (stop_id)  REFERENCES pilot_stops(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS stop_flags (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    alert_id          INT            NOT NULL,
    vehicle_id        VARCHAR(100)   NOT NULL,
    stop_id           INT            NOT NULL,
    flag              ENUM('visited','skipped','pending') NOT NULL DEFAULT 'pending',
    flagged_at        DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    skip_alert_msg_id BIGINT,
    FOREIGN KEY (alert_id) REFERENCES fuel_alerts(id),
    FOREIGN KEY (stop_id)  REFERENCES pilot_stops(id),
    INDEX idx_vehicle_flag (vehicle_id, flag),
    INDEX idx_pending (flag, flagged_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS truck_states (
    vehicle_id              VARCHAR(100) PRIMARY KEY,
    vehicle_name            VARCHAR(255),
    driver_name             VARCHAR(255),
    state                   VARCHAR(50)  NOT NULL DEFAULT 'UNKNOWN',
    fuel_pct                FLOAT,
    latitude                DOUBLE,
    longitude               DOUBLE,
    speed_mph               FLOAT,
    heading                 FLOAT,
    next_poll               DATETIME     NOT NULL,
    parked_since            DATETIME,
    alert_sent              TINYINT(1)   DEFAULT 0,
    overnight_alert_sent    TINYINT(1)   DEFAULT 0,
    open_alert_id           INT,
    assigned_stop_id        INT,
    assigned_stop_name      VARCHAR(255),
    assigned_stop_lat       DOUBLE,
    assigned_stop_lng       DOUBLE,
    assignment_time         DATETIME,
    in_yard                 TINYINT(1)   DEFAULT 0,
    yard_name               VARCHAR(100),
    sleeping                TINYINT(1)   DEFAULT 0,
    fuel_when_parked        FLOAT,
    last_updated            DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_next_poll (next_poll),
    INDEX idx_state (state)
) ENGINE=InnoDB;
"""

# Migration: add missing columns to existing truck_states tables
MIGRATION_SQL = [
    "ALTER TABLE truck_states ADD COLUMN IF NOT EXISTS sleeping TINYINT(1) DEFAULT 0",
    "ALTER TABLE truck_states ADD COLUMN IF NOT EXISTS fuel_when_parked FLOAT",
]


def init_db():
    """Create database + all tables if they don't exist. Run migrations for existing tables."""
    # Connect without db first to create it
    ssl = {"ssl": {"ssl_disabled": False}} if not DB_HOST.startswith("/") else {}
    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD,
        charset="utf8mb4",
        ssl_verify_cert=False,
        **ssl,
    )
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4;")
    conn.commit()
    conn.close()

    # Create tables
    conn = get_connection()
    with conn.cursor() as cur:
        for statement in SCHEMA_SQL.strip().split(";"):
            statement = statement.strip()
            if statement:
                cur.execute(statement)
    conn.commit()
    conn.close()

    # Run migrations (safe to run repeatedly — IF NOT EXISTS)
    conn = get_connection()
    with conn.cursor() as cur:
        for migration in MIGRATION_SQL:
            try:
                cur.execute(migration)
            except Exception as e:
                # IF NOT EXISTS not supported on older MySQL — ignore duplicate column errors
                if "Duplicate column" not in str(e):
                    raise
    conn.commit()
    conn.close()

    print("✅  Database schema ready.")


# ── pilot_stops ───────────────────────────────────────────────────────────────

def upsert_pilot_stop(row: dict) -> int:
    """Insert or update a stop from CSV seed. Returns stop id."""
    sql = """
        INSERT INTO pilot_stops
            (name, brand, address, city, state, zip, latitude, longitude, phone, has_diesel)
        VALUES
            (%(name)s, %(brand)s, %(address)s, %(city)s, %(state)s, %(zip)s,
             %(latitude)s, %(longitude)s, %(phone)s, %(has_diesel)s)
        ON DUPLICATE KEY UPDATE
            name=VALUES(name), address=VALUES(address),
            latitude=VALUES(latitude), longitude=VALUES(longitude)
    """
    with db_cursor() as cur:
        cur.execute(sql, row)
        return cur.lastrowid


def get_all_stops_with_diesel() -> list:
    """Return all stops that have diesel fuel."""
    with db_cursor() as cur:
        cur.execute("SELECT * FROM pilot_stops WHERE has_diesel = 1")
        return cur.fetchall()


def get_stop_by_id(stop_id: int) -> dict | None:
    """Get stop details by ID."""
    with db_cursor() as cur:
        cur.execute("SELECT * FROM pilot_stops WHERE id = %s", (stop_id,))
        return cur.fetchone()


# ── fuel_alerts ───────────────────────────────────────────────────────────────

def create_fuel_alert(vehicle_id, vehicle_name, driver_name,
                      fuel_pct, lat, lng, heading, speed_mph) -> int:
    sql = """
        INSERT INTO fuel_alerts
            (vehicle_id, vehicle_name, driver_name, fuel_pct,
             latitude, longitude, heading, speed_mph)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    with db_cursor() as cur:
        cur.execute(sql, (vehicle_id, vehicle_name, driver_name,
                          fuel_pct, lat, lng, heading, speed_mph))
        return cur.lastrowid


def update_alert_telegram_msg(alert_id: int, msg_id: int):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE fuel_alerts SET telegram_msg_id=%s WHERE id=%s",
            (msg_id, alert_id)
        )


def resolve_alert(alert_id: int):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE fuel_alerts SET status='resolved' WHERE id=%s",
            (alert_id,)
        )


def mark_alert_skipped(alert_id: int):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE fuel_alerts SET status='skipped' WHERE id=%s",
            (alert_id,)
        )


# ── stop_assignments ──────────────────────────────────────────────────────────

def create_stop_assignment(alert_id: int, stop_id: int, distance_miles: float) -> int:
    with db_cursor() as cur:
        cur.execute(
            """INSERT INTO stop_assignments (alert_id, stop_id, distance_miles)
               VALUES (%s, %s, %s)""",
            (alert_id, stop_id, distance_miles)
        )
        return cur.lastrowid


# ── stop_flags ────────────────────────────────────────────────────────────────

def create_pending_flag(alert_id: int, vehicle_id: str, stop_id: int) -> int:
    with db_cursor() as cur:
        cur.execute(
            """INSERT INTO stop_flags (alert_id, vehicle_id, stop_id, flag)
               VALUES (%s, %s, %s, 'pending')""",
            (alert_id, vehicle_id, stop_id)
        )
        return cur.lastrowid


def mark_flag_visited(flag_id: int):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE stop_flags SET flag='visited', flagged_at=NOW() WHERE id=%s",
            (flag_id,)
        )


def mark_flag_skipped(flag_id: int, skip_msg_id: int = None):
    with db_cursor() as cur:
        cur.execute(
            """UPDATE stop_flags
               SET flag='skipped', flagged_at=NOW(), skip_alert_msg_id=%s
               WHERE id=%s""",
            (skip_msg_id, flag_id)
        )


def get_pending_flag_by_alert(alert_id: int) -> dict | None:
    """Get pending flag for a specific alert."""
    with db_cursor() as cur:
        cur.execute(
            """SELECT sf.*, ps.name AS stop_name,
                      ps.latitude AS stop_lat, ps.longitude AS stop_lng
               FROM stop_flags sf
               JOIN pilot_stops ps ON sf.stop_id = ps.id
               WHERE sf.alert_id=%s AND sf.flag='pending'
               LIMIT 1""",
            (alert_id,)
        )
        return cur.fetchone()


def get_pending_flags_for_vehicle(vehicle_id: str) -> list:
    """Get all pending flags for a vehicle."""
    with db_cursor() as cur:
        cur.execute(
            """SELECT sf.*, ps.name AS stop_name,
                      ps.latitude AS stop_lat, ps.longitude AS stop_lng,
                      fa.telegram_msg_id
               FROM stop_flags sf
               JOIN pilot_stops ps ON sf.stop_id = ps.id
               JOIN fuel_alerts fa ON sf.alert_id = fa.id
               WHERE sf.vehicle_id=%s AND sf.flag='pending'
               ORDER BY sf.flagged_at ASC""",
            (vehicle_id,)
        )
        return cur.fetchall()


# ── truck_states ──────────────────────────────────────────────────────────────

def load_all_truck_states() -> dict:
    """Load all truck states from DB into memory. Returns dict: {vehicle_id: state_dict}"""
    with db_cursor() as cur:
        cur.execute("SELECT * FROM truck_states")
        rows = cur.fetchall()

    states = {}
    for row in rows:
        vid = row["vehicle_id"]
        states[vid] = {
            "vehicle_id":           vid,
            "vehicle_name":         row["vehicle_name"],
            "driver_name":          row["driver_name"],
            "state":                row["state"],
            "fuel_pct":             row["fuel_pct"],
            "lat":                  row["latitude"],
            "lng":                  row["longitude"],
            "speed_mph":            row["speed_mph"],
            "heading":              row["heading"],
            "next_poll":            row["next_poll"],
            "parked_since":         row["parked_since"],
            "alert_sent":           bool(row["alert_sent"]),
            "overnight_alert_sent": bool(row["overnight_alert_sent"]),
            "open_alert_id":        row["open_alert_id"],
            "assigned_stop_id":     row["assigned_stop_id"],
            "assigned_stop_name":   row["assigned_stop_name"],
            "assigned_stop_lat":    row["assigned_stop_lat"],
            "assigned_stop_lng":    row["assigned_stop_lng"],
            "assignment_time":      row["assignment_time"],
            "in_yard":              bool(row["in_yard"]),
            "yard_name":            row["yard_name"],
            # FIX: these two were missing — caused sleeping state loss on restart
            "sleeping":             bool(row.get("sleeping", 0)),
            "fuel_when_parked":     row.get("fuel_when_parked"),
        }
    return states


def save_truck_state(state: dict):
    """Upsert a single truck state to DB."""
    sql = """
        INSERT INTO truck_states
            (vehicle_id, vehicle_name, driver_name, state, fuel_pct, latitude, longitude,
             speed_mph, heading, next_poll, parked_since, alert_sent, overnight_alert_sent,
             open_alert_id, assigned_stop_id, assigned_stop_name, assigned_stop_lat,
             assigned_stop_lng, assignment_time, in_yard, yard_name,
             sleeping, fuel_when_parked)
        VALUES
            (%(vehicle_id)s, %(vehicle_name)s, %(driver_name)s, %(state)s, %(fuel_pct)s,
             %(lat)s, %(lng)s, %(speed_mph)s, %(heading)s, %(next_poll)s, %(parked_since)s,
             %(alert_sent)s, %(overnight_alert_sent)s, %(open_alert_id)s, %(assigned_stop_id)s,
             %(assigned_stop_name)s, %(assigned_stop_lat)s, %(assigned_stop_lng)s,
             %(assignment_time)s, %(in_yard)s, %(yard_name)s,
             %(sleeping)s, %(fuel_when_parked)s)
        ON DUPLICATE KEY UPDATE
            vehicle_name=VALUES(vehicle_name),
            driver_name=VALUES(driver_name),
            state=VALUES(state),
            fuel_pct=VALUES(fuel_pct),
            latitude=VALUES(latitude),
            longitude=VALUES(longitude),
            speed_mph=VALUES(speed_mph),
            heading=VALUES(heading),
            next_poll=VALUES(next_poll),
            parked_since=VALUES(parked_since),
            alert_sent=VALUES(alert_sent),
            overnight_alert_sent=VALUES(overnight_alert_sent),
            open_alert_id=VALUES(open_alert_id),
            assigned_stop_id=VALUES(assigned_stop_id),
            assigned_stop_name=VALUES(assigned_stop_name),
            assigned_stop_lat=VALUES(assigned_stop_lat),
            assigned_stop_lng=VALUES(assigned_stop_lng),
            assignment_time=VALUES(assignment_time),
            in_yard=VALUES(in_yard),
            yard_name=VALUES(yard_name),
            sleeping=VALUES(sleeping),
            fuel_when_parked=VALUES(fuel_when_parked)
    """
    with db_cursor() as cur:
        cur.execute(sql, state)


def save_all_truck_states(states: dict):
    """Batch save all truck states to DB."""
    for state in states.values():
        save_truck_state(state)


def reset_truck_states():
    """
    Reset (clear) all truck states from the database.
    Useful for fresh start — wipes history so trucks re-initialize from scratch.
    """
    with db_cursor() as cur:
        cur.execute("DELETE FROM truck_states")
    print("✅  Truck states reset — all history cleared from DB.")


def get_open_alert_for_vehicle(vehicle_id: str) -> dict | None:
    """Get the most recent open alert for a vehicle."""
    with db_cursor() as cur:
        cur.execute(
            """SELECT * FROM fuel_alerts
               WHERE vehicle_id=%s AND status='open'
               ORDER BY alert_sent_at DESC LIMIT 1""",
            (vehicle_id,)
        )
        return cur.fetchone()
