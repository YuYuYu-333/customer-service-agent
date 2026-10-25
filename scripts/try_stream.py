"""
手动试一下流式输出.
用法: python -m scripts.try_stream
"""

import asyncio
import logging

from app.agent import SYSTEM_PROMPT, run_agent_stream

logging.basicConfig(level=logging.INFO)


async def main():
    question = input("用户: ")
    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    history.append({"role": "user", "content": question})

    async for event in run_agent_stream(history):
        if event["type"] == "delta":
            print(event["text"], end="", flush=True)
        else:
            print(f"\n-- {event}")

    print()


if __name__ == '__main__':
    asyncio.run(main())
