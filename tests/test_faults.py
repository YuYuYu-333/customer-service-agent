"""故障注入:验证异常路径的兜底行为."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import app.agent as agent
import app.api as api
from app.agent import AgentResult
from app.session_store import MemoryStore


def _tool_call(name, arguments):
    """构造一个最小的 tool_call 对象,只保留 _execute 用到的字段"""
    return SimpleNamespace(
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_tool_timeout_returns_error(monkeypatch):
    """工具卡住时,超时后应该返回结构化错误,而不是一直等下去."""

    def slow(query):
        time.sleep(0.5)
        return "太慢了"

    monkeypatch.setitem(agent.FUNCTIONS, "search_policy", slow)
    monkeypatch.setattr(agent, "TOOL_TIMEOUT", 0.1)

    call = _tool_call("search_policy", '{"query":"x"}')
    content = asyncio.run(agent._run_tool(call))

    assert "超时" in json.loads(content)["error"]


@pytest.fixture(autouse=True)
def _clean_sessions():
    api._sessions.clear()
    yield
    api._sessions.clear()


def _spy_agent(monkeypatch):
    """替换 run_agent, 记录同时执行的最大数量."""
    state = {"active": 0, "peak": 0}

    async def fake_agent(messages):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.05)
        state["active"] -= 1
        return AgentResult("ok", 0, 0)

    monkeypatch.setattr(api, "run_agent", fake_agent)
    return state


def test_same_session_requests_are_serialized(monkeypatch):
    state = _spy_agent(monkeypatch)

    async def main():
        await asyncio.gather(
            api.chat(api.ChatRequest(message="a", session_id="s")),
            api.chat(api.ChatRequest(message="b", session_id="s")),
        )

    asyncio.run(main())
    assert state["peak"] == 1


def test_different_sessions_run_in_parallel(monkeypatch):
    state = _spy_agent(monkeypatch)

    async def main():
        await asyncio.gather(
            api.chat(api.ChatRequest(message="a", session_id="A")),
            api.chat(api.ChatRequest(message="b", session_id="B")),
        )

    asyncio.run(main())
    assert state["peak"] == 2


@pytest.fixture(autouse=True)
def _clean_sessions():
    api.store = MemoryStore()
    yield
    api.store = MemoryStore()
