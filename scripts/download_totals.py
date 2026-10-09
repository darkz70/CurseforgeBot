#!/usr/bin/env python3
"""
Сводка: сколько всего скачиваний у ваших модов (прямо сейчас).

Считает текущее число скачиваний каждого проекта на CurseForge
(официальный API с ключом либо публичный CFWidget) и присылает
в Telegram сводку с итогом по каждому моду и общим числом.

Запускается вручную: Actions → Download totals → Run workflow.
Локально: python scripts/download_totals.py --dry-run
"""
import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.json"

CF_API_URL = "https://api.curseforge.com/v1"
CFWIDGET_API = "https://api.cfwidget.com/minecraft/mc-mods/{slug}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

DRY_RUN = "--dry-run" in sys.argv


def load_json(path: Path, default):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default
    return default


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    if DRY_RUN:
        print("=" * 60)
        print("[DRY-RUN] Telegram message:")
        print(text)
        print("=" * 60)
        return True
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


def fmt(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def cf_downloads(slug: str, cf_id, api_key: str):
    """Возвращает число скачиваний на CurseForge: сначала официальный API, потом CFWidget."""
    if api_key and cf_id:
        try:
            resp = requests.get(
                f"{CF_API_URL}/mods/{int(cf_id)}",
                headers={**HEADERS, "x-api-key": api_key},
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("downloadCount")
        except (requests.RequestException, ValueError) as e:
            print(f"CF API ({slug}): {e}", file=sys.stderr)
    try:
        resp = requests.get(CFWIDGET_API.format(slug=slug), headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            return resp.json().get("downloads", {}).get("total")
    except (requests.RequestException, ValueError) as e:
        print(f"CFWidget ({slug}): {e}", file=sys.stderr)
    return None


def main() -> int:
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    cf_key = os.environ.get("CURSEFORGE_API_KEY", "")

    config = load_json(CONFIG_FILE, {})
    projects = config.get("projects", [])

    if not projects:
        print("Нет проектов в config.json")
        return 0

    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    lines = [f"📦 <b>Скачивания ваших модов</b>", f"🕐 {now}", ""]
    grand_total = 0
    any_data = False

    for proj in projects:
        slug = proj.get("slug", "")
        name = proj.get("name") or slug
        cf_id = proj.get("cf_id")

        cf = cf_downloads(slug, cf_id, cf_key)

        lines.append(f"<b>{html.escape(name)}</b>")
        if cf is not None:
            lines.append(f"  🟠 CurseForge: {fmt(cf)}")
            grand_total += cf
            any_data = True
        else:
            lines.append("  🟠 CurseForge: нет данных")
        lines.append("")

    if len(projects) > 1 or grand_total:
        lines.append(f"<b>🏆 Всего по всем модам: {fmt(grand_total)}</b>")

    if not any_data:
        print("Не удалось получить данные ни по одному проекту.", file=sys.stderr)
        return 1

    if not send_telegram(tg_token, tg_chat, "\n".join(lines)):
        return 1
    print("Сводка отправлена.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
