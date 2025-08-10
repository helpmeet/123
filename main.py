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
BOT_ID = os.getenv("BOT_ID")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

if not BOT_ID:
    raise ValueError("Ошибка: переменная окружения BOT_ID не задана!")

# Состояние сделок
known_deals = {}
bot_start_time = datetime.now(timezone.utc)

# === HTTP-сервер для Render ===
def fake_server():
    PORT = int(os.environ.get("PORT", 8000))
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"[{datetime.now(timezone.utc)}] 🌐 HTTP-сервер запущен на порту {PORT}")
        httpd.serve_forever()

# === Подпись запроса ===
def sign_request(path, params):
    query = '&'.join(f"{k}={v}" for k, v in sorted(params.items()))
    payload = f"{path}?{query}" if query else path
    return hmac.new(
        THREECOMMAS_API_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

# === Парсинг ISO-дат ===
def parse_iso_datetime(dt_str):
    if dt_str is None:
        return None
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return datetime.fromisoformat(dt_str)

# === Получение сделок по конкретному боту ===
def get_deals():
    params = {"limit": 100, "bot_id": BOT_ID}
    signature = sign_request(API_PATH, params)
    headers = {
        "APIKEY": THREECOMMAS_API_KEY,
        "Signature": signature
    }
    try:
        resp = requests.get(API_URL, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and "data" in data:
            return data["data"]
        else:
            print(f"[DEBUG] Неизвестный формат сделок: {data}")
            return []
    except Exception as e:
        print(f"[ERROR] Ошибка при получении сделок: {e}")
        return []

# === Получение статистики конкретного бота ===
def get_bot_stats():
    bot_url = f"https://api.3commas.io/public/api/ver1/bots/{BOT_ID}"
    signature = sign_request(f"/public/api/ver1/bots/{BOT_ID}", {})
    headers = {
        "APIKEY": THREECOMMAS_API_KEY,
        "Signature": signature
    }
    try:
        resp = requests.get(bot_url, headers=headers)
        resp.raise_for_status()
        bot = resp.json()
        start_date = parse_iso_datetime(bot["created_at"])
        days_running = max((datetime.now(timezone.utc) - start_date).days, 1)

        profit_total = float(bot.get("finished_deals_profit_usd", 0)) * 10
        roi = (profit_total / START_BUDGET) * (365 / days_running) * 100 * 10

        return {
            "days_running": days_running,
            "completed_deals": bot.get("finished_deals_count", 0),
            "profit_total": profit_total,
            "roi": roi,
            "positive_deals": bot.get("finished_deals_count", 0),
            "negative_deals": 0
        }
    except Exception as e:
        print(f"[ERROR] Ошибка при получении статистики бота: {e}")
        return None

# === Telegram-сообщения ===
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, data=payload)
        print(f"[DEBUG] Telegram status: {resp.status_code}")
        if not resp.ok:
            print(f"[ERROR] Telegram: {resp.text}")
    except Exception as e:
        print(f"[ERROR] Ошибка при отправке в Telegram: {e}")

# === Мониторинг сделок ===
def monitor_deals():
    print(f"[{datetime.now(timezone.utc)}] ▶️ Старт мониторинга сделок")
    while True:
        deals = get_deals()
        print(f"[DEBUG] Получено сделок: {len(deals)}")

        for deal in deals:
            deal_id = deal["id"]
            status = deal["status"]
            pair = deal["pair"].upper()
            bought_avg = float(deal.get("bought_average") or 0)
            bought_vol = float(deal.get("bought_volume") or 0)
            profit_usd = float(deal.get("actual_usd_profit") or 0) * 10

            if deal_id not in known_deals:
                known_deals[deal_id] = {"status": status, "entry_posted": False, "search_posted": False}

            prev = known_deals[deal_id]

            # 1. Закрытие сделки
            if status == "completed" and prev["status"] != "completed":
                msg = (
                    f"✅ Сделка закрыта по {pair}\n"
                    f"💵 Профит: +{profit_usd:.2f} USDT\n"
                )
                stats = get_bot_stats()
                if stats:
                    msg += (
                        f"📊 Статистика:\n"
                        f"📅 Дней работы: {stats['days_running']}\n"
                        f"🔁 Сделок: {stats['completed_deals']}\n"
                        f"📈 Плюсовых: {stats['positive_deals']}  📉 Минусовых: {stats['negative_deals']}\n"
                        f"💼 Бюджет: ${START_BUDGET:.2f}\n"
                        f"📊 Общая прибыль: ${stats['profit_total']:.2f}\n"
                        f"📈 Доходность (годовых): {stats['roi']:.2f}%"
                    )
                send_telegram_message(msg)

            # 2. Ищу точку входа (новая сделка с bought_volume=0)
            if status in ["active", "bought"] and bought_vol == 0 and not prev["search_posted"]:
                send_telegram_message(f"📊 Ищу точку входа по {pair}")
                prev["search_posted"] = True

            # 3. Вход в сделку (bought_vol > 0)
            if bought_vol > 0 and bought_avg > 0 and not prev["entry_posted"]:
                send_telegram_message(
                    f"📈 Вход в сделку по {pair}\n"
                    f"💵 Цена: {bought_avg:.4f}\n"
                    f"📦 Объём: {bought_vol:.2f} USDT"
                )
                prev["entry_posted"] = True

            prev["status"] = status

        time.sleep(POLL_INTERVAL)

# === Запуск ===
if __name__ == "__main__":
    threading.Thread(target=fake_server, daemon=True).start()
    monitor_deals()
