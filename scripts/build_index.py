"""
重建向量索引
文档内容或切分逻辑改了之后必须重跑,否则检索永远命中旧内容.
用法: python -m scripts.build_index
"""

import logging

from app.rag import build_index


def main():
    logging.basicConfig(level=logging.INFO)

    index = build_index()

    logging.info("索引重建完成, 共 %d 块", len(index))


if __name__ == '__main__':
    main()
