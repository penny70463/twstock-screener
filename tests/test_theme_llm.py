"""題材分類的 LLM 端點：THEME_LLM=gemini 時走 Gemini，不碰 NVIDIA。"""
import json

import src.classifier as classifier


def test_gemini_client_uses_gemini_endpoint(monkeypatch):
    monkeypatch.setattr(classifier.settings, "theme_llm", "gemini")
    monkeypatch.setattr(classifier.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(classifier.settings, "gemini_base_url", "https://generativelanguage.googleapis.com/v1beta/openai/")
    created = {}

    class Fake:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(classifier, "OpenAI", Fake)
    classifier._client()
    assert created["api_key"] == "test-key"
    assert created["base_url"].startswith("https://generativelanguage.googleapis.com/")
    assert created["timeout"] == 60.0
    assert created["max_retries"] == 0


def test_pick_agy_model_uses_fallback_then_family():
    live = ["gemini-3.8-flash-low", "claude-opus-5-5-low", "claude-sonnet-5-5-low"]
    assert classifier.pick_agy_model(
        "gemini-3.1-pro-low",
        ["claude-opus-5-5-low", "claude-sonnet-5-5-low"],
        live,
    ) == "claude-opus-5-5-low"
    # 備援名稱也整批改掉時，依家族前綴對上新 id，並先拿 low。
    assert classifier.pick_agy_model(
        "claude-opus-5-5-low",
        ["claude-sonnet-5-5-low"],
        ["claude-opus-5-6-high", "claude-opus-5-6-low", "gemini-3.9-flash-high"],
    ) == "claude-opus-5-6-low"


def test_antigravity_unknown_model_switches_once(monkeypatch, tmp_path):
    monkeypatch.setattr(classifier, "_agy_model_override", None)
    monkeypatch.setattr(classifier.settings, "theme_llm", "antigravity")
    monkeypatch.setattr(classifier.settings, "antigravity_bin", "/tmp/agy")
    monkeypatch.setattr(classifier.settings, "antigravity_model", "gemini-3.1-pro-low")
    monkeypatch.setattr(
        classifier.settings,
        "antigravity_model_fallbacks",
        ["claude-opus-5-5-low"],
    )
    monkeypatch.setattr(classifier, "_AGY_WORKDIR", tmp_path)
    calls: list[list[str]] = []

    class Proc:
        def __init__(self, code: int, stdout: str, stderr: str = ""):
            self.returncode = code
            self.stdout = stdout
            self.stderr = stderr

    ok = json.dumps({
        "status": "SUCCESS",
        "structured_output": {"themes": [{"name": "矽晶圓", "reason": "晶圓", "codes": ["3532"]}]},
        "usage": {"input_tokens": 10, "output_tokens": 20},
    })

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[1] == "models":
            return Proc(0, "Fetching available models...\nclaude-opus-5-5-low\tClaude Opus 5.5 (Low)\n")
        model = cmd[cmd.index("--model") + 1]
        if model == "gemini-3.1-pro-low":
            return Proc(1, "", "error: invalid model selection: model gemini-3.1-pro-low is not recognized")
        return Proc(0, ok)

    monkeypatch.setattr(classifier.subprocess, "run", fake_run)
    messages = [{"role": "user", "content": "[]"}]
    first = json.loads(classifier._call(None, messages, json_mode=True))
    second = json.loads(classifier._call(None, messages, json_mode=True))
    assert first["themes"][0]["codes"] == ["3532"]
    assert second["themes"][0]["codes"] == ["3532"]
    model_calls = [cmd[cmd.index("--model") + 1] for cmd in calls if "--model" in cmd]
    assert model_calls == ["gemini-3.1-pro-low", "claude-opus-5-5-low", "claude-opus-5-5-low"]
    assert sum(1 for cmd in calls if cmd[1] == "models") == 1


def test_antigravity_call_reads_structured_output(monkeypatch, tmp_path):
    monkeypatch.setattr(classifier, "_agy_model_override", None)
    monkeypatch.setattr(classifier.settings, "theme_llm", "antigravity")
    monkeypatch.setattr(classifier.settings, "antigravity_bin", "/tmp/agy")
    monkeypatch.setattr(classifier.settings, "antigravity_model", "claude-opus-5-5-low")
    monkeypatch.setattr(classifier, "_AGY_WORKDIR", tmp_path)
    seen = {}

    class Proc:
        returncode = 0
        stdout = json.dumps({
            "status": "SUCCESS",
            "structured_output": {"themes": [{"name": "矽晶圓", "reason": "晶圓", "codes": ["3532"]}]},
            "usage": {"input_tokens": 10, "output_tokens": 20},
        })
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = kwargs.get("cwd")
        return Proc()

    monkeypatch.setattr(classifier.subprocess, "run", fake_run)
    text = classifier._call(None, [{"role": "user", "content": "[]"}], json_mode=True)
    data = json.loads(text)
    assert data["themes"][0]["codes"] == ["3532"]
    assert seen["cwd"] == tmp_path
    assert seen["cmd"][0] == "/tmp/agy"
    assert "claude-opus-5-5-low" in seen["cmd"]
    assert "--sandbox" in seen["cmd"]


def test_call_uses_theme_model(monkeypatch):
    monkeypatch.setattr(classifier.settings, "theme_llm", "gemini")
    monkeypatch.setattr(classifier.settings, "gemini_model", "gemini-3.5-flash")
    seen = {}

    class FakeCompletions:
        def create(self, **kwargs):
            seen.update(kwargs)
            return []

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    classifier._call(FakeClient(), [{"role": "user", "content": "[]"}], json_mode=True)
    assert seen["model"] == "gemini-3.5-flash"
    assert seen["stream"] is True
