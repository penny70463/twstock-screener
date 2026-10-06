"""題材快取：命中的代號不打 API，逾時沒分到的代號不寫入。"""
import json

import src.classifier as classifier


def _use_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(classifier, "_CACHE_PATH", tmp_path / "theme_by_code.json")


def test_name_change_is_not_a_hit():
    fresh, cached = classifier._partition_cached(
        [
            {"code": "2330", "name": "台積電", "industry": "半導體"},
            {"code": "2303", "name": "聯電新名", "industry": "半導體"},
        ],
        {
            "2330": {"theme": "晶圓代工", "reason": "製造", "name": "台積電"},
            "2303": {"theme": "晶圓代工", "reason": "製造", "name": "聯電"},
        },
    )
    assert [s["code"] for s in cached] == ["2330"]
    assert [s["code"] for s in fresh] == ["2303"]


def test_skip_bucket_themes_are_not_hits():
    fresh, cached = classifier._partition_cached(
        [{"code": "1", "name": "甲", "industry": ""}],
        {"1": {"theme": "其他", "reason": "", "name": "甲"}},
    )
    assert cached == []
    assert fresh[0]["code"] == "1"


def test_full_cache_does_not_call_llm(monkeypatch, tmp_path):
    _use_cache(monkeypatch, tmp_path)
    classifier._save_cache({
        "screen-TW": {"2330": {"theme": "晶圓代工", "reason": "製造", "name": "台積電"}},
    })

    def boom(*_a, **_k):
        raise AssertionError("不該打 API")

    monkeypatch.setattr(classifier, "_client", boom)
    out = classifier.classify_themes(
        [{"code": "2330", "name": "台積電", "industry": "半導體"}],
        market="TW",
        cache_ns="screen-TW",
    )
    assert out["theme_status"] == "ok"
    assert out["themes"][0]["name"] == "晶圓代工"
    assert out["themes"][0]["stocks"] == [{"code": "2330", "name": "台積電"}]


def test_timeout_code_is_not_stored_cached_code_remains(monkeypatch, tmp_path):
    _use_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(classifier.time, "sleep", lambda *_a, **_k: None)
    classifier._save_cache({
        "breakout-tw": {"1101": {"theme": "水泥", "reason": "建材", "name": "台泥"}},
    })

    def fake_batch(_client, batch, _bi, _prompt):
        assert [s["code"] for s in batch] == ["9999"]
        return [], "timeout"

    monkeypatch.setattr(classifier, "_client", lambda: object())
    monkeypatch.setattr(classifier, "_classify_batch", fake_batch)
    out = classifier.classify_themes(
        [
            {"code": "1101", "name": "台泥", "industry": "水泥"},
            {"code": "9999", "name": "新股", "industry": "其他"},
        ],
        market="TW",
        cache_ns="breakout-tw",
    )
    assert out["theme_status"] == "timeout"
    assert out["themes"][0]["name"] == "水泥"
    saved = json.loads((tmp_path / "theme_by_code.json").read_text(encoding="utf-8"))
    assert "9999" not in saved["breakout-tw"]
    assert saved["breakout-tw"]["1101"]["theme"] == "水泥"


def test_successful_fresh_code_is_stored(monkeypatch, tmp_path):
    _use_cache(monkeypatch, tmp_path)

    def fake_batch(_client, batch, _bi, _prompt):
        return [{"name": "新題材", "reason": "原因", "codes": [s["code"] for s in batch]}], "ok"

    monkeypatch.setattr(classifier, "_client", lambda: object())
    monkeypatch.setattr(classifier, "_classify_batch", fake_batch)
    out = classifier.classify_themes(
        [{"code": "1", "name": "甲", "industry": ""}],
        market="US",
        cache_ns="screen-US",
    )
    assert out["theme_status"] == "ok"
    saved = json.loads((tmp_path / "theme_by_code.json").read_text(encoding="utf-8"))
    assert saved["screen-US"]["1"]["theme"] == "新題材"
    assert saved["screen-US"]["1"]["name"] == "甲"
