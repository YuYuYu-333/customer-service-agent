"""
HTTP 接口层.
把 agent.run_agent 暴露成 HTTP接口
负责会话隔离、参数校验、异常转换和耗时统计.
不负责对话逻辑,也不负责上下文裁剪.
"""

import json
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi.responses import StreamingResponse
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.agent import SYSTEM_PROMPT, run_agent, run_agent_stream
from app.rag import get_index
from app.tools import get_order_status

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    """启动时检查索引:缺失就告警,存在就预热缓存"""
    try:
        get_index()
        logger.info("政策索引加载完成")
    except RuntimeError as exc:
        logger.warning("政策检索不可用: %s", exc)
    yield


app = FastAPI(title="智能客服 Agent", lifespan=lifespan)

# 会话存在进程内存里.多 worker 部署时每个进程一份,会话会串;进程重启即丢失.
# 生产环境应换成 Redis,理由:跨进程共享、可持久化、支持 TTL 自动回收.
_sessions: dict[str, list[dict]] = {}
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(session_id):
    lock = _locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[session_id] = lock
    return lock


class ChatRequest(BaseModel):
    message: str = Field(
        min_length=1,
        max_length=2000,
        description="用户提问",
    )
    session_id: str | None = Field(
        default=None,
        description="不传表示开启新对话",
    )


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    tools_used: int
    tokens: int
    latency_ms: int


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/orders/{order_id}")
async def read_order(order_id: str):
    """直接查订单,不经过模型.用来把"数据错"和"模型错"区分开"""
    order = get_order_status(order_id)
    if "error" in order:
        raise HTTPException(status_code=404, detail=order["error"])
    return order


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    session_id = req.session_id or uuid4().hex

    # 同一会话的两个协程共用同一个 history,
    # 在 await 处交替执行会把消息写乱.
    async with _lock_for(session_id):
        history = _sessions.get(session_id)
        if history is None:
            history = [{"role": "system", "content": SYSTEM_PROMPT}]
            _sessions[session_id] = history

        before = len(history)
        history.append({"role": "user", "content": req.message})

        started = time.perf_counter()
        try:
            result = await run_agent(history)
        except Exception:
            logger.exception("会话 %s 处理失败", session_id)
            del history[before:]
            raise HTTPException(status_code=500, detail="服务器暂时不可用,请稍后重试")

        latency_ms = int((time.perf_counter() - started) * 1000)

    return ChatResponse(
        reply=result.reply,
        session_id=session_id,
        tools_used=result.tool_calls_used,
        tokens=result.prompt_tokens,
        latency_ms=latency_ms,
    )


def _sse(payload):
    """把字典序列化成一条 SSE 消息.两个换行表示一条消息结束."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    session_id = req.session_id or uuid4().hex

    async def events():
        yield _sse({"type": "session", "session_id": session_id})

        async with _lock_for(session_id):
            history = _sessions.get(session_id)
            if history is None:
                history = [{"role": "system", "content": SYSTEM_PROMPT}]
                _sessions[session_id] = history

            before = len(history)
            history.append({"role": "user", "content": req.message})

            try:
                async for event in run_agent_stream(history):
                    yield _sse(event)
            except Exception:
                logger.exception("会话 %s 流式处理失败", session_id)
                del history[before:]
                yield _sse({"type": "error", "message": "服务器暂时不可用,请稍后重试"})

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
