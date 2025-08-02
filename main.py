import os
import time
import hmac
import hashlib
import requests
import threading
import http.server
import socketserver
from datetime import datetime, timezone

# === Константы ===
START_BUDGET = 6000.0
API_PATH = "/public/api/ver1/deals"
API_URL = "https://api.3commas.io" + API_PATH

# === Настройки из окружения ===
THREECOMMAS_API_KEY = os.getenv("THREECOMMAS_API_KEY")
THREECOMMAS_API_SECRET = os.getenv("THREECOMMAS_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

# Состояние сделок
known_deals = {}

# === HTTP-сервер для Render ===
def fake_server():
    PORT = int(os.environ.get("PORT", 8000))
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"[{datetime.now(timezone.utc)}] 🌐 HTTP-сервер запущен на порту {PORT}")
        httpd.serve_forever()

# === IP-лог ===
def log_external_ip():
    try:
        ip = requests.get("https://api.ipify.org").text
        print(f"[{datetime.now(timezone.utc)}] [DEBUG] Внешний IP Render: {ip}")
    except Exception as e:
        print(f"[{datetime.now(timezone.utc)}] [DEBUG] Не удалось получить внешний IP: {e}")

# === Подпись запроса ===
def sign_request(path, params):
    query = '&'.join(f"{k}={v}" for k, v in sorted(params.items()))
    payload = f"{path}?{query}"
    return hmac.new(
        THREECOMMAS_API_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

# === Получение сделок ===
def get_deals():
    params = {"limit": 20}
    signature = sign_request(API_PATH, params)
    headers = {
        "APIKEY": THREECOMMAS_API_KEY,
        "Signature": signature
    }

    try:
        resp = requests.get(API_URL, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[{datetime.now(timezone.utc)}] ❌ Ошибка при получении сделок: {e}")
        return []

# === Получение статистики бота ===
def get_bot_stats():
    bots_url = "https://api.3commas.io/public/api/ver1/bots"
    params = {"limit": 1}
    signature = sign_request("/public/api/ver1/bots", params)
    headers = {
        "APIKEY": THREECOMMAS_API_KEY,
        "Signature": signature
    }

    try:
        resp = requests.get(bots_url, headers=headers, params=params)
        resp.raise_for_status()
        bots = resp.json()
        if not bots:
            return None

        bot = bots[0]
        start_date = datetime.fromisoformat(bot["created_at"].replace("Z", "+00:00"))
        days_running = (datetime.now(timezone.utc) - start_date).days or 1  # избегаем деление на 0
        completed_deals = int(bot["completed_deals_count"])
        final_balance = float(bot["usd_amount_scaled"])
        profit_total = START_BUDGET - (final_balance * 10)
        roi = (profit_total / START_BUDGET) / days_running * 365 * 100

        return {
            "bot_name": "🚀 Rocket AI Bot",
            "start_date": start_date.strftime("%Y-%m-%d"),
            "days_running": days_running,
            "completed_deals": completed_deals,
            "profit_total": profit_total,
            "roi": roi,
            "positive_deals": completed_deals,
            "negative_deals": 0
        }
    except Exception as e:
        print(f"[{datetime.now(timezone.utc)}] ❌ Ошибка при получении статистики: {e}")
        return None

# === Получение цены входа сделки ===
def get_entry_price(deal):
    price_fields = ["bought_average", "entry_price", "entry_price_usdt"]

    for field in price_fields:
        val = deal.get(field)
        if val is not None:
            try:
                return float(val)
            except:
                pass

    completed_orders = deal.get("completed_safety_orders") or []
    if completed_orders:
        try:
            price = float(completed_orders[0].get("price"))
            return price
        except:
            pass

    return 0.0

# === Telegram-сообщение ===
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.post(url, data=payload)
        print(f"[{datetime.now(timezone.utc)}] [DEBUG] Telegram status: {resp.status_code}")
        if not resp.ok:
            print(f"[{datetime.now(timezone.utc)}] ❌ Telegram error: {resp.text}")
    except Exception as e:
        print(f"[{datetime.now(timezone.utc)}] ❌ Ошибка при отправке в Telegram: {e}")

# === Основной цикл мониторинга сделок ===
def monitor_deals():
    print(f"[{datetime.now(timezone.utc)}] ▶️ Старт мониторинга сделок")
    while True:
        deals = get_deals()
        print(f"[{datetime.now(timezone.utc)}] Получено сделок: {len(deals)}")
        for deal in deals:
            deal_id = deal.get("id")
            status = deal.get("status", "")
            pair = deal.get("pair", "")
            dca = deal.get("completed_safety_orders_count", 0)

            bought_avg = get_entry_price(deal)
            bought_vol_raw = float(deal.get("bought_volume") or 0)
            bought_vol = bought_vol_raw * 10
            profit_pct = float(deal.get("actual_profit_percentage") or 0)
            profit_usd = bought_vol * (profit_pct / 100)

            if deal_id not in known_deals:
                msg = (
                    f"📈 <b>Новая сделка</b> по паре <b>{pair}</b>\n"
                    f"🟢 Статус: <code>{status}</code>\n"
                    f"💵 Цена входа: {bought_avg:.4f}\n"
                    f"📦 Объём: {bought_vol:.2f} USDT"
                )
                send_telegram_message(msg)
                known_deals[deal_id] = {"status": status, "dca": dca}
            else:
                prev = known_deals[deal_id]

                if dca > prev["dca"]:
                    msg = (
                        f"➕ <b>Докупил</b> #{dca} в сделке <b>{pair}</b>\n"
                        f"📊 Объём: {bought_vol:.2f} USDT"
                    )
                    send_telegram_message(msg)
                    known_deals[deal_id]["dca"] = dca

                if status == "completed" and prev["status"] != "completed":
                    msg = (
                        f"✅ <b>Сделка завершена</b>: <b>{pair}</b>\n"
                        f"📈 Прибыль: {profit_pct:.2f}%\n"
                        f"💰 В долларах: {profit_usd:.2f} USDT\n"
                        f"💵 Цена входа: {bought_avg:.4f}\n"
                        f"📦 Объём: {bought_vol:.2f} USDT\n\n"
                    )

                    stats = get_bot_stats()
                    if stats:
                        msg += (
                            f"<b>📊 Статистика стратегии:</b>\n"
                            f"{stats['bot_name']}\n"
                            f"📅 Старт: {stats['start_date']} ({stats['days_running']} дней)\n"
                            f"🔁 Сделок: {stats['completed_deals']}\n"
                            f"📈 Плюсовых: {stats['positive_deals']}  📉 Минусовых: {stats['negative_deals']}\n"
                            f"💼 Стартовый бюджет: ${START_BUDGET:.2f}\n"
                            f"📊 Общая прибыль: ${stats['profit_total']:.2f}\n"
                            f"📈 Доходность (годовых): {stats['roi']:.2f}%"
                        )
                    else:
                        msg += "⚠️ Не удалось получить статистику бота."

                    send_telegram_message(msg)
                    known_deals[deal_id]["status"] = status

        time.sleep(POLL_INTERVAL)

# === Запуск ===
if __name__ == "__main__":
    log_external_ip()
    threading.Thread(target=fake_server, daemon=True).start()
    monitor_deals()
