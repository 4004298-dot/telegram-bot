import os
import json
import time
import sqlite3
import logging
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import requests

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# =========================================
# CONFIG
# =========================================
BOT_VERSION = "LEXDESK_V7"
TIMEZONE_LABEL = "Europe/Moscow"

BOT_TOKEN = os.environ["BOT_TOKEN"]
GOOGLE_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

TASKS_SHEET = os.environ.get("TASKS_SHEET", "Поручения")
CONSULTS_SHEET = os.environ.get("CONSULTS_SHEET", "Консультации")
DONE_SHEET = os.environ.get("DONE_SHEET", "Выполнено")

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

TASK_TEMPLATE = (
    "📋 ПОРУЧЕНИЕ — отправь одним сообщением:\n"
    "п\n"
    "20.04\n"
    "Написать иск по делу А40-12345\n"
    "Сергей\n"
    "1\n\n"
    "Приоритет: 1-высокий, 2-средний, 3-низкий"
)

CONSULT_TEMPLATE = (
    "📅 КОНСУЛЬТАЦИЯ — отправь одним сообщением:\n"
    "к\n"
    "20.04 14:30\n"
    "Иванов Иван Иванович\n"
    "+7 999 123-45-67\n"
    "Раздел имущества при разводе\n"
    "Сергей"
)

# =========================================
# APP
# =========================================
app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

# =========================================
# GOOGLE AUTH
# =========================================
def build_google_clients():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return gc, sheets_service


gc, sheets_service = build_google_clients()


# =========================================
# SQLITE FOR DEDUPE
# =========================================
DB_PATH = "bot_state.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS processed_updates (
            update_id INTEGER PRIMARY KEY,
            created_at INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()


init_db()


def is_duplicate(update_id: int) -> bool:
    if update_id is None:
        return False

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cutoff = int(time.time()) - 7 * 24 * 3600
    cur.execute("DELETE FROM processed_updates WHERE created_at < ?", (cutoff,))

    try:
        cur.execute(
            "INSERT INTO processed_updates (update_id, created_at) VALUES (?, ?)",
            (int(update_id), int(time.time()))
        )
        conn.commit()
        conn.close()
        return False
    except sqlite3.IntegrityError:
        conn.close()
        return True


# =========================================
# TELEGRAM
# =========================================
def send_message(chat_id, text):
    r = requests.post(
        f"{BASE_URL}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text
        },
        timeout=30
    )
    app.logger.info("send_message %s %s", r.status_code, r.text[:300])


# =========================================
# UTILS
# =========================================
def now_str():
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def normalize_date(value: str) -> str:
    value = str(value or "").strip()

    if value.isdigit() and len(value) == 8:
        return f"{value[:2]}.{value[2:4]}.{value[4:]}"

    if len(value) == 5 and value[2] == ".":
        year = datetime.now().strftime("%Y")
        return f"{value}.{year}"

    return value


def get_spreadsheet():
    return gc.open_by_key(GOOGLE_SHEET_ID)


def get_worksheet(sheet_name: str):
    ss = get_spreadsheet()
    return ss.worksheet(sheet_name)


def get_sheet_id_by_title(sheet_title: str) -> int:
    metadata = sheets_service.spreadsheets().get(
        spreadsheetId=GOOGLE_SHEET_ID
    ).execute()

    for sheet in metadata["sheets"]:
        props = sheet["properties"]
        if props["title"] == sheet_title:
            return props["sheetId"]

    raise Exception(f'Лист "{sheet_title}" не найден')


def set_checkbox(sheet_title: str, row_number: int, col_number: int = 1):
    sheet_id = get_sheet_id_by_title(sheet_title)

    requests_body = {
        "requests": [
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": row_number - 1,
                        "endRowIndex": row_number,
                        "startColumnIndex": col_number - 1,
                        "endColumnIndex": col_number
                    },
                    "rule": {
                        "condition": {
                            "type": "BOOLEAN"
                        },
                        "strict": True,
                        "showCustomUi": True
                    }
                }
            }
        ]
    }

    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=GOOGLE_SHEET_ID,
        body=requests_body
    ).execute()


# =========================================
# BUSINESS LOGIC
# =========================================
def show_help(chat_id):
    text = (
        "Привет! Отправляй данные одним сообщением.\n\n"
        f"{TASK_TEMPLATE}\n\n"
        "──────────────\n\n"
        f"{CONSULT_TEMPLATE}\n\n"
        "──────────────\n"
        "Приоритет: 1=🔴высокий, 2=🟡средний, 3=🟢низкий\n"
        "Дата: 20.04 или 20042026 или 20.04.2026\n"
        f"🧩 {BOT_VERSION}"
    )
    send_message(chat_id, text)


