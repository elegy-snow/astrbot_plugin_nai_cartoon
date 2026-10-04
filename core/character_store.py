"""Per-user character card storage backed by AstrBot KV."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any


STORE_KEY_PREFIX = "characters:"


class CharacterStoreError(ValueError):
    pass


def _validate_card(name: str, card: Mapping[str, Any]) -> dict[str, Any]:
    clean_name = name.strip()
    if not clean_name or len(clean_name) > 40:
        raise CharacterStoreError("角色名不能为空且不能超过 40 字")
    if not re.fullmatch(r"[\w\u4e00-\u9fff-]+", clean_name):
        raise CharacterStoreError("角色名仅支持中英文、数字、下划线和连字符")
    ref = str(card.get("ref", "")).strip()
    look = str(card.get("look", "")).strip()
    if not ref:
        raise CharacterStoreError("角色称呼 ref 必填")
    if not look:
        raise CharacterStoreError("角色外貌 look 必填，请写清成年外貌特征")
    if len(ref) > 160 or len(look) > 2000:
        raise CharacterStoreError("角色称呼或外貌描述过长")
    if not re.search(r"\b(adult|woman|man|成年)\b", look, re.IGNORECASE):
        raise CharacterStoreError("原创角色外貌必须明确标注成年（如 adult woman）")
    raw_parts = card.get("parts") or {}
    if not isinstance(raw_parts, Mapping):
        raise CharacterStoreError("parts 必须是键值对象")
    parts: dict[str, str] = {}
    for key, value in raw_parts.items():
        key = str(key).strip()
        value = str(value).strip()
        if not re.fullmatch(r"[\w-]{1,32}", key):
            raise CharacterStoreError(f"无效的部件名：{key}")
        if value:
            parts[key] = value[:500]
    return {
        "name_zh": clean_name,
        "ref": ref,
        "look": look,
        "uc": str(card.get("uc", "")).strip()[:2000],
        "tag": str(card.get("tag", "")).strip()[:500],
        "parts": parts,
    }


class CharacterStore:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def _key(self, user_id: str) -> str:
        return f"{STORE_KEY_PREFIX}{user_id}"

    async def _load(self, user_id: str) -> dict[str, dict[str, Any]]:
        raw = await self.plugin.get_kv_data(self._key(user_id), "{}")
        if isinstance(raw, dict):
            parsed = raw
        else:
            try:
                parsed = json.loads(raw or "{}")
            except (TypeError, json.JSONDecodeError):
                parsed = {}
        if not isinstance(parsed, dict):
            return {}
        return {str(name): value for name, value in parsed.items() if isinstance(value, dict)}

    async def _save(self, user_id: str, cards: dict[str, dict[str, Any]]) -> None:
        await self.plugin.put_kv_data(self._key(user_id), json.dumps(cards, ensure_ascii=False))

    async def list(self, user_id: str) -> list[str]:
        return sorted(await self._load(user_id), key=str.casefold)

    async def get(self, user_id: str, name: str) -> dict[str, Any] | None:
        cards = await self._load(user_id)
        card = cards.get(name)
        if card is None:
            card = next((value for key, value in cards.items() if key.casefold() == name.casefold()), None)
        if card is None:
            return None
        return dict(card, slot=1)

    async def put(self, user_id: str, name: str, card: Mapping[str, Any]) -> None:
        clean = _validate_card(name, card)
        cards = await self._load(user_id)
        cards[clean["name_zh"]] = clean
        await self._save(user_id, cards)

    async def delete(self, user_id: str, name: str) -> bool:
        cards = await self._load(user_id)
        match = next((key for key in cards if key.casefold() == name.casefold()), None)
        if match is None:
            return False
        del cards[match]
        await self._save(user_id, cards)
        return True
