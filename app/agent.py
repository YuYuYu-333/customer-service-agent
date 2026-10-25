"""
Agent 主循环
把用户消息,工具和模型串成一个循环:
模型决定调不调工具,代码负责执行并把结果塞回历史.
不负责 HTTP 服务,也不负责多用户会话存储
"""

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import NamedTuple

from app.llm import CHAT_MODEL, chat_client
from app.memory import pinned_questions, trim
from app.tools import FUNCTIONS, TOOLS

# 单次用户提问允许的工具调用总次数
# 实测4次足够覆盖"查订单+算差价"这类组合,
# 再多基本上都是模型在原地打转,只会重复烧 token.
MAX_TOOL_CALLS = 4

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是电商平台的客服助手.
规则:
1. 订单、物流、金额、政策条款等事实性内容,只能来自工具返回结果,不要凭印象编造。如果工具返回的材料里没有相关内容,就如实说没有,不要用常识补充。
2. 工具返回 error 时,如实告诉用户查不到,并请用户确认信息,不要自己猜。
3. 回答简洁,先给结论再给依据。
4. 用户的问题超出客服范围时,礼貌说明无法处理。
5. 只回答用户问到的问题。要补充用户没问的内容,必须先检索到依据,否则不要提。
6. 检索只覆盖政策文档的一部分。说"没有规定"时要限定为"我这次查到的内容里没有",不要说成"政策里没有";并建议用户进一步确认。"""

# 工具预算用完时返回给模型的占位结果.
# 协议要求每个 tool_call 都必须有对应的 tool 消息,否则下一次请求直接 400.
_BUDGET_EXCEEDED = json.dumps(
    {"error": "本次对话的工具调用次数已达上限,请基于已有信息直接回答用户"},
    ensure_ascii=False,
)
_TOOL_TIMEOUT = json.dumps(
    {"error": "工具执行超时,请告知用户稍后重试,不要猜测结果"},
    ensure_ascii=False,
)


class AgentResult(NamedTuple):
    """一次 Agent 循环的结果."""

    reply: str
    tool_calls_used: int
    prompt_tokens: int


def _execute(tool_call):
    """
    执行一个工具调用,任何异常都转成结构化错误交给模型.
    """
    name = tool_call.function.name
    logger.info("调用工具 %s,参数: %s", name, tool_call.function.arguments)

    try:
        args = json.loads(tool_call.function.arguments or "{}")
    except json.JSONDecodeError:
        logger.warning("工具参数不是合法 JSON: %s", tool_call.function.arguments)
        return json.dumps({"error": f"调用 {name} 的参数格式错误"}, ensure_ascii=False)

    function = FUNCTIONS.get(name)
    if function is None:
        logger.warning("模型请求了未注册的工具: %s", name)
        return json.dumps(
            {"error": f"没有名为{name}的工具,可用的是:{','.join(FUNCTIONS)}"},
            ensure_ascii=False,
        )

    try:
        result = function(**args)
    except Exception as exc:
        # 工具内部的异常不能把整通对话炸掉,转成人话交回模型.
        logger.exception("工具 %s 执行失败", name)
        return json.dumps(
            {"error": f"工具 {name} 执行失败:{exc},请告知用户稍后重试"},
            ensure_ascii=False,
        )
    return json.dumps(result, ensure_ascii=False)


TOOL_TIMEOUT = 8


async def _run_tool(tool_call):
    """执行工具并施加超时,超时转成结构化错误交给模型."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_execute, tool_call),
            timeout=TOOL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("工具 %s 超时", tool_call.function.name)
        return _TOOL_TIMEOUT


def _call_obj(slot):
    """把积累好的工具调用字典包成 _execute 认识的属性对象."""
    return SimpleNamespace(
        id=slot["id"],
        function=SimpleNamespace(name=slot["name"], arguments=slot["arguments"]),
    )


