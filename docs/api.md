# 接口

README 里只留端点清单和状态码契约。请求/响应字段、SSE 事件格式、日志与指标定义都在这里。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 单页 Demo。直接返回 `web/index.html`，没有前端构建步骤 |
| POST | `/chat` | 同步对话，返回完整回复 |
| POST | `/chat/stream` | 流式对话，SSE |
| GET | `/health` | 存活探测，**不检查依赖** |
| GET | `/ready` | 就绪探测，查索引与存储，依赖不可用时返回 503 |
| GET | `/orders/{order_id}` | 直接查订单，不经过模型 |
| GET | `/metrics` | Prometheus 指标：请求数、耗时、token、工具调用、被拒次数 |

线上实例的交互式文档在 [`/docs`](https://customer-service-agent-9o0y.onrender.com/docs)，直接点开就能调。

## POST /chat

```json
{ "message": "订单 10001 到哪了", "session_id": null }
```

```json
{
  "reply": "您的订单已于 10 月 12 日签收。",
  "session_id": "3f9c8a...",
  "tools_used": 1,
  "tokens": 1873,
  "latency_ms": 1420,
  "sources": [
    { "type": "order", "id": "10001" }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `session_id` | 不传则新建一个；后续请求带上它即可延续同一段对话 |
| `tools_used` | 本轮调用了几次工具，上限 4 次 |
| `tokens` | 本轮所有模型调用的 `prompt_tokens` 累计（不含输出） |
| `latency_ms` | 只统计 `run_agent` 的耗时，不含网络传输 |
| `sources` | 本轮工具返回过的依据，去重后按首次出现排序。订单是 `{"type":"order","id":...}`，政策是 `{"type":"policy","section":...}` |

**并发语义**：同一个 `session_id` 的请求串行执行，不同 `session_id` 之间并行。原因是同一会话的两段历史是同一个列表，在 `await` 处交错写入会串消息。

**这个串行在多个 worker 之间同样成立**——锁在 Redis 里，不在进程内存里。抢不到锁最多等 30 秒，超时抛 `SessionBusy`，返回 503。

**状态码：**

| 码 | 什么时候出现 |
|---|---|
| `200` | 正常返回 |
| `429` | 该来源 IP 超过每分钟请求上限 |
| `503` | 今日 token 配额用完，或同一会话的上一条还在处理中 |
| `500` | 模型调用异常或存储不可用 |

## POST /chat/stream

返回 `text/event-stream`，每条消息为一行 `data: <json>`，以两个换行结束；最后一条固定是 `data: [DONE]`。

```text
data: {"type": "session", "session_id": "3f9c8a..."}
data: {"type": "tool", "name": "search_policy"}
data: {"type": "delta", "text": "退货运费"}
data: {"type": "delta", "text": "由商家承担。"}
data: {"type": "done", "token": 1873, "tool": 1}
data: [DONE]
```

| type | 字段 | 说明 |
|---|---|---|
| `session` | `session_id` | 服务端确认会话，**必定是第一条** |
| `tool` | `name` | 模型决定调用某个工具。此时正文还没有开始输出 |
| `delta` | `text` | 增量文本，客户端直接拼接即可 |
| `done` | `token` / `tool` / `sources` | 本轮结束，附带 token、工具调用统计和依据列表 |
| `error` | `message` | 流中途出错。此时 HTTP 状态码已经是 200，**错误只能以事件形式下发** |

```powershell
curl.exe -N -X POST http://127.0.0.1:8000/chat/stream `
  -H "Content-Type: application/json" `
  -d '{\"message\":\"退货运费谁出\"}'
```

**注意 Windows PowerShell 里的 `curl` 是 `Invoke-WebRequest` 的别名**，`-N` 之类的参数会被吞掉，所以要显式写 `curl.exe`。

服务端固定返回两个头：

```python
headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
```

`X-Accel-Buffering: no` 是给 Nginx 看的。少了它，Nginx 会把整个响应缓冲完再一次性下发，**流式在本地测试正常、一上反向代理就退化成整段返回**。

## 可观测性

**每个响应都带 `X-Request-ID`。** 请求头里自己带了就沿用，没带就生成一个 12 位十六进制串。它同时出现在响应头和整条调用链的日志里——`agent.py` 里那些 `logger.info` 一行都不用改，编号靠 `ContextVar` 自动带过去。

**日志是一行一条 JSON**，写到 stderr：

```json
{"ts": "2026-11-06T17:27:58", "level": "INFO", "logger": "app.api", "request_id": "3f9c8a7d21b4", "message": "request", "path": "/chat", "status": 200, "latency_ms": 1420.3}
```

纯文本日志要靠时间戳和会话 id 肉眼对齐，JSON 日志可以直接 `jq`，也可以灌进任何采集系统。

**`/metrics` 暴露的指标：**

| 指标 | 类型 | 标签 |
|---|---|---|
| `chat_requests_total` | Counter | `endpoint`、`status` |
| `chat_duration_seconds` | Histogram | `endpoint` |
| `chat_tokens_total` | Counter | — |
| `chat_tool_calls_total` | Counter | `tool` |
| `chat_rejected_total` | Counter | `reason`（`rate` / `quota`） |

`endpoint` 用的是**路由模板**（`/orders/{order_id}`）而不是真实路径。直接拿 URL 做标签，每来一个新订单号就多一条时间序列，几万个订单足以把 Prometheus 的内存吃光——这是线上最常见的监控事故之一。`tool` 标签同理：模型吐出的未注册工具名一律记成 `unknown`。

**`chat_duration_seconds` 对流式接口只计到「响应头就绪」**，不含后面逐字输出的时间。它量的是首帧耗时，不是端到端时长。
