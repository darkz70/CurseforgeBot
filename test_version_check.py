#!/usr/bin/env python3
"""Оффлайн-тесты version_check.py (только CurseForge): мок запросов к CF/Telegram."""
import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

import version_check as vc  # noqa: E402

SENT = []

_ORIGINAL_VERSIONS = vc.VERSIONS_FILE.read_text() if vc.VERSIONS_FILE.exists() else "{}"


def fake_send(text):
    SENT.append(text)
    return True


CF_FILES_V1 = {
    "data": [
        {"id": 100, "displayName": "Skin Totem 1.0.0", "fileName": "skintotem-1.0.0.jar",
         "fileDate": "2026-10-01T10:00:00+00:00", "gameVersions": ["1.21.1", "Fabric"],
         "changelog": "<p>Первый релиз</p>", "downloadCount": 100},
    ]
}

CF_FILES_V2 = {
    "data": [
        {"id": 101, "displayName": "Skin Totem 1.1.0", "fileName": "skintotem-1.1.0.jar",
         "fileDate": "2026-10-09T15:00:00+00:00", "gameVersions": ["1.21.1", "Fabric", "Forge"],
         "changelog": "<p>Добавлен скин крипера</p><p>Фикс краша</p>", "downloadCount": 5},
        {"id": 100, "displayName": "Skin Totem 1.0.0", "fileName": "skintotem-1.0.0.jar",
         "fileDate": "2026-10-01T10:00:00+00:00", "gameVersions": ["1.21.1", "Fabric"],
         "changelog": "<p>Первый релиз</p>", "downloadCount": 100},
    ]
}


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def make_get(cf_payload):
    def fake_get(url, params=None, headers=None, timeout=None):
        if url.startswith(vc.CF_API_URL):
            return FakeResp(cf_payload)
        raise AssertionError(f"unexpected url {url}")
    return fake_get


def reset_state():
    if vc.VERSIONS_FILE.exists():
        vc.VERSIONS_FILE.unlink()


def run(cf_payload):
    global SENT
    SENT = []
    with mock.patch.object(vc, "DRY_RUN", False), \
         mock.patch.object(vc, "CF_API_KEY", "test-key"), \
         mock.patch.object(vc.requests, "get", side_effect=make_get(cf_payload)), \
         mock.patch.object(vc, "send_telegram", side_effect=fake_send):
        code = vc.main()
    return code


def main():
    try:
        _run_all()
    finally:
        vc.VERSIONS_FILE.write_text(_ORIGINAL_VERSIONS)  # не трогаем боевые данные


def _run_all():
    reset_state()

    # 1) первый запуск — приветствие, запоминание версий
    code = run(CF_FILES_V1)
    assert code == 0, f"первый запуск: код {code}"
    assert len(SENT) == 1, f"ожидал 1 приветствие, получил {len(SENT)}"
    assert "подключен" in SENT[0] and "CurseForge" in SENT[0]
    state = json.loads(vc.VERSIONS_FILE.read_text())
    assert state["cf:skin-totem"]["last_file_id"] == 100
    print("OK: первый запуск — приветствие, состояние сохранено")

    # 2) без изменений — тишина
    code = run(CF_FILES_V1)
    assert code == 0
    assert len(SENT) == 0, f"ожидал 0 сообщений, получил {len(SENT)}"
    print("OK: без изменений — нет сообщений")

    # 3) вышла новая версия — одно уведомление
    code = run(CF_FILES_V2)
    assert code == 0
    assert len(SENT) == 1, f"ожидал 1 уведомление, получил {len(SENT)}"
    msg = SENT[0]
    assert "Новая версия мода" in msg
    assert "Skin Totem 1.1.0" in msg
    assert "Changelog" in msg
    assert "<p>" not in msg, "HTML-теги чейнджлога не вычищены"
    assert "1.21.1" in msg
    state = json.loads(vc.VERSIONS_FILE.read_text())
    assert state["cf:skin-totem"]["last_file_id"] == 101
    print("OK: новая версия — уведомление с changelog, состояние обновлено")
    print("\nПример сообщения:\n" + "-" * 40 + "\n" + msg)

    # 4) повторный прогон после уведомления — снова тишина
    code = run(CF_FILES_V2)
    assert code == 0 and len(SENT) == 0
    print("OK: повторный прогон — дублей нет")

    print("\nВСЕ ТЕСТЫ ПРОЙДЕНЫ ✅")


if __name__ == "__main__":
    main()
