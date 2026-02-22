"""
telegram_bot.py  –  Telegram messages for FleetFuel bot.
"""

import time
import logging
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_GROUP_ID

log = logging.getLogger(__name__)
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _post(method: str, payload: dict, retries: int = 4) -> dict | None:
    for attempt in range(retries + 1):
        try:
            resp = requests.post(f"{BASE_URL}/{method}", json=payload, timeout=10)
            if resp.status_code == 429:
                wait = max(resp.json().get("parameters", {}).get("retry_after", 5), 5)
                wait *= (attempt + 1)
                log.warning(f"Telegram 429 — waiting {wait}s (attempt {attempt+1})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            log.error(f"Telegram {method} failed (attempt {attempt+1}): {exc}")
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
    return None


def _send(text: str) -> int | None:
    result = _post("sendMessage", {
        "chat_id":                  TELEGRAM_GROUP_ID,
        "text":                     text,
        "parse_mode":               "Markdown",
        "disable_web_page_preview": True,
    })
    if result and result.get("ok"):
        return result["result"]["message_id"]
    return None


def _compass(heading: float) -> str:
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return dirs[round(heading / 22.5) % 16]


def _stop_note(stop_type_value: str) -> str:
    notes = {
        "Love's (within 50 mi, no Pilot nearby)":   "⚠️ No Pilot/Flying J within 50 mi — nearest Love's shown.",
        "Pilot/Flying J (within 80 mi extended)":   "⚠️ No stop within 50 mi — nearest Pilot at 80 mi shown.",
        "Love's (within 80 mi extended)":           "⚠️ No Pilot or Love's within 50 mi — nearest Love's at 80 mi shown.",
    }
    return notes.get(stop_type_value, "")


# ── Alerts ────────────────────────────────────────────────────────────────────

def send_low_fuel_alert(vehicle_name, driver_name, fuel_pct,
                        truck_lat, truck_lng, stop,
                        heading, speed_mph, stop_type) -> int | None:

    driver = driver_name or "Unknown"
    address = f"{stop.get('address', '')}, {stop.get('city', '')}, {stop.get('state', '')}"
    note = _stop_note(stop_type.value)
    note_line = f"\n⚠️ {note}" if note else ""
    truck_url = f"https://maps.google.com/?q={truck_lat},{truck_lng}"

    text = (
        f"⛽️ *LOW FUEL ALERT*\n"
        f"{'─' * 32}\n"
        f"🚛 *Truck:* {vehicle_name}\n"
        f"👤 *Driver:* {driver}\n"
        f"⛽️ *Fuel:*     {fuel_pct:.0f}%\n"
        f"📍 *Location:* {truck_lat:.4f}, {truck_lng:.4f} ({truck_url})\n"
        f"🧭 {speed_mph:.0f} mph · heading {_compass(heading)}\n"
        f"🏁 *Nearest Stop:*\n"
        f"🏪 {stop['name']}  ·  {stop.get('brand','').upper()}\n"
        f"📮 {address}\n"
        f"📏 {stop['distance_miles']:.1f} miles ahead\n"
        f"🗺 [Open in Google Maps]({stop['google_maps_url']})\n"
        f"✅ Alert closes automatically once truck stops at this location."
        f"{note_line}"
    )
    return _send(text)


def send_no_stop_alert(vehicle_name, driver_name, fuel_pct,
                       truck_lat, truck_lng, heading, speed_mph) -> int | None:

    driver = driver_name or "Unknown"
    truck_url = f"https://maps.google.com/?q={truck_lat},{truck_lng}"

    text = (
        f"🚨 *LOW FUEL ALERT — NO STOP FOUND*\n"
        f"{'─' * 32}\n"
        f"🚛 *Truck:* {vehicle_name}\n"
        f"👤 *Driver:* {driver}\n"
        f"⛽️ *Fuel:*     {fuel_pct:.0f}%\n"
        f"📍 *Location:* {truck_lat:.4f}, {truck_lng:.4f} ({truck_url})\n"
        f"🧭 {speed_mph:.0f} mph · heading {_compass(heading)}\n"
        f"\n"
        f"❌ No Pilot, Flying J, or Love's within 80 miles.\n"
        f"📞 *Dispatcher: contact driver immediately.*"
    )
    return _send(text)


def send_refueled_alert(vehicle_name, driver_name, stop_name, fuel_pct) -> None:
    driver = driver_name or "Unknown"
    text = (
        f"✅ *TRUCK REFUELED*\n"
        f"{'─' * 32}\n"
        f"🚛 *Truck:* {vehicle_name}\n"
        f"👤 *Driver:* {driver}\n"
        f"🏪 *Refueled at:* {stop_name}\n"
        f"⛽️ *Fuel now:*  {fuel_pct:.0f}%\n"
        f"✅ Alert closed."
    )
    _send(text)


def send_flagged_alert(vehicle_name, driver_name, stop_name,
                       fuel_pct, original_msg_id) -> int | None:
    driver = driver_name or "Unknown"
    text = (
        f"🚩 *FUEL STOP SKIPPED*\n"
        f"{'─' * 32}\n"
        f"🚛 *Truck:* {vehicle_name}\n"
        f"👤 *Driver:* {driver}\n"
        f"⛽️ *Fuel:*     {fuel_pct:.0f}%\n"
        f"🏪 *Skipped:* {stop_name}\n"
        f"📞 *Dispatcher: contact driver immediately.*"
    )
    return _send(text)


def send_left_yard_low_fuel(vehicle_name, driver_name, fuel_pct, yard_name) -> None:
    driver = driver_name or "Unknown"
    text = (
        f"🏠 *LEFT YARD — LOW FUEL*\n"
        f"{'─' * 32}\n"
        f"🚛 *Truck:* {vehicle_name}\n"
        f"👤 *Driver:* {driver}\n"
        f"⛽️ *Fuel:*     {fuel_pct:.0f}%\n"
        f"📍 *Departed:* {yard_name}\n"
        f"Finding nearest stop..."
    )
    _send(text)


def send_startup_message() -> None:
    _send("🚛 *FleetFuel Bot online.* Monitoring fuel levels.")
