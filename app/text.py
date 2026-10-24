"""
政策文档的读取与切分
被 build_index.py 调用,产出用于向量化的文本块.
不负责向量化,也不负责检索.
"""

from pathlib import Path

# 文档的路径从本文本位置推导,不依赖当前工作目录.
_DOC_PATH = Path(__file__).resolve().parent.parent / "data" / "docs" / "policy.txt"

# 分节标记:文档里每个 `##标题` 开始新的一节.
SECTION_MARKER = "## "


def load_policy():
    """
    读取政策文档全文.
    :return: 文档内容字符串
    """
    with open(_DOC_PATH, encoding="utf-8") as f:
        return f.read()


def chunk(text):
    """
    按一级标题切分文档,每块包含一个标题和它下面的全部正文.
    :param text:文档全文
    :return:文本块列表,空块会被丢弃
    """
    chunks = []
    current = []

    for line in text.splitlines():
        # 遇到新标题,先把上一节收进chunks,再开始新的一节.
        if line.startswith(SECTION_MARKER) and current:
            chunks.append("\n".join(current).strip())
            current = []

        current.append(line)

    # 最后一块后面没有"下一个标题"来触发上面的判断,必须在这里补一次.
    if current:
        chunks.append("\n".join(current).strip())

    return [c for c in chunks if c]
