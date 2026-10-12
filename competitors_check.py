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
    with open(path, "w", encoding="
