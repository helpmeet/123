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

# === Простой ISO-парсер дат ===
def parse_iso_datetime(dt_str):
    if dt_str is None:
        return None
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return datetime.fromisoformat(dt_str)

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
        profit_total = float(stats_data.get("completed_deals_usd_profit", 0)) * 10

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

# === Основной цикл мониторинга ===
def monitor_deals():
    print(f"[{datetime.now(timezone.utc)}] ▶️ Старт мониторинга сделок")
    while True:
        deals = get_deals()
        print(f"[{datetime.now(timezone.utc)}] Получено сделок: {len(deals)}")

        # 1. Закрытые сделки (completed)
        closed_ids = set()
        for deal in deals:
            deal_id = deal.get("id")
            status = deal.get("status", "").lower()
            closed_at = parse_iso_datetime(deal.get("closed_at"))
            if status == "completed" and closed_at and closed_at >= bot_start_time:
                closed_ids.add(deal_id)

        for deal in deals:
            deal_id = deal.get("id")
            if deal_id not in closed_ids:
                continue

            if known_deals.get(deal_id, {}).get("stage") != "closed":
                profit_usd = float(deal.get("actual_usd_profit") or 0) * 10
                pair = (deal.get("pair") or "").upper()
                try:
                    opened = parse_iso_datetime(deal["created_at"])
                    closed = parse_iso_datetime(deal["closed_at"])
                    delta = closed - opened
                    parts = []
                    if delta.days > 0:
                        parts.append(f"{delta.days} дн.")
                    h, m, s = delta.seconds // 3600, (delta.seconds % 3600) // 60, delta.seconds % 60
                    if h:
                        parts.append(f"{h} ч.")
                    if m:
                        parts.append(f"{m} мин.")
                    if s or not parts:
                        parts.append(f"{s} сек.")
                    duration = "🚀🚀🚀 Сделка заняла " + " ".join(parts)
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
                        f"📅 Запущен {stats['days_running']} дн.\n"
                        f"🔁 Сделок: {stats['completed_deals']}\n"
                        f"📈 Плюсовых: {stats['positive_deals']}  📉 Минусовых: {stats['negative_deals']}\n"
                        f"💼 Начальный бюджет: ${START_BUDGET:.2f}\n\n"
                        f"📊 Общая прибыль: ${stats['profit_total']:.2f}\n"
                        f"📈 ROI (годовых): {stats['roi']:.2f}%"
                    )
                else:
                    msg += "⚠️ Не удалось получить статистику бота."
                send_telegram_message(msg)
                known_deals[deal_id] = {"stage": "closed"}

        # 2. Открытые сделки — две стадии + DCA
        for deal in deals:
            deal_id = deal.get("id")
            status = (deal.get("status") or "").lower()
            pair = (deal.get("pair") or "").upper()
            created_at = parse_iso_datetime(deal.get("created_at"))
            dca = int(deal.get("completed_safety_orders_count") or 0)

            bought_avg = float(deal.get("bought_average_price") or 0.0)
            if bought_avg == 0.0:
                bought_avg = float(deal.get("base_order_average_price") or 0.0)
            bought_vol = float(deal.get("bought_volume") or 0.0)

            if status == "completed":
                continue

            if deal_id not in known_deals:
                known_deals[deal_id] = {
                    "stage": None,
                    "dca": 0,
                    "sent_looking": False,
                    "sent_entered": False,
                }

            st = known_deals[deal_id]

            # Игнорируем сделки, начатые до старта скрипта (но обновляем DCA)
            if created_at and created_at < bot_start_time:
                if dca > st.get("dca", 0):
                    st["dca"] = dca
                continue

            # 1) Ищу точку входа
            if not st["sent_looking"] and bought_vol == 0.0:
                send_telegram_message(f"📊 <b>Ищу точку входа</b> по паре <b>{pair}</b>")
                st["sent_looking"] = True
                st["stage"] = "looking"

            # 2) Вход в сделку
            if not st["sent_entered"] and (bought_vol > 0.0 or status == "bought"):
                send_telegram_message(
                    f"📈 <b>Вход в сделку</b> по паре <b>{pair}</b>\n"
                    f"💵 Цена входа: {bought_avg:.4f}\n"
                    f"📦 Объём: {bought_vol:.2f} USDT"
                )
                st["sent_entered"] = True
                st["stage"] = "entered"

            # 3) DCA — докупка
            if dca > st.get("dca", 0):
                send_telegram_message(
                    f"➕ <b>Докупил</b> #{dca} в сделке <b>{pair}</b>\n"
                    f"📊 Объём: {bought_vol:.2f} USDT"
                )
                st["dca"] = dca

            st["status"] = status

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    log_external_ip()
    threading.Thread(target=fake_server, daemon=True).start()
    monitor_deals()
