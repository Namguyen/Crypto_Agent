import os
import json
from datetime import datetime

HISTORY_FILE = "crypto_history.json"
MAX_RECORD   = int(os.getenv("MAX_HISTORY_RECORDS", 500))


def keep_track(coin: str, usd_price: float, vnd_price: float):
    """Append a price record to local JSON, capped at MAX_RECORD."""
    record = {
        "coin": coin.upper(),
        "USD":  usd_price,
        "VND":  vnd_price,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, IOError):
            history = []

    history.append(record)
    history = history[-MAX_RECORD:]

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def show_history(coin: str = None, limit: int = 10) -> str:
    """Return recent price lookups as a formatted string, optionally filtered by coin."""
    if not os.path.exists(HISTORY_FILE):
        return " Chưa có lịch sử nào được lưu."

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
    except (json.JSONDecodeError, IOError):
        return " Không đọc được file lịch sử."

    if not history:
        return " Lịch sử trống."

    if coin:
        alias_map = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana",
                     "bnb": "binancecoin", "doge": "dogecoin", "ton": "the-open-network"}
        coin_key = alias_map.get(coin.strip().lower(), coin.strip().lower()).upper()
        history  = [r for r in history if r.get("coin") == coin_key]
        if not history:
            return f"Không có lịch sử cho {coin_key}."
        label = f"Lịch sử {coin_key}"
    else:
        label = "Lịch sử tất cả"

    recent = history[-limit:][::-1]
    lines  = [f"\n{label} ({len(recent)} lần gần nhất):", "-" * 55]
    for r in recent:
        usd = r.get('USD', r.get('usd', 0))  # handle both USD and usd keys
        vnd = r.get('VND', r.get('vnd', 0))
        lines.append(
            f"  {r['coin']:<14} ${usd:>12,.2f}"
            f"  |  {vnd:>16,.0f} đ  |  {r['time']}"
        )
    lines.append("-" * 55)
    return "\n".join(lines)