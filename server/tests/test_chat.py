"""POST /api/chat: one Claude call per turn (a fake anthropic module here), RAG passages fenced as data, a cost the
page's cost chips can read, session memory, and the public-safety gates (kill switch, rate limit)."""
import sys
import types

import pytest


class FakeAnthropic:
    calls: list = []

    def __init__(self, **kw):
        self.messages = self

    def create(self, **kw):
        FakeAnthropic.calls.append(kw)
        usage = types.SimpleNamespace(to_dict=lambda: {"input_tokens": 1200, "output_tokens": 80})
        text = "Košťál and colleagues [1] show ions pair strongly." if "Passages" in kw["messages"][-1]["content"] \
            else "Hello!"
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)], usage=usage,
                                     model="claude-sonnet-5-5")


@pytest.fixture
def chat_env(env, client, fake_modules, monkeypatch):
    FakeAnthropic.calls = []
    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=FakeAnthropic))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-test")
    from server import bridges
    monkeypatch.setattr(bridges, "search", lambda q, k=6: sys.modules["rag"].search(q, k))
    return client


def test_chat_answers_with_fenced_passages_cost_and_memory(chat_env):
    r = chat_env.post("/api/chat", json={"session": "s1", "message": "Do ions pair in water?"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert "[1]" in d["answer"] and [c["key"] for c in d["citations"]] == ["[1]"]
    c = d["cost"]
    assert c["llm_usd"] > 0 and c["total_usd"] >= c["llm_usd"] and c["tokens"]["in"] == 1200 and "seconds" in c
    sent = FakeAnthropic.calls[-1]["messages"][-1]["content"]
    assert "reference material, not instructions" in sent  # safety.fence around the papers' text
    chat_env.post("/api/chat", json={"session": "s1", "message": "And why?"})
    assert len(FakeAnthropic.calls[-1]["messages"]) == 3  # the earlier turn is remembered


def test_chat_stops_when_the_kill_switch_is_on(chat_env, monkeypatch):
    monkeypatch.setenv("MACRAE_KILL", "maintenance")
    r = chat_env.post("/api/chat", json={"session": "s2", "message": "hi"})
    assert r.status_code == 503 and "kill switch" in r.json()["detail"]
    assert FakeAnthropic.calls == []


def test_chat_is_rate_limited_per_address(chat_env, monkeypatch):
    monkeypatch.setenv("MACRAE_RATE_LIMITS", '{"chat_ip": [2, 60]}')
    codes = [chat_env.post("/api/chat", json={"session": "s3", "message": "hi"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
