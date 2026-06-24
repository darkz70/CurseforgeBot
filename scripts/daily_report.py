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
TMP_DIR = Path("/tmp")

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

    def parse_cf_number(s):
        s = s.strip().replace(",", "")
        if s.upper().endswith("M"):
            return int(float(s[:-1]) * 1_000_000)
        if s.upper().endswith("K"):
            return int(float(s[:-1]) * 1_000)
        return int(float(s))

    ld_match = re.search(
        r'"interactionStatistic".*?"userInteractionCount"\s*:\s*(\d+)', html, re.S
    )
    if ld_match:
        count = int(ld_match.group(1))
    else:
        dl_match = re.search(r'([\d,.]+[KkMm]?)\s+Downloads', html)
        if dl_match:
            count = parse_cf_number(dl_match.group(1))
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


def send_telegram_photo(token: str, chat_id: str, photo_path: Path, caption: str = ""):
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    with open(photo_path, "rb") as f:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
            files={"photo": f},
            timeout=30,
        )
    resp.raise_for_status()


def warsaw_offset(dt: datetime) -> int:
    year = dt.year
    last_sun_mar = max(d for d in range(25, 32) if _date(year, 3, d).weekday() == 6)
    last_sun_oct = max(d for d in range(25, 32) if _date(year, 10, d).weekday() == 6)
    dst_start = datetime(year, 3, last_sun_mar, 1, tzinfo=timezone.utc)
    dst_end   = datetime(year, 10, last_sun_oct, 1, tzinfo=timezone.utc)
    return 2 if dst_start <= dt < dst_end else 1


def hourly_keys(history: dict) -> list[str]:
    """Только настоящие часовые ключи вида YYYY-MM-DDTHH:00 (без посторонних записей)."""
    return sorted(k for k in history if "T" in k)


def make_bar_chart(history: dict, slug: str, hours: int = 24) -> str:
    """Текстовый график скачиваний за последние N часов."""
    now = datetime.now(timezone.utc)
    counts = []
    for i in range(hours - 1, -1, -1):
        t = now - timedelta(hours=i)
        key = t.strftime("%Y-%m-%dT%H:00")
        entry = history.get(key, {}).get(slug)
        counts.append(entry["downloadCount"] if entry else None)

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


def make_png_chart(history: dict, slug: str, name: str, hours: int = 12) -> Path | None:
    """PNG-график прироста скачиваний по часам за последние N часов."""
    keys = hourly_keys(history)
    recent = keys[-(hours + 1):]
    if len(recent) < 2:
        return None

    labels, deltas = [], []
    for i in range(1, len(recent)):
        prev = history[recent[i - 1]].get(slug, {}).get("downloadCount")
        curr = history[recent[i]].get(slug, {}).get("downloadCount")
        if prev is None or curr is None:
            continue
        labels.append(recent[i][11:16])
        deltas.append(curr - prev)

    if not deltas:
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    fig, ax = plt.subplots(figsize=(8, 3.2), dpi=150)
    colors = ["#4caf50" if d >= 0 else "#e53935" for d in deltas]
    ax.bar(labels, deltas, color=colors)
    ax.set_title(f"{name} — скачивания за {hours} ч (по часам, UTC)")
    ax.set_ylabel("Δ за час")
    ax.axhline(0, color="#999999", linewidth=0.8)
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()

    out_path = TMP_DIR / f"chart_{slug}.png"
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def trend_arrow(history: dict, slug: str, hour_key: str, delta_hour: int) -> str:
    """Сравнивает прирост этого часа с приростом предыдущего часа."""
    keys = [k for k in hourly_keys(history) if k != hour_key]
    if len(keys) < 2:
        return "→"
    p1 = history[keys[-1]].get(slug, {}).get("downloadCount")
    p0 = history[keys[-2]].get(slug, {}).get("downloadCount")
    if p1 is None or p0 is None:
        return "→"
    prev_delta = p1 - p0
    if delta_hour > prev_delta:
        return "↑"
    if delta_hour < prev_delta:
        return "↓"
    return "→"


