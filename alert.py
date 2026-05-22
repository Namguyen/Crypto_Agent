import json
import os
import threading
import time
import requests
from datetime import datetime

# Configuration
ALERTS_FILE = "alerts.json"
CHECK_INTERVAL_SEC = 600
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price?ids={}&vs_currencies=usd"

_monitor_thread = None

def start_monitor():
    global _monitor_thread
    if _monitor_thread is None or not _monitor_thread.is_alive():
        _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
        _monitor_thread.start()
        print(f"[Alert Monitor] Started – checking every {CHECK_INTERVAL_SEC} seconds")
    return _monitor_thread

# Global in‑memory cache for prices during a check cycle
_prices_cache = {}
_alerts_lock = threading.Lock()

def _load_alerts():
    """Load alerts from JSON file. Returns list of alert dicts."""
    if not os.path.exists(ALERTS_FILE):
        return []
    try:
        with open(ALERTS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

def _save_alerts(alerts):
    """Save alerts list to JSON file."""
    with open(ALERTS_FILE, "w") as f:
        json.dump(alerts, f, indent=2)

def _fetch_price(coin_id):
    """Get current USD price for a single coin from CoinGecko."""
    try:
        url = COINGECKO_PRICE_URL.format(coin_id.lower())
        data = requests.get(url, timeout=8).json()
        return data[coin_id.lower()]["usd"]
    except Exception:
        return None

def _check_alert(alert):
    """
    Check if an alert condition is met.
    alert = {
        "id": str,
        "coin": "bitcoin",
        "condition": "above" or "below",
        "price": float,
        "triggered": bool,
        "created_at": str
    }
    Returns (triggered_now, current_price) or (False, None) if not met.
    """
    if alert.get("triggered", False):
        return False, None

    coin = alert["coin"].lower()
    # Use cached price if available (avoid duplicate API calls)
    if coin not in _prices_cache:
        _prices_cache[coin] = _fetch_price(coin)
    current = _prices_cache[coin]
    if current is None:
        return False, None

    condition = alert["condition"]
    target = alert["price"]
    met = (condition == "above" and current > target) or (condition == "below" and current < target)
    return met, current

def _trigger_alert(alert, current_price):
    """Do something when an alert fires: print, log, optionally inject into agent."""
    coin = alert["coin"].upper()
    condition = "lên trên" if alert["condition"] == "above" else "xuống dưới"
    message = (
        f"\n🔔 ALERT: {coin} đã {condition} ${alert['price']:,.2f}!\n"
        f"   Giá hiện tại: ${current_price:,.2f}\n"
        f"   (Được tạo lúc: {alert['created_at']})\n"
    )
    print(message)
    # Optional: write to an alert log file
    with open("alerts.log", "a", encoding="utf-8") as log:
        log.write(f"{datetime.now().isoformat()} - {message.strip()}\n")

    # Mark as triggered
    alert["triggered"] = True
    alert["triggered_at"] = datetime.now().isoformat()

def _monitor_loop():
    """Background thread: periodically check all active alerts."""
    while True:
        with _alerts_lock:
            alerts = _load_alerts()
        # Clear price cache for this cycle
        global _prices_cache
        _prices_cache = {}

        changed = False
        for alert in alerts:
            met, price = _check_alert(alert)
            if met:
                _trigger_alert(alert, price)
                changed = True

        if changed:
            with _alerts_lock:
                _save_alerts(alerts)

        time.sleep(CHECK_INTERVAL_SEC)

def start_monitor():
    """Launch the background monitoring thread (daemon)."""
    thread = threading.Thread(target=_monitor_loop, daemon=True)
    thread.start()
    print(f"[Alert Monitor] Started – checking every {CHECK_INTERVAL_SEC} seconds")


def add_alert(coin: str, condition: str, price: float) -> str:
    """
    Add a new price alert.
    coin: CoinGecko ID (e.g., 'bitcoin', 'ethereum', 'solana')
    condition: 'above' or 'below'
    price: threshold in USD
    """
    coin_lower = coin.lower()
    # Validate coin exists? 
    with _alerts_lock:
        alerts = _load_alerts()
        # Create new alert with unique ID (simple timestamp)
        alert_id = f"{coin_lower}_{condition}_{price}_{int(time.time())}"
        new_alert = {
            "id": alert_id,
            "coin": coin_lower,
            "condition": condition,
            "price": price,
            "triggered": False,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        alerts.append(new_alert)
        _save_alerts(alerts)
    return f" Đã thêm cảnh báo: {coin.upper()} {condition} ${price:,.2f}"

def remove_alert(alert_id: str) -> str:
    """Remove an alert by its ID."""
    with _alerts_lock:
        alerts = _load_alerts()
        new_alerts = [a for a in alerts if a["id"] != alert_id]
        if len(new_alerts) == len(alerts):
            return f"Không tìm thấy cảnh báo với ID '{alert_id}'"
        _save_alerts(new_alerts)
    return f"Đã xóa cảnh báo ID: {alert_id}"

def list_alerts() -> str:
    """Return a formatted string of all active (including triggered) alerts."""
    with _alerts_lock:
        alerts = _load_alerts()
    if not alerts:
        return "Chưa có cảnh báo nào."

    lines = [" Danh sách cảnh báo:"]
    for a in alerts:
        status = "Đã kích hoạt" if a.get("triggered") else "Đang theo dõi"
        lines.append(
            f"  ID: {a['id']}\n"
            f"     Coin: {a['coin'].upper()} | {a['condition']} ${a['price']:,.2f} | {status}\n"
            f"     Tạo lúc: {a['created_at']}"
        )
    return "\n".join(lines)

def reset_alert(alert_id: str = None, coin: str = None) -> str:
    """
    Reset a triggered alert so it can fire again.
    If alert_id given, reset that specific alert.
    If coin given, reset all alerts for that coin (optional).
    """
    with _alerts_lock:
        alerts = _load_alerts()
        changed = False
        if alert_id:
            for a in alerts:
                if a["id"] == alert_id and a.get("triggered"):
                    a["triggered"] = False
                    a.pop("triggered_at", None)
                    changed = True
                    break
            if not changed:
                return f"Không tìm thấy cảnh báo đã kích hoạt với ID '{alert_id}'"
        elif coin:
            coin_lower = coin.lower()
            for a in alerts:
                if a["coin"] == coin_lower and a.get("triggered"):
                    a["triggered"] = False
                    a.pop("triggered_at", None)
                    changed = True
            if not changed:
                return f"Không có cảnh báo nào đã kích hoạt cho {coin.upper()}"
        else:
            return "Cần cung cấp alert_id hoặc coin để reset."
        if changed:
            _save_alerts(alerts)
    return f"Đã reset cảnh báo cho {coin.upper() if coin else alert_id}"