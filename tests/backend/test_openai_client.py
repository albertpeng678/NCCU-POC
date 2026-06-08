import backend.openai_client as oc


def test_get_client_returns_singleton(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    oc._client = None
    c1 = oc.get_client()
    c2 = oc.get_client()
    assert c1 is c2 and type(c1).__name__ == "AsyncOpenAI"


def test_get_client_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oc._client = None
    assert oc.get_client() is None
