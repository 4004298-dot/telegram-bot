from flask import Flask, request
import requests
import os
import logging

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

def send_message(chat_id, text):
    requests.post(
        f"{BASE_URL}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text
        },
        timeout=30
    )

@app.route("/", methods=["GET"])
def index():
    return "ok", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        app.logger.info(f"UPDATE: {data}")

        if not data:
            return "ok", 200

        if "message" in data:
            chat_id = data["message"]["chat"]["id"]
            text = data["message"].get("text", "")

            if text == "/start":
                send_message(chat_id, "Бот работает. Отправь сообщение.")
                return "ok", 200

            if text == "/ping":
                send_message(chat_id, "pong")
                return "ok", 200

            send_message(chat_id, f"Принял: {text}")

        return "ok", 200

    except Exception as e:
        app.logger.exception("WEBHOOK ERROR")
        return f"error: {str(e)}", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
