#!/usr/bin/env python3
"""
Рейтинг мода в категории CurseForge по скачиваниям.

Через официальный API узнаёт позицию каждого проекта в своей категории
(сортировка по TotalDownloads) и присылает уведомление в Telegram,
если позиция изменилась с прошлой проверки.

Нужен CURSEFORGE_API_KEY (без него ранки получить нельзя — скрипт тихо
пропускает прогон).

Состояние — в data/ranks.json (формат совместим со старым).
Настройки категории — в config.json:
  "game_id": 432        — игра (432 = Minecraft)
  "category_id": 6      — категория (6 = Mods); посмотрите свою через
                          https://api.curseforge.com/v1/categories?gameId=432
"""
import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RANKS_FILE = DATA_DIR / "ranks.json"
CONFIG_FILE = ROOT / "config.json"

CF_API_URL = "https://api.curseforge.com/v1"
CF_MOD_URL = "https://www.curseforge.com/minecraft/mc-mods/{slug}"
PAGE_SIZE = 50          # максимум для CF API
MAX_PAGES = 200         # защита от бесконечного цикла (10 000 модов)

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


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы", file=sys.stderr)
        return False
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
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
    resp.raise_for_status()
    return resp.json()


def fetch_page(game_id: int, category_id: int, index: int) -> dict:
    return api_get(
        f"{CF_API_URL}/mods",
        params={
            "gameId": game_id,
            "categoryId": category_id,
            "sort": "TotalDownloads",
            "pageSize": PAGE_SIZE,
            "index": index,
        },
    )


def find_rank(cf_id: int, my_downloads: int, game_id: int, category_id: int):
    """Ищет позицию мода в категории. Возвращает (position, total) или (None, total)."""
    first = fetch_page(game_id, category_id, 0)
    total = (first.get("pagination") or {}).get("totalCount", 0)
    mods = first.get("data") or []
    if total == 0 and not mods:
        return None, 0

    # определяем направление сортировки по первой странице
    dls = [m.get("downloadCount", 0) for m in mods if m.get("downloadCount") is not None]
    descending = len(dls) >= 2 and dls[0] >= dls[-1]

    def scan(page_data, base_position):
        for i, m in enumerate(page_data):
            if m.get("id") == cf_id:
                return base_position + i
        return None

    if descending:
        pos = scan(mods, 1)
        if pos:
            return pos, total
        for page in range(1, MAX_PAGES):
            index = page * PAGE_SIZE
            if index >= total:
                break
            data = (fetch_page(game_id, category_id, index) or {}).get("data") or []
            if not data:
                break
            pos = scan(data, index + 1)
            if pos:
                return pos, total
        return None, total
    else:
        # сортировка по возрастанию — идём с конца
        pos = scan(list(reversed(mods)), max(total - len(mods), 0) + 1) if mods else None
        if pos:
            return pos, total
        for page in range(1, MAX_PAGES):
            index = max(total - (page + 1) * PAGE_SIZE, 0)
            data = (fetch_page(game_id, category_id, index) or {}).get("data") or []
            if not data:
                break
            pos = scan(list(reversed(data)), index + len(data))
            if pos:
                return pos, total
            if index == 0:
                break
        return None, total


def main() -> int:
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")

    config = load_json(CONFIG_FILE, {})
    projects = config.get("projects", [])
    game_id = int(config.get("game_id", 432))
    category_id = int(config.get("category_id", 6))

    if not projects:
        print("Нет проектов в config.json — пропускаем.")
        return 0

    if not CF_API_KEY:
        print("CURSEFORGE_API_KEY не задан — рейтинг категории недоступен, пропускаем.")
        return 0

    state = load_json(RANKS_FILE, {})
    messages: list[str] = []
    errors = 0

    for project in projects:
        slug = project.get("slug", "")
        name = project.get("name") or slug
        cf_id = project.get("cf_id")
        if not cf_id:
            print(f"[{slug}] нет cf_id в config.json — пропускаем.", file=sys.stderr)
            continue

        try:
            position, total = find_rank(int(cf_id), 0, game_id, category_id)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"[{slug}] ошибка получения ранка: {e}", file=sys.stderr)
            continue

        # свои скачивания — из данных категории (модем по найденному моду,
        # либо отдельным запросом, если позиция не нашлась)
        my_downloads = None
        try:
            mod_data = api_get(f"{CF_API_URL}/mods/{int(cf_id)}").get("data") or {}
            my_downloads = mod_data.get("downloadCount")
        except Exception as e:  # noqa: BLE001
            print(f"[{slug}] не удалось получить скачивания: {e}", file=sys.stderr)

        prev = state.get(slug, {})
        prev_position = prev.get("position")

        entry = {
            "rank": position,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "downloads": my_downloads if my_downloads is not None else prev.get("downloads"),
            "my_downloads": my_downloads if my_downloads is not None else prev.get("my_downloads"),
            "position": position if position is not None else prev_position,
            "total": total,
        }
        state[slug] = entry

        dl_str = f"{my_downloads:,}".replace(",", " ") if my_downloads else "—"

        if prev_position is None:
            # первый прогон — только запомним (не спамим), но если есть позиция — сообщим
            if position is not None:
                messages.append(
                    f"🏆 <b>{html.escape(name)}</b> — рейтинг в категории зафиксирован:\n"
                    f"Место: <b>#{position}</b> из {total}\n"
                    f"⬇️ Скачиваний: {dl_str}\n"
                    f"🔗 {CF_MOD_URL.format(slug=slug)}"
                )
        elif position is None:
            messages.append(
                f"⚠️ <b>{html.escape(name)}</b>: мод не найден в топ-{MAX_PAGES * PAGE_SIZE} "
                f"категории (всего в категории {total})."
            )
        elif position != prev_position:
            arrow = "📈" if position < prev_position else "📉"
            direction = "поднялся" if position < prev_position else "опустился"
            messages.append(
                f"{arrow} <b>{html.escape(name)}</b> {direction} в рейтинге категории!\n"
                f"Место: #{prev_position} → <b>#{position}</b> (из {total})\n"
                f"⬇️ Скачиваний: {dl_str}\n"
                f"🔗 {CF_MOD_URL.format(slug=slug)}"
            )

    if messages:
        ok = True
        for msg in messages:
            if not send_telegram(tg_token, tg_chat, msg):
                ok = False
                break
        if not ok:
            print("Telegram недоступен — состояние не сохранено.", file=sys.stderr)
            return 1

    save_json(RANKS_FILE, state)
    print(f"Проверено проектов: {len(projects)}, уведомлений: {len(messages)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
