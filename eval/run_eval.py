"""
评估集运行器.
对每道题跑一次全新的 Agent 会话,记录回答、耗时和 token。
不负责生成题目,也不负责判定对错。
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from app.agent import SYSTEM_PROMPT, run_agent

_EVAL_DIR = Path(__file__).resolve().parent
_QUESTIONS = _EVAL_DIR / "questions.jsonl"
_RAW = _EVAL_DIR / "raw.json"

# 评估时把日志压到 WARNING,否则 20 道题的 INFO 日志会把结果刷没.
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


def load_questions():
    """
    逐行读取 jsonl, 跳过空行.
    :return: 题目字典的列表
    """
    with open(_QUESTIONS, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


async def run_one(question):
    """ 用全新会话跑一道题,返回回答、工具次数、token 和耗时."""
    history = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    started = time.perf_counter()
    result = await run_agent(history)
    latency_ms = int((time.perf_counter() - started) * 1000)

    return {
        "reply": result.reply,
        "tools_used": result.tool_calls_used,
        "tokens": result.prompt_tokens,
        "latency_ms": latency_ms
    }


async def main():
    questions = load_questions()
    results = []

    for item in questions:
        try:
            outcome = await run_one(item["question"])
        except Exception as exc:
            # 一道题失败不能中断整轮评估,否则前面的结果全白跑.
            logger.exception("%s 执行失败", item["id"])
            outcome = {
                "reply": f"EXCEPTION: {exc}",
                "tools_used": 0,
                "tokens": 0,
                "latency_ms": 0
            }

        results.append({**item, **outcome})
        print(f"[{item['id']}] {outcome['latency_ms']}ms {outcome['tools_used']}次工具")

    print(f"\n共 {len(results)} 题执行完成")

    with open(_RAW, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"原始结果已写入 {_RAW.name}")


if __name__ == '__main__':
    asyncio.run(main())
