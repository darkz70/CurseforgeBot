#!/usr/bin/env python3
"""Оффлайн-тесты category_rank.py."""
import importlib
import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

import category_rank as cr  # noqa: E402

SENT = []
# категория из 120 модов, наш мод (cf_id 1567155) на 15-м месте по скачиваниям
MODS = [{"id": 9000 + i, "downloadCount": 100000 - i * 500} for i in range(120)]
MODS[14] = {"id": 1567155, "downloadCount": 2416}


def fake_send(token, chat, text):
    SENT.append(text)
    return True


def fake_api_get(url, params=None):
    if url.endswith("/mods/1567155"):
        return {"data": {"downloadCount": 2416}}
    index = (params or {}).get("index", 0)
    page = MODS[index:index + cr.PAGE_SIZE]
    return {"data": page, "pagination": {"index": index, "pageSize": cr.PAGE_SIZE,
                                          "resultCount": len(page), "totalCount": len(MODS)}}


def run(state):
    global SENT
    SENT = []
    cr.RANKS_FILE.write_text(json.dumps(state))
    with mock.patch.object(cr, "CF_API_KEY", "test-key"), \
         mock.patch.object(cr, "api_get", side_effect=fake_api_get), \
         mock.patch.object(cr, "send_telegram", side_effect=fake_send):
        import os
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"}):
            code = cr.main()
    return code, json.loads(cr.RANKS_FILE.read_text())


_ORIGINAL_RANKS = cr.RANKS_FILE.read_text() if cr.RANKS_FILE.exists() else "{}"


def main():
    try:
        _run_all()
    finally:
        cr.RANKS_FILE.write_text(_ORIGINAL_RANKS)  # не трогаем боевые данные


def _run_all():
    # 1) состояние из архива (позиция 16) → реально 15 → уведомление о подъёме
    old = {"skin-totem": {"rank": None, "checked_at": "x", "downloads": 1000,
                          "my_downloads": 2416, "position": 16, "total": 16}}
    code, state = run(old)
    assert code == 0
    assert len(SENT) == 1 and "поднялся" in SENT[0] and "#16" in SENT[0] and "#15" in SENT[1 - 1]
    assert state["skin-totem"]["position"] == 15 and state["skin-totem"]["total"] == 120
    print("OK: смена позиции → уведомление «поднялся»")

    # 2) позиция не изменилась → тишина
    code, state = run(state)
    assert code == 0 and len(SENT) == 0
    print("OK: позиция та же → нет сообщений")

    # 3) чистое состояние (новый проект) → «рейтинг зафиксирован»
    code, state = run({})
    assert code == 0 and len(SENT) == 1 and "зафиксирован" in SENT[0] and "#15" in SENT[0]
    print("OK: первый прогон → базовое уведомление с местом")

    # 4) нет ключа → тихий пропуск
    SENT.clear()
    with mock.patch.object(cr, "CF_API_KEY", ""):
        code = cr.main()
    assert code == 0 and len(SENT) == 0
    print("OK: без CURSEFORGE_API_KEY → тихий пропуск")

    print("\nВСЕ ТЕСТЫ РЕЙТИНГА ПРОЙДЕНЫ ✅")
    print("\nПример сообщения:\n" + "-" * 40)
    _, _ = run({"skin-totem": {"rank": None, "checked_at": "x", "downloads": 1000,
                               "my_downloads": 2416, "position": 16, "total": 16}})
    print(SENT[0])


if __name__ == "__main__":
    main()
