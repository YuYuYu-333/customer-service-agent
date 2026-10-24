"""
模型客户端
集中创建两个client:
- chat_client:DeepSeek,负责对话和工具调用
- embed_client:硅基流动,负责把文本变成向量
其他模块从这里取 client,不自己创建,避免 key 和 base_url 散落在各处
"""

from openai import AsyncOpenAI, OpenAI

from app.config import DEEPSEEK_API_KEY, SILICONFLOW_API_KEY

CHAT_MODEL = "deepseek-flash"
EMBED_MODEL = "BAAI/bge-m3"

# chat 是秒级的长调用,是并发场景下主要的等待来源,必须走原生异步.
chat_client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

# 向量化几十毫秒,且只在检索时调用.
# 保持同步,由 agent.py 用 asyncio.to_thread 卸载,避免把 rag/tools 三层全改成 async.
# build_index 是离线脚本,也用它.
embed_client = OpenAI(
    api_key=SILICONFLOW_API_KEY,
    base_url="https://api.siliconflow.cn/v1"
)
