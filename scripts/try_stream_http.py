"""
通过真实 HTTP 试流式接口,并测量首字时间.
先启动服务: uvicorn app.api:app --port 8000
"""

import asyncio
import json
import time

import httpx

URL = "http://127.0.0.1:8000/chat/stream"
QUESTION = "你好，你能做什么"


async def main():
    started = time.perf_counter()
    tool_at = None
    first_at = None

    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream("POST", URL, json={"message": QUESTION}) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break

                event = json.loads(payload)
                if event["type"] == "tool" and tool_at is None:
                    tool_at = time.perf_counter() - started
                elif event["type"] == "delta":
                    if first_at is None:
                        first_at = time.perf_counter() - started

                    print(event["text"], end="", flush=True)

    total = time.perf_counter() - started
    tool_s = f"{tool_at:.2f}s" if tool_at else "无"
    print(f"\n\n工具事件: {tool_s}   首字: {first_at: .2f} s 总时长: {total: .2f} s ")


if __name__ == '__main__':
    asyncio.run(main())
