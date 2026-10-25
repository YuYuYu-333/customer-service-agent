"""
BM25 检索:只看字面重合,不理解语义.
中文没有空格,用字符二元组当词: "退货运费" -> "退货","货运","运费"
不需要分词库,代价是词序信息丢失.
"""

import math
from collections import Counter

from app.text import chunk, load_policy

_K1 = 1.5
_B = 0.75

# 模块级缓存,进程存活期间只建一次
_blocks = None
_docs = None
_df = None
_avg_len = 0.0


def tokenize(text):
    """切成字符二元组,去掉所有空白"""
    text = "".join(ch for ch in text if not ch.isspace())
    if len(text) < 2:
        return [text] if text else []
    return [text[i:i + 2] for i in range(len(text) - 1)]


def _build():
    global _blocks, _docs, _df, _avg_len

    _blocks = chunk(load_policy())
    _docs = [Counter(tokenize(block)) for block in _blocks]

    _df = Counter()
    for counts in _docs:
        for term in counts:
            _df[term] += 1

    total = sum(sum(counts.values()) for counts in _docs)
    _avg_len = total / len(_docs) if _docs else 0.0


def _ensure():
    if _docs is None:
        _build()


def search_bm25(query, k=5):
    """返回[(得分,原文), ...],按得分降序,只保留得分大于 0 的块."""
    _ensure()

    n = len(_docs)
    query_terms = Counter(tokenize(query))
    scored = []

    for i, counts in enumerate(_docs):
        length = sum(counts.values())
        score = 0.0
        for term, qf in query_terms.items():
            f = counts.get(term, 0)
            if not f:
                continue
            df = _df[term]
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            denom = f + _K1 * (1 - _B + _B * length / _avg_len)
            score += idf * f * (_K1 + 1) / denom * qf
        if score > 0:
            scored.append((score, _blocks[i]))

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:k]
