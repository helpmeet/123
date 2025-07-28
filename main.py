import os
import time
import hmac
import hashlib
import requests
import threading
import http.server
import socketserver
from datetime import datetime

# === 🔐 Настройки через переменные окружения ===
THREECOMMAS_API_KEY = os.getenv("THREECOMMAS_API_KEY")
THREECOMMAS_API_SECRET = os.getenv("THREECOMMAS_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

API_URL = "https://api.3commas.io/public/api/ver1/deals"
known_deals = {}

# === 🌐 Фейковый сервер для Render (не засыпал) ===
def fake_server():
PORT = int(os.environ.get("PORT", 8000))
Handler = http.server.SimpleHTTPRequestHandler
with socketserver.TCPServer(("", PORT), Handler) as httpd:
print("🌐 Fake HTTP server running on port", PORT)
httpd.serve_forever()

# === 🧠 Внешний IP (для отладки) ===
def log_external_ip():
try:
ip = requests.get("https://api.ipify.org").text
print(f"[DEBUG] Внешний IP Render: {ip}")
except Exception as e:
print(f"[DEBUG] Не удалось получить внешний IP: {e}")

# === 🔐 Подпись запроса к 3Commas ===
def sign_request(params):
payload = '&'.join([f'{k}={v}' for k, v in sorted(params.items())])
signature = hmac.new(
bytes(THREECOMMAS_API_SECRET, 'utf-8'),
msg=bytes(payload, 'utf-8'),
digestmod=hashlib.sha256
).hexdigest()
return signature

# === 📊 Получение активных сделок ===
def get_deals():
params = {"limit": 20}
headers = {
"APIKEY": THREECOMMAS_API_KEY,
"Signature": sign_request(params)
}
response = requests.get(API_URL, headers=headers, params=params)
print(f"[DEBUG] HTTP status: {response.status_code}")
print(f"[DEBUG] Response text: {response.text[:300]}")
response.raise_for_status()
return response.json()

# === 📤 Отправка уведомлений в Telegram ===
def send_telegram_message(text):
url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
data = {
"chat_id": TELEGRAM_CHAT_ID,
"text": text,
"parse_mode": "HTML"
}
try:
resp = requests.post(url, data=data)
print(f"[DEBUG] Telegram send status: {resp.status_code}")
if resp.status_code != 200:
print(f"[DEBUG] Telegram error: {resp.text}")
except Exception as e:
print(f"Ошибка при отправке в Telegram: {e}")

# === 🔁 Основная логика мониторинга ===
def monitor_deals():
while True:
try:
deals = get_deals()
print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Получено сделок: {len(deals)}")

for deal in deals:
deal_id = deal["id"]
print(f"[DEBUG] Обрабатывается сделка ID: {deal_id}, статус: {deal['status']}")

dca_count = deal["completed_safety_orders_count"]
status = deal["status"]
bought_avg = float(deal.get("bought_average") or 0)
bought_vol = float(deal.get("bought_volume") or 0) * 10
profit_pct = float(deal.get("actual_profit_percentage", 0)) * 10

# Тестовая отправка
send_telegram_message(f"‼️ Проверка. Сделка: {deal['pair']} ID: {deal_id}")

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
f"📊 Объём: {bought_vol:.2f} {deal['base_order_volume_type']}"
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
print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ❌ Ошибка при получении сделок: {e}")
time.sleep(POLL_INTERVAL)

# === 🚀 Запуск ===
if __name__ == "__main__":
threading.Thread(target=fake_server, daemon=True).start()
log_external_ip()
print("📡 Мониторинг сделок 3Commas запущен...")
monitor_deals()
