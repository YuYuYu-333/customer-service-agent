"""
向量检索
把文本变成向量,算相似度,按问题找最相关的几块.
被 tools.py 的 search_policy 调用,不负责对话逻辑
"""
import json
import logging
from pathlib import Path

from app.llm import EMBED_MODEL, embed_client
from app.text import chunk, load_policy

# 检索返回的块数.政策文档共 40 块,取 5 块是为了覆盖复合问题:
# 用户一句话里问两件事时,单一 query 会偏向其中一件,只取 3 块会漏掉另一件的依据.
# 代价是噪音变多,阶段 9 用混合检索和 rerank 收敛.
TOP_K = 5

# 索引是生成物, 不进 git , 放在项目根.
_INDEX_PATH = Path(__file__).resolve().parent.parent / "index.json"

logger = logging.getLogger(__name__)

# 模块级缓存:进程存活期间只从磁盘读一次.
_index = None


def embed(text):
    """
    把一段文本变成1024维向量
    :param text: 任意文本
    :return: 浮点数列表
    """
    resp = embed_client.embeddings.create(
        model=EMBED_MODEL,
        input=text,
    )
    return resp.data[0].embedding


def cos(a, b):
    """
    两个已归一化向量的相似度,直接点积即可,不需要算模长
    :param a: 第一个向量
    :param b: 第二个向量
    :return: -1.0 ~ 1.0,越大越相关
    """
    return sum(x * y for x, y in zip(a, b))


def build_index():
    """
    把政策文档切块并逐块向量化,写入 index.json.
    :return: 索引列表,每个元素是 {"text": 块内容, "vec": 1024 维向量}
    """
    blocks = chunk(load_policy())
    index = [{"text": block, "vec": embed(block)} for block in blocks]

    with open(_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)

    logger.info("索引已写入 %s,共 %d 块", _INDEX_PATH.name, len(index))
    return index


def load_index():
    """
    从磁盘读取索引,文件不存在时返回 None.
    """
    if not _INDEX_PATH.exists():
        return None

    with open(_INDEX_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_index():
    """
    懒加载索引,进程存活期间只读一次盘.
    :raise RuntimeError: index.json 不存在时
    :return: 索引列表
    """
    global _index

    if _index is None:
        _index = load_index()
        if _index is None:
            raise RuntimeError(
                "index.json 不存在,请先运行 python -m scripts.build_index"
            )

    return _index


def search(query, k=TOP_K):
    """
    按问题检索最相关的 k 个文本块.
    :param query:用户的问题原文
    :param k:返回块数
    :return: [(相似度, 文本块), ...],按相似度从高到低
    """
    # 先确认索引可用,否则白白花一次向量化调用
    index = get_index()

    query_vec = embed(query)
    scored = [(cos(query_vec, item["vec"]), item["text"]) for item in index]
    scored.sort(reverse=True)

    return scored[:k]
