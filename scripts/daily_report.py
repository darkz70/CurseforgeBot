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
from datetime import date as _date, datetime, timedelta, timezone
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

ALERT_THRESHOLD = int(os.environ.get("ALERT_THRESHOLD", "50"))


def load_json(path: Path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_config():
    return load_json(CONFIG_FILE, {})


def fetch_download_count(slug: str) -> dict:
    """Парсит публичную страницу мода на CurseForge — без API-ключа."""
    url = CURSEFORGE_MOD_URL.format(slug=slug)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    html = resp.text

    ld_match = re.search(
        r'"interactionStatistic".*?"userInteractionCount"\s*:\s*(\d+)', html, re.S
    )
    if ld_match:
        count = int(ld_match.group(1))
    else:
        dl_match = re.search(r'([\d,]+)\s+Downloads', html)
        if dl_match:
            count = int(dl_match.group(1).replace(",", ""))
        else:
            raise ValueError(f"Не удалось найти число скачиваний на странице: {url}")

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


def warsaw_offset(dt: datetime) -> int:
    year = dt.year
    last_sun_mar = max(d for d in range(25, 32) if _date(year, 3, d).weekday() == 6)
    last_sun_oct = max(d for d in range(25, 32) if _date(year, 10, d).weekday() == 6)
    dst_start = datetime(year, 3, last_sun_mar, 1, tzinfo=timezone.utc)
    dst_end   = datetime(year, 10, last_sun_oct, 1, tzinfo=timezone.utc)
    return 2 if dst_start <= dt < dst_end else 1


def make_bar_chart(history: dict, slug: str, hours: int = 24) -> str:
    """Текстовый график скачиваний за последние N часов."""
    now = datetime.now(timezone.utc)
    counts = []
    for i in range(hours - 1, -1, -1):
        t = now - timedelta(hours=i)
        key = t.strftime("%Y-%m-%dT%H:00")
        entry = history.get(key, {}).get(slug)
        counts.append(entry["downloadCount"] if entry else None)

    # Вычисляем дельты между часами
    deltas = []
    for i in range(1, len(counts)):
        if counts[i] is not None and counts[i - 1] is not None:
            deltas.append(counts[i] - counts[i - 1])
        else:
            deltas.append(None)

    known = [d for d in deltas if d is not None]
    if not known:
        return ""

    max_val = max(known) if known else 1
    if max_val == 0:
        max_val = 1

    blocks = "░▒▓█"
    bar = ""
    for d in deltas:
        if d is None:
            bar += "·"
        else:
            idx = min(int(d / max_val * (len(blocks) - 1)), len(blocks) - 1)
            bar += blocks[idx]

    return f"`{bar}`  (макс. +{max(known):,}/ч)"


def get_best_hour_today(history: dict, slug: str, today_str: str) -> tuple[int, str] | tuple[None, None]:
    """Лучший час сегодня по приросту."""
    today_keys = sorted(k for k in history if k.startswith(today_str))
    best_delta = None
    best_hour = None
    for i in range(1, len(today_keys)):
        prev_count = history[today_keys[i-1]].get(slug, {}).get("downloadCount")
        curr_count = history[today_keys[i]].get(slug, {}).get("downloadCount")
        if prev_count is not None and curr_count is not None:
            delta = curr_count - prev_count
            if best_delta is None or delta > best_delta:
                best_delta = delta
                best_hour = today_keys[i]
    return best_delta, best_hour


def get_streak(history: dict, slug: str, today_str: str) -> int:
    """Количество дней подряд с хоть одним скачиванием."""
    streak = 0
    check_date = _date.fromisoformat(today_str)
    while True:
        date_str = check_date.isoformat()
        day_keys = [k for k in history if k.startswith(date_str)]
        if not day_keys:
            break
        # Проверяем был ли прирост хоть в один час
        day_keys_sorted = sorted(day_keys)
        had_download = False
        for i in range(1, len(day_keys_sorted)):
            prev = history[day_keys_sorted[i-1]].get(slug, {}).get("downloadCount")
            curr = history[day_keys_sorted[i]].get(slug, {}).get("downloadCount")
            if prev is not None and curr is not None and curr > prev:
                had_download = True
                break
        if not had_download:
            break
        streak += 1
        check_date -= timedelta(days=1)
    return streak


def next_round_number(current: int) -> int | None:
    """Ближайшее круглое число выше текущего."""
    for milestone in [100, 250, 500, 750, 1000, 2000, 5000, 10000, 25000, 50000, 100000]:
        if current < milestone:
            return milestone
    # Выше 100к — кратные 100к
    step = 100000
    return ((current // step) + 1) * step


def forecast_eod(history: dict, slug: str, today_str: str, current: int) -> int | None:
    """Прогноз скачиваний к концу дня на основе среднего за сегодня."""
    today_keys = sorted(k for k in history if k.startswith(today_str))
    if len(today_keys) < 2:
        return None
    first = history[today_keys[0]].get(slug, {}).get("downloadCount")
    if first is None:
        return None
    hours_passed = len(today_keys)
    total_today = current - first
    if hours_passed == 0:
        return None
    avg_per_hour = total_today / hours_passed
    hours_left = 24 - hours_passed
    return current + int(avg_per_hour * hours_left)


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
    hour_key = now_utc.strftime("%Y-%m-%dT%H:00")
    hour_label = now_utc.strftime("%d.%m.%Y %H:%M UTC")
    today_str = now_utc.strftime("%Y-%m-%d")

    warsaw_dt = now_utc + timedelta(hours=warsaw_offset(now_utc))
    warsaw_label = warsaw_dt.strftime("%H:%M Варшава")

    sorted_keys = sorted(history.keys())
    prev_key = sorted_keys[-1] if sorted_keys else None

    def find_closest_key(target_dt: datetime) -> str | None:
        target_str = target_dt.strftime("%Y-%m-%dT%H:00")
        candidates = [k for k in sorted_keys if k <= target_str]
        return candidates[-1] if candidates else None

    day_key   = find_closest_key(now_utc - timedelta(days=1))
    week_key  = find_closest_key(now_utc - timedelta(weeks=1))
    month_key = find_closest_key(now_utc - timedelta(days=30))

    # Прошлая неделя — тот же час 7 дней назад
    last_week_key = find_closest_key(now_utc - timedelta(weeks=1))

    def fmt_delta(current: int, ref_key: str | None, slug: str) -> str:
        if ref_key is None:
            return "n/a"
        ref = (history.get(ref_key) or {}).get(slug, {}).get("downloadCount")
        if ref is None:
            return "n/a"
        d = current - ref
        sign = "+" if d >= 0 else ""
        pct = f" ({sign}{d/ref*100:.0f}%)" if ref > 0 else ""
        return f"{sign}{d:,}{pct}"

    hour_entry = {}
    lines = [f"<b>📊 CurseForge — {hour_label} ({warsaw_label})</b>", ""]

    total_now = 0
    total_delta_hour = 0
    alerts = []

    for proj in projects:
        slug = proj.get("slug") or proj.get("id")
        display_name = proj.get("name", str(slug))
        try:
            stats = fetch_download_count(str(slug))
        except Exception as e:
            lines.append(f"⚠️ {display_name}: ошибка ({e})")
            continue

        name = stats["name"]
        current = stats["downloadCount"]
        prev = (history.get(prev_key) or {}).get(str(slug), {}).get("downloadCount")
        delta_hour = current - prev if prev is not None else 0

        hour_entry[str(slug)] = {"name": name, "downloadCount": current}
        total_now += current
        total_delta_hour += delta_hour

        sign = "+" if delta_hour >= 0 else ""

        lines.append(f"📦 <b>{name}</b>")
        lines.append(f"   Всего: {current:,}")
        lines.append(f"   За час: {sign}{delta_hour:,}")
        lines.append(f"   За день: {fmt_delta(current, day_key, str(slug))}")
        lines.append(f"   За неделю: {fmt_delta(current, week_key, str(slug))}")
        lines.append(f"   За месяц: {fmt_delta(current, month_key, str(slug))}")

        # Сравнение с прошлой неделей
        week_ago = (history.get(last_week_key) or {}).get(str(slug), {}).get("downloadCount")
        if week_ago is not None and week_ago > 0:
            diff = current - week_ago
            sign_w = "+" if diff >= 0 else ""
            pct = diff / week_ago * 100
            lines.append(f"   Vs прошлая неделя: {sign_w}{diff:,} ({sign_w}{pct:.0f}%)")

        # Рекорд дня
        best_delta, best_hour_key = get_best_hour_today(history, str(slug), today_str)
        if best_delta is not None and best_hour_key:
            best_hour_label = best_hour_key[11:16]  # "HH:00"
            lines.append(f"   🏆 Рекорд часа сегодня: +{best_delta:,} в {best_hour_label} UTC")

        # Streak
        streak = get_streak(history, str(slug), today_str)
        if streak > 0:
            lines.append(f"   🔥 Серия: {streak} дн. подряд")

        # До круглого числа
        milestone = next_round_number(current)
        if milestone:
            left = milestone - current
            lines.append(f"   🎯 До {milestone:,}: осталось {left:,}")

        # Прогноз к концу дня
        forecast = forecast_eod(history, str(slug), today_str, current)
        if forecast:
            lines.append(f"   📈 Прогноз к концу дня: ~{forecast:,}")

        # График за 24 часа
        bar = make_bar_chart(history, str(slug))
        if bar:
            lines.append(f"   {bar}")

        lines.append("")

        # Алерт
        if delta_hour >= ALERT_THRESHOLD:
            alerts.append(f"🚨 <b>{name}</b>: +{delta_hour:,} скачиваний за час!")

    history[hour_key] = hour_entry
    save_json(HISTORY_FILE, history)

    lines.append(f"<b>Итого (все проекты): {total_now:,}</b>")
    sign_total = "+" if total_delta_hour >= 0 else ""
    lines.append(f"<b>Прирост за час: {sign_total}{total_delta_hour:,}</b>")

    send_telegram(tg_token, tg_chat_id, "\n".join(lines))

    # Алерты отправляем отдельным сообщением
    for alert in alerts:
        send_telegram(tg_token, tg_chat_id, alert)

    print("Отчёт отправлен.")


if __name__ == "__main__":
    main()
