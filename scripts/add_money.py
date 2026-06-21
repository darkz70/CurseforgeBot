#!/usr/bin/env python3
"""
Записывает сумму дохода за сегодня в data/money.json и подтверждает в Telegram.
Запускается вручную через GitHub Actions workflow_dispatch (Add money).
"""
import json
import os
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MONEY_FILE = DATA_DIR / "money.json"


def load_json(path: Path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send_telegram(token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=20,
    )
    resp.raise_for_status()


def main():
    amount = float(os.environ["MONEY_AMOUNT"])
    note = os.environ.get("MONEY_NOTE", "")
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat_id = os.environ["TELEGRAM_CHAT_ID"]

    today = date.today().isoformat()
    money = load_json(MONEY_FILE, {})
    money.setdefault(today, []).append({"amount": amount, "note": note})
    save_json(MONEY_FILE, money)

    text = f"💰 Записан доход за {today}: {amount:.2f}"
    if note:
        text += f" ({note})"
    send_telegram(tg_token, tg_chat_id, text)
    print(text)


if __name__ == "__main__":
    main()
