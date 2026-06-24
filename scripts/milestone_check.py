#!/usr/bin/env python3
"""
Проверяет достижение milestone (круглых чисел скачиваний).
Запускается при каждом обновлении статистики.
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MILESTONE_FILE = DATA_DIR / "milestones.json"
CONFIG_FILE = ROOT / "config.json"

CURSEFORGE_MOD_URL = "https://www.curseforge.com/minecraft/mc-mods/{slug}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

MILESTONES = [
    100, 250, 500, 750, 1_000, 1_500, 2_000, 2_500, 3_000, 4_000, 5_000,
    7_500, 10_000, 15_000, 20_000, 25_000, 50_000, 75_000, 100_000,
    250_000, 500_000, 1_000_000,
]


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


def fetch_project_id(slug):
    if not CF_API_KEY:
        return None
    try:
        resp = requests.get(
            f"{CURSEFORGE_API_URL}/mods/search",
            headers={"x-api-key": CF_API_KEY, "Accept": "application/json"},
            params={"gameId": 432, "slug": slug, "pageSize": 1},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return data[0]["id"] if data else None
    except Exception:
        return None


def fetch_downloads(slug):
    if CF_API_KEY:
        pid = fetch_project_id(slug)
        if pid:
            try:
                resp = requests.get(
                    f"{CURSEFORGE_API_URL}/mods/{pid}",
                    headers={"x-api-key": CF_API_KEY, "Accept": "application/json"},
                    timeout=20,
                )
                resp.raise_for_status()
                return resp.json().get("data", {}).get("downloadCount")
            except Exception as e:
                print(f"CF API error: {e}")
    # Fallback HTML
    try:
        url = CURSEFORGE_MOD_URL.format(slug=slug)
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        html = resp.text
        abbr = re.search(r'<abbr[^>]+title="([\d,]+)"[^>]*>[^<]*[Dd]ownload', html)
        if abbr:
            return int(abbr.group(1).replace(",", ""))
        ld = re.search(r'"interactionStatistic".*?"userInteractionCount"\s*:\s*(\d+)', html, re.S)
        if ld:
            return int(ld.group(1))
        dl = re.search(r'([\d,.]+[KkMm]?)\s+Downloads', html)
        if dl:
            s = dl.group(1).strip().replace(',', '')
            if s.upper().endswith('M'): return int(float(s[:-1]) * 1_000_000)
            if s.upper().endswith('K'): return int(float(s[:-1]) * 1_000)
            return int(float(s))
    except Exception:
        pass
    return None
def next_milestone(current):
    for m in MILESTONES:
        if current < m:
            return m
    step = 500_000
    return ((current // step) + 1) * step


def milestone_emoji(m):
    if m >= 1_000_000:
        return "🏆"
    if m >= 100_000:
        return "💎"
    if m >= 10_000:
        return "🥇"
    if m >= 1_000:
        return "🎯"
    return "⭐"


def main():
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat_id = os.environ["TELEGRAM_CHAT_ID"]

    cfg = load_json(CONFIG_FILE, {})
    projects = cfg.get("projects", [])
    state = load_json(MILESTONE_FILE, {})

    for proj in projects:
        slug = str(proj.get("slug") or proj.get("id"))
        name = proj.get("name", slug)

        current = fetch_downloads(slug)
        if current is None:
            continue

        prev = state.get(slug, {}).get("downloads", 0)
        reached = state.get(slug, {}).get("reached", [])

        # Проверяем все milestone между prev и current
        newly_reached = [m for m in MILESTONES if prev < m <= current and m not in reached]

        for m in newly_reached:
            emoji = milestone_emoji(m)
            next_m = next_milestone(current)
            msg = (
                f"{emoji} <b>{name}</b> достиг {m:,} скачиваний на CurseForge!\n\n"
                f"🎉 Поздравляем с новым рубежом!\n"
                f"📦 Текущий счёт: {current:,}\n"
                f"🎯 Следующая цель: {next_m:,} (осталось {next_m - current:,})"
            )
            send_telegram(tg_token, tg_chat_id, msg)
            reached.append(m)
            print(f"Milestone {m:,} достигнут для {name}!")

        state.setdefault(slug, {}).update({
            "downloads": current,
            "reached": reached,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        })

    save_json(MILESTONE_FILE, state)
    print("Milestone проверен.")


if __name__ == "__main__":
    main()
