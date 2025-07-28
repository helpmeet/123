import os
import time
import hmac
import hashlib
import requests
from datetime import datetime

# === Логирование ===
def log(msg):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}")

# === Внешний IP для фильтрации 3Commas ===
def print_external_ip():
    try:
        ip = requests.get("https://api.ipify.org").text
        log(f"🌐 External IP: {ip}")
    except Exception as e:
        log(f"[ERROR] Failed to get external IP: {e}")

print_external_ip()

# === Конфигурация из окружения ===
API_KEY = os.getenv("THREECOMMAS_API_KEY")
API_SECRET = os.getenv("THREECOMMAS_API_SECRET")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))
API_BASE = "https://api.3commas.io/public/api"

known_deals = {}

# === Подпись запроса ===
def sign(path, params):
    query = '&'.join(f"{k}={v}" for k, v in sorted(params.items()))
    payload = f"{path}?{query}" if query else path
    return hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

# === Запрос к 3Commas ===
def get(path, params=None):
    params = params or {}
    headers = {
        "APIKEY": API_KEY,
        "Signature": sign(path, params)
    }
    url = API_BASE + path
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()

# === Отправка сообщения в Telegram ===
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, data=payload)
    except Exception as e:
        log(f"[ERROR] Telegram error: {e}")

# === Получить цену и объем последнего ордера ===
def get_last_order_price_and_qty(deal_id):
    try:
        orders = get(f"/ver1/deals/{deal_id}/market_orders")
        if not orders:
            return None, None
        last = orders[-1]
        price = float(last.get("price") or 0)
        qty = float(last.get("quantity") or 0)
        return price, qty
    except Exception as e:
        log(f"[ERROR] Ошибка получения ордеров: {e}")
        return None, None

# === Мониторинг сделок ===
def monitor_deals():
    log("▶️ Starting deals monitor...")
    while True:
        try:
            deals = get("/ver1/deals", {"scope": "active", "limit": 100})
            for deal in deals:
                deal_id = deal["id"]
                pair = deal["pair"]
                quote = pair.split("_")[-1]
                dca = int(deal.get("completed_safety_orders_count") or 0)
                status = deal["status"]
                prev = known_deals.get(deal_id, {"dca": 0, "status": ""})

                if deal_id not in known_deals:
                    price, qty = get_last_order_price_and_qty(deal_id)
                    if price and qty:
                        msg = (
                            f"🛒 Новая сделка по цене: 1 {quote} = {price:.6f} USDT\n"
                            f"📊 Объём: {qty:.6f} {quote}"
                        )
                        send_telegram_message(msg)

                elif dca > prev["dca"]:
                    price, qty = get_last_order_price_and_qty(deal_id)
                    if price and qty:
                        msg = (
                            f"🛒 Докупка: 1 {quote} = {price:.6f} USDT\n"
                            f"📊 Кол-во: {qty:.6f} {quote}"
                        )
                        send_telegram_message(msg)

                if status == "completed" and prev["status"] != "completed":
                    profit = float(deal.get("actual_usd_profit") or 0)
                    msg = f"✅ Сделка завершена. Прибыль: {profit:.2f} USDT"
                    send_telegram_message(msg)

                known_deals[deal_id] = {"dca": dca, "status": status}

        except Exception as e:
            log(f"[ERROR] {e}")

        time.sleep(POLL_INTERVAL)

# === Запуск ===
if name == "__main__":
    monitor_deals()