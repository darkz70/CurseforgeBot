#!/usr/bin/env python3
"""
Уведомления о НОВЫХ ВЕРСИЯХ мода в Telegram.

Проверяет файлы проекта на CurseForge (через официальный API или публичный
CFWidget API) и версии на Modrinth (публичный API). Как только появляется
новая версия/файл — присылает уведомление в Telegram с названием версии,
списком игровых версий, загрузчиков и changelog.

Состояние хранится в data/versions.json. Первый запуск — «тихая» инициализация
(запоминает текущую последнюю версию и присылает подтверждение, что бот подключен).

Переменные окружения:
  TELEGRAM_BOT_TOKEN  — токен бота (обязательно)
  TELEGRAM_CHAT_ID    — id чата (обязательно)
  CURSEFORGE_API_KEY  — ключ CurseForge (опционально; без него используется CFWidget)
  MODRINTH_TOKEN      — токен Modrinth (опционально; публичные проекты работают без него)

Флаг --dry-run: не слать в Telegram, а печатать сообщения в консоль.
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
VERSIONS_FILE = DATA_DIR / "versions.json"
CONFIG_FILE = ROOT / "config.json"

CF_API_URL = "https://api.curseforge.com/v1"
CFWIDGET_API = "https://api.cfwidget.com/minecraft/mc-mods/{slug}"
MODRINTH_API = "https://api.modrinth.com/v2"
CF_MOD_URL = "https://www.curseforge.com/minecraft/mc-mods/{slug}"
MR_MOD_URL = "https://modrinth.com/mod/{slug}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT  10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

DRY_RUN = "--dry-run" in sys.argv

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
CF_API_KEY = os.environ.get("CURSEFORGE_API_KEY", "")
MR_TOKEN = os.environ.get("MODRINTH_TOKEN", "")

MAX_CHANGELOG_LEN = 900   # обрезаем слишком длинные чейнджлоги
MAX_NEW_PER_RUN = 5       # максимум уведомлений за один прогон (анти-спам)


# ---------------------------------------------------------------------------
# утилиты
# ---------------------------------------------------------------------------

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


def get_config() -> dict:
    return load_json(CONFIG_FILE, {})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fmt_dt(iso_or_epoch) -> str:
    """Форматирует дату (ISO-строку или unix-epoch) в читаемый вид."""
    try:
        if isinstance(iso_or_epoch, (int, float)):
            dt = datetime.fromtimestamp(iso_or_epoch, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(iso_or_epoch).replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y %H:%M UTC")
    except (ValueError, TypeError, OSError):
        return str(iso_or_epoch)


def clean_changelog(text: str) -> str:
    """Убирает лишнюю разметку и обрезает чейнджлог."""
    if not text:
        return ""
    import re
    # блочные теги превращаем в переносы строк, остальные — вырезаем
    text = re.sub(r"(?i)</?(p|br|li|ul|ol|div|h[1-6]|hr|pre|blockquote)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) > MAX_CHANGELOG_LEN:
        text = text[:MAX_CHANGELOG_LEN].rstrip() + "…"
    return text


def send_telegram(text: str) -> bool:
    """Отправляет сообщение в Telegram (или печатает при --dry-run)."""
    if DRY_RUN:
        print("=" * 60)
        print("[DRY-RUN] Telegram message:")
        print(text)
        print("=" * 60)
        return True
    if not TG_TOKEN or not TG_CHAT:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы", file=sys.stderr)
        return False
    resp = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={
            "chat_id": TG_CHAT,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"ERROR: Telegram вернул {resp.status_code}: {resp.text}", file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# CurseForge: получение списка файлов
# ---------------------------------------------------------------------------

def fetch_cf_files_official(cf_id: int) -> list[dict] | None:
    """Официальный API CurseForge (нужен ключ). Возвращает файлы, новые сверху."""
    headers = dict(HEADERS)
    headers["x-api-key"] = CF_API_KEY
    try:
        resp = requests.get(
            f"{CF_API_URL}/mods/{cf_id}/files",
            params={"pageSize": 20, "sort": "FileDate"},
            headers=headers,
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"CF API: статус {resp.status_code} для мода {cf_id}", file=sys.stderr)
            return None
        data = resp.json().get("data", [])
    except (requests.RequestException, ValueError) as e:
        print(f"CF API: ошибка для мода {cf_id}: {e}", file=sys.stderr)
        return None

    files = []
    for f in data:
        files.append({
            "id": f.get("id"),
            "name": f.get("displayName") or f.get("fileName") or str(f.get("id")),
            "filename": f.get("fileName", ""),
            "date": f.get("fileDate", ""),          # ISO строка
            "game_versions": [v for v in f.get("gameVersions", []) if v],
            "changelog": f.get("changelog", "") or "",
            "downloads": f.get("downloadCount", 0),
        })
    # по убыванию даты
    files.sort(key=lambda x: x["date"] or "", reverse=True)
    return files


def fetch_cf_files_cfwidget(slug: str) -> list[dict] | None:
    """Публичный CFWidget API — без ключа."""
    try:
        resp = requests.get(CFWIDGET_API.format(slug=slug), headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            print(f"CFWidget: статус {resp.status_code} для {slug}", file=sys.stderr)
            return None
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"CFWidget: ошибка для {slug}: {e}", file=sys.stderr)
        return None

    files = []
    for filename, meta in (data.get("files") or {}).items():
        ts = meta.get("time") or 0
        files.append({
            "id": meta.get("id") or filename,
            "name": meta.get("display") or filename,
            "filename": filename,
            "date": ts,                             # unix timestamp
            "game_versions": [v for v in (meta.get("versions") or []) if v],
            "changelog": "",
            "downloads": meta.get("downloads", {}).get("total", 0)
            if isinstance(meta.get("downloads"), dict) else meta.get("downloads", 0),
        })
    files.sort(key=lambda x: x["date"] or 0, reverse=True)
    return files


def fetch_cf_files(project: dict) -> list[dict] | None:
    cf_id = project.get("cf_id")
    slug = project.get("slug", "")
    if CF_API_KEY and cf_id:
        files = fetch_cf_files_official(int(cf_id))
        if files is not None:
            return files
        print("CF API не сработал — пробую CFWidget…", file=sys.stderr)
    if slug:
        return fetch_cf_files_cfwidget(slug)
    return None


# ---------------------------------------------------------------------------
# Modrinth: получение списка версий
# ---------------------------------------------------------------------------

def fetch_mr_versions(slug: str) -> list[dict] | None:
    headers = {"User-Agent": "CurseforgeBot/2.0 (github.com/darkz70/CurseforgeBot)"}
    if MR_TOKEN:
        headers["Authorization"] = MR_TOKEN
    try:
        resp = requests.get(
            f"{MODRINTH_API}/project/{slug}/version",
            params={"per_page": 10},
            headers=headers,
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"Modrinth: статус {resp.status_code} для {slug}", file=sys.stderr)
            return None
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"Modrinth: ошибка для {slug}: {e}", file=sys.stderr)
        return None

    versions = []
    for v in data:
        versions.append({
            "id": v.get("id"),
            "name": v.get("name") or v.get("version_number") or v.get("id"),
            "version_number": v.get("version_number", ""),
            "date": v.get("date_published", ""),
            "game_versions": v.get("game_versions", []) or [],
            "loaders": v.get("loaders", []) or [],
            "changelog": v.get("changelog", "") or "",
            "downloads": v.get("downloads", 0),
        })
    versions.sort(key=lambda x: x["date"] or "", reverse=True)
    return versions


# ---------------------------------------------------------------------------
# формирование сообщений
# ---------------------------------------------------------------------------

def msg_new_cf(name: str, slug: str, file: dict) -> str:
    gv = ", ".join(file["game_versions"]) if file["game_versions"] else "—"
    lines = [
        "🎉 <b>Новая версия мода!</b>",
        "",
        f"<b>{html.escape(name)}</b> (CurseForge)",
        f"📦 {html.escape(str(file['name']))}",
        f"🎮 {html.escape(gv)}",
        f"🕐 {fmt_dt(file['date'])}",
    ]
    changelog = clean_changelog(file.get("changelog", ""))
    if changelog:
        lines += ["", "<b>Changelog:</b>", html.escape(changelog)]
    lines += ["", f"🔗 {CF_MOD_URL.format(slug=slug)}/files"]
    return "\n".join(lines)


def msg_new_mr(name: str, slug: str, ver: dict) -> str:
    gv = ", ".join(ver["game_versions"]) if ver["game_versions"] else "—"
    loaders = ", ".join(ver["loaders"]) if ver["loaders"] else ""
    lines = [
        "🎉 <b>Новая версия мода!</b>",
        "",
        f"<b>{html.escape(name)}</b> (Modrinth)",
        f"📦 {html.escape(str(ver['name']))}",
        f"🎮 {html.escape(gv)}" + (f"\n⚙️ {html.escape(loaders)}" if loaders else ""),
        f"🕐 {fmt_dt(ver['date'])}",
    ]
    changelog = clean_changelog(ver.get("changelog", ""))
    if changelog:
        lines += ["", "<b>Changelog:</b>", html.escape(changelog)]
    lines += ["", f"🔗 {MR_MOD_URL.format(slug=slug)}/versions"]
    return "\n".join(lines)


def msg_hello(name: str, source: str, latest: dict | None) -> str:
    ver = html.escape(str(latest["name"])) if latest else "нет данных"
    return (
        "✅ <b>Бот уведомлений о версиях подключен</b>\n\n"
        f"Слежу за <b>{html.escape(name)}</b> на {source}.\n"
        f"Текущая последняя версия: {ver}\n"
        "О новых версиях буду сообщать здесь. 🎉"
    )


# ---------------------------------------------------------------------------
# основная логика
# ---------------------------------------------------------------------------

def check_curseforge(project: dict, state: dict, messages: list[str]) -> dict:
    slug = project.get("slug", "")
    name = project.get("name") or slug
    key = f"cf:{slug}"
    st = state.get(key, {})

    files = fetch_cf_files(project)
    if not files:
        print(f"[{slug}] CurseForge: не удалось получить файлы", file=sys.stderr)
        st["error_at"] = now_iso()
        return st

    latest = files[0]
    known_ids = set(st.get("known_file_ids", []))

    if not st:  # первый запуск — тихая инициализация + подтверждение
        messages.append(msg_hello(name, "CurseForge", latest))
        st = {
            "known_file_ids": [f["id"] for f in files[:MAX_NEW_PER_RUN]],
            "last_file_id": latest["id"],
            "last_date": latest["date"],
            "checked_at": now_iso(),
        }
        return st

    new_files = [f for f in files if f["id"] not in known_ids]
    # старые по дате вперёд, свежие последними
    new_files.reverse()

    for f in new_files[:MAX_NEW_PER_RUN]:
        messages.append(msg_new_cf(name, slug, f))

    if new_files:
        known_ids.update(f["id"] for f in new_files)
        # держим список известных файлов компактным
        all_ids = [f["id"] for f in files]
        st["known_file_ids"] = [i for i in all_ids if i in known_ids][:20]
        st["last_file_id"] = latest["id"]
        st["last_date"] = latest["date"]
        st["checked_at"] = now_iso()
        st.pop("error_at", None)
    else:
        st["checked_at"] = now_iso()
        st.pop("error_at", None)
    return st


def check_modrinth(mr_project: dict, state: dict, messages: list[str]) -> dict:
    slug = mr_project.get("slug", "")
    name = mr_project.get("name") or slug
    key = f"mr:{slug}"
    st = state.get(key, {})

    versions = fetch_mr_versions(slug)
    if not versions:
        print(f"[{slug}] Modrinth: не удалось получить версии", file=sys.stderr)
        st["error_at"] = now_iso()
        return st

    latest = versions[0]
    known_ids = set(st.get("known_version_ids", []))

    if not st:
        messages.append(msg_hello(name, "Modrinth", latest))
        st = {
            "known_version_ids": [v["id"] for v in versions[:MAX_NEW_PER_RUN]],
            "last_version_id": latest["id"],
            "last_date": latest["date"],
            "checked_at": now_iso(),
        }
        return st

    new_versions = [v for v in versions if v["id"] not in known_ids]
    new_versions.reverse()

    for v in new_versions[:MAX_NEW_PER_RUN]:
        messages.append(msg_new_mr(name, slug, v))

    if new_versions:
        known_ids.update(v["id"] for v in new_versions)
        all_ids = [v["id"] for v in versions]
        st["known_version_ids"] = [i for i in all_ids if i in known_ids][:20]
        st["last_version_id"] = latest["id"]
        st["last_date"] = latest["date"]
    st["checked_at"] = now_iso()
    st.pop("error_at", None)
    return st


def main() -> int:
    config = get_config()
    state = load_json(VERSIONS_FILE, {})
    messages: list[str] = []
    new_state: dict = {}
    errors = 0

    # CurseForge-проекты
    for project in config.get("projects", []):
        slug = project.get("slug", "")
        if not slug:
            continue
        try:
            new_state[f"cf:{slug}"] = check_curseforge(project, state, messages)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"[{slug}] CurseForge: исключение: {e}", file=sys.stderr)
            new_state[f"cf:{slug}"] = state.get(f"cf:{slug}", {})

    # Modrinth-проекты
    for mr in config.get("modrinth_projects", []):
        slug = mr.get("slug", "")
        if not slug:
            continue
        try:
            new_state[f"mr:{slug}"] = check_modrinth(mr, state, messages)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"[{slug}] Modrinth: исключение: {e}", file=sys.stderr)
            new_state[f"mr:{slug}"] = state.get(f"mr:{slug}", {})

    if not messages:
        print("Новых версий нет.")
        if not DRY_RUN:
            save_json(VERSIONS_FILE, new_state)
        return 1 if errors else 0

    # отправка. Состояние сохраняем ТОЛЬКО после успешной отправки,
    # чтобы при сбое Telegram уведомление ушло при следующем прогоне.
    failed = False
    for msg in messages:
        if not send_telegram(msg):
            failed = True
            break

    if failed:
        print("Не удалось отправить сообщения — состояние НЕ сохранено.", file=sys.stderr)
        return 1

    if not DRY_RUN:
        save_json(VERSIONS_FILE, new_state)
    print(f"Отправлено сообщений: {len(messages)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
