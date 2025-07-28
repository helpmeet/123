import os
import time
import hmac
import hashlib
import requests
from datetime import datetime

# === Конфигурация через переменные окружения ===
API_KEY = os.getenv("THREECOMMAS_API_KEY")
API_SECRET = os.getenv("THREECOMMAS_API_SECRET")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

if not all([API_KEY, API_SECRET, TG_TOKEN, TG_CHAT_ID]):
    raise ValueError("❌ Отсутствуют переменные окружения: API_KEY, API_SECRET, TG_TOKEN, TG_CHAT_ID")

API_BASE = "https://api.3commas.io/public/api"
known_deals = {}

# === Логирование ===
def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

# === Подпись запроса ===
def sign(path, params):
    query = '&'.join(f"{k}={v}" for k, v in sorted(params.items()))
    payload = f"{path}?{query}" if query else path
    return hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

# === Отправка GET-запроса с подписью ===
def get(path, params=None):
    params = params or {}
    headers = {
        "APIKEY": API_KEY,
        "Signature": sign(path, params)
    }
    url = API_BASE + path
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()

# === Отправка сообщения в Telegram ===
def send_telegram_message(text):
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        payload = {
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=payload)
        if not response.ok:
            log(f"[ERROR] Telegram: {response.text}")
    except Exception as e:
        log(f"[ERROR] Telegram send failed: {e}")

# === Получение последнего ордера сделки ===
def get_last_order_price_and_qty(deal_id):
    try:
        path = f"/ver1/deals/{deal_id}/market_orders"
        orders = get(path)
        if not orders:
            return None, None
        last = orders[-1]
        price = float(last.get("price") or 0)
        qty = float(last.get("quantity") or 0)
        return price, qty
    except Exception as e:
        log(f"[ERROR] Ошибка при получении ордеров сделки {deal_id}: {e}")
        return None, None

# === Кэш статистики бота ===
_last_stats = None
_last_stats_time = 0

def get_bot_stats():
    deals = get("/ver1/deals", {"scope": "finished", "limit": 1000})
    accounts = get("/ver1/accounts")

    total_deals = len(deals)
    total_profit = sum(float(d.get("actual_usd_profit") or 0) for d in deals)

    if deals:
        first_closed = min(datetime.fromisoformat(d["closed_at"].replace("Z", "")) for d in deals if d.get("closed_at"))
        days_working = (datetime.utcnow() - first_closed).days or 1
    else:
        days_working = 0

    usdt_account = next((a for a in accounts if a["currency_code"] == "USDT"), accounts[0])
    initial = float(usdt_account.get("initial_total") or 0)
    balance = float(usdt_account.get("available_funds") or 0)

    monthly_pct = (total_profit / initial) * (30 / max(1, days_working)) * 100
    yearly_pct = monthly_pct * 12

    return {
        "total_deals": total_deals,
        "total_profit": total_profit,
        "days_working": days_working,
        "initial": initial,
        "balance": balance,
        "monthly_pct": monthly_pct,
        "yearly_pct": yearly_pct
    }

def get_cached_bot_stats():
    global _last_stats, _last_stats_time
    if time.time() - _last_stats_time > 60:
        _last_stats = get_bot_stats()
        _last_stats_time = time.time()
    return _last_stats

# === Основной цикл мониторинга ===
def monitor_deals():
    log("▶️ Мониторинг сделок запущен...")
    while True:
        try:
            deals = get("/ver1/deals", {"scope": "active", "limit": 100})
            for deal in deals:
                deal_id = deal["id"]
                pair = deal["pair"]
                status = deal["status"]
                dca = int(deal.get("completed_safety_orders_count") or 0)
                base, quote = pair.split("_")

                prev = known_deals.get(deal_id, {"dca": 0, "status": ""})

                # Новая сделка
                if deal_id not in known_deals:
                    price, qty = get_last_order_price_and_qty(deal_id)
                    if price and qty:
                        msg = (
                            f"🛒 Покупаю {base} по цене 1 {base} = {price:.6f} {quote}\n"
                            f"📊 Объем сделки: {qty:.6f} {base}"
                        )
                        send_telegram_message(msg)

                # Докупка
                elif dca > prev["dca"]:
                    price, qty = get_last_order_price_and_qty(deal_id)
                    if price and qty:
                        msg = (
                            f"🛒 Докупаю {base} по цене 1 {base} = {price:.6f} {quote}\n"
                            f"📊 Объем докупки: {qty:.6f} {base}"
                        )
                        send_telegram_message(msg)

                # Сделка завершена
                if status == "completed" and prev["status"] != "completed":
                    profit = float(deal.get("actual_usd_profit") or 0)
                    created = datetime.fromisoformat(deal["created_at"].replace("Z", ""))
                    closed = datetime.fromisoformat(deal["closed_at"].replace("Z", ""))
                    duration = int((closed - created).total_seconds() // 60)

                    stats = get_cached_bot_stats()

                    msg = (
                        "✅ Сделка завершена ✅\n"
                        f"  💰 Бот заработал = {profit:.2f} USDT\n"
                        f"  ⌚️ Сделка заняла: {duration} минут\n\n"
                        f"  ⚙️ Статистика бота:\n"
                        f"  🤖 Бот работает: {stats['days_working']} дней\n"
                        f"  🤝 Совершил сделок: {stats['total_deals']}\n"
                        f"  🏦 Начальный бюджет: {stats['initial']:.2f}$\n"
                        f"  🤑 Чистая прибыль: {stats['total_profit']:.2f}$\n"
                        f"  💳 Итого на балансе: {stats['balance']:.2f}$\n"
                        f"  💵 % в месяц: {stats['monthly_pct']:.2f}%\n"
                        f"  💰 % годовых: {stats['yearly_pct']:.2f}%"
                    )
                    send_telegram_message(msg)

                known_deals[deal_id] = {"dca": dca, "status": status}

        except Exception as e:
            log(f"[ERROR] {e}")

        time.sleep(POLL_INTERVAL)

# === Точка входа ===
if __name__ == "__main__":
    try:
        monitor_deals()
    except KeyboardInterrupt:
        log("🛑 Мониторинг остановлен пользователем.")