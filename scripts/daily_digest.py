#!/usr/bin/env python3
"""
Ежедневный дайджест — отправляет итоги дня в Telegram в 21:00 UTC.
"""
import json
import os
import sys
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HISTORY_FILE = DATA_DIR / "history.json"
CONFIG_FILE = ROOT / "config.json"
MODRINTH_FILE = DATA_DIR / "modrinth.json"


def load_json(path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=20,
    ).raise_for_status()


def hourly_keys(history):
    return sorted(k for k in history if "T" in k)


def get_day_stats(history, slug, date_str):
    day_keys = sorted(k for k in hourly_keys(history) if k.startswith(date_str))
    if len(day_keys) < 2:
        return None
    first = history[day_keys[0]].get(slug, {}).get("downloadCount")
    last = history[day_keys[-1]].get(slug, {}).get("downloadCount")
    if first is None or last is None:
        return None
    total = last - first

    # Лучший час
    best_delta = 0
    best_hour = None
    for i in range(1, len(day_keys)):
        p = history[day_keys[i-1]].get(slug, {}).get("downloadCount")
        c = history[day_keys[i]].get(slug, {}).get("downloadCount")
        if p is not None and c is not None:
            d = c - p
            if d > best_delta:
                best_delta = d
                best_hour = day_keys[i][11:16]

    # Активных часов
    active_hours = sum(
        1 for i in range(1, len(day_keys))
        if history[day_keys[i-1]].get(slug, {}).get("downloadCount") is not None
        and history[day_keys[i]].get(slug, {}).get("downloadCount") is not None
        and history[day_keys[i]].get(slug, {}).get("downloadCount", 0)
        > history[day_keys[i-1]].get(slug, {}).get("downloadCount", 0)
    )

    return {
        "total": total,
        "best_delta": best_delta,
        "best_hour": best_hour,
        "active_hours": active_hours,
        "final_count": last,
    }


def get_streak(history, slug, today_str):
    streak = 0
    check_date = _date.fromisoformat(today_str)
    while True:
        date_str = check_date.isoformat()
        day_keys = sorted(k for k in hourly_keys(history) if k.startswith(date_str))
        if not day_keys:
            break
        had = False
        for i in range(1, len(day_keys)):
            p = history[day_keys[i-1]].get(slug, {}).get("downloadCount")
            c = history[day_keys[i]].get(slug, {}).get("downloadCount")
            if p is not None and c is not None and c > p:
                had = True
                break
        if not had:
            break
        streak += 1
        check_date -= timedelta(days=1)
    return streak


def main():
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat_id = os.environ["TELEGRAM_CHAT_ID"]

    cfg = load_json(CONFIG_FILE, {})
    projects = cfg.get("projects", [])
    if not projects:
        sys.exit(0)

    history = load_json(HISTORY_FILE, {})
    modrinth = load_json(MODRINTH_FILE, {})

    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")
    yesterday_str = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")

    lines = [f"<b>📅 Дайджест за {now_utc.strftime('%d.%m.%Y')}</b>", ""]

    grand_total = 0

    for proj in projects:
        slug = str(proj.get("slug") or proj.get("id"))
        name = proj.get("name", slug)

        today_stats = get_day_stats(history, slug, today_str)
        yesterday_stats = get_day_stats(history, slug, yesterday_str)

        if today_stats is None:
            lines.append(f"📦 <b>{name}</b> — нет данных за сегодня")
            lines.append("")
            continue

        total = today_stats["total"]
        grand_total += total

        # Сравнение с вчера
        vs_yesterday = ""
        if yesterday_stats and yesterday_stats["total"] > 0:
            diff = total - yesterday_stats["total"]
            sign = "+" if diff >= 0 else ""
            pct = diff / yesterday_stats["total"] * 100
            vs_yesterday = f" (vs вчера: {sign}{diff:,} / {sign}{pct:.0f}%)"

        streak = get_streak(history, slug, today_str)
        streak_str = f"🔥 {streak} дн. подряд" if streak > 1 else ""

        lines.append(f"📦 <b>{name}</b>")
        lines.append(f"   Скачиваний за день: <b>+{total:,}</b>{vs_yesterday}")
        lines.append(f"   Всего: {today_stats['final_count']:,}")
        if today_stats["best_hour"]:
            lines.append(f"   🏆 Лучший час: +{today_stats['best_delta']:,} в {today_stats['best_hour']} UTC")
        lines.append(f"   ⏰ Активных часов: {today_stats['active_hours']}/24")
        if streak_str:
            lines.append(f"   {streak_str}")

        # Modrinth если одобрен
        mr_data = modrinth.get(slug) or modrinth.get(
            next((p["slug"] for p in cfg.get("modrinth_projects", []) if p.get("name") == name), ""), {}
        )
        if mr_data and mr_data.get("status") == "approved":
            mr_dl = mr_data.get("mr_downloads", 0)
            cf_dl = today_stats["final_count"]
            total_all = cf_dl + mr_dl
            lines.append(f"   🟠 CF: {cf_dl:,} | 🟢 MR: {mr_dl:,} | Всего: {total_all:,}")

        lines.append("")

    lines.append(f"<b>Итого скачиваний за день: +{grand_total:,}</b>")

    send_telegram(tg_token, tg_chat_id, "\n".join(lines))
    print("Дайджест отправлен.")


if __name__ == "__main__":
    main()
