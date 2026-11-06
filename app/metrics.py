"""
Prometheus 指标.集中定义,api 和 agent 都从这里取,避免循环 import.
"""

from prometheus_client import Counter, Histogram

REQUESTS = Counter("chat_requests_total", "请求总数", ["endpoint", "status"])
DURATION = Histogram("chat_duration_seconds", "请求耗时", ["endpoint"])
TOKENS = Counter("chat_tokens_total", "消耗的 prompt token")
TOOL_CALLS = Counter("chat_tool_calls_total", "工具调用次数", ["tool"])
REJECTED = Counter("chat_rejected_total", "被拒绝的请求", ["reason"])
TICKETS = Counter("chat_tickets_total", "转人工工单数", ["category"])