def get_best_hour_today(history: dict, slug: str, today_str: str) -> tuple[int, str] | tuple[None, None]:
    """Лучший час сегодня по приросту."""
    today_keys = sorted(k for k in history if k.startswith(today_str) and "T" in k)
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


def first_download_today(history: dict, slug: str, today_str: str) -> tuple[str, int] | None:
    """Первый час сегодня, когда количество скачиваний выросло."""
    keys = hourly_keys(history)
    day_keys = [k for k in keys if k.startswith(today_str)]
    for k in day_keys:
        idx = keys.index(k)
        if idx == 0:
            continue
        prev_k = keys[idx - 1]
        prev_v = history.get(prev_k, {}).get(slug, {}).get("downloadCount")
        curr_v = history.get(k, {}).get(slug, {}).get("downloadCount")
        if prev_v is not None and curr_v is not None and curr_v > prev_v:
            return k[11:16], curr_v - prev_v
    return None


def get_recent_deltas(history: dict, slug: str, hours: int, exclude_key: str | None = None) -> list[int]:
    keys = [k for k in hourly_keys(history) if k != exclude_key]
    recent = keys[-(hours + 1):]
    deltas = []
    for i in range(1, len(recent)):
        p = history[recent[i - 1]].get(slug, {}).get("downloadCount")
        c = history[recent[i]].get(slug, {}).get("downloadCount")
        if p is not None and c is not None:
            deltas.append(c - p)
    return deltas


def detect_viral(delta_hour: int, recent_deltas: list[int], multiplier: float, min_absolute: int) -> bool:
    """Вирусный рост: прирост за час намного выше обычного среднего."""
    if delta_hour < min_absolute:
        return False
    positive = [d for d in recent_deltas if d > 0]
    if not positive:
        return delta_hour >= min_absolute
    avg_recent = sum(positive) / len(positive)
    return delta_hour >= max(min_absolute, avg_recent * multiplier)


def avg_per_day(history: dict, slug: str, today_str: str, max_days: int = 14) -> float | None:
    """Среднее количество скачиваний в день (по полностью завершённым дням)."""
    by_date: dict[str, list[tuple[str, int]]] = {}
    for k in hourly_keys(history):
        date_str = k.split("T")[0]
        if date_str == today_str:
            continue  # сегодняшний день неполный — не считаем
        val = history[k].get(slug, {}).get("downloadCount")
        if val is None:
            continue
        by_date.setdefault(date_str, []).append((k, val))

    day_totals = []
    for date_str in sorted(by_date.keys())[-max_days:]:
        items = sorted(by_date[date_str])
        if len(items) < 2:
            continue
        day_totals.append(items[-1][1] - items[0][1])

    if not day_totals:
        return None
    return sum(day_totals) / len(day_totals)


def get_top_hours(history: dict, slug: str, top_n: int = 10) -> list[tuple[int, str]]:
    """Топ-N рекордных часов по приросту скачиваний за всю историю."""
    keys = hourly_keys(history)
    records = []
    for i in range(1, len(keys)):
        prev_v = history[keys[i - 1]].get(slug, {}).get("downloadCount")
        curr_v = history[keys[i]].get(slug, {}).get("downloadCount")
        if prev_v is not None and curr_v is not None:
            d = curr_v - prev_v
            if d > 0:
                records.append((d, keys[i]))
    records.sort(key=lambda x: -x[0])
    return records[:top_n]


