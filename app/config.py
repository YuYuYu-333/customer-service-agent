"""
配置读取
所有环境变量都从这里取,其他模块不直接访问os.environ
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# .env 在根目录,本文件在 app/ 下,所以往上两层
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY")
# 可选:没配就退回进程内存.单机够用,多 worker 会串,所以启动时要告警.
REDIS_URL = os.getenv("REDIS_URL")
# 可选:限流与配额阈值.不配就用默认值,本地开发不用管.
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE") or 10)
DAILY_TOKEN_LIMIT = int(os.getenv("DAILY_TOKEN_LIMIT") or 1_000_000)


def _require(name, value):
    """
    必需的配置项缺失时立刻报错,而不是等到运行时收到401
    """
    if not value:
        raise RuntimeError(
            f"缺少环境变量{name}.请复制.env.example 为 .env 并填写"
        )


_require("DEEPSEEK_API_KEY", DEEPSEEK_API_KEY)
_require("SILICONFLOW_API_KEY", SILICONFLOW_API_KEY)
