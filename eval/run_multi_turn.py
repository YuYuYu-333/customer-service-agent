"""
多轮评估运行器.
和 run_eval.py 唯一的区别: 那边每道题新建一份
history, 这边一整段会话共用一份.
"""

import asyncio
import json
import logging
from pathlib import Path

from app.agent import SYSTEM_PROMPT, run_agent
from app.memory import MAX_CONTEXT_TOKENS, count_tokens

_EVAL_DIR = Path(__file__).resolve().parent
_SESSIONS = _EVAL_DIR / "multi_turn.jsonl"
_RAW = _EVAL_DIR / "multi_turn_raw.json"


def load_sessions():
    """逐行读 jsonl, 跳过空行."""
    with open(_SESSIONS, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def hit(reply, keywords):
    """全部关键词都出现才算通过."""
    return all(word in reply for word in keywords)


async def run_session(session):
    """
    跑完一段多轮会话.
    :param session: 会话字典, 含 id / topic / turns
    :return: 每轮的提问、回答、命中情况, 顺序与 turns 一致
    """
    # 整段共用这一份 history
    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    turns = []

    for index, turn in enumerate(session["turns"], 1):
        history.append({"role": "user", "content": turn["question"]})
        # run_agent 会把 assistant 消息和 tool 消息就地追加进 history,
        # 所以下一轮直接复用同一个列表,上下文自然接上
        result = await run_agent(history)
        turns.append({
            "question": turn["question"],
            "expect_keywords": turn["expect_keywords"],
            "reply": result.reply,
            "passed": hit(result.reply, turn["expect_keywords"]),
            "tools_used": result.tool_calls_used,
            "tokens": result.prompt_tokens,
        })

        # 打印的是「这一轮结束时历史有多大」.超过预算, 下一轮就会触发裁剪.
        used = count_tokens(history)
        flag = "  ← 超预算,下一轮会裁剪" if used > MAX_CONTEXT_TOKENS else ""
        print(f"    第 {index} 轮 上下文 {used} token {flag} ")

    return {"id": session["id"], "topic": session["topic"], "turns": turns}


async def main():
    sessions = load_sessions()
    results = []

    for session in sessions:
        try:
            outcome = await run_session(session)
        except Exception as exc:
            logging.exception("%s 执行失败", session["id"])
            outcome = {"id": session["id"], "topic": session["topic"], "turns": [],
                       "error":
                           f"{type(exc).__name__}: {exc}"}
        results.append(outcome)

        marks = "".join("√" if t["passed"] else "×" for t in outcome["turns"])
        print(f"[{outcome['id']}] {marks}")

    total = sum(len(r["turns"]) for r in results)
    passed = sum(t["passed"] for r in results for t
                 in r["turns"])
    print(f"\n共 {len(results)} 段 / {total} 轮, 通过 {passed} ")

    with open(_RAW, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"原始结果已写入 {_RAW.name}")


if __name__ == '__main__':
    asyncio.run(main())
