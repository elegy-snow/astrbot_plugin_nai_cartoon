"""会话 LLM 出图路径的提示词整理与负面组装。

分工：**细化提示词由会话中的 LLM 完成**（用户提中文需求 → LLM 写成 NovelAI 标签），
本模块只负责不可委托的部分：

1. 把模型给的文本整理成一行标签（全角逗号、换行、重复逗号）；
2. 把角色卡的称呼/外貌接进正向提示词，重复标签只留一份；
3. **保证成年守卫与基础负面永远存在**——`NEG_BASE`（含 `loli, child, aged down,
   petite, flat chest`）不允许被模型参数或配置移除，只能叠加。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .prompt_builder import (
    NEG_BUBBLE,
    NEG_NO_SEX,
    NEG_SECTION,
    NEG_XRAY,
    base_negative,
)

_WHITESPACE = re.compile(r"\s+")


def normalize_tags(text: Any) -> str:
    """整理成一行英文标签：全角逗号转半角、压缩空白、丢掉空标签。"""
    value = str(text or "")
    for source, target in (("，", ","), ("、", ","), ("；", ","), ("\r", " "), ("\n", " ")):
        value = value.replace(source, target)
    cleaned = []
    for piece in value.split(","):
        tag = _WHITESPACE.sub(" ", piece).strip()
        if tag:
            cleaned.append(tag)
    return ", ".join(cleaned)


def dedupe_forward(prompt: Any, extra: Any) -> str:
    """把 extra 的标签接在 prompt 后面，重复标签（忽略大小写）只保留第一次出现。"""
    seen: set[str] = set()
    merged: list[str] = []
    for chunk in (prompt, extra):
        for piece in str(chunk or "").split(","):
            tag = piece.strip()
            if not tag:
                continue
            key = tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(tag)
    return ", ".join(merged)


def merge_character(prompt: Any, card: Mapping[str, Any]) -> tuple[str, str]:
    """把角色卡的称呼与外貌接进正向提示词，返回 (正向提示词, 角色负面词)。

    角色框（char_captions）站点不支持，所以外貌按 skill 的做法内嵌进主提示词。
    """
    parts = [
        normalize_tags(card.get("ref", "")),
        normalize_tags(card.get("look", "")),
    ]
    extra = ", ".join(part for part in parts if part)
    return dedupe_forward(normalize_tags(prompt), extra), normalize_tags(card.get("uc", ""))


def detect_conditionals(text: Any) -> dict[str, bool]:
    """按提示词内容判断要补哪些条件负面（规则与 prompt_builder 一致）。"""
    lowered = str(text or "").casefold()
    return {
        "bubble": "speech bubble" in lowered,
        "xray": "x-ray" in lowered or "x ray" in lowered,
        "section": "cross-section" in lowered or "cross section" in lowered,
    }


def compose_negative(
    extra: Any = "",
    *,
    bw: bool = True,
    no_sex: bool = False,
    bubble: bool = False,
    xray: bool = False,
    section: bool = False,
) -> str:
    """成年守卫 + 基础负面 + 条件负面 + 额外负面（保序去重）。

    `extra` 来自会话 LLM，只做叠加；第一段永远是 `base_negative(bw)`。
    """
    parts = [base_negative(bw)]
    if no_sex:
        parts.append(NEG_NO_SEX)
    if bubble:
        parts.append(NEG_BUBBLE)
    if xray:
        parts.append(NEG_XRAY)
    if section:
        parts.append(NEG_SECTION)
    cleaned = normalize_tags(extra)
    if cleaned:
        parts.append(cleaned)
    return ", ".join(dict.fromkeys(part for part in parts if part))
