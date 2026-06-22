#!/usr/bin/env python3
"""
Проверяет статус мода на Modrinth и уведомляет в Telegram когда:
- мод одобрен (статус меняется с 'processing' на 'approved')
- выходит новая версия
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODRINTH_FILE = DATA_DIR / "modrinth.json"
CONFIG_FILE = ROOT / "config.json"

MODRINTH_API = "https://api.modrinth.com/v2"
HEADERS = {"User-Agent": "CurseforgeBot/1.0"}


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
    requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=20).raise_for_status()


def fetch_modrinth_project(slug: str) -> dict | None:
    try:
        resp = requests.get(f"{MODRINTH_API}/project/{slug}", headers=HEADERS, timeout=20)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Ошибка Modrinth API для {slug}: {e}")
        return None


def fetch_modrinth_versions(slug: str) -> list:
    try:
        resp = requests.get(f"{MODRINTH_API}/project/{slug}/version", headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def main():
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat_id = os.environ["TELEGRAM_CHAT_ID"]

    cfg = load_json(CONFIG_FILE, {})
    modrinth_projects = cfg.get("modrinth_projects", [])

    if not modrinth_projects:
        print("Нет modrinth_projects в config.json — пропускаем.")
        return

    state = load_json(MODRINTH_FILE, {})
    alerts = []

    for proj in modrinth_projects:
        slug = proj.get("slug")
        name = proj.get("name", slug)

        data = fetch_modrinth_project(slug)
        if not data:
            print(f"Не удалось получить данные для {slug}")
            continue

        status = data.get("status", "unknown")
        prev_status = state.get(slug, {}).get("status")

        # Статус изменился
        if prev_status and prev_status != status:
            if status == "approved":
                alerts.append(
                    f"🎉 <b>{name}</b> одобрен на Modrinth!\n"
                    f"Статус: {prev_status} → <b>{status}</b>\n"
                    f"https://modrinth.com/mod/{slug}"
                )
            else:
                alerts.append(
                    f"ℹ️ <b>{name}</b> на Modrinth: статус изменился\n"
                    f"{prev_status} → <b>{status}</b>"
                )

        # Проверка новой версии
        versions = fetch_modrinth_versions(slug)
        if versions:
            latest = versions[0]
            latest_id = latest.get("id")
            prev_version_id = state.get(slug, {}).get("latest_version_id")

            if prev_version_id and prev_version_id != latest_id:
                ver_name = latest.get("name", latest_id)
                game_versions = ", ".join(latest.get("game_versions", []))
                loaders = ", ".join(latest.get("loaders", []))
                changelog = (latest.get("changelog") or "").strip()[:300]
                msg = (
                    f"🆕 <b>{name}</b> — новая версия на Modrinth!\n"
                    f"<b>{ver_name}</b>\n"
                    f"MC: {game_versions} | {loaders}\n"
                )
                if changelog:
                    msg += f"\n<i>{changelog}</i>\n"
                msg += f"\nhttps://modrinth.com/mod/{slug}"
                alerts.append(msg)

            state.setdefault(slug, {})["latest_version_id"] = latest_id
            state[slug]["downloads"] = data.get("downloads", 0)

        state.setdefault(slug, {})["status"] = status
        state[slug]["name"] = name
        state[slug]["checked_at"] = datetime.now(timezone.utc).isoformat()

    save_json(MODRINTH_FILE, state)

    for alert in alerts:
        send_telegram(tg_token, tg_chat_id, alert)
        print(f"Отправлено: {alert[:80]}")

    if not alerts:
        print("Нет изменений на Modrinth.")


if __name__ == "__main__":
    main()
