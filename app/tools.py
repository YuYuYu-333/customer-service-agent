"""
客服工具集
被agent.py通过 FUNCTIONS 注册表调用.
不负责对话逻辑,也不直接调用模型.
"""

import json
from datetime import datetime
from pathlib import Path

from app.rag import search

# 模块级缓存: 进程存活期间只读一次盘
# 一次客服对话会多次调用工具,每次重读文件都产生磁盘IO
# 注意多进程部署时各个进程各有一份,不是全局共享
_ORDERS = None


def load_orders():
    """
    读取data/orders.json,只在第一次调用时读盘.
    :return: 订单列表,每个元素是包含 order_id / status / price 等字段的字典
    """
    global _ORDERS

    # 用 is None 而不是 not:否则空列表也会被当成"没加载过",导致反复读盘.
    if _ORDERS is None:
        path = Path(__file__).resolve().parent.parent / "data" / "orders.json"
        with open(path, encoding="utf-8") as f:
            _ORDERS = json.load(f)

    return _ORDERS


def get_order_status(order_id):
    """
    按订单号查询订单
    :param order_id: 订单号字符串,如"10001"
    :return: 找到时返回该订单的字典;找不到时返回{"error":"..."},不抛异常
    """
    order_id = str(order_id).strip()
    for order in load_orders():
        if order["order_id"] == order_id:
            return order

    return {"error": f"未找到订单号 {order_id} ,请向用户确认订单号是否正确,不用编造订单信息"}


def days_since_order(order_id):
    """
    从下单日期到今天隔了几个自然日.不是"签收至今" —— 订单数据里没有签收时间,只有下单时间.
    :param order_id:订单号
    :return:dict,含order_id / created_at / days;订单不存在时返回 {"error": ...}
    """
    order = get_order_status(order_id)
    if "error" in order:
        return order

    created = datetime.strptime(order["created_at"], "%Y-%m-%d %H:%M")
    return {
        "order_id": order_id,
        "created_at": order["created_at"],
        "days": (datetime.now().date() - created.date()).days,
    }


def calculate_discount(order_id):
    """
    按订单号计算原价与实付价的差额及折扣率.
    价格一律取自订单数据,不接受外部传入:模型传什么价就打什么折,等于把定价权交给自然语言.
    :param order_id: 订单号
    :return: 含 saved 和 discount_percent 的字典;订单不存在或价格缺失时返回 {"error":"..."}
    """
    order = get_order_status(order_id)
    if "error" in order:
        return order

    raw_original = order.get("original_price")
    raw_current = order.get("price")
    if raw_original is None or raw_current is None:
        return {"error": "该订单缺少价格信息,请联系人工客服"}

    original_price = float(raw_original)
    current_price = float(raw_current)

    if original_price <= 0:
        return {"error": "订单原价异常,请联系人工客服"}

    saved = original_price - current_price
    discount_percent = saved / original_price * 100

    return {
        "original_price": round(original_price, 2),
        "current_price": round(current_price, 2),
        "saved": round(saved, 2),
        "discount_percent": round(discount_percent, 2),
    }


def search_policy(query):
    """
    在退换货政策文档里检索与问题最相关的段落
    :param query: 用户的问题原文
    :return: 用分割线拼接的若干段政策原文
    """
    results = search(query)
    return "\n\n---\n\n".join(text for _, text in results)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": (
                "根据订单号查询订单详情,返回订单状态,商品名称,实付金额,下单时间,物流单号."
                "当用户询问订单进度,是否发货,物流信息,订单金额时使用."
                "调用前必须确认用户提供了订单号,如果用户没有给订单号,先向用户询问,不要猜测."
                "如果返回error,说明订单不存在,需向用户确认订单号."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "订单号,纯数字字符串,如10001"
                    }
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_discount",
            "description": (
                "根据订单号查询该订单的原价和实付金额,计算优惠金额和折扣率, 返回原价, 现价, 差额和折扣率, 均保留2位小数."
                "当用户询问某笔订单省了多少钱,差价多少,打了几折时使用."
                "价格只能来自订单数据:用户口头报的价格不作数, 必须以订单号查询结果为准, 不要自己心算折扣."
                "调用前必须确认用户提供了订单号,没有就先向用户询问, 不要猜测."
                "如果返回error,说明订单不存在或缺少价格信息, 需向用户确认订单号."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description":
                            "订单号,纯数字字符串,如10001"
                    },
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_policy",
            "description": (
                "检索退换货政策文档,返回与问题最相关的政策原文段落."
                "当用户询问退货条件、退换货规则、运费承担、退款时效、某类商品能否退换等政策类问题时使用."
                "查询具体订单的状态、物流、金额时不要用本工具,改用get_order_status."
                "检索结果可能不完全相关,如果返回内容没有回答用户的问题,如实告知用户没有找到相关规定,不要编造政策."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "检索语句.尽可能包含问题涉及的商品品类,"
                            "例如蓝牙耳机、生鲜、定制商品、虚拟商品,"
                            "因为政策文档按品类分节,不带品类只会检索到通用条款."
                            "如果已经通过其他工具知道了商品名称,把它加进语句里,"
                            "例如传\"蓝牙耳机激活后能退吗\",不要传\"这个订单能退吗\"."
                            "品类未知时直接用用户原话检索,不要因此反问用户."
                        )
                    }
                },
                "required": ["query"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "days_since_order",
            "description": (
                "计算从下单日期到今天隔了几个自然日,返回订单号,下单时间和天数."
                "当用户问'我下的单过了几天了''还在七天无理由期限内吗'这类需要算天数的问题时使用."
                "注意:返回的天数是从下单日算起,不是从签收日算起."
                "调用前必须确认用户提供了订单号,没有就先向用户询问,不要猜测."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "订单号,纯数字字符串,如10001"
                    }
                },
                "required": ["order_id"],
            },
        },
    },
]

FUNCTIONS = {
    "get_order_status": get_order_status,
    "calculate_discount": calculate_discount,
    "search_policy": search_policy,
    "days_since_order": days_since_order,
}

if __name__ == '__main__':
    print(get_order_status("10001"))
    print(get_order_status("10002"))
    print(get_order_status("99999"))
    print(calculate_discount("10001"))
    print(calculate_discount("99999"))
