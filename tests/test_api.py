"""
HTTP 接口层测试.
用 TestClient 直接调用路由,不启动uvicorn,也不调用真实模型.
run_agent 被替换成假的,测试只关心路由行为:状态码、会话隔离、异常兜底.
"""

import asyncio
import json
import logging
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import api
from app.agent import AgentResult
from app.logging import JsonFormatter, request_id_var
from app.session_store import MemoryStore

client = TestClient(api.app)


@pytest.fixture(autouse=True)
def clear_sessions():
    """每个测试前清空会话字典,避免测试之间互相污染."""
    api._sessions.clear()
    yield


@pytest.fixture
def fake_agent(monkeypatch):
    """把 run_agent 换成立刻返回的假函数,不发任何网络请求."""

    async def _fake(messages):
        return AgentResult("假回答", 1, 100)

    monkeypatch.setattr(api, "run_agent", _fake)
    return _fake


@pytest.fixture(autouse=True)
def clear_sessions():
    """每个测试前换一个干净的存储,避免测试之间互相污染."""
    api.store = MemoryStore()
    yield


def test_health():
    assert client.get("/health").status_code == 200


def test_order_found():
    response = client.get("/orders/10001")

    assert response.status_code == 200
    assert response.json()["product"] == "无线蓝牙耳机"


def test_order_not_found():
    response = client.get("/orders/99999")

    assert response.status_code == 404
    assert "99999" in response.json()["detail"]


def test_chat_rejects_empty_message():
    assert client.post("/chat", json={"message": ""}).status_code == 422


def test_chat_rejects_too_long_message():
    response = client.post("/chat", json={"message": "问" * 2001})
    assert response.status_code == 422


def test_chat_returns_reply_and_session(fake_agent):
    response = client.post("/chat", json={"message": "订单10001到哪了"})

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "假回答"
    assert body["session_id"]
    assert body["tools_used"] == 1


def test_sessions_are_isolated(monkeypatch):
    seen = []

    async def _fake(messages):
        seen.append([m["content"] for m in messages if m.get("role") == "user"])
        return AgentResult("ok", 0, 10)

    monkeypatch.setattr(api, "run_agent", _fake)

    client.post("/chat", json={"message": "问题A"})
    client.post("/chat", json={"message": "问题B"})

    assert seen[0] == ["问题A"]
    assert seen[1] == ["问题B"]


def test_chat_returns_500_when_agent_fails(monkeypatch):
    async def _boom(messages):
        raise RuntimeError("模型挂了")

    monkeypatch.setattr(api, "run_agent", _boom)

    response = client.post("/chat", json={"message": "随便问问"})

    assert response.status_code == 500


def test_chat_stream_emits_events(monkeypatch):
    async def fake_stream(messages):
        yield {"type": "delta", "text": "你"}
        yield {"type": "delta", "text": "好"}
        yield {"type": "done", "token": 10, "tool": 0}

    monkeypatch.setattr(api, "run_agent_stream", fake_stream)

    with client.stream("POST", "/chat/stream", json={"message": "hi"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    assert '"text": "你"' in body
    assert "[DONE]" in body


def test_chat_returns_sources(monkeypatch):
    async def agent_with_sources(history):
        return AgentResult("已发货", 1, 100, ({"type": "order", "id": "10001"},))

    monkeypatch.setattr(api, "run_agent", agent_with_sources)

    response = client.post("/chat", json={"message": "订单10001到哪了"})

    assert response.status_code == 200
    assert response.json()["sources"] == [{"type": "order", "id": "10001"}]


def test_stream_done_event_carries_sources(monkeypatch):
    async def fake_stream(messages):
        yield {"type": "delta", "text": "你好"}
        yield {"type": "done", "token": 10, "tool": 0, "sources": [{"type": "policy", "section": "六、退货运费"}]}

    monkeypatch.setattr(api, "run_agent_stream", fake_stream)

    with client.stream("POST", "/chat/stream", json={"message": "hi"}) as resp:
        body = "".join(resp.iter_text())

    assert "六、退货运费" in body


def test_chat_returns_503_when_session_busy(monkeypatch):
    from app.session_store import SessionBusy

    def _busy(session_id):
        raise SessionBusy("occupied")

    monkeypatch.setattr(api.store, "lock", _busy)

    response = client.post("/chat", json={"message": "hi"})

    assert response.status_code == 503


def test_rate_limit_returns_429(fake_agent):
    codes = [client.post("/chat", json={"message": "hi"}).status_code for _ in range(11)]

    assert codes[:10] == [200] * 10
    assert codes[10] == 429


def test_quota_exceeded_returns_503(monkeypatch,
                                    fake_agent):
    monkeypatch.setattr("app.limits.DAILY_TOKEN_LIMIT", 0)

    response = client.post("/chat", json={"message": "hi"})

    assert response.status_code == 503


def test_tokens_are_charged_after_success(fake_agent):
    client.post("/chat", json={"message": "hi"})

    used = asyncio.run(api.store.get_int(f"quota:{date.today().isoformat()}"))

    assert used == 100


def test_metrics_endpoint():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "chat_requests_total" in response.text


def test_health_has_request_id():
    response = client.get("/health")
    assert response.headers["x-request-id"]


def test_log_line_is_json():
    token = request_id_var.set("abc123")
    try:
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
        data = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert data["message"] == "hello"
    assert data["request_id"] == "abc123"
