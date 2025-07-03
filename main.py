import os
import time
import hmac
import hashlib
import requests

THREECOMMAS_API_KEY = os.getenv("THREECOMMAS_API_KEY")
THREECOMMAS_API_SECRET = os.getenv("THREECOMMAS_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

API_URL = "https://api.3commas.io/public/api/ver1/deals"
known_deals = {}

def sign_request(params):
    payload = '&'.join([f'{k}={v}' for k, v in sorted(params.items())])
    signature = hmac.new(
        bytes(THREECOMMAS_API_SECRET, 'utf-8'),
        msg=bytes(payload, 'utf-8'),
        digestmod=hashlib.sha256
    ).hexdigest()
    return signature

def get_deals():
    params = {"limit": 20}
    headers = {
        "APIKEY": THREECOMMAS_API_KEY,
        "Signature": sign_request(params)
    }
    response = requests.get(API_URL, headers=headers, params=params)
    response.raise_for_status()
    return response.json()

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    requests.post(url, data=data)

def monitor_deals():
    while True:
        try:
            deals = get_deals()
            for deal in deals:
                deal_id = deal["id"]
                dca_count = deal["completed_safety_orders_count"]
                status = deal["status"]

                if deal_id not in known_deals:
                    msg = (
                        f"📈 <b>Новая сделка:</b> {deal['pair']}\n"
                        f"🟢 Статус: {status}\n"
                        f"💵 Цена входа: {deal['bought_average'] or '—'}\n"
                        f"🧱 DCA: 0"
                    )
                    send_telegram_message(msg)
                    known_deals[deal_id] = {"dca": dca_count, "status": status}

                else:
                    prev = known_deals[deal_id]

                    if dca_count > prev["dca"]:
                        msg = (
                            f"➕ <b>DCA докупка</b> #{dca_count} в сделке {deal['pair']}\n"
                            f"Общий объем: {deal['bought_volume']} {deal['base_order_volume_type']}"
                        )
                        send_telegram_message(msg)
                        known_deals[deal_id]["dca"] = dca_count

                    if status == "completed" and prev["status"] != "completed":
                        msg = (
                            f"✅ <b>Сделка закрыта</b>: {deal['pair']}\n"
                            f"📈 Профит: {deal['actual_profit_percentage']}%\n"
                            f"🧱 DCA шагов: {dca_count}"
                        )
                        send_telegram_message(msg)
                        known_deals[deal_id]["status"] = "completed"

        except Exception as e:
            print(f"Ошибка: {e}")
        time.sleep(POLL_INTERVAL)

if name == "__main__":
    monitor_deals()