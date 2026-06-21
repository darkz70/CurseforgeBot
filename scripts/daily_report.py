#!/usr/bin/env python3
"""
Ежедневный отчёт по проектам CurseForge: скачивания (из API) + доход (из data/money.json).
Запускается через GitHub Actions раз в день.
"""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HISTORY_FILE = DATA_DIR / "history.json"
MONEY_FILE = DATA_DIR / "money.json"
CONFIG_FILE = ROOT / "config.json"

CURSEFORGE_API = "https://api.curseforge.com/v1/mods/{mod_id}"


def load_json(path: Path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_config():
    cfg = load_json(CONFIG_FILE, {})
    # project_ids можно задать в config.json или через переменную окружения CURSEFORGE_PROJECT_IDS (через запятую)
    env_ids = os.environ.get("CURSEFORGE_PROJECT_IDS", "")
    if env_ids:
        cfg["projects"] = [
            {"id": int(pid.strip()), "name": f"Project {pid.strip()}"}
            for pid in env_ids.split(",") if pid.strip()
        ]
    return cfg


def fetch_download_count(mod_id: int, api_key: str) -> dict:
    headers = {"Accept": "application/json", "x-api-key": api_key}
    resp = requests.get(CURSEFORGE_API.format(mod_id=mod_id), headers=headers, timeout=20)
    resp.raise_for_status()
    payload = resp.json()["data"]
    return {
        "name": payload.get("name", f"Project {mod_id}"),
        "downloadCount": int(payload.get("downloadCount", 0)),
    }


def send_telegram(token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=20,
    )
    resp.raise_for_status()


def main():
    api_key = os.environ["CURSEFORGE_API_KEY"]
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat_id = os.environ["TELEGRAM_CHAT_ID"]

    cfg = get_config()
    projects = cfg.get("projects", [])
    if not projects:
        print("Нет проектов в config.json и в CURSEFORGE_PROJECT_IDS — нечего считать.")
        sys.exit(1)

    history = load_json(HISTORY_FILE, {})
    money = load_json(MONEY_FILE, {})

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    today_entry = {}
    lines = [f"<b>📊 Отчёт CurseForge — {today}</b>", ""]

    total_today = 0
    total_delta = 0

    for proj in projects:
        mod_id = proj["id"]
        try:
            stats = fetch_download_count(mod_id, api_key)
        except Exception as e:
            lines.append(f"⚠️ {proj.get('name', mod_id)}: ошибка запроса ({e})")
            continue

        name = stats["name"]
        current = stats["downloadCount"]
        prev = history.get(yesterday, {}).get(str(mod_id), {}).get("downloadCount")
        delta = current - prev if prev is not None else 0

        today_entry[str(mod_id)] = {"name": name, "downloadCount": current}
        total_today += current
        total_delta += delta

        sign = "+" if delta >= 0 else ""
        lines.append(f"📦 <b>{name}</b>")
        lines.append(f"   Всего скачиваний: {current}")
        lines.append(f"   За сегодня: {sign}{delta}")
        lines.append("")

    history[today] = today_entry
    save_json(HISTORY_FILE, history)

    lines.append(f"<b>Итого скачиваний (все проекты): {total_today}</b>")
    sign_total = "+" if total_delta >= 0 else ""
    lines.append(f"<b>Прирост за сегодня: {sign_total}{total_delta}</b>")

    # Доход за сегодня (если записан вручную через workflow add_money.yml)
    today_money = money.get(today, [])
    if today_money:
        day_sum = sum(item["amount"] for item in today_money)
        lines.append("")
        lines.append(f"<b>💰 Доход за {today}: {day_sum:.2f}</b>")
        for item in today_money:
            note = f" — {item['note']}" if item.get("note") else ""
            lines.append(f"   {item['amount']:.2f}{note}")
    else:
        lines.append("")
        lines.append("💰 Доход за сегодня не записан (используйте workflow «Add money»).")

    total_money = sum(item["amount"] for day in money.values() for item in day)
    lines.append(f"💰 Доход всего: {total_money:.2f}")

    send_telegram(tg_token, tg_chat_id, "\n".join(lines))
    print("Отчёт отправлен.")


if __name__ == "__main__":
    main()
