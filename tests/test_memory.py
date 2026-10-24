"""
trim 和 pinned_questions 的行为测试.
不调用任何 API,全是纯函数,毫秒级跑完.
"""

from types import SimpleNamespace

from app.memory import pinned_questions, trim


def _fake_tool_call(name="get_order_status", args='{"order_id": "10001"}'):
    """
    造一个和 SDK 返回结构一致的 tool_call,用来喂给count_tokens.
    """
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=args))


def test_trim_returns_same_list_when_under_budget():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "您好,请问有什么可以帮您"},
    ]

    assert trim(messages, budget=10000) is messages


def test_trim_cuts_at_user_boundary():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "问" * 500},
        {"role": "assistant", "content": "答" * 500},
        {"role": "user", "content": "最后一问"},
    ]

    result = trim(messages, budget=100)

    assert len(result) == 2
    assert result[0] is messages[0]
    assert result[1]["content"] == "最后一问"


def test_trim_keeps_tool_call_pair_of_last_round():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "问" * 500},
        {"role": "assistant", "content": "答" * 500},
        {"role": "user", "content": "最后一问"},
        SimpleNamespace(
            role="assistant",
            content=None,
            tool_calls=[_fake_tool_call()],
        ),
        {"role": "tool", "tool_call_id": "c1", "content": "结果"},
    ]

    result = trim(messages, budget=150)

    assert len(result) == 4
    assert result[0] is messages[0]
    assert result[1]["content"] == "最后一问"
    assert result[2].role == "assistant"
    assert result[3]["role"] == "tool"


def test_pinned_questions_returns_earliest_questions():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "订单10001到哪了"},
        {"role": "assistant", "content": "已发货"},
        {"role": "user", "content": "手机激活了还能退吗"},
        {"role": "assistant", "content": "不能"},
        {"role": "user", "content": "我这单能退吗"},
        {"role": "assistant", "content": "看是否激活"},
        {"role": "user", "content": "第四问"},
    ]

    note = pinned_questions(messages, max_items=2)

    assert "订单10001到哪了" in note
    assert "手机激活了还能退吗" in note
    assert "第四问" not in note
