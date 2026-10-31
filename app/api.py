"""
HTTP 接口层.
把 agent.run_agent 暴露成 HTTP接口
负责会话隔离、参数校验、异常转换和耗时统计.
不负责对话逻辑,也不负责上下文裁剪.
"""

import json
import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi.responses import StreamingResponse
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.agent import SYSTEM_PROMPT, run_agent, run_agent_stream
from app.config import REDIS_URL
from app.rag import get_index
from app.session_store import SessionStore, build_store
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

    if REDIS_URL:
        logger.info("会话存储:Redis")
    else:
        logger.warning("会话存储:进程内存(未配置REDIS_URL,多 worker 会串会丢)")
    yield


app = FastAPI(title="智能电商客服 Agent", lifespan=lifespan)

# 会话存在哪由 store 决定;api 层只认接口,不关心是内存还是 Redis.
# 会话锁也归 store:锁的作用域必须和数据一致,否则多 worker 时锁形同虚设.
store: SessionStore = build_store()


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
    async with store.lock(session_id):
        history = await store.load(session_id)
        if history is None:
            history = [{"role": "system", "content": SYSTEM_PROMPT}]

        history.append({"role": "user", "content": req.message})

        started = time.perf_counter()
        try:
            result = await run_agent(history)
        except Exception:
            logger.exception("会话 %s 处理失败", session_id)
            raise HTTPException(status_code=500, detail="服务器暂时不可用,请稍后重试")

        latency_ms = int((time.perf_counter() - started) * 1000)
        await store.save(session_id, history)

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

        async with store.lock(session_id):
            history = await store.load(session_id)
            if history is None:
                history = [{"role": "system", "content": SYSTEM_PROMPT}]
            history.append({"role": "user", "content": req.message})

            try:
                async for event in run_agent_stream(history):
                    yield _sse(event)
            except Exception:
                logger.exception("会话 %s 流式处理失败", session_id)
                yield _sse({"type": "error", "message": "服务器暂时不可用,请稍后重试"})
            else:
                await store.save(session_id, history)

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
