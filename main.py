import os
import time
import hmac
import hashlib
import requests
import threading
import http.server
import socketserver

# === Настройки ===
THREECOMMAS_API_KEY = os.getenv("THREECOMMAS_API_KEY")
THREECOMMAS_API_SECRET = os.getenv("THREECOMMAS_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

API_PATH = "/public/api/ver1/deals"
API_URL = "https://api.3commas.io" + API_PATH
known_deals = {}

# === Фейковый сервер для Render (чтобы не засыпал) ===
def fake_server():
    PORT = int(os.environ.get("PORT", 8000))
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🌐 Фейковый HTTP сервер запущен на порту {PORT}")
        httpd.serve_forever()

# === Подпись запроса ===
def sign_request(path, params):
    query_string = '&'.join(f"{k}={v}" for k, v in sorted(params.items()))
    payload = f"{path}?{query_string}"
    signature = hmac.new(
        bytes(THREECOMMAS_API_SECRET, 'utf-8'),
        msg=bytes(payload, 'utf-8'),
        digestmod=hashlib.sha256
    ).hexdigest()
    return signature

# === Получение сделок с отладкой ===
def get_deals():
    params = {"limit": 20}
    signature = sign_request(API_PATH, params)
    headers = {
        "APIKEY": THREECOMMAS_API_KEY,
        "Signature": signature
    }

    try:
        response = requests.get(API_URL, headers=headers, params=params)

        print(f"[DEBUG] HTTP status: {response.status_code}")
        print(f"[DEBUG] Response text: {response.text[:300]}")  # первые 300 символов

        response.raise_for_status()
        return response.json()

    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ❌ Ошибка при получении сделок: {e}")
        return []

# === Отправка сообщений в Telegram с логами ===
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.post(url, data=data)
        if resp.ok:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ✅ Отправлено сообщение в Telegram:\n{text}\n")
        else:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ❌ Ошибка Telegram: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ❌ Ошибка при отправке в Telegram: {e}")

# === Основной мониторинг сделок ===
def monitor_deals():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ▶️ Старт мониторинга сделок")
    while True:
        try:
            deals = get_deals()
            for deal in deals:
                deal_id = deal["id"]
                dca_count = deal["completed_safety_orders_count"]
                status = deal["status"]

                bought_avg = float(deal.get("bought_average") or 0)
                bought_vol = float(deal.get("bought_volume") or 0) * 10
                profit_pct = float(deal.get("actual_profit_percentage") or 0) * 10

                if deal_id not in known_deals:
                    msg = (
                        f"📈 <b>Новая сделка</b> по паре <b>{deal['pair']}</b>\n"
                        f"🟢 Статус: <code>{status}</code>\n"
                        f"💵 Цена входа: {bought_avg:.2f}"
                    )
                    send_telegram_message(msg) 
                    known_deals[deal_id] = {"dca": dca_count, "status": status}

                else:
                    prev = known_deals[deal_id]

                    if dca_count > prev["dca"]:
                        msg = (
                            f"➕ <b>Докупил</b> #{dca_count} в сделке <b>{deal['pair']}</b>\n"
                            f"📊 Объём: {bought_vol:.2f} {deal.get('base_order_volume_type', '')}"
                        )
                        send_telegram_message(msg)
                        known_deals[deal_id]["dca"] = dca_count

                    if status == "completed" and prev["status"] != "completed":
                        msg = (
                            f"✅ <b>Сделка завершена</b>: <b>{deal['pair']}</b>\n"
                            f"📈 Прибыль: {profit_pct:.2f}%"
                        )
                        send_telegram_message(msg)
                        known_deals[deal_id]["status"] = status

        except Exception as e:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ❌ Ошибка в основном цикле: {e}")
        time.sleep(POLL_INTERVAL)

# === Запуск приложения ===
if __name__ == "__main__":
    threading.Thread(target=fake_server, daemon=True).start()
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 📡 Мониторинг сделок 3Commas запущен...")
    monitor_deals()