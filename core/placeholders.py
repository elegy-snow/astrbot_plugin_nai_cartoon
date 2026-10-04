"""Placeholder substitution for character-aware prompts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


PLACEHOLDER_RE = re.compile(r"【(?:(\d+)(?::(look|name)|\.([\w-]+))?|(style|quality))】")
REMAINING_RE = re.compile(r"【[^】]*】")


class PlaceholderError(ValueError):
    pass


def _clean_commas(text: str) -> str:
    text = re.sub(r"\s*,\s*,+", ", ", text)
    text = re.sub(r"(?:,\s*){2,}", ", ", text)
    text = re.sub(r"\s+([,.])", r"\1", text)
    text = re.sub(r"(?:^\s*,\s*|\s*,\s*$)", "", text)
    return text.strip()


def replace_placeholders(
    prompt: str,
    characters: list[Mapping[str, Any]],
    *,
    style_tags: str = "",
    quality_tags: str = "",
    char_boxes: bool = False,
) -> str:
    slots: dict[int, Mapping[str, Any]] = {}
    for char in characters:
        try:
            slot = int(char["slot"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PlaceholderError("角色卡缺少有效 slot") from exc
        if slot < 1 or slot in slots:
            raise PlaceholderError("角色 slot 必须为不重复的正整数")
        slots[slot] = char

    def replace(match: re.Match[str]) -> str:
        raw_slot, field, part_name, global_field = match.groups()
        if global_field == "style":
            return style_tags
        if global_field == "quality":
            return quality_tags
        slot = int(raw_slot)
        char = slots.get(slot)
        if char is None:
            raise PlaceholderError(f"缺少 {slot} 号角色卡")
        ref = str(char.get("ref") or char.get("name_zh") or "").strip()
        if field == "name":
            return str(char.get("name_zh") or "").strip()
        if field == "look":
            if not ref:
                raise PlaceholderError(f"{slot} 号角色缺少称呼 ref")
            look = str(char.get("look") or "").strip()
            tag = str(char.get("tag") or "").strip()
            if char_boxes:
                return ref
            parts = [ref]
            if tag and tag.casefold() != ref.casefold() and tag.casefold() not in look.casefold():
                parts.append(tag)
            if look:
                parts.append(look)
            return ", ".join(parts)
        if part_name:
            parts = char.get("parts") or {}
            if not isinstance(parts, Mapping):
                raise PlaceholderError(f"{slot} 号角色 parts 格式无效")
            return str(parts.get(part_name) or "").strip()
        if not ref:
            raise PlaceholderError(f"{slot} 号角色缺少称呼 ref")
        return ref

    try:
        result = PLACEHOLDER_RE.sub(replace, prompt)
    except PlaceholderError:
        raise
    result = result.replace("【style】", style_tags).replace("【quality】", quality_tags)
    result = _clean_commas(result)
    if REMAINING_RE.search(result):
        raise PlaceholderError("提示词中存在无法识别或未替换的【占位符】")
    return result
