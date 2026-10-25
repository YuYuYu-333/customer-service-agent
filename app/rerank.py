"""
用 cross-encoder 重排候选块.
向量检索分别编码问题和文档,重排模型把两者拼在一起读,
能理解条件和否定,代价是每对都要单独算一次.
"""

import httpx

from app.config import SILICONFLOW_API_KEY
from app.rag import TOP_K, search

RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
_URL = "https://api.siliconflow.cn/v1/rerank"
_TIMEOUT = 10.0


def rerank(query, documents):
    """返回与 documents 等长的分数列表,顺序和输入一致"""
    resp = httpx.post(
        _URL,
        headers={"Authorization": f"Bearer {SILICONFLOW_API_KEY}"},
        json={"model": RERANK_MODEL, "query": query, "documents": documents},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()

    scores = [0.0] * len(documents)
    for item in resp.json()["results"]:
        scores[item["index"]] = item["relevance_score"]
    return scores


def rerank_search(query, k=TOP_K, recall=10):
    """先召回 recall 条,重排后取前 k 条."""
    candidates = search(query, k=recall)
    documents = [text for _, text in candidates]
    scores = rerank(query, documents)

    ordered = sorted(zip(scores, documents), key=lambda pair: pair[0], reverse=True)
    return ordered[:k]
