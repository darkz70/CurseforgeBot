#!/usr/bin/env python3
"""
Еженедельный PDF отчёт — отправляет красивый PDF с графиками в Telegram.
Запускается каждое воскресенье в 20:00 UTC.
"""
import io
import json
import os
import sys
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HISTORY_FILE = DATA_DIR / "history.json"
CONFIG_FILE = ROOT / "config.json"


def load_json(path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=20).raise_for_status()


def send_document(token, chat_id, doc_bytes, filename, caption=""):
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    requests.post(
        url,
        data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
        files={"document": (filename, doc_bytes, "application/pdf")},
        timeout=60,
    ).raise_for_status()


def hourly_keys(history):
    return sorted(k for k in history if "T" in k)


def get_daily_totals(history, slug, days=7):
    """Возвращает список (date_str, total_downloads_that_day) за последние N дней."""
    today = _date.today().isoformat()
    results = []
    for d in range(days, 0, -1):
        date_str = (_date.today() - timedelta(days=d)).isoformat()
        day_keys = sorted(k for k in hourly_keys(history) if k.startswith(date_str))
        if len(day_keys) < 2:
            results.append((date_str, 0))
            continue
        first = history[day_keys[0]].get(slug, {}).get("downloadCount")
        last = history[day_keys[-1]].get(slug, {}).get("downloadCount")
        if first is not None and last is not None:
            results.append((date_str, last - first))
        else:
            results.append((date_str, 0))
    return results


def make_pdf(history, projects, now_utc):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages
    except ImportError:
        return None

    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        for proj in projects:
            slug = str(proj.get("slug") or proj.get("id"))
            name = proj.get("name", slug)

            daily = get_daily_totals(history, slug, days=7)
            dates = [d[0][5:] for d in daily]  # MM-DD
            totals = [d[1] for d in daily]

            # Почасовой за последние 7 дней
            hour_keys = [k for k in hourly_keys(history)
                         if k >= (now_utc - timedelta(days=7)).strftime("%Y-%m-%dT%H:00")]
            hour_labels = [k[11:16] for k in hour_keys]
            hour_deltas = []
            for i in range(1, len(hour_keys)):
                p = history[hour_keys[i-1]].get(slug, {}).get("downloadCount")
                c = history[hour_keys[i]].get(slug, {}).get("downloadCount")
                hour_deltas.append((c - p) if p is not None and c is not None else 0)
            hour_labels = hour_labels[1:]

            fig, axes = plt.subplots(2, 1, figsize=(10, 8))
            fig.suptitle(f"{name} — Недельный отчёт\n{now_utc.strftime('%d.%m.%Y')}", fontsize=14)

            # День
            axes[0].bar(dates, totals, color="#4fc3f7")
            axes[0].set_title("Скачивания по дням")
            axes[0].set_ylabel("Скачиваний")
            axes[0].set_xlabel("Дата")

            # Час
            if hour_deltas:
                colors = ["#66bb6a" if d >= 0 else "#ef5350" for d in hour_deltas]
                step = max(1, len(hour_labels) // 24)
                tick_pos = list(range(0, len(hour_labels), step))
                tick_labels = [hour_labels[i] for i in tick_pos]
                axes[1].bar(range(len(hour_deltas)), hour_deltas, color=colors)
                axes[1].set_xticks(tick_pos)
                axes[1].set_xticklabels(tick_labels, rotation=45, fontsize=7)
                axes[1].set_title("Прирост по часам (7 дней)")
                axes[1].set_ylabel("Δ скачиваний")
                axes[1].axhline(0, color="#999", linewidth=0.8)

            # Итого за неделю
            week_total = sum(totals)
            best_day = max(daily, key=lambda x: x[1])
            fig.text(0.5, 0.01,
                     f"Итого за неделю: {week_total:,}  |  Лучший день: {best_day[0][5:]} (+{best_day[1]:,})",
                     ha="center", fontsize=10, color="#333")

            fig.tight_layout(rect=[0, 0.04, 1, 1])
            pdf.savefig(fig)
            plt.close(fig)

    buf.seek(0)
    return buf.read()


def main():
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat_id = os.environ["TELEGRAM_CHAT_ID"]

    cfg = load_json(CONFIG_FILE, {})
    projects = cfg.get("projects", [])
    if not projects:
        print("Нет проектов.")
        sys.exit(1)

    history = load_json(HISTORY_FILE, {})
    now_utc = datetime.now(timezone.utc)

    pdf_bytes = make_pdf(history, projects, now_utc)
    if not pdf_bytes:
        send_telegram(tg_token, tg_chat_id, "⚠️ Не удалось сгенерировать PDF отчёт.")
        return

    filename = f"weekly_report_{now_utc.strftime('%Y_%m_%d')}.pdf"
    caption = f"📋 <b>Недельный отчёт — {now_utc.strftime('%d.%m.%Y')}</b>"
    send_document(tg_token, tg_chat_id, pdf_bytes, filename, caption)
    print("PDF отчёт отправлен.")


if __name__ == "__main__":
    main()
