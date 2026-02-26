"""
telegram_bot.py  –  Send / edit Telegram messages to the fleet group
All messages go to one group. Driver mentions are included in the message text.
"""

import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_GROUP_ID

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _post(method: str, payload: dict) -> dict:
    resp = requests.post(f"{BASE_URL}/{method}", json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _fuel_bar(pct: float) -> str:
    """Visual ASCII fuel bar, e.g.  ▓▓▓░░░░░░░  28%"""
    filled = round(pct / 10)
    empty  = 10 - filled
    bar    = "▓" * filled + "░" * empty
    return f"{bar}  {pct:.0f}%"


def send_low_fuel_alert(
    vehicle_name: str,
    driver_name:  str | None,
    fuel_pct:     float,
    stop:         dict,            # best stop dict from truck_stop_finder
    heading:      float,
    speed_mph:    float,
) -> int | None:
    """
    Send low-fuel alert with recommended stop.
    Returns Telegram message_id for future edits.
    """
    driver_line = f"👤 *Driver:* {driver_name}" if driver_name else "👤 *Driver:* Unknown"
    direction   = _heading_to_direction(heading)

    text = (
        f"⛽ *LOW FUEL ALERT*\n"
        f"{'─' * 30}\n"
        f"🚛 *Truck:* {vehicle_name}\n"
        f"{driver_line}\n"
        f"⛽ *Fuel Level:* {_fuel_bar(fuel_pct)}\n"
        f"🧭 *Heading:* {direction} ({heading:.0f}°)  |  🚀 {speed_mph:.0f} mph\n"
        f"\n"
        f"📍 *Recommended Stop (ahead):*\n"
        f"🏪 *{stop['name']}*  ({stop.get('brand', 'Pilot')})\n"
        f"📮 {stop.get('address', '')}, {stop.get('city', '')}, {stop.get('state', '')}\n"
        f"📏 Distance: *{stop['distance_miles']:.1f} miles*\n"
        f"🗺 [Open in Maps]({stop['google_maps_url']})\n"
        f"\n"
        f"⚠️ _Please refuel at the above stop to avoid breakdown._\n"
        f"✅ This alert will auto-resolve once the truck stops nearby."
    )

    payload = {
        "chat_id":    TELEGRAM_GROUP_ID,
        "text":       text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    result = _post("sendMessage", payload)
    if result.get("ok"):
        return result["result"]["message_id"]
    return None


def send_no_stop_found_alert(
    vehicle_name: str,
    driver_name:  str | None,
    fuel_pct:     float,
    heading:      float,
) -> int | None:
    """Alert when no suitable stop was found in range/heading."""
    driver_line = f"👤 *Driver:* {driver_name}" if driver_name else "👤 *Driver:* Unknown"
    direction   = _heading_to_direction(heading)

    text = (
        f"⛽🚨 *CRITICAL FUEL ALERT — NO STOP FOUND*\n"
        f"{'─' * 30}\n"
        f"🚛 *Truck:* {vehicle_name}\n"
        f"{driver_line}\n"
        f"⛽ *Fuel Level:* {_fuel_bar(fuel_pct)}\n"
        f"🧭 *Heading:* {direction} ({heading:.0f}°)\n"
        f"\n"
        f"❌ *No Pilot/Flying J stop found within 50 miles ahead.*\n"
        f"⚠️ _Dispatcher: Please contact driver immediately and find nearest fuel source._"
    )

    payload = {
        "chat_id":    TELEGRAM_GROUP_ID,
        "text":       text,
        "parse_mode": "Markdown",
    }
    result = _post("sendMessage", payload)
    if result.get("ok"):
        return result["result"]["message_id"]
    return None


def send_skip_alert(
    vehicle_name:    str,
    driver_name:     str | None,
    stop_name:       str,
    original_msg_id: int | None,
) -> int | None:
    """
    Alert sent when truck passed the assigned stop without stopping.
    """
    driver_line = f"👤 *Driver:* {driver_name}" if driver_name else "👤 *Driver:* Unknown"
    ref_line = f"_(See original alert ↑ msg #{original_msg_id})_\n" if original_msg_id else ""

    text = (
        f"🚩 *FUEL STOP SKIPPED*\n"
        f"{'─' * 30}\n"
        f"🚛 *Truck:* {vehicle_name}\n"
        f"{driver_line}\n"
        f"🏪 *Skipped Stop:* {stop_name}\n"
        f"{ref_line}"
        f"\n"
        f"⚠️ _Truck passed the recommended fuel stop without stopping._\n"
        f"📞 *Dispatcher: Please contact driver immediately!*\n"
        f"🔴 Truck may run out of fuel."
    )

    payload = {
        "chat_id":    TELEGRAM_GROUP_ID,
        "text":       text,
        "parse_mode": "Markdown",
    }
    result = _post("sendMessage", payload)
    if result.get("ok"):
        return result["result"]["message_id"]
    return None


def send_resolved_alert(
    vehicle_name: str,
    driver_name:  str | None,
    stop_name:    str,
    fuel_pct:     float,
) -> None:
    """Notify group that truck has refueled / stop was visited."""
    driver_line = f"👤 *Driver:* {driver_name}" if driver_name else "👤 *Driver:* Unknown"

    text = (
        f"✅ *FUEL ALERT RESOLVED*\n"
        f"{'─' * 30}\n"
        f"🚛 *Truck:* {vehicle_name}\n"
        f"{driver_line}\n"
        f"🏪 *Stopped at:* {stop_name}\n"
        f"⛽ *Current Fuel:* {_fuel_bar(fuel_pct)}\n"
        f"\n"
        f"👍 _Truck has refueled. Alert closed._"
    )

    _post("sendMessage", {
        "chat_id":    TELEGRAM_GROUP_ID,
        "text":       text,
        "parse_mode": "Markdown",
    })


def send_startup_message():
    """Simple bot startup ping to the group."""
    _post("sendMessage", {
        "chat_id":    TELEGRAM_GROUP_ID,
        "text":       "🚛 *FleetFuel Bot is online.*\nMonitoring truck fuel levels...",
        "parse_mode": "Markdown",
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _heading_to_direction(heading: float) -> str:
    """Convert degrees to compass direction string."""
    directions = [
        "N", "NNE", "NE", "ENE",
        "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW",
        "W", "WNW", "NW", "NNW",
    ]
    idx = round(heading / 22.5) % 16
    return directions[idx]
