import requests
import time
import hmac
import hashlib
import os
from datetime import datetime
from flask import Flask

app = Flask(__name__)

# === Функция логирования с таймстампом ===
def log(msg):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}")

# Получаем внешний IP и выводим в лог
def print_external_ip():
    try:
        ip = requests.get("https://api.ipify.org").text
        log(f"Current external IP: {ip}")
    except Exception as e:
        log(f"Failed to get external IP: {e}")

# Вызовем при старте
print_external_ip()

# --- Остальной код приложения ---
# Например, тут твой Flask, мониторинг сделок и т.п.

@app.route('/')
def home():
    return "✅ Bot is running"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    log(f"Starting Flask server on port {port}")
    app.run(host="0.0.0.0", port=port)