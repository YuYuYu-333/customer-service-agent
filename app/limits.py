"""
限流与配额.策略在这里,存储原语在 store 里.
阈值从 config 读,默认值写在 config 里,本地不用配.
"""

from datetime import date

from app.config import DAILY_TOKEN_LIMIT, RATE_LIMIT_PER_MINUTE
from app.session_store import SessionStore

RATE_WINDOW = 60  # 限流窗口,秒
QUOTA_TTL = 48 * 3600  # 配额键的存活时间,只为回收旧键


class RateLimited(Exception):
    """超过每分钟请求上限."""


class QuotaExceeded(Exception):
    """超过每日 token 配额."""


async def check_rate(store: SessionStore, client_ip: str) -> None:
    """按来源 IP 计数,超过阈值抛 RateLimited."""
    used = await store.incr(f"rate:{client_ip}", RATE_WINDOW)
    if used > RATE_LIMIT_PER_MINUTE:
        raise RateLimited(f"{client_ip} 超过 {RATE_LIMIT_PER_MINUTE} 次/分钟")


async def check_quota(store: SessionStore) -> None:
    """今天累计用量已达上限就抛 QuotaExceeded."""
    used = await store.get_int(_quota_key())
    if used >= DAILY_TOKEN_LIMIT:
        raise QuotaExceeded(f"今日 token 已用 {used}/{DAILY_TOKEN_LIMIT}")


async def charge_tokens(store: SessionStore, tokens: int) -> None:
    """把这一轮的 prompt token 计入今天."""
    if tokens > 0:
        await store.incr(_quota_key(), QUOTA_TTL, amount=tokens)


def _quota_key() -> str:
    """按日期分键.跨天自动换新键,旧键由 TTL 回收."""
    return f"quota:{date.today().isoformat()}"