async def run_agent_stream(messages):
    """
    流式版 Agent 循环,逐条 yield 事件字典.
    事件类型: tool / delta / done
    """
    tool_calls_used = 0
    tools_enabled = True
    prompt_tokens = 0

    while True:
        view = trim(messages)

        if len(view) < len(messages):
            note = pinned_questions(messages)
            if note:
                system = messages[0]
                view = [{"role": "system", "content": system["content"] + "\n\n" + note}] + view[1:]

        stream = await chat_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=view,
            tools=TOOLS if tools_enabled else None,
            stream=True,
            stream_options={"include_usage": True},
        )

        content = []
        slots = {}
        finish_reason = None

        async for chunk in stream:
            if chunk.usage:
                prompt_tokens += chunk.usage.prompt_tokens
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            if delta.content:
                content.append(delta.content)
                yield {"type": "delta", "text": delta.content}
            for piece in delta.tool_calls or []:
                slot = slots.setdefault(piece.index, {"id": "", "name": "", "arguments": ""})
                if piece.id:
                    slot["id"] = piece.id
                if piece.function and piece.function.name:
                    slot["name"] += piece.function.name
                if piece.function and piece.function.arguments:
                    slot["arguments"] += piece.function.arguments

            if choice.finish_reason:
                finish_reason = choice.finish_reason

        if not slots:
            messages.append({"role": "assistant", "content": "".join(content)})
            yield {"type": "done", "token": prompt_tokens, "tool": tool_calls_used}
            return

        calls = [_call_obj(slots[i]) for i in sorted(slots)]
        messages.append(
            {
                "role": "assistant",
                "content": "".join(content) or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in calls
                ],
            }
        )

        for call in calls:
            yield {"type": "tool", "name": call.function.name}
            if tool_calls_used < MAX_TOOL_CALLS:
                tool_calls_used += 1
                result = await _run_tool(call)
            else:
                tools_enabled = False
                result = _BUDGET_EXCEEDED
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})


async def run_agent(messages):
    """
    跑完一次完整的 Agent 循环,返回本轮的回答和工具调用次数
    :param messages: 完整对话历史,含 system 和本轮用户消息.函数会就地追加内容
    :return: AgentResult,含 reply 和 tool_calls_used
    """
    tool_calls_used = 0
    tools_enabled = True
    prompt_tokens = 0

    while True:
        # 每轮重新裁剪:循环里刚追加的工具消息必须出现在下一次请求里.
        # messages 本身始终完整,所以 pinned 永远取得到最早的提问.
        view = trim(messages)

        # 只有真的裁过才注入,否则正常对话会被"你看不全"这类提示污染.
        if len(view) < len(messages):
            logger.info("触发裁剪: %d 条 -> %d 条", len(messages), len(view))
            note = pinned_questions(messages)
            if note:
                system = messages[0]
                view = [{"role": "system", "content": system["content"] + "\n\n" + note}] + view[1:]

        response = await chat_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=view,
            # 预算用完后传 None, SDK 会把这个字段整个省掉, 模型就调不了工具了.
            tools=TOOLS if tools_enabled else None,
            stream=False,
        )
        prompt_tokens += response.usage.prompt_tokens
        message = response.choices[0].message

        # 先写回历史:下一轮模型要靠它看见自己上一轮说过什么, 点过哪些工具.
        messages.append(message)

        if not message.tool_calls:
            return AgentResult(message.content, tool_calls_used, prompt_tokens)

        for tool_call in message.tool_calls:
            if tool_calls_used < MAX_TOOL_CALLS:
                tool_calls_used += 1
                content = await _run_tool(tool_call)
            else:
                tools_enabled = False
                content = _BUDGET_EXCEEDED

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": content,
                }
            )


async def _cli():
    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        question = input("用户:")
        if question.strip() == "quit":
            break

        history.append({"role": "user", "content": question})
        result = await run_agent(history)
        print("客服:", result.reply)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_cli())
