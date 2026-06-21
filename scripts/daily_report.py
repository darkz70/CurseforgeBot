#!/usr/bin/env python3
"""
Отчёт по проектам CurseForge: скачивания (парсинг публичной страницы).
Запускается через GitHub Actions каждый час.
Публичный API-ключ не требуется.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HISTORY_FILE = DATA_DIR / "history.json"
CONFIG_FILE = ROOT / "config.json"

CURSEFORGE_MOD_URL = "https://www.curseforge.com/minecraft/mc-mods/{slug}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


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
    return cfg


def fetch_download_count(slug: str) -> dict:
    """Парсит публичную страницу мода на CurseForge — без API-ключа."""
    url = CURSEFORGE_MOD_URL.format(slug=slug)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    html = resp.text

    # Число скачиваний в JSON-LD или в атрибутах страницы
    # Вариант 1: ищем interactionStatistic в JSON-LD
    ld_match = re.search(
        r'"interactionStatistic".*?"userInteractionCount"\s*:\s*(\d+)', html, re.S
    )
    if ld_match:
        count = int(ld_match.group(1))
    else:
        # Вариант 2: ищем "X Downloads" в тексте страницы
        dl_match = re.search(r'([\d,]+)\s+Downloads', html)
        if dl_match:
            count = int(dl_match.group(1).replace(",", ""))
        else:
            raise ValueError(f"Не удалось найти число скачиваний на странице: {url}")

    # Название мода
    title_match = re.search(r'<title>([^<]+)</title>', html)
    name = title_match.group(1).split(" - ")[0].strip() if title_match else slug

    return {"name": name, "downloadCount": count}


def send_telegram(token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=20,
    )
    resp.raise_for_status()


def main():
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat_id = os.environ["TELEGRAM_CHAT_ID"]

    cfg = get_config()
    projects = cfg.get("projects", [])
    if not projects:
        print("Нет проектов в config.json — нечего считать.")
        sys.exit(1)

    history = load_json(HISTORY_FILE, {})

    now_utc = datetime.now(timezone.utc)
    # Ключ для hourly-истории: "2025-06-21T14:00"
    hour_key = now_utc.strftime("%Y-%m-%dT%H:00")
    hour_label = now_utc.strftime("%d.%m.%Y %H:00 UTC")

    # Предыдущая запись (любая) для вычисления дельты за час
    sorted_keys = sorted(history.keys())
    prev_key = sorted_keys[-1] if sorted_keys else None

    hour_entry = {}
    lines = [f"<b>📊 CurseForge — {hour_label}</b>", ""]

    total_now = 0
    total_delta = 0

    for proj in projects:
        slug = proj.get("slug") or proj.get("id")  # slug обязателен
        display_name = proj.get("name", str(slug))
        try:
            stats = fetch_download_count(str(slug))
        except Exception as e:
            lines.append(f"⚠️ {display_name}: ошибка ({e})")
            continue

        name = stats["name"]
        current = stats["downloadCount"]
        prev = (history.get(prev_key) or {}).get(str(slug), {}).get("downloadCount")
        delta = current - prev if prev is not None else 0

        hour_entry[str(slug)] = {"name": name, "downloadCount": current}
        total_now += current
        total_delta += delta

        sign = "+" if delta >= 0 else ""
        lines.append(f"📦 <b>{name}</b>")
        lines.append(f"   Всего скачиваний: {current:,}")
        lines.append(f"   За час: {sign}{delta:,}")
        lines.append("")

    history[hour_key] = hour_entry
    save_json(HISTORY_FILE, history)

    lines.append(f"<b>Итого (все проекты): {total_now:,}</b>")
    sign_total = "+" if total_delta >= 0 else ""
    lines.append(f"<b>Прирост за час: {sign_total}{total_delta:,}</b>")

    send_telegram(tg_token, tg_chat_id, "\n".join(lines))
    print("Отчёт отправлен.")


if __name__ == "__main__":
    main()
