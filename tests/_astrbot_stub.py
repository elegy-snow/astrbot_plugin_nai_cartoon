"""本机没有安装 astrbot，测试会话 LLM 出图工具需要一个最小桩。

桩只提供 `main.py` 真正 import 的名字（与参考插件验证过的导入路径一致）：
少任何一个都会 ImportError，这样导入路径写错时测试会直接红，而不是被静默掩盖。
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


class Image:
    def __init__(self, file: str = "", **kwargs):
        self.file = file
        for key, value in kwargs.items():
            setattr(self, key, value)

    @classmethod
    def fromBytes(cls, payload: bytes):
        return cls(file="base64://" + bytes(payload).hex())


class MessageChain:
    def __init__(self, chain=None):
        self.chain = list(chain or [])


class AstrMessageEvent:
    pass


class Context:
    pass


class Star:
    def __init__(self, context=None, config=None):
        self.context = context
        self.config = config


def register(*args, **kwargs):
    def decorator(cls):
        cls.register_args = args
        return cls

    return decorator


class _Filter:
    def command(self, *args, **kwargs):
        def decorator(func):
            return func

        return decorator

    def llm_tool(self, *args, **kwargs):
        def decorator(func):
            return func

        return decorator


class _Logger:
    def __init__(self):
        self.records: list[tuple[str, str]] = []

    def _record(self, level, message, *args):
        try:
            text = message % args if args else message
        except (TypeError, ValueError):
            text = str(message)
        self.records.append((level, text))

    def debug(self, message, *args):
        self._record("debug", message, *args)

    def info(self, message, *args):
        self._record("info", message, *args)

    def warning(self, message, *args):
        self._record("warning", message, *args)

    def error(self, message, *args):
        self._record("error", message, *args)


filter = _Filter()
logger = _Logger()


def install() -> None:
    """把桩装进 sys.modules；已安装则不动。"""
    if "astrbot" in sys.modules:
        return
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = logger
    event_module = types.ModuleType("astrbot.api.event")
    event_module.AstrMessageEvent = AstrMessageEvent
    event_module.MessageChain = MessageChain
    event_module.filter = filter
    components = types.ModuleType("astrbot.api.message_components")
    components.Image = Image
    star = types.ModuleType("astrbot.api.star")
    star.Context = Context
    star.Star = Star
    star.register = register
    astrbot.api = api
    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.event": event_module,
            "astrbot.api.message_components": components,
            "astrbot.api.star": star,
        }
    )


def load_plugin():
    """按真实包路径导入插件 main（`main.py` 用的是相对导入）。"""
    install()
    root = Path(__file__).resolve().parents[1]
    parent = str(root.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return importlib.import_module(f"{root.name}.main")
