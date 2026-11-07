"""
Agent 主循环测试.
用假的 chat client 喂预设 chunk,不访问网络也不启动 HTTP.
覆盖:工具调用分片聚合、并行调用的 index 顺序、done 事件字段、依据去重.
"""

import asyncio
from types import SimpleNamespace

from app import agent
from app.agent import run_agent, run_agent_stream


def _usage(prompt_tokens):
    """只带用量、没有 choices 的收尾 chunk,和 include_usage 的真实行为一致."""
    return SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=prompt_tokens))


def _chunk(content=None, tool_calls=None, finish=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)],
        usage=None,
    )


def _piece(index, id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _call_chunk(id, name, arguments):
    """一整条工具调用塞进一个 chunk.分片的情况在用例里手工拆."""
    return _chunk(tool_calls=[_piece(0, id=id, name=name, arguments=arguments)], finish="tool_calls")


def _stream(chunks):
    async def gen():
        for chunk in chunks:
            yield chunk

    return gen()


class FakeChat:
    """chat_client 的替身:每次 create 按顺序吐一轮 chunk."""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _stream(self.rounds.pop(0))


def _install(monkeypatch, rounds):
    fake = FakeChat(rounds)
    monkeypatch.setattr(agent, "chat_client", fake)
    return fake


def _run(stream):
    async def collect():
        return [event async for event in stream]

    return asyncio.run(collect())


def _messages():
    return [{"role": "system", "content": "x"},
            {"role": "user", "content": "y"}]


def test_tool_call_fragments_are_merged(monkeypatch):
    """id / name / arguments 分三个 chunk 到,拼完必须是一条完整的调用."""
    _install(
        monkeypatch,
        [
            [
                _chunk(tool_calls=[_piece(0, id="call_1", name="get_order", arguments="")]),
                _chunk(tool_calls=[_piece(0, name="_status", arguments='{"order')]),
                _chunk(tool_calls=[_piece(0, arguments='_id": 10001}')], finish="tool_calls"),
                _usage(120),
            ],
            [
                _chunk(content="订单 10001 "),
                _chunk(content="已发货", finish="stop"),
                _usage(80),
            ],
        ],
    )

    messages = _messages()
    events = _run(run_agent_stream(messages))

    assert [e["type"] for e in events] == ["tool", "delta", "delta", "done"]
    assert events[0]["name"] == "get_order_status"

    done = events[-1]
    assert done["tool"] == 1
    assert done["token"] == 200
    assert done["sources"] == [{"type": "order", "id": "10001"}]

    assert messages[2]["tool_calls"][0]["function"] == {"name": "get_order_status",
                                                        "arguments": '{"order_id": 10001}', }
    assert messages[3]["tool_call_id"] == "call_1"
    assert messages[4]["content"] == "订单 10001 已发货"


def test_parallel_calls_keep_index_order(monkeypatch):
    """同一轮两个调用挤在一个 chunk 里,且 index 是倒序到的."""
    _install(
        monkeypatch,
        [
            [
                _chunk(
                    tool_calls=[
                        _piece(1, id="c2", name="days_since_order", arguments='{"order_id": 10002}'),
                        _piece(0, id="c1", name="get_order_status", arguments='{"order_id": 10001}'),
                    ],
                    finish="tool_calls",
                ),
                _usage(50),
            ],
            [_chunk(content="好", finish="stop"), _usage(10)],
        ],
    )

    messages = _messages()
    events = _run(run_agent_stream(messages))

    assert messages[2]["tool_calls"][0]["id"] == "c1"
    assert messages[2]["tool_calls"][1]["id"] == "c2"
    assert [m["tool_call_id"] for m in messages[3:5]] == ["c1", "c2"]
    assert events[0]["name"] == "get_order_status"
    assert events[1]["name"] == "days_since_order"
    assert events[-1]["tool"] == 2


def test_same_order_queried_twice_counts_once(monkeypatch):
    """查两次同一单,工具算两次,依据只留一条."""
    _install(
        monkeypatch,
        [
            [_call_chunk("c1", "get_order_status", '{"order_id": 10001}'), _usage(30)],
            [_call_chunk("c2", "get_order_status", '{"order_id": 10001}'), _usage(30)],
            [_chunk(content="查好了", finish="stop"), _usage(20)],
        ],
    )

    events = _run(run_agent_stream(_messages()))

    done = events[-1]
    assert done["tool"] == 2
    assert done["token"] == 80
    assert done["sources"] == [{"type": "order", "id": "10001"}]


def test_run_agent_adapts_stream_events(monkeypatch):
    """run_agent 只是把流式事件拼成 AgentResult:结论、工具次数、token、依据都要对上."""
    _install(
        monkeypatch,
        [
            [
                _call_chunk("c1", "get_order_status", '{"order_id": 10001}'),
                _usage(120),
            ],
            [
                _chunk(content="订单 10001 "),
                _chunk(content="已发货", finish="stop"),
                _usage(80),
            ],
        ],
    )

    result = asyncio.run(run_agent(_messages()))

    assert result.reply == "订单 10001 已发货"
    assert result.tool_calls_used == 1
    assert result.prompt_tokens == 200
    assert result.sources == ({"type": "order", "id": "10001"},)
