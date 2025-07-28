import os
import time
import threading
import hmac
import hashlib
import requests
from datetime import datetime
from flask import Flask, jsonify

# --- Настройки из окружения ---
API_KEY = os.getenv("THREECOMMAS_API_KEY")
API_SECRET = os.getenv("THREECOMMAS_API_SECRET")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))
PORT = int(os.getenv("PORT", "8000"))

API_BASE = "https://api.3commas.io/public/api"
known_deals = {}

app = Flask(__name__)

def log(msg):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

def sign(path, params):
    # Формируем подпись для 3Commas API
    query = '&'.join(f"{k}={v}" for k, v in sorted(params.items())) if params else ""
    payload = f"{path}?{query}" if query else path
    return hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

def get(path, params=None):
    params = params or {}
    headers = {
        "APIKEY": API_KEY,
        "Signature": sign(path, params)
    }
    url = API_BASE + path
    resp = requests.get(url, headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.post(url, data=payload)
        if not resp.ok:
            log(f"[ERROR] Telegram message failed: {resp.text}")
    except Exception as e:
        log(f"[ERROR] Exception sending Telegram message: {e}")

def get_last_order_price_and_qty(deal_id):
    try:
        path = f"/ver1/deals/{deal_id}/market_orders"
        orders = get(path)
        if not orders:
            return None, None
        last = orders[-1]
        price = float(last.get("price") or 0)
        qty = float(last.get("quantity") or 0)
        return price, qty
    except Exception as e:
        log(f"[ERROR] Error fetching market orders for deal {deal_id}: {e}")
        return None, None

def get_bot_stats():
    try:
        deals = get("/ver1/deals", {"scope": "finished", "limit": 1000})
        accounts = get("/ver1/accounts")

        total_deals = len(deals)
        total_profit = sum(float(d.get("actual_usd_profit") or 0) for d in deals)

        if deals:
            first_closed = min(
                datetime.fromisoformat(d["closed_at"].replace("Z", "")) 
                for d in deals if d.get("closed_at")
            )
            days_working = max(1, (datetime.utcnow() - first_closed).days)
        else:
            days_working = 0

        usdt_account = next((a for a in accounts if a["currency_code"] == "USDT"), accounts[0])
        initial = float(usdt_account.get("initial_total") or 0)
        balance = float(usdt_account.get("available_funds") or 0)

        monthly_pct = (total_profit / initial) * (30 / days_working) * 100 if initial > 0 else 0
        yearly_pct = monthly_pct * 12

        return {
            "total_deals": total_deals,
            "total_profit": total_profit,
            "days_working": days_working,
            "initial": initial,
            "balance": balance,
            "monthly_pct": monthly_pct,
            "yearly_pct": yearly_pct
        }
    except Exception as e:
        log(f"[ERROR] Error fetching bot stats: {e}")
        return {}

def monitor_deals():
    log("▶️ Starting deals monitor...")
    while True:
        try:
            deals = get("/ver1/deals", {"scope": "active", "limit": 100})
            for deal in deals:
                deal_id = deal["id"]
                pair = deal["pair"]
                status = deal["status"]
                dca = int(deal.get("completed_safety_orders_count") or 0)
                quote = pair.split("_")[-1]

                prev = known_deals.get(deal_id, {"dca": 0, "status": ""})

                # Новая сделка
                if deal_id not in known_deals:
                   price, qty = get_last_order_price_and_qty(deal_id)
                    if price and qty:
                        msg = (
                            f"🛒 Покупаю по цене 1 {quote} = {price:.6f} USDT\n"
                            f"📊 Объем сделки: {qty:.6f} {quote}"
                        )
                        send_telegram_message(msg)

                # Докупка
                elif dca > prev["dca"]:
                    price, qty = get_last_order_price_and_qty(deal_id)
                    if price and qty:
                        msg = (
                            f"🛒 Докупаю по цене 1 {quote} = {price:.6f} USDT\n"
                            f"📊 Объем докупки: {qty:.6f} {quote}"
                        )
                        send_telegram_message(msg)

                # Сделка завершена
                if status == "completed" and prev["status"] != "completed":
                    profit = float(deal.get("actual_usd_profit") or 0)
                    created = datetime.fromisoformat(deal["created_at"].replace("Z", ""))
                    closed = datetime.fromisoformat(deal["closed_at"].replace("Z", ""))
                    duration = int((closed - created).total_seconds() // 60)

                    stats = get_bot_stats()

                    msg = (
                        "✅ Сделка завершена ✅\n"
                        f"  💰 Бот заработал = {profit:.2f} USDT\n"
                        f"  ⌚️ Сделка заняла: {duration} минут\n\n"
                        f"  ⚙️ Статистика бота:\n"
                        f"  🤖 Бот работает: {stats.get('days_working', 0)} дней\n"
                        f"  🤝 Совершил сделок: {stats.get('total_deals', 0)}\n"
                        f"  🏦 Начальный бюджет: {stats.get('initial', 0):.2f}$\n"
                        f"  🤑 Чистая прибыль: {stats.get('total_profit', 0):.2f}$\n"
                        f"  💳 Итого на балансе: {stats.get('balance', 0):.2f}$\n"
                        f"  💵 % в месяц: {stats.get('monthly_pct', 0):.2f}%\n"
                        f"  💰 % годовых: {stats.get('yearly_pct', 0):.2f}%"
                    )
                    send_telegram_message(msg)

                known_deals[deal_id] = {"dca": dca, "status": status}
        except Exception as e:
            log(f"[ERROR] Exception in monitor loop: {e}")

        time.sleep(POLL_INTERVAL)

@app.route("/")
def healthcheck():
    return jsonify({"status": "ok", "message": "Bot is running"})

if name == "__main__":
    monitor_thread = threading.Thread(target=monitor_deals, daemon=True)
    monitor_thread.start()
    log(f"Starting Flask server on port {PORT}")
    app.run(host="0.0.0.0", port=PORT)