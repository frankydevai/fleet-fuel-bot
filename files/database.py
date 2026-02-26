"""
database.py  –  MySQL connection + schema management
Tables:
  pilot_stops       – seeded from your CSV
  fuel_alerts       – one row per low-fuel event
  stop_assignments  – which stop was assigned to which alert
  stop_flags        – visited / skipped tracking
"""

import pymysql
import pymysql.cursors
from contextlib import contextmanager
from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME


# ── Connection pool (simple) ──────────────────────────────────────────────────

def get_connection():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
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
    brand       VARCHAR(50)    NOT NULL DEFAULT 'Pilot',   -- Pilot | Flying J | One9
    address     VARCHAR(512),
    city        VARCHAR(100),
    state       CHAR(2),
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
    heading         FLOAT,                         -- degrees 0-360
    speed_mph       FLOAT,
    alert_sent_at   DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    telegram_msg_id BIGINT,                        -- Telegram message_id for edits
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
    id              INT AUTO_INCREMENT PRIMARY KEY,
    alert_id        INT            NOT NULL,
    vehicle_id      VARCHAR(100)   NOT NULL,
    stop_id         INT            NOT NULL,
    flag            ENUM('visited','skipped','pending') NOT NULL DEFAULT 'pending',
    flagged_at      DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    skip_alert_msg_id BIGINT,                      -- Telegram message_id of skip alert
    FOREIGN KEY (alert_id) REFERENCES fuel_alerts(id),
    FOREIGN KEY (stop_id)  REFERENCES pilot_stops(id),
    INDEX idx_vehicle_flag (vehicle_id, flag)
) ENGINE=InnoDB;
"""


def init_db():
    """Create database + all tables if they don't exist."""
    # Connect without db first to create it
    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD,
        charset="utf8mb4",
    )
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4;")
    conn.commit()
    conn.close()

    # Now create tables
    conn = get_connection()
    with conn.cursor() as cur:
        for statement in SCHEMA_SQL.strip().split(";"):
            statement = statement.strip()
            if statement:
                cur.execute(statement)
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


def get_open_alert_for_vehicle(vehicle_id: str) -> dict | None:
    """Return the most recent open alert for a vehicle, or None."""
    with db_cursor() as cur:
        cur.execute(
            """SELECT * FROM fuel_alerts
               WHERE vehicle_id=%s AND status='open'
               ORDER BY alert_sent_at DESC LIMIT 1""",
            (vehicle_id,)
        )
        return cur.fetchone()


def get_recent_alert_time(vehicle_id: str):
    """Return the sent time of the most recent alert (any status)."""
    with db_cursor() as cur:
        cur.execute(
            """SELECT alert_sent_at FROM fuel_alerts
               WHERE vehicle_id=%s
               ORDER BY alert_sent_at DESC LIMIT 1""",
            (vehicle_id,)
        )
        row = cur.fetchone()
        return row["alert_sent_at"] if row else None


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


def get_pending_flags_older_than(minutes: int) -> list:
    """Return all pending flags created more than `minutes` ago."""
    with db_cursor() as cur:
        cur.execute(
            """SELECT sf.*, fa.vehicle_id, fa.vehicle_name, fa.driver_name,
                      ps.name AS stop_name, ps.latitude AS stop_lat,
                      ps.longitude AS stop_lng, fa.telegram_msg_id
               FROM stop_flags sf
               JOIN fuel_alerts fa ON sf.alert_id = fa.id
               JOIN pilot_stops  ps ON sf.stop_id  = ps.id
               WHERE sf.flag = 'pending'
                 AND sf.flagged_at < DATE_SUB(NOW(), INTERVAL %s MINUTE)""",
            (minutes,)
        )
        return cur.fetchall()


def get_pending_flag_for_alert(alert_id: int) -> dict | None:
    with db_cursor() as cur:
        cur.execute(
            """SELECT sf.*, ps.latitude AS stop_lat, ps.longitude AS stop_lng,
                      ps.name AS stop_name
               FROM stop_flags sf
               JOIN pilot_stops ps ON sf.stop_id = ps.id
               WHERE sf.alert_id=%s AND sf.flag='pending'
               LIMIT 1""",
            (alert_id,)
        )
        return cur.fetchone()
