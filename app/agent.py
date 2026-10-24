"""
Agent 主循环
把用户消息,工具和模型串成一个循环:
模型决定调不调工具,代码负责执行并把结果塞回历史.
不负责 HTTP 服务,也不负责多用户会话存储
"""

import json
import logging

from app.llm import CHAT_MODEL, chat_client
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
4. 用户的问题超出客服范围时,礼貌说明无法处理。"""

# 工具预算用完时返回给模型的占位结果.
# 协议要求每个 tool_call 都必须有对应的 tool 消息,否则下一次请求直接 400.
_BUDGET_EXCEEDED = json.dumps(
    {"error": "本次对话的工具调用次数已达上限,请基于已有信息直接回答用户"},
    ensure_ascii=False,
)


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


def run_agent(messages):
    """
    跑完一次完整的 Agent 循环,返回最终回答
    :param messages: 完整对话历史,含 system 和本轮用户消息.函数会就地追加内容
    :return: 模型的最终回答文本
    """
    tool_calls_used = 0
    tools_enabled = True

    while True:
        response = chat_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=messages,
            # 预算用完后传 None, SDK 会把这个字段整个省掉,模型就调不了工具了.
            tools=TOOLS if tools_enabled else None,
            stream=False,
        )
        message = response.choices[0].message

        # 先写回历史:下一轮模型要靠它看见自己上一轮说过什么,点过哪些工具.
        messages.append(message)

        if not message.tool_calls:
            return message.content

        for tool_call in message.tool_calls:
            if tool_calls_used < MAX_TOOL_CALLS:
                tool_calls_used += 1
                content = _execute(tool_call)
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


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        question = input("用户:")
        if question.strip() == "quit":
            break

        history.append({"role": "user", "content": question})
        print("客服:", run_agent(history))
