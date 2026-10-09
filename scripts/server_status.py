#!/usr/bin/env python3
"""
Проверяет доступность CurseForge и Modrinth.
Уведомляет если сервер лёг или восстановился.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
STATUS_FILE = DATA_DIR / "server_status.json"

SERVICES = [
    {"name": "CurseForge", "url": "https://www.curseforge.com", "emoji": "🟠"},
    {"name": "Modrinth",   "url": "https://modrinth.com",       "emoji": "🟢"},
    {"name": "GitHub",     "url": "https://github.com",         "emoji": "⚫"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
}


def load_json(path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=20,
    ).raise_for_status()


def check_service(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        return resp.status_code < 500, resp.status_code, None
    except requests.exceptions.ConnectionError:
        return False, None, "Нет соединения"
    except requests.exceptions.Timeout:
        return False, None, "Таймаут"
    except Exception as e:
        return False, None, str(e)


def main():
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat_id = os.environ["TELEGRAM_CHAT_ID"]

    state = load_json(STATUS_FILE, {})
    now = datetime.now(timezone.utc).isoformat()
    alerts = []

    for svc in SERVICES:
        name = svc["name"]
        emoji = svc["emoji"]
        url = svc["url"]

        is_up, code, error = check_service(url)
        prev_up = state.get(name, {}).get("up", True)  # по умолчанию считаем что был вверх

        if not is_up and prev_up:
            # Лёг
            reason = f"HTTP {code}" if code else error
            alerts.append(f"🔴 <b>{name}</b> недоступен!\n{reason}")
        elif is_up and not prev_up:
            # Восстановился
            alerts.append(f"✅ <b>{name}</b> снова работает!")

        state[name] = {
            "up": is_up,
            "code": code,
            "checked_at": now,
        }

    save_json(STATUS_FILE, state)

    for alert in alerts:
        send_telegram(tg_token, tg_chat_id, alert)

    if not alerts:
        print("Все сервисы работают нормально.")
    else:
        print(f"Отправлено {len(alerts)} алертов.")


if __name__ == "__main__":
    main()
