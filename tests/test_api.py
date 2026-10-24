"""
HTTP 接口层测试.
用 TestClient 直接调用路由,不启动uvicorn,也不调用真实模型.
run_agent 被替换成假的,测试只关心路由行为:状态码、会话隔离、异常兜底.
"""

import pytest
from fastapi.testclient import TestClient

from app import api
from app.agent import AgentResult

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
