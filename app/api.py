"""
HTTP 接口层.
把 agent.run_agent 暴露成 HTTP接口
负责会话隔离、参数校验、异常转换和耗时统计.
不负责对话逻辑,也不负责上下文裁剪.
"""
import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

import logging
from app.agent import SYSTEM_PROMPT, run_agent, run_agent_stream
from app.config import REDIS_URL
from app.limits import QuotaExceeded, RateLimited, charge_tokens, check_quota, check_rate
from app.logging import request_id_var, setup_logging
from app.metrics import DURATION, REJECTED, REQUESTS, TOKENS
from app.rag import get_index
from app.session_store import SessionBusy, SessionStore, build_store
from app.tools import get_order_status

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    """启动时检查索引:缺失就告警,存在就预热缓存"""
    setup_logging()
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


def _endpoint_label(request: Request) -> str:
    """用路由模板做标签:/orders/10001 和 /orders/10002 必须算同一条指标"""
    route = request.scope.get("route")
    return getattr(route, "path", "unmatched")


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid4().hex[:12]
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        REQUESTS.labels(endpoint="unmatched", status="500").inc()
        logger.exception("request failed")
        raise
    else:
        endpoint = _endpoint_label(request)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        REQUESTS.labels(endpoint=endpoint, status=str(response.status_code)).inc()

        DURATION.labels(endpoint=endpoint).observe(latency_ms / 1000)
        response.headers["X-Request-ID"] = request_id
        logger.info("request", extra={
            "path": endpoint,
            "status": response.status_code,
            "latency_ms": latency_ms,
        })
        return response
    finally:
        request_id_var.reset(token)


async def _check_limits(request: Request) -> None:
    """请求前置检查:限流 + 配额.超了直接抛 HTTPException."""
    client_ip = request.client.host if request.client else "unknown"
    try:
        await check_rate(store, client_ip)
        await check_quota(store)
    except RateLimited:
        REJECTED.labels(reason="rate").inc()
        raise HTTPException(status_code=429, detail="请求太频繁,请稍后再试")
    except QuotaExceeded:
        REJECTED.labels(reason="quota").inc()
        raise HTTPException(status_code=503, detail="今日额度已用完,请明天再试")


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
    sources: list[dict] = Field(default_factory=list, description="本轮工具返回过的依据")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    """就绪探测:依赖不可用返回 503.进程不会被重启,只是不接流量."""
    checks = {}

    try:
        get_index()
        checks["index"] = "ok"
    except RuntimeError:
        checks["index"] = "missing"

    try:
        healthy = await asyncio.wait_for(store.ping(), timeout=1.0)
        checks["store"] = "ok" if healthy else "unreachable"
    except TimeoutError:
        checks["store"] = "timeout"

    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "not_ready", "checks": checks},
    )


@app.get("/orders/{order_id}")
async def read_order(order_id: str):
    """直接查订单,不经过模型.用来把"数据错"和"模型错"区分开"""
    order = get_order_status(order_id)
    if "error" in order:
        raise HTTPException(status_code=404, detail=order["error"])
    return order


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, _: None = Depends(_check_limits)):
    session_id = req.session_id or uuid4().hex

    # 同一会话的两个协程共用同一个 history,
    # 在 await 处交替执行会把消息写乱.
    try:
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
    except SessionBusy:
        raise HTTPException(status_code=503, detail="该会话正在处理上一条消息,请稍后重试")

    await charge_tokens(store, result.prompt_tokens)
    TOKENS.inc(result.prompt_tokens)

    return ChatResponse(
        reply=result.reply,
        session_id=session_id,
        tools_used=result.tool_calls_used,
        tokens=result.prompt_tokens,
        latency_ms=latency_ms,
        sources=list(result.sources),
    )


def _sse(payload):
    """把字典序列化成一条 SSE 消息.两个换行表示一条消息结束."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, _: None = Depends(_check_limits)):
    session_id = req.session_id or uuid4().hex

    async def events():
        yield _sse({"type": "session", "session_id": session_id})

        used_tokens = 0
        try:
            async with store.lock(session_id):
                history = await store.load(session_id)
                if history is None:
                    history = [{"role": "system", "content": SYSTEM_PROMPT}]
                history.append({"role": "user", "content": req.message})

                try:
                    async for event in run_agent_stream(history):
                        if event.get("type") == "done":
                            used_tokens = event.get("token", 0)
                        yield _sse(event)
                except Exception:
                    logger.exception("会话 %s 流式处理失败", session_id)
                    yield _sse({"type": "error", "message": "服务器暂时不可用,请稍后重试"})
                else:
                    await store.save(session_id, history)
                    await charge_tokens(store, used_tokens)

                    TOKENS.inc(used_tokens)
        except SessionBusy:
            yield _sse({"type": "error", "message": "该会话正在处理上一条消息,请稍后重试"})

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


_WEB_INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"


@app.get("/", include_in_schema=False)
async def index():
    """单页 Demo。没有前端构建步骤，直接返回这一个文件。"""
    return FileResponse(_WEB_INDEX)
