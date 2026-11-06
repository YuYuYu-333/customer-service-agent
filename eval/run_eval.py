"""
评估集运行器.
对每道题跑一次全新的 Agent 会话,记录回答、耗时和 token。
不负责生成题目,也不负责判定对错。
"""

import asyncio
import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

from app.agent import SYSTEM_PROMPT, run_agent
from app.llm import CHAT_MODEL

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


def _git_commit():
    """当前提交号。取不到就写 unknown——评估不能因为 git 不可用而失败。"""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return done.stdout.strip() or "unknown"


def _previous_results():
    """上一轮的 raw.json,按 id 建索引。文件不存在或坏了都当没有。"""
    if not _RAW.exists():
        return {}
    try:
        return {item["id"]: item for item in json.loads(_RAW.read_text(encoding="utf-8"))}
    except (json.JSONDecodeError, KeyError):
        logger.warning("旧的 raw.json 无法解析, 这一轮不复用判定缓存")
        return {}


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
    previous = _previous_results()
    commit = _git_commit()
    run_at = datetime.now().astimezone().isoformat(timespec="seconds")
    results = []

    for item in questions:
        try:
            outcome = await run_one(item["question"])
            outcome["error"] = None
        except Exception as exc:
            # 一道题失败不能中断整轮评估,否则前面的结果全白跑.
            logger.exception("%s 执行失败", item["id"])
            outcome = {
                "reply": "",
                "tools_used": 0,
                "tokens": 0,
                "latency_ms": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }
        merged = {
            **item,
            **outcome,
            "model": CHAT_MODEL,
            "commit": commit,
            "run_at": run_at,
        }

        old = previous.get(item["id"], {})
        # 缓存键是「id + 回答文本」。回答变了就必须重判, 否则会拿上一轮的判定给这一轮的新回答背书。
        if old.get("reply") == outcome["reply"] and "verdict" in old:
            for key in ("verdict", "method"):
                merged[key] = old[key]

        results.append(merged)
        mark = " 失败" if outcome["error"] else ""
        print(f"[{item['id']}] {outcome['latency_ms']}ms {outcome['tools_used']}次工具{mark}")

    print(f"\n共 {len(results)} 题执行完成")

    with open(_RAW, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"原始结果已写入 {_RAW.name}")


if __name__ == '__main__':
    asyncio.run(main())
