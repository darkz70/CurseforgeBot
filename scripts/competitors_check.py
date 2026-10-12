#!/usr/bin/env python3
"""
Конкуренты рядом по скачиваниям.

Находит ваш мод в категории CurseForge (сортировка по TotalDownloads)
и показывает соседей: кто чуть впереди (например, 3 200) и кто чуть
позади (например, 3 050) при ваших 3 100.

Присылает в Telegram:
  🥊 таблицу соседей (каждый запуск, если включено)
  📉 «тебя обогнали» / 📈 «ты обогнал» — когда состав соседей сверху/снизу меняется

Дополнительно: если в config.json заполнить "competitors" вручную
([{"cf_id": 123, "name": "X"}]), их скачивания отслеживаются всегда,
даже если они далеко от вас в рейтинге.

Нужен CURSEFORGE_API_KEY (без него ранжирование недоступно — тихий пропуск).
Состояние — data/competitors.json.

Переменные окружения:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, CURSEFORGE_API_KEY
  FORCE_TABLE=1 — прислать таблицу даже если ничего не изменилось

Локально: python scripts/competitors_check.py --dry-run
"""
import html
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
COMP_FILE = DATA_DIR / "competitors.json"
CONFIG_FILE = ROOT / "config.json"

CF_API_URL = "https://api.curseforge.com/v1"
CF_MOD_URL = "https://www.curseforge.com/minecraft/mc-mods/{slug}"

PAGE_SIZE = 50
PAGE_DELAY = 0.35
MAX_BINARY_STEPS = 40

DRY_RUN = "--dry-run" in sys.argv
FORCE_TABLE = os.environ.get("FORCE_TABLE", "") == "1"

CF_API_KEY = os.environ.get("CURSEFORGE_API_KEY", "")

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
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default
    return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fmt(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if DRY_RUN:
        print("=" * 60)
        print("[DRY-RUN] Telegram message:")
        print(text)
        print("=" * 60)
        return True
    if not token or not chat:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы", file=sys.stderr)
        return False
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"ERROR: Telegram {resp.status_code}: {resp.text}", file=sys.stderr)
        return False
    return True


def api_get(url: str, params: dict | None = None):
    headers = dict(HEADERS)
    headers["x-api-key"] = CF_API_KEY
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} для {url}: {resp.text[:300]}")
    return resp.json()


def my_downloads(cf_id: int) -> int | None:
    try:
        return api_get(f"{CF_API_URL}/mods/{cf_id}").get("data", {}).get("downloadCount")
    except Exception as e:  # noqa: BLE001
        print(f"[{cf_id}] не удалось получить скачивания: {e}", file=sys.stderr)
        return None


def get_category_page(game_id: int, category_id: int, index: int) -> dict:
    return api_get(f"{CF_API_URL}/mods", params={
        "gameId": game_id,
        "categoryId": category_id,
        "sort": "TotalDownloads",
        "pageSize": PAGE_SIZE,
        "index": index,
    })


