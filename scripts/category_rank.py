
#!/usr/bin/env python3
"""
Ищет моды рядом с твоим по скачиваниям на CurseForge.
Показывает кто обгоняет и кого ты обгоняешь.
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

CF_API_URL = "https://api.curseforge.com/v1"
CF_API_KEY = os.environ.get("CURSEFORGE_API_KEY")

CF_PAGE_URL = "https://www.curseforge.com/minecraft/mc-mods?sortBy=total-downloads&gameVersionTypeId=1&page={page}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


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


def fetch_neighbors_via_api(my_downloads: int, my_cf_id: int) -> dict:
    """Ищет моды рядом по скачиваниям через CF API."""
    if not CF_API_KEY:
        return {}

    try:
        # Ищем моды с сортировкой по скачиваниям, берём страницы вокруг нашего числа
        above = []
        below = []

        # Поиск модов с похожим числом скачиваний
        for page in range(0, 5):
            resp = requests.get(
                f"{CF_API_URL}/mods/search",
                headers={"x-api-key": CF_API_KEY, "Accept": "application/json"},
                params={
                    "gameId": 432,
                    "classId": 6,  # Mods
                    "sortField": 6,  # TotalDownloads
                    "sortOrder": "desc",
                    "pageSize": 50,
                    "index": page * 50,
                },
                timeout=20,
            )
            resp.raise_for_status()
            mods = resp.json().get("data", [])
            if not mods:
                break

            for mod in mods:
                dl = mod.get("downloadCount", 0)
                mod_id = mod.get("id")
                name = mod.get("name", "")
                slug = mod.get("slug", "")

                if mod_id == my_cf_id:
                    continue

                if dl > my_downloads:
                    above.append({"name": name, "slug": slug, "downloads": dl, "id": mod_id})
                elif dl <= my_downloads:
                    below.append({"name": name, "slug": slug, "downloads": dl, "id": mod_id})

            # Если нашли достаточно модов ниже — хватит
            if len(below) >= 3:
                break

        # Берём 3 ближайших выше и 3 ниже
        above_sorted = sorted(above, key=lambda x: x["downloads"])[:3]
        below_sorted = sorted(below, key=lambda x: x["downloads"], reverse=True)[:3]

        return {"above": above_sorted, "below": below_sorted}

    except Exception as e:
        print(f"Ошибка CF API поиска соседей: {e}")
        return {}


def fetch_my_downloads(slug: str, cf_id: int | None) -> int | None:
    """Получает текущее число скачиваний мода."""
    if CF_API_KEY and cf_id:
        try:
            resp = requests.get(
                f"{CF_API_URL}/mods/{cf_id}",
                headers={"x-api-key": CF_API_KEY, "Accept": "application/json"},
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json().get("data", {}).get("downloadCount")
        except Exception as e:
            print(f"Ошибка получения скачиваний: {e}")

    # Fallback — парсинг HTML
    try:
        url = f"https://www.curseforge.com/minecraft/mc-mods/{slug}"
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
            s = dl.group(1).strip().replace(",", "")
            if s.upper().endswith("M"): return int(float(s[:-1]) * 1_000_000)
            if s.upper().endswith("K"): return int(float(s[:-1]) * 1_000)
            return int(float(s))
    except Exception:
        pass
    return None


def main():
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat_id = os.environ["TELEGRAM_CHAT_ID"]

    cfg = load_json(CONFIG_FILE, {})
    projects = cfg.get("projects", [])
    state = load_json(RANK_FILE, {})

    for proj in projects:
        slug = str(proj.get("slug") or proj.get("id"))
        name = proj.get("name", slug)
        cf_id = proj.get("cf_id")

        my_downloads = fetch_my_downloads(slug, cf_id)
        if my_downloads is None:
            print(f"Не удалось получить скачивания для {name}")
            continue

        print(f"{name}: {my_downloads:,} скачиваний")

        neighbors = fetch_neighbors_via_api(my_downloads, cf_id or 0)

        above = neighbors.get("above", [])
        below = neighbors.get("below", [])

        if not above and not below:
            print(f"Соседи не найдены для {name}")
            state.setdefault(slug, {}).update({
                "downloads": my_downloads,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            })
            continue

        lines = [f"⚔️ <b>{name}</b> — рейтинг соседей\n📦 Твои скачивания: {my_downloads:,}\n"]

        if above:
            lines.append("📈 <b>Обгоняют тебя:</b>")
            for m in above:
                diff = m["downloads"] - my_downloads
                lines.append(f"  • {m['name']}: {m['downloads']:,} (+{diff:,})")

        lines.append("")

        if below:
            lines.append("📉 <b>Ты обгоняешь:</b>")
            for m in below:
                diff = my_downloads - m["downloads"]
                lines.append(f"  • {m['name']}: {m['downloads']:,} (-{diff:,})")

        # Проверяем изменения с прошлого раза
        prev_above = state.get(slug, {}).get("above_ids", [])
        curr_above_ids = [m["id"] for m in above]
        newly_overtaken = [m for m in below if m["id"] in prev_above]
        if newly_overtaken:
            lines.append("")
            lines.append("🎉 <b>Ты обогнал:</b>")
            for m in newly_overtaken:
                lines.append(f"  • {m['name']}!")

        send_telegram(tg_token, tg_chat_id, "\n".join(lines))

        state.setdefault(slug, {}).update({
            "downloads": my_downloads,
            "above_ids": curr_above_ids,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        })

    save_json(RANK_FILE, state)
    print("Рейтинг соседей проверен.")


if __name__ == "__main__":
    main()
