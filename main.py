from telethon import TelegramClient, events
import os
import requests

API_ID = int(os.environ['API_ID'])
API_HASH = os.environ['API_HASH']
BOT_TOKEN = os.environ['BOT_TOKEN']
CHANNEL_ID = os.environ['CHANNEL_ID']

client = TelegramClient('./3commas_session', API_ID, API_HASH)

@client.on(events.NewMessage(from_users='3commas_notifications_bot'))
async def handler(event):
    msg = event.message.message
    print("Получено сообщение от 3Commas:", msg)

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHANNEL_ID,
        'text': msg
    }
    requests.post(url, data=payload)

print("Бот запущен, ждём сообщения...")
client.start()
client.run_until_disconnected()