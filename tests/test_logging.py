"""
日志配置：handler 必须真的装到 root 上。
"""

import logging

from app.logging import setup_logging


def test_setup_logging_installs_root_handler():
    root = logging.getLogger()
    uvicorn_error = logging.getLogger("uvicorn.error")
    original_handlers = root.handlers[:]
    original_level = root.level
    original_propagate = uvicorn_error.propagate
    try:
        root.handlers = []
        uvicorn_error.propagate = False
        setup_logging()
        assert root.handlers, "root 没装 handler，INFO 日志会被整个丢掉"
        assert type(root.handlers[0].formatter).__name__ == "JsonFormatter"
        assert uvicorn_error.propagate is True, "uvicorn 的 handler 被清空后必须向上传递"
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)
        uvicorn_error.propagate = original_propagate
