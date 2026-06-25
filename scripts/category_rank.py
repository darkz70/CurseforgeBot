#!/usr/bin/env python3
"""
Находит моды рядом с твоим по скачиваниям.
Парсит новые моды CurseForge и сравнивает через CFWidget API.
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
CFWIDGET_HEADERS = {"User-Agent": "CurseforgeBot/1.0 (github.com/darkz70/CurseforgeBot)"}

# Новые моды отсортированные по дате
CF_NEW_MODS_URL = "https://www.curseforge.com/minecraft/mc-mods?sortBy=created-date&page={page}"
CFWIDGET_API = "https://api.cfwidget.com/minecraft/mc-mods/{slug}"


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


def fetch_my_downloads(slug: str) -> int | None:
    """Получает точные скачивания через CFWidget."""
    try:
        resp = requests.get(
            CFWIDGET_API.format(slug=slug),
            headers=CFWIDGET_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        downloads = data.get("downloads", {})
        if isinstance(downloads, dict):
            return downloads.get("total", 0)
        return int(downloads)
    except Exception as e:
        print(f"CFWidget ошибка для {slug}: {e}")
        return None


def fetch_new_mod_slugs(pages: int = 10) -> list[str]:
    """Парсит slugs новых модов с CurseForge."""
    slugs = []
    for page in range(1, pages + 1):
        try:
            url = CF_NEW_MODS_URL.format(page=page)
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            html = resp.text
            # Ищем slugs модов
            found = re.findall(r'href="/minecraft/mc-mods/([a-z0-9-]+)"', html)
            for slug in found:
                if slug not in slugs and slug not in ("search", "all"):
                    slugs.append(slug)
        except Exception as e:
            print(f"Ошибка парсинга страницы {page}: {e}")
            break
    return slugs


def main():
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat_id = os.environ["TELEGRAM_CHAT_ID"]

    cfg = load_json(CONFIG_FILE, {})
    projects = cfg.get("projects", [])
    state = load_json(RANK_FILE, {})

    for proj in projects:
        slug = str(proj.get("slug") or proj.get("id"))
        name = proj.get("name", slug)

        # Мои скачивания
        my_downloads = fetch_my_downloads(slug)
        if my_downloads is None:
            print(f"Не удалось получить скачивания для {name}")
            continue

        print(f"{name}: {my_downloads:,} скачиваний")

        # Собираем новые моды
        all_slugs = fetch_new_mod_slugs(pages=8)
        # Убираем себя
        all_slugs = [s for s in all_slugs if s != slug]

        # Получаем скачивания для каждого мода
        mods_data = []
        for s in all_slugs[:80]:  # не больше 80 чтобы не спамить API
            dl = fetch_my_downloads(s)
            if dl is not None:
                mods_data.append({"slug": s, "downloads": dl})

        # Добавляем себя
        mods_data.append({"slug": slug, "name": name, "downloads": my_downloads, "is_me": True})

        # Сортируем по скачиваниям
        mods_data = sorted(mods_data, key=lambda x: x["downloads"], reverse=True)

        # Находим свою позицию
        my_pos = next((i for i, m in enumerate(mods_data) if m.get("is_me")), None)
        if my_pos is None:
            print("Не удалось найти себя в списке")
            continue

        # 3 выше и 3 ниже
        above = mods_data[max(0, my_pos - 3):my_pos]
        below = mods_data[my_pos + 1:my_pos + 4]

        prev_pos = state.get(slug, {}).get("position")
        pos_change = ""
        if prev_pos and prev_pos != my_pos + 1:
            diff = prev_pos - (my_pos + 1)
            pos_change = f" (↑ +{diff})" if diff > 0 else f" (↓ {abs(diff)})"

        lines = [f"⚔️ <b>{name}</b> — рейтинг среди новых модов\n"]
        lines.append(f"📊 Позиция: <b>#{my_pos + 1}</b> из {len(mods_data)}{pos_change}\n")

        if above:
            lines.append("⬆️ <b>Обгоняют тебя:</b>")
            for m in above:
                diff = m["downloads"] - my_downloads
                lines.append(f"  • {m['slug']}: {m['downloads']:,} (+{diff:,})")

        lines.append(f"\n🟢 <b>{name}: {my_downloads:,}</b>\n")

        if below:
            lines.append("⬇️ <b>Ты обгоняешь:</b>")
            for m in below:
                diff = my_downloads - m["downloads"]
                lines.append(f"  • {m['slug']}: {m['downloads']:,} (-{diff:,})")

        send_telegram(tg_token, tg_chat_id, "\n".join(lines))

        state.setdefault(slug, {}).update({
            "my_downloads": my_downloads,
            "position": my_pos + 1,
            "total": len(mods_data),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        })

    save_json(RANK_FILE, state)
    print("Рейтинг проверен.")


if __name__ == "__main__":
    main()
    
