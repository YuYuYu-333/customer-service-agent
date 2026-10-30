"""
工具层的行为测试.
全部是纯函数,不调用 API,毫秒级跑完.
search_policy 需要真实的 embedding调用,不放这里,由评估集覆盖.
"""
from app.tools import (
    FUNCTIONS,
    TOOLS,
    calculate_discount,
    days_since_order,
    get_order_status,
)


def test_get_order_status_returns_order():
    order = get_order_status("10001")

    assert order["order_id"] == "10001"
    assert order["product"] == "无线蓝牙耳机"
    assert order["status"] == "已发货"


def test_get_order_status_returns_error_instead_of_raising():
    """
    订单不存在时返回 error 字典,不抛异常.
    这是刻意设计:工具抛异常会炸掉整轮对话,
    改成返回结构化错误,由 agent._execute 转交给模型.
    """
    result = get_order_status("99999")

    assert "error" in result
    assert "99999" in result["error"]


def test_calculate_discount():
    result = calculate_discount(259, 199)

    assert result["saved"] == 60.0
    assert result["discount_percent"] == 23.17


def test_calculate_discount_accepts_string_input():
    """模型传参类型不可信,字符串也要能算."""
    assert calculate_discount("259", "199") == calculate_discount(259, 199)


def test_calculate_discount_rejects_non_positive_price():
    assert "error" in calculate_discount(0, 100)
    assert "error" in calculate_discount(-1, 100)


def test_tools_and_functions_stay_in_sync():
    """
    TOOLS 里声明的名字必须和 FUNCTIONS 注册的键完全一致.
    少一个:模型能调但查不到实现,静默失败。
    多一个:实现存在但模型不知道,形同虚设。
    """
    declared = {item["function"]["name"] for item in TOOLS}
    assert set(FUNCTIONS) == declared


def test_days_since_order_returns_days():
    """返回的 days 应该是整数,且能正确解析 orders.json 里的时间格式"""
    result = days_since_order("10001")
    assert result["order_id"] == "10001"
    assert isinstance(result["days"], int)
    assert result["days"] >= 0


def test_days_since_order_handles_missing_order():
    """订单不存在时,和 get_order_status 一样返回 error,而不是抛异常"""
    result = days_since_order("99999")
    assert "error" in result
