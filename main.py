import os
import time
import hmac
import hashlib
import requests
import threading
import logging
import http.server
import socketserver
from datetime import datetime, timezone, timedelta

# === Настройки ===
START_BUDGET = 6000.0
API_PATH = "/public/api/ver1/deals"
API_URL = "https://api.3commas.io" + API_PATH

THREECOMMAS_API_KEY = os.getenv("THREECOMMAS_API_KEY")
THREECOMMAS_API_SECRET = os.getenv("THREECOMMAS_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))
RENDER_APP_URL = os.getenv("RENDER_APP_URL")  # Например https://yourapp.onrender.com

# === Логи ===
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# === Время старта бота ===
bot_start_time = datetime.now(timezone.utc)

# === Состояние сделок ===
known_deals = {}

# === Подпись запроса ===
def sign_request(path, params):
    query = '&'.join(f"{k}={v}" for k, v in sorted(params.items()))
    payload = f"{path}?{query}" if query else path
    return hmac.new(
        THREECOMMAS_API_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

# === Парсер ISO-дат ===
def parse_iso_datetime(dt_str):
    if dt_str is None:
        return None
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return datetime.fromisoformat(dt_str)

# === Получение сделок ===
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
            logging.warning(f"Неизвестный формат данных сделок: {data}")
            return []
    except Exception as e:
        logging.error(f"Ошибка при получении сделок: {e}")
        return []

# === Получение статистики бота ===
def get_bot_stats():
    try:
        bots_url = "https://api.3commas.io/public/api/ver1/bots"
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
            logging.warning("Боты не получены или формат некорректен.")
            return None
        bot = bots[0]
        start_date = datetime.fromisoformat(bot["created_at"].replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - start_date
        days_running = max(delta.days, 1)
        hours_running = delta.seconds // 3600
        minutes_running = (delta.seconds % 3600) // 60

        deals_stats_url = f"https://api.3commas.io/public/api/ver1/bots/{bot['id']}/deals_stats"
        signature_stats = sign_request(f"/public/api/ver1/bots/{bot['id']}/deals_stats", {})
        headers["Signature"] = signature_stats

        stats_resp = requests.get(deals_stats_url, headers=headers)
        stats_resp.raise_for_status()
        stats_data = stats_resp.json()

        completed_deals = int(stats_data.get("completed", 0))
        profit_total = float(stats_data.get("completed_deals_usd_profit", 0)) * 10

        roi = (profit_total / START_BUDGET) * (365 / days_running) * 100 if START_BUDGET > 0 else 0

        return {
            "start_date": start_date.strftime("%Y-%m-%d"),
            "days_running": days_running,
            "hours_running": hours_running,
            "minutes_running": minutes_running,
            "completed_deals": completed_deals,
            "profit_total": profit_total,
            "roi": roi,
            "positive_deals": completed_deals,
            "negative_deals": 0
        }
    except Exception as e:
        logging.error(f"Ошибка при получении статистики бота: {e}")
        return None

# === Отправка сообщения в Telegram ===
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.post(url, data=payload)
        logging.info(f"Telegram статус: {resp.status_code}")
        if not resp.ok:
            logging.error(f"Ошибка Telegram: {resp.text}")
    except Exception as e:
        logging.error(f"Ошибка при отправке в Telegram: {e}")

# === HTTP-сервер для Render ===
def run_http_server():
    PORT = int(os.environ.get("PORT", 8000))
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        logging.info(f"HTTP-сервер запущен на порту {PORT}")
        httpd.serve_forever()

# === Самопинг публичного URL Render ===
def self_ping():
    if not RENDER_APP_URL:
        logging.warning("RENDER_APP_URL не задан, самопинг не будет работать")
        return
    while True:
        try:
            resp = requests.get(RENDER_APP_URL)
            logging.info(f"Самопинг {RENDER_APP_URL} статус: {resp.status_code}")
        except Exception as e:
            logging.error(f"Самопинг не удался: {e}")
        time.sleep(300)  # 5 минут

# === Основной цикл мониторинга сделок ===
def monitor_deals():
    logging.info("Старт мониторинга сделок")
    while True:
        deals = get_deals()
        logging.info(f"Получено сделок: {len(deals)}")

        # Отфильтруем закрытые сделки после старта бота
        closed_deals_ids = set()
        for deal in deals:
            deal_id = deal.get("id")
            status = deal.get("status", "")
            closed_at_str = deal.get("closed_at")
            if status == "completed" and closed_at_str:
                closed_at = parse_iso_datetime(closed_at_str)
                if closed_at and closed_at >= bot_start_time:
                    closed_deals_ids.add(deal_id)

        for deal in deals:
            deal_id = deal.get("id")
            if deal_id not in closed_deals_ids:
                continue

            profit_usd = float(deal.get("actual_usd_profit") or 0) * 10
            pair = deal.get("pair", "").upper()

            if deal_id not in known_deals:
                known_deals[deal_id] = {"stage": None}

            stage = known_deals[deal_id]["stage"]

            if stage != "closed":
                try:
                    opened = parse_iso_datetime(deal["created_at"])
                    closed = parse_iso_datetime(deal["closed_at"])
                    delta = closed - opened
                    days = delta.days
                    hours = delta.seconds // 3600
                    minutes = (delta.seconds % 3600) // 60
                    duration = f"🚀🚀🚀 Сделка заняла {days} дн. {hours} ч. {minutes} мин."
                except Exception:
                    duration = "🚀🚀🚀 Время сделки недоступно"

                msg = (
                    f"✅✅✅ Сделка успешно завершена\n"
                    f"💵💵💵 Профит +{profit_usd:.2f} USDT\n"
                    f"{duration}\n\n"
                )

                stats = get_bot_stats()
                if stats:
                    msg += (
                        f"📊 Статистика бота:\n"
                        f"📅 Запущен {stats['days_running']} дн. {stats['hours_running']} ч. {stats['minutes_running']} мин.\n"
                        f"🔁 Совершил сделок: {stats['completed_deals']}\n"
                        f"📈 Плюсовых: {stats['positive_deals']}  📉 Минусовых: {stats['negative_deals']}\n"
                        f"💼 Начальный бюджет: ${START_BUDGET:.2f}\n\n"
                        f"📊 Общая прибыль: ${stats['profit_total']:.2f}\n"
                        f"📈 Доходность (годовых): {stats['roi']:.2f}%"
                    )
                else:
                    msg += "⚠️ Не удалось получить статистику бота."

                send_telegram_message(msg)
                known_deals[deal_id]["stage"] = "closed"

        # Обработка открытых сделок
        for deal in deals:
            deal_id = deal.get("id")
            status = deal.get("status", "")
            pair = deal.get("pair", "").upper()
            dca = deal.get("completed_safety_orders_count", 0)

            bought_avg = float(deal.get("bought_average") or 0)
            bought_vol = float(deal.get("bought_volume") or 0)

            if status == "completed":
                continue

            if deal_id not in known_deals:
                known_deals[deal_id] = {"stage": None, "dca": 0}

            stage = known_deals[deal_id].get("stage")
            prev_dca = known_deals[deal_id].get("dca", 0)

            if bought_avg == 0 and stage != "looking":
                send_telegram_message(f"📊 <b>Ищу точку входа</b> по паре <b>{pair}</b>")
                known_deals[deal_id]["stage"] = "looking"

            elif bought_avg == 0 and status in ("active", "new") and stage == "looking":
                send_telegram_message(f"📌 <b>Выставлен начальный ордер</b> по паре <b>{pair}</b>")
                known_deals[deal_id]["stage"] = "order_placed"

            elif bought_avg > 0 and stage != "entered":
                send_telegram_message(
                    f"📈 <b>Вход в сделку</b> по паре <b>{pair}</b>\n"
                    f"💵 Цена входа: {bought_avg:.4f}\n"
                    f"📦 Объём: {bought_vol:.2f} USDT"
                )
                known_deals[deal_id]["stage"] = "entered"

            if dca > prev_dca:
                send_telegram_message(
                    f"➕ <b>Докупил</b> #{dca} в сделке <b>{pair}</b>\n"
                    f"📊 Объём: {bought_vol:.2f} USDT"
                )
                known_deals[deal_id]["dca"] = dca

            known_deals[deal_id]["status"] = status

        time.sleep(POLL_INTERVAL)

# === Запуск ===
if __name__ == "__main__":
    logging.info("Запуск бота")
    # Запускаем HTTP-сервер для Render (в отдельном потоке)
    threading.Thread(target=run_http_server, daemon=True).start()
    # Запускаем самопинг (чтобы Render не засыпал)
    threading.Thread(target=self_ping, daemon=True).start()
    # Запускаем мониторинг сделок в главном потоке
    monitor_deals()