def get_streak(history: dict, slug: str, today_str: str) -> int:
    """Количество дней подряд с хоть одним скачиванием."""
    streak = 0
    check_date = _date.fromisoformat(today_str)
    while True:
        date_str = check_date.isoformat()
        day_keys = [k for k in history if k.startswith(date_str) and "T" in k]
        if not day_keys:
            break
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
    step = 100000
    return ((current // step) + 1) * step


def forecast_eod(history: dict, slug: str, today_str: str, current: int) -> int | None:
    """Прогноз скачиваний к концу дня на основе среднего за сегодня."""
    today_keys = sorted(k for k in history if k.startswith(today_str) and "T" in k)
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

    # Настройки новых фич (с дефолтами, можно переопределить в config.json)
    viral_multiplier = float(cfg.get("viral_multiplier", 4))
    viral_min_absolute = int(cfg.get("viral_min_absolute", 20))
    send_png_chart = bool(cfg.get("send_png_chart", True))
    png_chart_hours = int(cfg.get("png_chart_hours", 12))
    top_records_count = int(cfg.get("top_records_count", 10))

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
    last_week_key = week_key

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
    png_charts = []  # [(slug, name, path)]

    for proj in projects:
        slug = str(proj.get("slug") or proj.get("id"))
        display_name = proj.get("name", slug)
        try:
            stats = fetch_download_count(slug)
        except Exception as e:
            lines.append(f"⚠️ {display_name}: ошибка ({e})")
            lines.append("")
            continue

        name = stats["name"]
        current = stats["downloadCount"]
        prev = (history.get(prev_key) or {}).get(slug, {}).get("downloadCount")
        delta_hour = current - prev if prev is not None else 0

        hour_entry[slug] = {"name": name, "downloadCount": current}
        # Прогрессивно кладём текущий час в history, чтобы все функции ниже
        # (тренд, "первое скачивание дня", топ-часов и т.д.) видели его сразу.
        history[hour_key] = hour_entry

        total_now += current
        total_delta_hour += delta_hour

        # --- Антиспам: если за этот час прироста нет — короткая строка без деталей ---
        if delta_hour == 0:
            lines.append(f"📦 <b>{name}</b> — без изменений (всего: {current:,})")
            lines.append("")
        else:
            sign = "+" if delta_hour >= 0 else ""
            arrow = trend_arrow(history, slug, hour_key, delta_hour)

            lines.append(f"📦 <b>{name}</b>")
            lines.append(f"   Всего: {current:,}")
            lines.append(f"   За час: {sign}{delta_hour:,} {arrow}")
            lines.append(f"   За день: {fmt_delta(current, day_key, slug)}")
            lines.append(f"   За неделю: {fmt_delta(current, week_key, slug)}")
            lines.append(f"   За месяц: {fmt_delta(current, month_key, slug)}")

            week_ago = (history.get(last_week_key) or {}).get(slug, {}).get("downloadCount")
            if week_ago is not None and week_ago > 0:
                diff = current - week_ago
                sign_w = "+" if diff >= 0 else ""
                pct = diff / week_ago * 100
                lines.append(f"   Vs прошлая неделя: {sign_w}{diff:,} ({sign_w}{pct:.0f}%)")

            # Первое скачивание дня
            first_dl = first_download_today(history, slug, today_str)
            if first_dl:
                lines.append(f"   🌅 Первое скачивание дня: {first_dl[0]} UTC (+{first_dl[1]:,})")

            # Вирусный рост
            recent_deltas = get_recent_deltas(history, slug, 24, exclude_key=hour_key)
            if detect_viral(delta_hour, recent_deltas, viral_multiplier, viral_min_absolute):
                lines.append("   🚀 Вирусный рост! Прирост в разы выше обычного.")
                alerts.append(f"🚀 <b>{name}</b>: вирусный рост — +{delta_hour:,} за час!")

            # Рекорд дня
            best_delta, best_hour_key = get_best_hour_today(history, slug, today_str)
            if best_delta is not None and best_hour_key:
                lines.append(f"   🏆 Рекорд часа сегодня: +{best_delta:,} в {best_hour_key[11:16]} UTC")

            # Серия дней
            streak = get_streak(history, slug, today_str)
            if streak > 0:
                lines.append(f"   🔥 Серия: {streak} дн. подряд")

            # До круглого числа
            milestone = next_round_number(current)
            if milestone:
                left = milestone - current
                lines.append(f"   🎯 До {milestone:,}: осталось {left:,}")

            # Прогноз к концу дня
            forecast = forecast_eod(history, slug, today_str, current)
            if forecast:
                lines.append(f"   📈 Прогноз к концу дня: ~{forecast:,}")

            # Среднее в день
            avg_day = avg_per_day(history, slug, today_str)
            if avg_day is not None:
                lines.append(f"   📊 Среднее в день: ~{avg_day:,.0f}")

            # Текстовый график за 24ч
            bar = make_bar_chart(history, slug)
            if bar:
                lines.append(f"   {bar}")

            # Топ-10 рекордных часов
            top_hours = get_top_hours(history, slug, top_records_count)
            if top_hours:
                lines.append(f"   🏅 Топ-{len(top_hours)} рекордных часов:")
                for i, (d, k) in enumerate(top_hours, 1):
                    date_part = f"{k[8:10]}.{k[5:7]}"
                    lines.append(f"      {i}. +{d:,} — {date_part} {k[11:16]} UTC")

            lines.append("")

        # --- Алерт по абсолютному порогу (как раньше) ---
        if delta_hour >= ALERT_THRESHOLD:
            alerts.append(f"🚨 <b>{name}</b>: +{delta_hour:,} скачиваний за час!")

        # --- PNG-график за N часов (готовим, отправим после текстового отчёта) ---
        if send_png_chart:
            chart_path = make_png_chart(history, slug, name, hours=png_chart_hours)
            if chart_path:
                png_charts.append((slug, name, chart_path))

    save_json(HISTORY_FILE, history)

    lines.append(f"<b>Итого (все проекты): {total_now:,}</b>")
    sign_total = "+" if total_delta_hour >= 0 else ""
    lines.append(f"<b>Прирост за час: {sign_total}{total_delta_hour:,}</b>")

    report_text = "\n".join(lines)

    # Telegram ограничивает подпись к фото 1024 символами. Если отчёт укладывается —
    # шлём текст как подпись к первому графику (одно сообщение вместо двух).
    # Если не укладывается (несколько проектов, длинный топ-10 и т.п.) — как раньше,
    # отдельным текстовым сообщением, а графики — следом со своей короткой подписью.
    CAPTION_LIMIT = 1024
    combined_sent = False
    if png_charts and len(report_text) <= CAPTION_LIMIT:
        first_slug, first_name, first_chart_path = png_charts[0]
        try:
            send_telegram_photo(tg_token, tg_chat_id, first_chart_path, caption=report_text)
            combined_sent = True
        except Exception as e:
            print(f"Не удалось отправить совмещённое сообщение: {e}")
        finally:
            first_chart_path.unlink(missing_ok=True)
        png_charts = png_charts[1:]  # первый график уже отправлен с подписью

    if not combined_sent:
        send_telegram(tg_token, tg_chat_id, report_text)

    for slug, name, chart_path in png_charts:
        try:
            send_telegram_photo(
                tg_token, tg_chat_id, chart_path,
                caption=f"📈 {name} — последние {png_chart_hours} ч"
            )
        except Exception as e:
            print(f"Не удалось отправить график для {name}: {e}")
        finally:
            chart_path.unlink(missing_ok=True)

    for alert in alerts:
        send_telegram(tg_token, tg_chat_id, alert)

    print("Отчёт отправлен.")


if __name__ == "__main__":
    main()


# ─── Конкуренты ────────────────────────────────────────────────────────────────

def fetch_competitors(cfg: dict, history: dict, now_utc: datetime) -> str | None:
    """Сравнение с конкурентами из config.json -> competitors."""
    competitors = cfg.get("competitors", [])
    if not competitors:
        return None

    lines = ["<b>⚔️ Сравнение с конкурентами</b>"]
    for comp in competitors:
        slug = str(comp.get("slug") or comp.get("id"))
        name = comp.get("name", slug)
        try:
            stats = fetch_download_count(slug)
            current = stats["downloadCount"]
            lines.append(f"  • {name}: {current:,}")
        except Exception as e:
            lines.append(f"  • {name}: ошибка ({e})")
    return "\n".join(lines)
