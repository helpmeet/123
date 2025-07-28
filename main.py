import os
import time
import hashlib
import hmac
import requests
import threading
import http.server
import socketserver
from datetime import datetime

# === Конфигурация из переменных окружения ===
API_KEY = os.getenv("THREECOMMAS_API_KEY")
API_SECRET = os.getenv("THREECOMMAS_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

BASE_URL = "https://api.3commas.io/public/api/ver1"
known_deals = {}

# === Подпись и авторизация запроса ===
def signed_request(method, path, params=None):
if params is None:
params = {}

params["timestamp"] = int(time.time() * 1000)
payload = '&'.join(f"{k}={v}" for k, v in sorted(params.items()))
signature = hmac.new(
API_SECRET.encode(),
msg=payload.encode(),
digestmod=hashlib.sha256
).hexdigest()

headers = {
"APIKEY": API_KEY,
"Signature": signature
}

url = f"{BASE_URL}/{path}"
if method.upper() == "GET":
r = requests.get(url, headers=headers, params=params)
else:
r = requests.post(url, headers=headers, json=params)

print(f"[DEBUG] Status: {r.status_code}")
print(f"[DEBUG] Response: {r.text[:300]}")
r.raise_for_status()
return r.json()

# === Получение сделок (авторизованный запрос) ===
def get_deals():
return signed_request("GET", "deals", {
"scope": "active",
"limit": 20
})

# === Telegram уведомление ===
def send_telegram_message(text):
url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
payload = {
"chat_id": TELEGRAM_CHAT_ID,
"text": text,
"parse_mode": "HTML"
}
try:
resp = requests.post(url, data=payload)
print(f"[DEBUG] Telegram status: {resp.status_code}")
if resp.status_code != 200:
print(f"[DEBUG] Telegram error: {resp.text}")
except Exception as e:
print(f"[ERROR] Telegram exception: {e}")

# === Основная логика мониторинга ===
def monitor_deals():
while True:
try:
deals = get_deals()
print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Получено сделок: {len(deals)}")

for deal in deals:
deal_id = deal["id"]
status = deal["status"]
dca_count = deal["completed_safety_orders_count"]
bought_avg = float(deal.get("bought_average") or 0)
bought_vol = float(deal.get("bought_volume") or 0)
profit_pct = float(deal.get("actual_profit_percentage") or 0)

if deal_id not in known_deals:
msg = (
f"📈 <b>Новая сделка</b> по паре <b>{deal['pair']}</b>\n"
f"🟢 Статус: <code>{status}</code>\n"
f"💵 Цена входа: {bought_avg:.4f}"
)
send_telegram_message(msg)
known_deals[deal_id] = {"dca": dca_count, "status": status}

else:
prev = known_deals[deal_id]

# Новая Safety-покупка
if dca_count > prev["dca"]:
msg = (
f"➕ <b>Докупка #{dca_count}</b> в сделке <b>{deal['pair']}</b>\n"
f"📊 Объём: {bought_vol:.2f}"
)
send_telegram_message(msg)
known_deals[deal_id]["dca"] = dca_count

# Сделка завершена
if status == "completed" and prev["status"] != "completed":
msg = (
f"✅ <b>Сделка завершена</b> по паре <b>{deal['pair']}</b>\n"
f"📈 Прибыль: {profit_pct:.2f}%"
)
send_telegram_message(msg)
known_deals[deal_id]["status"] = status

except Exception as e:
print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ❌ Ошибка: {e}")

time.sleep(POLL_INTERVAL)

# === Фейковый сервер (для Render, чтобы не засыпал) ===
def fake_server():
PORT = int(os.environ.get("PORT", 8000))
Handler = http.server.SimpleHTTPRequestHandler
with socketserver.TCPServer(("", PORT), Handler) as httpd:
print(f"🌐 Fake HTTP server running on port {PORT}")
httpd.serve_forever()

# === Старт ===
if __name__ == "__main__":
threading.Thread(target=fake_server, daemon=True).start()
print("📡 Старт мониторинга сделок 3Commas...")
monitor_deals()
