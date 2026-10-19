"""連線在收到任何 byte 前 ReadTimeout，必須退避重試，不能拆掉排程。"""
import requests

from src.advisor import data as adv_data


class _Body:
    status_code = 200
    headers: dict = {}

    def raise_for_status(self):
        return None

    def iter_content(self, _n):
        yield b'{"ok": true}'


def test_read_timeout_before_any_byte_is_retried(monkeypatch):
    calls = {"n": 0}

    def fake_get(*_a, **_k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ReadTimeout("read timeout=30")
        return _Body()

    monkeypatch.setattr(adv_data.requests, "get", fake_get)
    monkeypatch.setattr(adv_data.time, "sleep", lambda _s: None)

    assert adv_data._get_json("https://example.test/quotes") == {"ok": True}
    assert calls["n"] == 3
