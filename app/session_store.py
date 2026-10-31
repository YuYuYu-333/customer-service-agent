"""
会话存储接口.
api 层只依赖这个接口,换实现(Memory/Redis)时 api 层不用改.
"""

import asyncio
import copy
import json
import time
from contextlib import asynccontextmanager
from typing import Protocol
from uuid import uuid4

import redis.asyncio as redis

from app.config import REDIS_URL


class SessionStore(Protocol):
    async def load(self, session_id: str) -> list[dict] | None:
        """取出这个会话的消息列表;没有就返回None"""

    async def save(self, session_id: str, messages: list[dict]) -> None:
        """整份覆盖写入"""

    async def delete(self, session_id: str) -> None:
        """删除会话"""

    def lock(self, session_id: str):
        """同一会话的临界区.跨进程也必须互斥."""


class MemoryStore:
    """进程内存实现.单进程够用,多 worker 各存一份.也是 Redis 版的行为参照."""

    def __init__(self):
        self._data: dict[str, list[dict]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def load(self, session_id: str) -> list[dict] | None:
        messages = self._data.get(session_id)
        return copy.deepcopy(messages) if messages is not None else None

    async def save(self, session_id, messages: list[dict]) -> None:
        self._data[session_id] = copy.deepcopy(messages)

    async def delete(self, session_id: str) -> None:
        self._data.pop(session_id, None)

    def lock(self, session_id: str):
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock


# 只删自己持有的锁:get 和 del 必须是原子操作,否则可能删掉别人的
_UNLOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


class RedisStore:
    """Redis 实现.跨进程共享,多个 worker 看到同一份会话."""

    def __init__(self, url: str, ttl_seconds: int = 1800):
        # 本机是 Redis 5.0,不支持 RESP3 握手,显式用旧协议
        self._client = redis.from_url(url, decode_responses=True, protocol=2)
        self._ttl = ttl_seconds

    async def load(self, session_id: str) -> list[dict] | None:
        raw = await self._client.get(f"session:{session_id}")
        return json.loads(raw) if raw is not None else None

    async def save(self, session_id: str, messages: list[dict]) -> None:
        await self._client.set(
            f"session:{session_id}",
            json.dumps(messages, ensure_ascii=False),
            ex=self._ttl,
        )

    async def delete(self, session_id: str) -> None:
        await self._client.delete(f"session:{session_id}")


    @asynccontextmanager
    async def lock(self, session_id: str, ttl: int = 120, wait: float = 30):
        """抢到锁才进临界区;抢不到就轮询等待,超时抛 TimeoutError."""
        key = f"lock:{session_id}"
        token = uuid4().hex
        deadline = time.monotonic() + wait

        while not await self._client.set(key, token, nx=True, ex=ttl):
            if time.monotonic() > deadline:
                raise TimeoutError(f"会话 {session_id} 正忙")
            await asyncio.sleep(0.05)

        try:
            yield
        finally:
            await self._client.eval(_UNLOCK_LUA, 1, key, token)


def build_store() -> SessionStore:
    """按配置选实现:配了 REDIS_URL 就用 Redis,没配退回进程内存."""
    if REDIS_URL:
        return RedisStore(REDIS_URL)
    return MemoryStore()
