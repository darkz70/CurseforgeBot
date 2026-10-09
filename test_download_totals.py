#!/usr/bin/env python3
"""Оффлайн-тесты download_totals.py."""
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

import download_totals as dt  # noqa: E402

SENT = []


class FakeResp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


def fake_get(url, params=None, headers=None, timeout=None):
    if url.endswith("/mods/1567155"):
        return FakeResp({"data": {"downloadCount": 2671}})
    if "modrinth" in url:
        return FakeResp({"downloads": 14})
    raise AssertionError(f"unexpected {url}")


def main():
    global SENT
    SENT = []
    with mock.patch.object(dt, "DRY_RUN", True), \
         mock.patch.object(dt.requests, "get", side_effect=fake_get), \
         mock.patch.object(dt, "send_telegram", side_effect=lambda t, c, x: SENT.append(x) or True), \
         mock.patch.dict("os.environ", {"CURSEFORGE_API_KEY": "k"}):
        code = dt.main()
    assert code == 0, f"код {code}"
    assert len(SENT) == 1
    msg = SENT[0]
    assert "Скачивания ваших модов" in msg
    assert "2 671" in msg and "14" in msg
    assert "2 685" in msg, "итог CF+Modrinth неверный"
    assert "Skin Totem" in msg
    print("OK: сводка со скачиваниями по модам и общим итогом")
    print("\nПример сообщения:\n" + "-" * 40 + "\n" + msg)


if __name__ == "__main__":
    main()
