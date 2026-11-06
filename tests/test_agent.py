"""
Agent的依据提取：用假的工具调用对象，不发网络请求.
"""

import json
from types import SimpleNamespace

from app.agent import _dedup, _execute


def _call(name, **arguments):
    return SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def test_sources_include_order_id():
    content, sources = _execute(_call("get_order_status", order_id="10001"))
    assert "error" not in json.loads(content)
    assert sources == [{"type": "order", "id": "10001"}]


def test_sources_empty_when_order_missing():
    content, sources = _execute(_call("get_order_status", order_id="99999"))
    assert "error" in json.loads(content)
    assert sources == []


def test_discount_sources_fall_back_to_args():
    """calculate_discount 的返回值里没有 order_id，只看返回值会漏."""
    content, sources = _execute(_call("calculate_discount", order_id="10001"))
    assert "saved" in json.loads(content)
    assert sources == [{"type": "order", "id": "10001"}]


def test_sources_parse_policy_sections(monkeypatch):
    text = "## 六、退货运费\n满99包邮\n\n---\n\n## 七、退款处理时效\n1个工作日"
    monkeypatch.setattr("app.agent.FUNCTIONS", {"search_policy": lambda query: text})
    _, sources = _execute(_call("search_policy", query="运费"))
    assert sources == [
        {"type": "policy", "section": "六、退货运费"},
        {"type": "policy", "section": "七、退款处理时效"},
    ]


def test_unknown_tool_has_no_sources():
    _, sources = _execute(_call("delete_all_orders"))
    assert sources == []


def test_bad_arguments_have_no_sources():
    call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="get_order_status", arguments="{not json"),
    )
    content, sources = _execute(call)
    assert "error" in json.loads(content)
    assert sources == []


def test_dedup_keeps_first_occurrence():
    sources = [
        {"type": "order", "id": "10001"},
        {"type": "policy", "section": "六、退货运费"},
        {"type": "order", "id": "10001"},
    ]
    assert _dedup(sources) == [
        {"type": "order", "id": "10001"},
        {"type": "policy", "section": "六、退货运费"},
    ]


def test_create_ticket_returns_notice():
    content, sources = _execute(_call("create_ticket", category="complaint", summary="物流太慢"))
    ticket = json.loads(content)
    assert ticket["ticket_id"].startswith("T-")
    assert ticket["status"] == "open"
    assert "不会有人工坐席" in ticket["notice"]
    assert sources == []


def test_unknown_category_falls_back_to_other():
    content, _ = _execute(_call("create_ticket", category="随便写的", summary="x"))
    assert json.loads(content)["category"] == "other"


def test_ticket_has_no_contact_field():
    content, _ = _execute(_call("create_ticket", category="order", summary="改地址"))
    assert "contact" not in json.loads(content)
