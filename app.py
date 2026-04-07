from flask import Flask, request
import requests
import os

app = Flask(__name__)

TOKEN = os.getenv("BOT_TOKEN")
URL = f"https://api.telegram.org/bot{TOKEN}"

@app.route("/", methods=["POST"])
def webhook():
    data = request.json

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        requests.post(f"{URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": f"Принял: {text}"
        })

    return "ok"

@app.route("/", methods=["GET"])
def index():
    return "bot is alive"
