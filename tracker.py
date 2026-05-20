import os
import json

from datetime import datetime

HISTORY_FILE = "crypto_history.json"

def keep_track(coin: str , us_price: float, vnd_price:float):
    
    record = {
        "coin" : coin.upper(),
        "USD" : us_price,
        "VND" : vnd_price,
        "time" : datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, IOError):
            history = []
 
    history.append(record)
 
    # Ghi lại toàn bộ
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
 
def show_history(limit: int = 10):
    """Hiển thị lịch sử tra giá gần nhất."""
    if not os.path.exists(HISTORY_FILE):
        print("📭 Chưa có lịch sử nào được lưu.")
        return
 
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
    except (json.JSONDecodeError, IOError):
        print("⚠️ Không đọc được file lịch sử.")
        return
 
    if not history:
        print("📭 Lịch sử trống.")
        return
 
    recent = history[-limit:][::-1]  # Mới nhất trước
    print(f"\n📜 Lịch sử tra giá ({len(recent)} lần gần nhất):")
    print("-" * 50)
    for r in recent:
        print(f" {r['coin']:<12} ${r['USD']:>12,.2f}  |  {r['VND']:>16,.0f} đ  |  {r['time']}")
    print("-" * 50)
