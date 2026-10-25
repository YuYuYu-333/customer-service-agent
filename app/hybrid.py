"""
向量与 BM25 的混合检索,用 RRF 融合.
两种分数不可比:cos 在 -1~1,BM25 没有上限.
所以只看名次,不看分数.
"""

from app.bm25 import search_bm25
from app.rag import TOP_K, search

_RRF_K = 60


def rrf(ranked_list, k=_RRF_K):
    """每块得分 = sum(1 / (k + 名次)),出现在多路结果里的块自然得分更高"""
    scores = {}
    texts = {}

    for results in ranked_list:
        for rank, (_, text) in enumerate(results, start=1):
            scores[text] = scores.get(text, 0.0) + 1 / (k + rank)
            texts[text] = text

    ordered = sorted(scores, key=lambda text: scores[text], reverse=True)
    return [(scores[text], texts[text]) for text in ordered]


def hybrid_search(query, k=TOP_K):
    """两路召回后用 RRF融合,取前 k 块."""
    return rrf([search(query, k), search_bm25(query, k)])[:k]
