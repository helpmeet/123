import os
import time
import hmac
import hashlib
import requests
import threading
import http.server
import socketserver
from datetime import datetime, timezone
import dateutil.parser

# === Константы ===
START_BUDGET = 6000.0
API_PATH = "/public/api/ver1/deals"
API_URL = "https://api.3commas.io" + API_PATH
LEVERAGE = 10  # множитель для объёмов

# === Настройки из окружения ===
THREECOMMAS_API_KEY = os.getenv("THREECOMMAS_API_KEY")
THREECOMMAS_API_SECRET = os.getenv("THREECOMMAS_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

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

# === Получение внешнего IP (для отладки) ===
def log_external_ip():
    try:
        ip = requests.get("https://api.ipify.org").text
        print(f"[{datetime.now(timezone.utc)}] [DEBUG] Внешний IP Render: {ip}")
    except Exception as e:
        print(f"[{datetime.now(timezone.utc)}] [DEBUG] Не удалось получить внешний IP: {e}")

# === Подпись запроса ===
def sign_request(path, params):
    query = '&'.join(f"{k}={v}" for k, v in sorted(params.items()))
    payload = f"{path}?{query}" if query else path
    return hmac.new(
        THREECOMMAS_API_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

# === Получение списка сделок ===
def get_deals():
    params = {"limit": 100}
    signature = sign_request(API_PATH, params)
    headers = {
        "APIKEY": THREECOMMAS_API_KEY,
        "Signature": signature
    }

    try:
        resp = requests.get(API_URL, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and 'data' in data:
            return data['data']
        elif isinstance(data, list):
            return data
        else:
            print(f"[{datetime.now(timezone.utc)}] ❌ Неизвестный формат данных сделок: {data}")
            return []
    except Exception as e:
        print(f"[{datetime.now(timezone.utc)}] ❌ Ошибка при получении сделок: {e}")
        return []

# === Получение статистики бота ===
def get_bot_stats():
    bots_url = "https://api.3commas.io/public/api/ver1/bots"
    try:
        params = {"limit": 1}
        signature = sign_request("/public/api/ver1/bots", params)
        headers = {
            "APIKEY": THREECOMMAS_API_KEY,
            "Signature": signature
        }

        resp = requests.get(bots_url, headers=headers, params=params)
        resp.raise_for_status()
        bots_data = resp.json()
        bots = bots_data.get("data") if isinstance(bots_data, dict) else bots_data

        if not bots or not isinstance(bots, list):
            print("[STATS] ❌ Боты не получены или формат некорректен.")
            return None

        bot = bots[0]
        start_date = datetime.fromisoformat(bot["created_at"].replace("Z", "+00:00"))
        days_running = max((datetime.now(timezone.utc) - start_date).days, 1)

        deals_stats_url = f"https://api.3commas.io/public/api/ver1/bots/{bot['id']}/deals_stats"
        signature_stats = sign_request(f"/public/api/ver1/bots/{bot['id']}/deals_stats", {})
        headers["Signature"] = signature_stats

        stats_resp = requests.get(deals_stats_url, headers=headers)
        stats_resp.raise_for_status()
        stats_data = stats_resp.json()

        completed_deals = int(stats_data.get("completed", 0))
        profit_total = float(stats_data.get("completed_deals_usd_profit", 0)) * LEVERAGE

        roi = (profit_total / START_BUDGET) * (365 / days_running) * 100 if START_BUDGET > 0 else 0

        return {
            "start_date": start_date.strftime("%Y-%m-%d"),
            "days_running": days_running,
            "completed_deals": completed_deals,
            "profit_total": profit_total,
            "roi": roi,
            "positive_deals": completed_deals,
            "negative_deals": 0
        }
    except Exception as e:
        print(f"[STATS] ❌ Ошибка при получении статистики бота: {e}")
        return None

# === Отправка сообщений в Telegram ===
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

# === Подхват открытых сделок при старте ===
def catch_up_open_deals():
    deals = get_deals()
    for deal in deals:
        deal_id = deal.get("id")
        status = (deal.get("status") or "").lower()
        created_at = deal.get("created_at")
        if not created_at:
            continue
        created_at_dt = dateutil.parser.isoparse(created_at)

        bought_vol = float(deal.get("bought_volume") or 0.0)
        dca = int(deal.get("completed_safety_orders_count") or 0)

        if created_at_dt < bot_start_time and status == "bought":
            known_deals[deal_id] = {
                "stage": "entered",
                "sent_entered": True,
                "last_volume": bought_vol,
                "dca": dca
            }

# === Основной цикл мониторинга ===
def monitor_deals():
    print(f"[{datetime.now(timezone.utc)}] ▶️ Старт мониторинга сделок")
    while True:
        try:
            deals = get_deals()
            print(f"[{datetime.now(timezone.utc)}] Получено сделок: {len(deals)}")

            for deal in deals:
                deal_id = deal.get("id")
                status = (deal.get("status") or "").lower()
                created_at = deal.get("created_at")
                closed_at = deal.get("closed_at")
                pair = (deal.get("pair") or "").upper()
                bought_vol = float(deal.get("bought_volume") or 0.0)
                dca = int(deal.get("completed_safety_orders_count") or 0)

                created_at_dt = dateutil.parser.isoparse(created_at) if created_at else None
                closed_at_dt = dateutil.parser.isoparse(closed_at) if closed_at else None

                # Инициализация сделки
                if deal_id not in known_deals:
                    known_deals[deal_id] = {
                        "stage": None,
                        "dca": 0,
                        "sent_entered": False,
                        "last_volume": 0.0
                    }

                st = known_deals[deal_id]

                # ===== Новая сделка =====
                if status == "bought" and not st.get("sent_entered") and created_at_dt >= bot_start_time:
                    st["stage"] = "entered"
                    st["sent_entered"] = True
                    st["last_volume"] = bought_vol
                    st["dca"] = dca
                    send_telegram_message(
                        f"📈 <b>Вход в сделку</b> по паре <b>{pair}</b>\n"
                        f"💵 Цена входа: {float(deal.get('bought_average_price') or 0):.4f}\n"
                        f"📦 Объём: {bought_vol * LEVERAGE:.2f} USDT"
                    )

                # ===== DCA / докупка =====
                if status == "bought" and st.get("stage") == "entered":
                    if bought_vol > st.get("last_volume", 0.0):
                        last_dca_amount = bought_vol - st.get("last_volume", 0.0)
                        st["last_volume"] = bought_vol
                        st["dca"] = dca
                        send_telegram_message(
                            f"➕ <b>Классная цена, докупаю 🤖</b>\n"
                            f"📊 Объём: {last_dca_amount * LEVERAGE:.2f} USDT"
                        )

                # ===== Закрытие сделки =====
                if status == "completed" and st.get("stage") == "entered":
                    profit_usd = float(deal.get("actual_usd_profit") or 0) * LEVERAGE
                    send_telegram_message(
                        f"✅✅✅ Сделка по паре <b>{pair}</b> закрыта\n"
                        f"💵💵💵 Профит +{profit_usd:.2f} USDT"
                    )
                    st["stage"] = "closed"

                known_deals[deal_id] = st

            time.sleep(POLL_INTERVAL)
        except Exception as e:
            print(f"[{datetime.now(timezone.utc)}] ❌ Ошибка в основном цикле: {e}")
            time.sleep(POLL_INTERVAL)

# === Старт программы ===
if __name__ == "__main__":
    log_external_ip()
    threading.Thread(target=fake_server, daemon=True).start()
    catch_up_open_deals()  # подхватываем старые открытые сделки
    monitor_deals()
