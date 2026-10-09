#!/usr/bin/env python3
"""
Проверяет статус мода на Modrinth через официальный API с токеном.
- Точные скачивания
- Статус мода
- Новые версии с changelog
- Сравнение CurseForge vs Modrinth
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODRINTH_FILE = DATA_DIR / "modrinth.json"
CONFIG_FILE = ROOT / "config.json"

MODRINTH_API = "https://api.modrinth.com/v2"

CF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

CURSEFORGE_MOD_URL = "https://www.curseforge.com/minecraft/mc-mods/{slug}"

STATUS_LABELS = {
    "approved":   "✅ Одобрен",
    "processing": "⏳ На рассмотрении",
    "rejected":   "❌ Отклонён",
    "withheld":   "⚠️ Приостановлен",
    "draft":      "📝 Черновик",
    "unlisted":   "🔒 Скрытый",
    "scheduled":  "🕐 Запланирован",
    "unknown":    "❓ Неизвестен",
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


def mr_headers(token: str) -> dict:
    return {
        "Authorization": token,
        "User-Agent": "CurseforgeBot/2.0 (github.com/darkz70/CurseforgeBot)",
    }


def fetch_modrinth_project(slug: str, token: str) -> dict | None:
    try:
        resp = requests.get(
            f"{MODRINTH_API}/project/{slug}",
            headers=mr_headers(token),
            timeout=20,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Ошибка Modrinth API для {slug}: {e}")
        return None


def fetch_modrinth_versions(slug: str, token: str) -> list:
    try:
        resp = requests.get(
            f"{MODRINTH_API}/project/{slug}/version",
            headers=mr_headers(token),
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def fetch_cf_downloads(slug: str) -> int | None:
    try:
        url = CURSEFORGE_MOD_URL.format(slug=slug)
        resp = requests.get(url, headers=CF_HEADERS, timeout=30)
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
            if s.upper().endswith("M"):
                return int(float(s[:-1]) * 1_000_000)
            if s.upper().endswith("K"):
                return int(float(s[:-1]) * 1_000)
            return int(float(s))
    except Exception:
        pass
    return None


def main():
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat_id = os.environ["TELEGRAM_CHAT_ID"]
    announce_chat_id = os.environ.get("ANNOUNCE_CHAT_ID", tg_chat_id)
    mr_token = os.environ["MODRINTH_TOKEN"]

    cfg = load_json(CONFIG_FILE, {})
    modrinth_projects = cfg.get("modrinth_projects", [])
    cf_projects = {p["slug"]: p for p in cfg.get("projects", [])}

    if not modrinth_projects:
        print("Нет modrinth_projects в config.json — пропускаем.")
        return

    state = load_json(MODRINTH_FILE, {})

    for proj in modrinth_projects:
        slug = proj.get("slug")
        name = proj.get("name", slug)
        cf_slug = proj.get("cf_slug") or next(
            (s for s, p in cf_projects.items() if p.get("name") == name), None
        )

        data = fetch_modrinth_project(slug, mr_token)
        if not data:
            print(f"Не удалось получить данные для {slug}")
            continue

        status = data.get("status", "unknown")
        downloads_mr = data.get("downloads", 0)
        followers = data.get("followers", 0)
        prev = state.get(slug, {})
        prev_status = prev.get("status")
        is_new = prev_status is None

        status_label = STATUS_LABELS.get(status, status)

        # Первое обнаружение
        if is_new:
            msg = (
                f"👁 <b>{name}</b> найден на Modrinth!\n"
                f"Статус: <b>{status_label}</b>\n"
                f"Скачиваний: {downloads_mr:,}\n"
                f"Подписчиков: {followers:,}\n"
                f"https://modrinth.com/mod/{slug}"
            )
            send_telegram(tg_token, tg_chat_id, msg)

        # Изменение статуса
        elif prev_status != status:
            if status == "approved":
                msg = (
                    f"🎉 <b>{name}</b> одобрен на Modrinth!\n"
                    f"Статус: {STATUS_LABELS.get(prev_status, prev_status)} → <b>{status_label}</b>\n"
                    f"Скачиваний: {downloads_mr:,} | Подписчиков: {followers:,}\n"
                    f"https://modrinth.com/mod/{slug}"
                )
            elif status == "rejected":
                msg = (
                    f"😔 <b>{name}</b> отклонён на Modrinth.\n"
                    f"Статус: {STATUS_LABELS.get(prev_status, prev_status)} → <b>{status_label}</b>\n"
                    f"Проверь почту или раздел модерации."
                )
            else:
                msg = (
                    f"ℹ️ <b>{name}</b> на Modrinth: статус изменился\n"
                    f"{STATUS_LABELS.get(prev_status, prev_status)} → <b>{status_label}</b>"
                )
            send_telegram(tg_token, tg_chat_id, msg)

        # Новая версия
        versions = fetch_modrinth_versions(slug, mr_token)
        if versions:
            latest = versions[0]
            latest_id = latest.get("id")
            prev_version_id = prev.get("latest_version_id")

            if prev_version_id and prev_version_id != latest_id:
                ver_name = latest.get("name", latest_id)
                game_versions = ", ".join(latest.get("game_versions", []))
                loaders = ", ".join(v.capitalize() for v in latest.get("loaders", []))
                changelog = (latest.get("changelog") or "").strip()
                if len(changelog) > 500:
                    changelog = changelog[:500] + "..."

                msg = (
                    f"🆕 <b>{name}</b> — новая версия!\n"
                    f"<b>{ver_name}</b>\n"
                    f"🎮 MC: {game_versions}\n"
                    f"⚙️ {loaders}\n"
                )
                if changelog:
                    msg += f"\n📝 <i>{changelog}</i>\n"
                msg += f"\n🔗 https://modrinth.com/mod/{slug}"

                send_telegram(tg_token, tg_chat_id, msg)
                if announce_chat_id != tg_chat_id:
                    send_telegram(tg_token, announce_chat_id, msg)

            state.setdefault(slug, {})["latest_version_id"] = latest_id

        # Сравнение CurseForge vs Modrinth (только если одобрен)
        if cf_slug and not is_new and status == "approved":
            cf_downloads = fetch_cf_downloads(cf_slug)
            if cf_downloads is not None:
                prev_cf = prev.get("cf_downloads")
                prev_mr = prev.get("mr_downloads", 0)
                if prev_cf is not None:
                    delta_cf = cf_downloads - prev_cf
                    delta_mr = downloads_mr - prev_mr
                    if delta_cf > 0 or delta_mr > 0:
                        total = cf_downloads + downloads_mr
                        cf_pct = cf_downloads / total * 100 if total > 0 else 0
                        mr_pct = downloads_mr / total * 100 if total > 0 else 0
                        msg = (
                            f"📊 <b>{name}</b> — CurseForge vs Modrinth\n\n"
                            f"🟠 CurseForge: {cf_downloads:,} ({cf_pct:.0f}%)"
                            + (f" +{delta_cf:,}" if delta_cf > 0 else "") + "\n"
                            f"🟢 Modrinth: {downloads_mr:,} ({mr_pct:.0f}%)"
                            + (f" +{delta_mr:,}" if delta_mr > 0 else "") + "\n"
                            f"📦 Всего: {total:,}"
                        )
                        send_telegram(tg_token, tg_chat_id, msg)
                state.setdefault(slug, {})["cf_downloads"] = cf_downloads

        # Сохраняем состояние
        state.setdefault(slug, {}).update({
            "status": status,
            "name": name,
            "mr_downloads": downloads_mr,
            "followers": followers,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        })

    save_json(MODRINTH_FILE, state)
    print("Modrinth проверен.")


if __name__ == "__main__":
    main()
