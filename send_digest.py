"""
Утренний дайджест LexDesk.
Запускается Railway Cron: 0 5 * * 1-5  (пн-пт, 08:00 Москва = 05:00 UTC)
"""
import os
import json
from datetime import datetime

import gspread
import requests
from google.oauth2.service_account import Credentials

BOT_TOKEN               = os.environ["BOT_TOKEN"]
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
GOOGLE_SHEET_ID         = os.environ["GOOGLE_SHEET_ID"]
DIGEST_CHAT_ID          = os.environ["DIGEST_CHAT_ID"]

TASKS_SHEET    = os.environ.get("TASKS_SHEET",    "Поручения")
CONSULTS_SHEET = os.environ.get("CONSULTS_SHEET", "Консультации")

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def build_gc():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)


def send(text: str):
    requests.post(
        f"{BASE_URL}/sendMessage",
        json={"chat_id": DIGEST_CHAT_ID, "text": text},
        timeout=30,
    )


def parse_date(val: str):
    """Пробует разобрать дату в форматах ДД.ММ.ГГГГ и ДД.ММ."""
    val = str(val or "").strip()
    for fmt in ("%d.%m.%Y", "%d.%m"):
        try:
            dt = datetime.strptime(val, fmt)
            if fmt == "%d.%m":
                dt = dt.replace(year=datetime.now().year)
            return dt.date()
        except ValueError:
            continue
    return None


def main():
    today = datetime.now().date()
    weekday_names = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    month_names = ["января","февраля","марта","апреля","мая","июня",
                   "июля","августа","сентября","октября","ноября","декабря"]

    date_label = f"{today.day} {month_names[today.month-1]}, {weekday_names[today.weekday()]}"

    gc = build_gc()
    ss = gc.open_by_key(GOOGLE_SHEET_ID)

    # ── Консультации сегодня ──────────────────────────────────────────
    consults_ws = ss.worksheet(CONSULTS_SHEET)
    all_consults = consults_ws.get_all_records()

    todays_consults = []
    for row in all_consults:
        d = parse_date(str(row.get("Дата", "")))
        if d == today:
            todays_consults.append(row)

    # Сортируем по времени
    todays_consults.sort(key=lambda r: str(r.get("Время", "")))

    # ── Поручения ────────────────────────────────────────────────────
    tasks_ws = ss.worksheet(TASKS_SHEET)
    all_tasks = tasks_ws.get_all_records()

    overdue_tasks = []
    today_tasks   = []

    for row in all_tasks:
        done = str(row.get("исполнение", "")).upper()
        if done in ("TRUE", "ИСТИНА", "1"):
            continue  # выполнено

        deadline = parse_date(str(row.get("Срок исполнения", "")))
        if deadline is None:
            continue

        if deadline < today:
            overdue_tasks.append(row)
        elif deadline == today:
            today_tasks.append(row)

    # ── Формируем сообщение ──────────────────────────────────────────
    lines = [f"☀️ Доброе утро! {date_label}\n"]

    # Консультации
    if todays_consults:
        lines.append(f"📅 КОНСУЛЬТАЦИИ СЕГОДНЯ ({len(todays_consults)}):")
        for c in todays_consults:
            tm      = str(c.get("Время", "")).strip()
            fio     = str(c.get("ФИО", "")).strip()
            lawyer  = str(c.get("Адвокат", "")).strip()
            subject = str(c.get("Суть вопроса", "")).strip()
            time_str = f"{tm} — " if tm else ""
            lawyer_str = f" ({lawyer})" if lawyer else ""
            subj_str = f"\n    {subject}" if subject else ""
            lines.append(f"  • {time_str}{fio}{lawyer_str}{subj_str}")
    else:
        lines.append("📅 Консультаций сегодня нет.")

    lines.append("")

    # Поручения на сегодня
    if today_tasks:
        lines.append(f"📋 ПОРУЧЕНИЯ НА СЕГОДНЯ ({len(today_tasks)}):")
        for t in today_tasks:
            task     = str(t.get("Задача", "")).strip()
            assignee = str(t.get("Исполнитель", "")).strip()
            prio     = str(t.get("Приоритет", "")).strip()
            prio_icon = "🔴" if prio == "высокий" else "🟡" if prio == "средний" else "🟢"
            lines.append(f"  • {task} — {assignee} {prio_icon}")

    # Просроченные
    if overdue_tasks:
        lines.append(f"\n⚠️ ПРОСРОЧЕННЫЕ ({len(overdue_tasks)}):")
        for t in overdue_tasks:
            task     = str(t.get("Задача", "")).strip()
            assignee = str(t.get("Исполнитель", "")).strip()
            deadline = str(t.get("Срок исполнения", "")).strip()
            lines.append(f"  • {task} — {assignee} (срок: {deadline}) 🔴")

    if not today_tasks and not overdue_tasks:
        lines.append("📋 Просроченных поручений нет.")

    lines.append("\n🧩 LEXDESK_V7")

    send("\n".join(lines))
    print("Digest sent.")


if __name__ == "__main__":
    main()
