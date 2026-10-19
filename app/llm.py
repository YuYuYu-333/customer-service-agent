"""
模型客户端
集中创建两个client:
- chat_client:DeepSeek,负责对话和工具调用
- embed_client:硅基流动,负责把文本变成向量
其他模块从这里取 client,不自己创建,避免 key 和 base_url 散落在各处
"""

from openai import OpenAI

from app.config import DEEPSEEK_API_KEY, SILICONFLOW_API_KEY

CHAT_MODEL = "deepseek-flash"
EMBED_MODEL = "BAAI/bge-m3"

# 一个client只能在"同一平台的不同模型"之间切换,所以必须要两个client
chat_client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

embed_client = OpenAI(
    api_key=SILICONFLOW_API_KEY,
    base_url="https://api.siliconflow.cn/v1"
)
