import os
import time
import hmac
import hashlib
import requests
import threading
import http.server
import socketserver
from datetime import datetime

# === Настройки из окружения ===
THREECOMMAS_API_KEY = os.getenv("THREECOMMAS_API_KEY")
THREECOMMAS_API_SECRET = os.getenv("THREECOMMAS_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

# === Константы API ===
API_PATH = "/public/api/ver1/deals"
API_URL = "https://api.3commas.io" + API_PATH

# Словарь для отслеживания состояния сделок
known_deals = {}

# === Фейковый HTTP-сервер, чтобы Render не засыпал ===
def fake_server():
    PORT = int(os.environ.get("PORT", 8000))
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"[{datetime.utcnow()}] 🌐 HTTP-сервер запущен на порту {PORT}")
        httpd.serve_forever()

# === Логирование внешнего IP для whitelist 3Commas ===
def log_external_ip():
    try:
        ip = requests.get("https://api.ipify.org").text
        print(f"[{datetime.utcnow()}] [DEBUG] Внешний IP Render: {ip}")
    except Exception as e:
        print(f"[{datetime.utcnow()}] [DEBUG] Не удалось получить внешний IP: {e}")

# === Подпись запроса в 3Commas ===
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
        print(f"[DEBUG] HTTP status: {resp.status_code}")
        print(f"[DEBUG] Response text: {resp.text[:250]}")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[{datetime.utcnow()}] ❌ Ошибка при получении сделок: {e}")
        return []

# === Отправка сообщения в Telegram с логом ===
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.post(url, data=payload)
        print(f"[{datetime.utcnow()}] [DEBUG] Telegram status: {resp.status_code}")
        if not resp.ok:
            print(f"[{datetime.utcnow()}] ❌ Telegram error: {resp.text}")
    except Exception as e:
        print(f"[{datetime.utcnow()}] ❌ Ошибка при отправке в Telegram: {e}")

# === Основная логика обработки сделок ===
def monitor_deals():
    print(f"[{datetime.utcnow()}] ▶️ Старт мониторинга сделок")
    while True:
        deals = get_deals()
        print(f"[{datetime.utcnow()}] Получено сделок: {len(deals)}")
        for deal in deals:
            deal_id = deal.get("id")
            status = deal.get("status", "")
            pair = deal.get("pair", "")
            dca = deal.get("completed_safety_orders_count", 0)

            bought_avg = float(deal.get("bought_average") or 0)
            bought_vol = float(deal.get("bought_volume") or 0) * 10
            profit_pct = float(deal.get("actual_profit_percentage") or 0) * 10

            # Лог состояния
            print(f"[DEBUG] Deal ID {deal_id}, status {status}, dca {dca}")

            # Новая сделка
            if deal_id not in known_deals:
                msg = (
                    f"📈 <b>Новая сделка</b> по паре <b>{pair}</b>\n"
                    f"🟢 Статус: <code>{status}</code>\n"
                    f"💵 Цена входа: {bought_avg:.2f}"
                )
                send_telegram_message(msg)
                known_deals[deal_id] = {"status": status, "dca": dca}
            else:
                prev = known_deals[deal_id]

                # Докупил DCA
if dca > prev["dca"]:
                    msg = (
                        f"➕ <b>Докупил</b> #{dca} в сделке <b>{pair}</b>\n"
                        f"📊 Объём: {bought_vol:.2f} {deal.get('base_order_volume_type','')}"
                    )
                    send_telegram_message(msg)
                    known_deals[deal_id]["dca"] = dca

                # Сделка завершена
                if status == "completed" and prev["status"] != "completed":
                    msg = (
                        f"✅ <b>Сделка завершена</b>: <b>{pair}</b>\n"
                        f"📈 Прибыль: {profit_pct:.2f}%"
                    )
                    send_telegram_message(msg)
                    known_deals[deal_id]["status"] = status

        time.sleep(POLL_INTERVAL)

# === Запуск приложения ===
if __name__ == "__main__":
    log_external_ip()
    threading.Thread(target=fake_server, daemon=True).start()
    monitor_deals()