def find_position(cf_id: int, my_dl: int, game_id: int, category_id: int):
    """Бинарный поиск позиции мода + окно соседей. Возвращает (position, total, window)."""
    first = get_category_page(game_id, category_id, 0)
    total = (first.get("pagination") or {}).get("totalCount", 0)
    mods0 = first.get("data") or []
    if total == 0 or not mods0:
        return None, 0, []

    dls = [m.get("downloadCount", 0) for m in mods0 if m.get("downloadCount") is not None]
    descending = len(dls) >= 2 and dls[0] >= dls[-1]

    def page_at(index: int) -> list[dict]:
        if index == 0:
            return mods0
        time.sleep(PAGE_DELAY)
        return (get_category_page(game_id, category_id, index) or {}).get("data") or []

    def dl_at(index: int):
        idx = min(max(index, 0), max(total - 1, 0))
        page = page_at(idx - idx % PAGE_SIZE)
        if not page:
            return None
        item = page[idx % PAGE_SIZE] if idx % PAGE_SIZE < len(page) else page[-1]
        return item.get("downloadCount")

    def rank_index(index: int) -> int:
        """Позиция (1-based) по скачиваниям для индекса страницы."""
        return index + 1 if descending else total - index

    # 1) ищем свой мод на первых страницах (вдруг он в топе)
    for p in range(min(4, (total + PAGE_SIZE - 1) // PAGE_SIZE)):
        page = page_at(p * PAGE_SIZE)
        for i, m in enumerate(page):
            if m.get("id") == cf_id:
                pos = rank_index(p * PAGE_SIZE + i)
                return pos, total, window_around(p * PAGE_SIZE + i, total, descending, page_at)

    # 2) бинарный поиск по числу скачиваний
    lo, hi = 0, max(total - 1, 0)
    for _ in range(MAX_BINARY_STEPS):
        if hi - lo <= PAGE_SIZE:
            break
        mid = (lo + hi) // 2
        mid_dl = dl_at(mid)
        if mid_dl is None:
            break
        if (descending and mid_dl >= my_dl) or (not descending and mid_dl <= my_dl):
            lo = mid
        else:
            hi = mid

    # 3) линейно сканируем найденное окно
    start = max(lo - PAGE_SIZE, 0)
    end = min(lo + 2 * PAGE_SIZE, total)
    idx = start - start % PAGE_SIZE
    while idx < end:
        page = page_at(idx)
        if not page:
            break
        for i, m in enumerate(page):
            j = idx + i
            if m.get("id") == cf_id:
                return rank_index(j), total, window_around(j, total, descending, page_at)
        idx += PAGE_SIZE
    # мод не найден — вернём окно вокруг бинарной оценки
    return None, total, window_around(lo, total, descending, page_at)


def window_around(center_idx: int, total: int, descending: bool, page_at, radius: int = 3):
    """Собирает соседей: radius выше и radius ниже (в порядке рейтинга)."""
    start = max(center_idx - radius, 0)
    end = min(center_idx + radius + 1, total)
    collected = {}
    page_start = start - start % PAGE_SIZE
    while page_start < end:
        page = page_at(page_start)
        if not page:
            break
        for i, m in enumerate(page):
            j = page_start + i
            if start <= j < end:
                collected[j] = m
        page_start += PAGE_SIZE
    mods = [collected[j] for j in sorted(collected)]
    # в порядке рейтинга (по убыванию скачиваний)
    if not descending:
        mods.reverse()
    return mods


def build_table(name: str, slug: str, my_dl: int, position: int | None,
                total: int, window: list[dict], radius: int) -> str:
    lines = [f"🥊 <b>Конкуренты рядом — {html.escape(name)}</b>"]
    pos_str = f"#{fmt(position)} из {fmt(total)}" if position else f"вне топ-{fmt(total)}?"
    lines.append(f"Ваш мод: <b>{fmt(my_dl)}</b> скачиваний · {pos_str}")
    lines.append("")

    above = [m for m in window if m.get("downloadCount", 0) > my_dl]
    below = [m for m in window if m.get("downloadCount", 0) < my_dl]
    above = above[-radius:]        # ближайшие сверху
    below = below[:radius]         # ближайшие снизу

    for m in reversed(above):      # от дальнего к ближайшему
        d = m.get("downloadCount", 0) - my_dl
        mslug = m.get("slug", "")
        mname = html.escape(str(m.get("name", mslug)))
        lines.append(f"⬆️ <b>+{fmt(d)}</b> · {mname} — {fmt(m.get('downloadCount', 0))}")
        if mslug:
            lines.append(f"    {CF_MOD_URL.format(slug=mslug)}")

    lines.append(f"🎯 <b>{html.escape(name)} — {fmt(my_dl)}</b>")

    for m in below:
        d = my_dl - m.get("downloadCount", 0)
        mslug = m.get("slug", "")
        mname = html.escape(str(m.get("name", mslug)))
        lines.append(f"⬇️ <b>−{fmt(d)}</b> · {mname} — {fmt(m.get('downloadCount', 0))}")
        if mslug:
            lines.append(f"    {CF_MOD_URL.format(slug=mslug)}")

    return "\n".join(lines)


def check_manual_competitors(manual: list[dict]) -> list[str]:
    """Скачивания конкурентов, заданных вручную в config.json."""
    lines = []
    for c in manual:
        cf_id = c.get("cf_id")
        name = c.get("name") or str(cf_id)
        dl = my_downloads(int(cf_id)) if cf_id else None
        if dl is not None:
            lines.append(f"👀 {html.escape(name)}: {fmt(dl)}")
    return lines


def main() -> int:
    config = load_json(CONFIG_FILE, {})
    projects = config.get("projects", [])
    game_id = int(config.get("game_id", 432))
    category_id = int(config.get("category_id", 6))
    radius = int(config.get("competitors_range", 3))
    manual = config.get("competitors", [])

    if not projects:
        print("Нет проектов в config.json")
        return 0
    if not CF_API_KEY:
        print("CURSEFORGE_API_KEY не задан — конкуренты недоступны, пропускаем.")
        return 0

    state = load_json(COMP_FILE, {})
    new_state = {}
    messages: list[str] = []
    errors = 0

    for proj in projects:
        slug = proj.get("slug", "")
        name = proj.get("name") or slug
        cf_id = proj.get("cf_id")
        if not cf_id:
            continue
        try:
            dl = my_downloads(int(cf_id))
            if dl is None:
                raise RuntimeError("не удалось получить скачивания мода")
            position, total, window = find_position(int(cf_id), dl, game_id, category_id)

            prev = state.get(slug, {})
            prev_above = set(prev.get("above_ids", []))
            prev_below = set(prev.get("below_ids", []))

            above = [m for m in window if m.get("downloadCount", 0) > dl][-radius:]
            below = [m for m in window if m.get("downloadCount", 0) < dl][:radius]
            cur_above = {m.get("id") for m in above if m.get("id")}
            cur_below = {m.get("id") for m in below if m.get("id")}

            # обгоны: был снизу → теперь сверху (тебя обогнали);
            # был сверху → теперь снизу (ты обогнал)
            overtaken_by = (cur_above & prev_below) if prev else set()
            we_overtook = (cur_below & prev_above) if prev else set()
            id2mod = {m.get("id"): m for m in window}

            for mid in overtaken_by:
                m = id2mod.get(mid, {})
                messages.append(
                    f"📉 <b>{html.escape(name)}</b>, тебя обогнал мод "
                    f"<b>{html.escape(str(m.get('name', mid)))}</b>!\n"
                    f"У него {fmt(m.get('downloadCount', 0))}, у тебя {fmt(dl)} "
                    f"(разница {fmt(m.get('downloadCount', 0) - dl)})."
                )
            for mid in we_overtook:
                m = id2mod.get(mid, {})
                messages.append(
                    f"📈 <b>{html.escape(name)}</b>, ты обогнал мод "
                    f"<b>{html.escape(str(m.get('name', mid)))}</b>!\n"
                    f"У тебя {fmt(dl)}, у него {fmt(m.get('downloadCount', 0))} "
                    f"(разница {fmt(dl - m.get('downloadCount', 0))})."
                )

            if FORCE_TABLE or not prev or (config.get("competitors_daily_table", True)):
                messages.append(build_table(name, slug, dl, position, total, window, radius))

            manual_lines = check_manual_competitors(manual)
            if manual_lines and (FORCE_TABLE or not prev):
                messages.append("👀 <b>Конкуренты из списка</b>\n" + "\n".join(manual_lines))

            new_state[slug] = {
                "my_downloads": dl,
                "position": position,
                "total": total,
                "above_ids": sorted(cur_above),
                "below_ids": sorted(cur_below),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"[{slug}] ошибка: {e}", file=sys.stderr)
            if slug in state:
                new_state[slug] = state[slug]

    failed = False
    for msg in messages:
        if not send_telegram(msg):
            failed = True
            break
    if failed:
        print("Telegram недоступен — состояние не сохранено.", file=sys.stderr)
        return 1

    if not DRY_RUN:
        save_json(COMP_FILE, new_state)
    if errors:
        print(f"WARNING: ошибок у проектов: {errors}. Повторим при следующем запуске.")
    print(f"Готово. Уведомлений: {len(messages)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
