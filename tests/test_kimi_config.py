from app.reasoning_client import ReasoningClient


def test_explicit_kimi_mode_reuses_configuration(monkeypatch):
    monkeypatch.setenv("REASONING_USE_KIMI", "true")
    monkeypatch.setenv("MOONSHOT_API_KEY", "local-test-key")
    monkeypatch.setenv("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1")
    monkeypatch.setenv("MOONSHOT_MODEL", "kimi-k3")
    monkeypatch.setenv("REASONING_BASE_URL", "https://unrelated.invalid/v1")
    client = ReasoningClient()
    assert client.key == "local-test-key"
    assert client.base_url == "https://api.moonshot.ai/v1"
    assert client.model == "kimi-k3"
    client.validate_distinct_model()
