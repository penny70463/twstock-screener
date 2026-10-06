"""題材分類的 LLM 端點：THEME_LLM=gemini 時走 Gemini，不碰 NVIDIA。"""
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