def parse_task(chat_id, lines):
    if len(lines) < 2:
        send_message(
            chat_id,
            "❌ Мало данных. Нужно минимум:\nп\nСрок\nЗадача\n\n" + TASK_TEMPLATE
        )
        return

    deadline = normalize_date(lines[0])
    task = lines[1]
    assignee = lines[2] if len(lines) > 2 else ""
    prio_raw = str(lines[3]).strip() if len(lines) > 3 else "2"
    priority = "высокий" if prio_raw == "1" else "низкий" if prio_raw == "3" else "средний"

    ws = get_worksheet(TASKS_SHEET)

    row = [
        False,
        now_str(),
        deadline,
        "",
        task,
        "",
        assignee,
        priority
    ]

    ws.append_row(row, value_input_option="USER_ENTERED")
    row_number = len(ws.get_all_values())
    set_checkbox(TASKS_SHEET, row_number, 1)

    send_message(
        chat_id,
        "✅ Поручение записано!\n"
        f"🆔 Строка: {row_number}\n"
        f"📋 {task}\n"
        f"👤 {assignee or '—'}\n"
        f"📅 Срок: {deadline}\n"
        f"🎯 {priority}\n"
        f"🧩 {BOT_VERSION}"
    )


def parse_consult(chat_id, lines):
    if len(lines) < 2:
        send_message(
            chat_id,
            "❌ Мало данных. Нужно минимум:\nк\nДата время\nФИО\n\n" + CONSULT_TEMPLATE
        )
        return

    date_time_parts = str(lines[0]).split()
    date = normalize_date(date_time_parts[0]) if len(date_time_parts) > 0 else ""
    tm = date_time_parts[1] if len(date_time_parts) > 1 else ""

    fio = lines[1] if len(lines) > 1 else ""
    phone_raw = lines[2] if len(lines) > 2 else ""
    digits = "".join(c for c in phone_raw if c.isdigit())
    if digits.startswith("7") and len(digits) == 11:
        digits = "8" + digits[1:]
    phone = digits if digits else phone_raw
    subject = lines[3] if len(lines) > 3 else ""
    lawyer = lines[4] if len(lines) > 4 else ""

    ws = get_worksheet(CONSULTS_SHEET)

    row = [
        fio,
        phone,
        date,
        tm,
        subject,
        lawyer,
    ]

    ws.append_row(row, value_input_option="USER_ENTERED")
    row_number = len(ws.get_all_values())

    send_message(
        chat_id,
        "✅ Консультация записана!\n"
        f"🆔 Строка: {row_number}\n"
        f"👤 {fio or '—'}\n"
        f"📞 {phone or '—'}\n"
        f"📅 {date}{(' в ' + tm) if tm else ''}\n"
        f"⚖️ {lawyer or '—'}\n"
        f"📌 {subject or '—'}\n"
        f"🧩 {BOT_VERSION}"
    )


def handle_text_message(chat_id, text):
    text = str(text or "").strip()
    if not text:
        return

    if text in ["/start", "/help", "/помощь"]:
        show_help(chat_id)
        return

    if text == "/ping":
        send_message(chat_id, "pong")
        return

    lines = [x.strip() for x in text.split("\n") if x.strip()]
    if not lines:
        return

    cmd = lines[0].lower()

    if cmd in ["п", "/п", "p", "/p", "1"]:
        parse_task(chat_id, lines[1:])
        return

    if cmd in ["к", "/к", "k", "/k", "2"]:
        parse_consult(chat_id, lines[1:])
        return

    send_message(
        chat_id,
        "❓ Не понял формат.\n\n"
        "Для поручения начни с: п\n"
        "Для консультации начни с: к\n\n"
        "Напиши /start — покажу шаблоны."
    )


# =========================================
# ROUTES
# =========================================
@app.route("/", methods=["GET"])
def index():
    return "ok", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        app.logger.info("UPDATE: %s", data)

        if not data:
            return "ok", 200

        update_id = data.get("update_id")
        if is_duplicate(update_id):
            app.logger.info("duplicate update skipped: %s", update_id)
            return "ok", 200

        if "message" in data:
            msg = data["message"]
            chat_id = msg["chat"]["id"]
            text = msg.get("text", "")
            handle_text_message(chat_id, text)

        return "ok", 200

    except Exception as e:
        app.logger.exception("WEBHOOK ERROR")
        return f"error: {str(e)}", 200


@app.route("/api/data", methods=["GET"])
def api_data():
    try:
        tasks_ws    = get_worksheet(TASKS_SHEET)
        consults_ws = get_worksheet(CONSULTS_SHEET)
        done_ws     = get_worksheet(DONE_SHEET)
        return jsonify({
            "tasks":    tasks_ws.get_all_records(),
            "consults": consults_ws.get_all_records(),
            "done":     done_ws.get_all_records(),
        }), 200
    except Exception as e:
        app.logger.exception("api_data error")
        return jsonify({"error": str(e)}), 500


@app.route("/dashboard", methods=["GET"])
def dashboard():
    return send_file("dashboard-preview.html")


# =========================================
# MAIN
# =========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
