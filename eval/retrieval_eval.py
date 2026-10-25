"""
检索层单独评测:只调 search(),不调模型.
7 块文档 + TOP_K=5 时 recall@5 的随机基线就有 71%,
没有区分度.所以这里看正确块的排名:recall@1 和 MRR.
"""

import json
from pathlib import Path

from app.bm25 import search_bm25
from app.hybrid import hybrid_search
from app.rag import search
from app.rerank import rerank_search

_QUESTIONS = Path(__file__).resolve().parent / "retrieval_questions.jsonl"
_K = 5


def load_rows():
    rows = []
    with open(_QUESTIONS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def rank_of(results, section):
    """正确章节在结果里的名次, 从 1 开始;没命中返回None"""
    for i, (_, text) in enumerate(results, start=1):
        if section in text[:40]:
            return i
    return None


def evaluate(rows, retrieve):
    hits1 = 0
    score_sum = 0.0

    for row in rows:
        rank = rank_of(retrieve(row["question"], _K), row["expect_section"])
        if rank == 1:
            hits1 += 1
        score_sum += 1 / rank if rank else 0
        print(f'{row["id"]:6} rank={rank}')

    n = len(rows)
    print(f" recall@1={hits1}/{n}  MRR {score_sum / n:.3f}")


def main():
    rows = load_rows()
    methods = [
        ("vector", search),
        ("bm25", search_bm25),
        ("hybrid", hybrid_search),
        ("rerank", rerank_search),
    ]

    for level in ("easy", "hard"):
        print(f"== {level} ==")
        subset = [r for r in rows if r["level"] == level]
        for name, fn in methods:
            print(name)
            evaluate(subset, fn)


if __name__ == '__main__':
    main()
