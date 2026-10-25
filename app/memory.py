"""
对话历史的 token 计量.
被 agent.py 在每次请求前调用,用来判断是否超预算.
不负责裁剪,也不负责发起请求.
"""

import tiktoken

# DeepSeek 不用这个分词器,算出来是近似值.
# 用途是控制预算,不是精确计费.
_ENC = tiktoken.get_encoding("cl100k_base")

# 每条消息除正文外还有 role、分隔符等固定开销,OpenAI文档给的估值是 4.
_MESSAGE_OVERHEAD = 4


def _text_of(message):
    """
    取出这条消息的正文.兼容 dict 和模型返回的对象两种形态.
    """
    if isinstance(message, dict):
        return message.get("content") or ""
    return message.content or ""


def _call_parts(call):
    """工具调用的名字和参数.可能是 SDK 对象,也可能是手工拼的字典."""
    if isinstance(call, dict):
        function = call.get("function") or {}
        return function.get("name") or "", function.get("arguments") or ""
    return call.function.name, call.function.arguments or ""


def count_tokens(messages):
    """
    估算一组消息占多少 token.
    :param messages:对话历史,元素可能是 dict,也可能是模型返回的 message 对象
    :return:估算的 token 总数
    """
    total = 0

    for message in messages:
        total += _MESSAGE_OVERHEAD
        total += len(_ENC.encode(_text_of(message)))

        # 模型请求工具时 content 是空的,真正占 token 的是 tool_calls 里的工具名和参数
        tool_calls = (
            message.get("tool_calls")
            if isinstance(message, dict)
            else message.tool_calls
        )
        for call in tool_calls or []:
            name, arguments = _call_parts(call)
            total += len(_ENC.encode(name))
            total += len(_ENC.encode(arguments))

    return total


# 上下文预算.DeepSeek 实际支持的上限远大于此,
# 这里收紧的两个理由:客服会话不需要携带很久以前的内容;
# 预算小一些,裁剪逻辑在测试和演示中才容易被触发.
MAX_CONTEXT_TOKENS = 4000


def _role_of(message):
    """
    取出这条消息的角色.兼容 dict 和模型返回的对象.
    """
    if isinstance(message, dict):
        return message.get("role")
    return message.role


def trim(messages, budget=MAX_CONTEXT_TOKENS):
    """
    超预算时丢弃最早的用户轮次,返回裁剪后的新列表.
    :param messages:完整对话历史,第一条必须是 system
    :param budget:token 预算
    :return:裁剪后的列表,第一条始终是 system
    """
    if count_tokens(messages) <= budget:
        return messages

    system = messages[:1]
    rest = messages[1:]

    # 每轮对话从 role="user" 开始.只在 user 处切,
    # 否则会拆散 assistant(tool_calls) 和它对应的 tool 消息, 下一次请求直接 400.
    starts = [i for i, msg in enumerate(rest) if _role_of(msg) == "user"]
    if not starts:
        return system

    # 从最新一轮往前扩,记住最后一个还装得下的切点.
    kept = None
    for start in reversed(starts):
        candidate = system + rest[start:]
        if count_tokens(candidate) <= budget:
            kept = candidate
        else:
            break

    # 连一轮都装不下时, 宁可超预算也要保留用户当前的问题.
    return kept if kept is not None else system + rest[starts[-1]:]


def pinned_questions(messages, max_items=3):
    """
    取用户最早的几条提问,作为裁剪后仍然保留的上下文.
    :param messages:完整对话历史(不是裁剪后的)
    :param max_items:最多保留几条
    :return:一段可直接拼进 system 的文本;没有用户消息时返回空字符串
    """
    questions = [
        _text_of(message)
        for message in messages[1:]
        if _role_of(message) == "user"
    ]

    if not questions:
        return ""

    lines = "\n".join(f"- {q}" for q in questions[:max_items])
    return f"用户在本次对话中最初提出的问题:\n{lines}"
