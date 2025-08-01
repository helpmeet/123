import time
from datetime import datetime, timezone
import threading
import requests

# === Конфигурация ===
POLL_INTERVAL = 60  # Интервал опроса, сек
START_BUDGET = 6000.0

TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"
API_KEY = "YOUR_3COMMAS_API_KEY"
BOT_ID = "YOUR_BOT_ID"

# === Глобальные переменные ===
known_deals = {}
accumulated_profit_usd = 0.0


# === Вспомогательные функции ===
def parse_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"[Telegram] Ошибка отправки: {e}")


def get_deals():
    try:
        url = "https://api.3commas.io/public/api/ver1/deals"
        params = {"bot_id": BOT_ID, "limit": 100}
        headers = {"APIKEY": API_KEY}
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[Deals] Ошибка получения сделок: {e}")
        return []


def get_bot_stats():
    try:
        url = f"https://api.3commas.io/public/api/ver1/bots/{BOT_ID}/stats"
        headers = {"APIKEY": API_KEY}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[Stats] Ошибка получения статистики: {e}")
        return None


def format_duration(seconds):
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}ч {m}м {s}с"


# === Основной цикл мониторинга сделок ===
def monitor_deals():
    global accumulated_profit_usd
    print(f"[{datetime.now(timezone.utc)}] ▶️ Старт мониторинга сделок")
    while True:
        deals = get_deals()
        print(f"[{datetime.now(timezone.utc)}] Получено сделок: {len(deals)}")
        for deal in deals:
            deal_id = deal.get("id")
            status = deal.get("status", "")
            pair = deal.get("pair", "")
            dca = deal.get("completed_safety_orders_count", 0)

            bought_avg = parse_float(deal.get("bought_average"))
            bought_vol_raw = parse_float(deal.get("bought_volume"))
            bought_vol = bought_vol_raw * 10  # Умножаем объем на 10
            profit_pct = parse_float(deal.get("actual_profit_percentage"))

            # Прибыль в долларах (умножаем на 10)
            profit_usd = bought_vol_raw * (profit_pct / 100) * 10

            duration_sec = deal.get("duration_in_seconds", 0)

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
                    # Обновляем накопленную прибыль
                    accumulated_profit_usd += profit_usd

                    stats = get_bot_stats()
                    if stats:
                        start_date_raw = stats.get("start_date", None)
                        days_running = 1
                        roi = 0.0
                        if start_date_raw:
                            try:
                                start_date = datetime.strptime(start_date_raw, "%Y-%m-%d")
                                days_running = max((datetime.now() - start_date).days, 1)
                                roi = (accumulated_profit_usd / START_BUDGET) / days_running * 365 * 100
                            except Exception as e:
                                print(f"Ошибка расчета ROI: {e}")

                        positive_deals = stats.get("positive_deals", 0)
                        negative_deals = stats.get("negative_deals", 0)
                        completed_deals = stats.get("completed_deals", 0)
                        profit_total = stats.get("profit_total", 0.0)
                        bot_name = stats.get("bot_name", "н/д")

                        current_balance = START_BUDGET + accumulated_profit_usd

                        msg = (
                            f"✅ <b>Сделка завершена</b>: <b>{pair}</b>\n"
                            f"📈 Прибыль: {profit_pct:.2f}%\n"
                            f"💰 В долларах: {profit_usd:.2f} USDT\n"
                            f"💵 Цена входа: {bought_avg:.4f}\n"
                            f"📦 Объём: {bought_vol:.2f} USDT\n"
                            f"⏳ Длительность: {format_duration(duration_sec)}\n\n"
                            f"📊 <b>Статистика стратегии:</b>\n"
                            f"🤖 {bot_name}\n"
                            f"📅 Старт: {start_date_raw} ({days_running} дней)\n"
                            f"🔁 Сделок: {completed_deals}\n"
                            f"📈 Плюсовых: {positive_deals}  📉 Минусовых: {negative_deals}\n"
                            f"💼 Начальный баланс: ${START_BUDGET:.2f}\n"
                            f"📊 Общая прибыль: ${profit_total:.2f}\n"
                            f"💰 Текущий баланс: ${current_balance:.2f}\n"
                            f"📈 Доходность (годовых): {roi:.2f}%"
                        )
                    else:
                        msg = "⚠️ Не удалось получить статистику бота."

                    send_telegram_message(msg)
                    known_deals[deal_id]["status"] = status

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    # Тут можно добавить лог внешнего IP или запуск фейкового сервера
    monitor_deals()
