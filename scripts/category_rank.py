#!/usr/bin/env python3
"""
Парсит топ модов категории на CurseForge и показывает позицию твоего мода.
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RANK_FILE = DATA_DIR / "ranks.json"
CONFIG_FILE = ROOT / "config.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

CF_CATEGORY_URL = "https://www.curseforge.com/minecraft/mc-mods?sortBy=total-downloads&page={page}"


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


def fetch_top_mods(pages=5):
    """Парсит топ модов по скачиваниям (первые pages страниц)."""
    mods = []
    for page in range(1, pages + 1):
        try:
            url = CF_CATEGORY_URL.format(page=page)
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            html = resp.text

            # Ищем slug и название каждого мода
            matches = re.findall(
                r'href="/minecraft/mc-mods/([^"]+)"[^>]*>([^<]{3,60})</a>',
                html
            )
            for slug, title in matches:
                if slug not in [m["slug"] for m in mods]:
                    mods.append({"slug": slug, "name": title.strip(), "page": page})
        except Exception as e:
            print(f"Ошибка парсинга страницы {page}: {e}")
            break
    return mods


def main():
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat_id = os.environ["TELEGRAM_CHAT_ID"]

    cfg = load_json(CONFIG_FILE, {})
    projects = cfg.get("projects", [])
    state = load_json(RANK_FILE, {})

    top_mods = fetch_top_mods(pages=10)
    top_slugs = [m["slug"] for m in top_mods]

    for proj in projects:
        slug = str(proj.get("slug") or proj.get("id"))
        name = proj.get("name", slug)

        if slug in top_slugs:
            rank = top_slugs.index(slug) + 1
            prev_rank = state.get(slug, {}).get("rank")

            rank_change = ""
            if prev_rank and prev_rank != rank:
                diff = prev_rank - rank
                if diff > 0:
                    rank_change = f" (↑ +{diff} позиций)"
                else:
                    rank_change = f" (↓ {abs(diff)} позиций)"

            msg = (
                f"🏆 <b>{name}</b> в топе CurseForge!\n"
                f"Позиция: <b>#{rank}</b>{rank_change}\n"
                f"из топ-{len(top_slugs)} модов по скачиваниям\n"
                f"https://www.curseforge.com/minecraft/mc-mods/{slug}"
            )
            send_telegram(tg_token, tg_chat_id, msg)

            state.setdefault(slug, {})["rank"] = rank
        else:
            print(f"{name} не найден в топ-{len(top_slugs)}")
            state.setdefault(slug, {})["rank"] = None

        state[slug]["checked_at"] = datetime.now(timezone.utc).isoformat()

    save_json(RANK_FILE, state)
    print("Рейтинг проверен.")


if __name__ == "__main__":
    main()
