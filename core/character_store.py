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
    if not clean_name:
        raise CharacterStoreError("角色名不能为空")
    if not re.fullmatch(r"[\w\u4e00-\u9fff-]+", clean_name):
        raise CharacterStoreError("角色名仅支持中英文、数字、下划线和连字符")
    ref = str(card.get("ref", "")).strip()
    look = str(card.get("look", "")).strip()
    if not ref:
        raise CharacterStoreError("角色称呼 ref 必填")
    if not look:
        raise CharacterStoreError("角色外貌 look 必填，请写清成年外貌特征")
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
            parts[key] = value
    return {
        "name_zh": clean_name,
        "ref": ref,
        "look": look,
        "uc": str(card.get("uc", "")).strip(),
        "tag": str(card.get("tag", "")).strip(),
        "parts": parts,
    }


class CharacterStore:
    def __init__(self, plugin: Any, configured_cards: Any = "{}"):
        self.plugin = plugin
        if isinstance(configured_cards, str):
            try:
                configured_cards = json.loads(configured_cards or "{}")
            except json.JSONDecodeError:
                configured_cards = {}
        self.configured_cards = self._normalize_configured_cards(configured_cards)

    @staticmethod
    def _normalize_configured_cards(raw: Any) -> dict[str, dict[str, Any]]:
        if isinstance(raw, list):
            entries = ((str(card.get("name_zh") or card.get("name") or ""), card) for card in raw if isinstance(card, dict))
        elif isinstance(raw, dict) and ("ref" in raw or "look" in raw):
            name = str(raw.get("name_zh") or raw.get("name") or "角色")
            entries = ((name, raw),)
        elif isinstance(raw, dict):
            entries = raw.items()
        else:
            return {}
        normalized: dict[str, dict[str, Any]] = {}
        for name, card in entries:
            name = str(name).strip()
            if name and isinstance(card, dict):
                normalized[name] = card
        return normalized

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

    def _configured_card(self, name: str) -> dict[str, Any] | None:
        matched_name = next((key for key in self.configured_cards if key.casefold() == name.casefold()), None)
        if matched_name is None:
            return None
        card = self.configured_cards[matched_name]
        return _validate_card(matched_name, card)

    async def list(self, user_id: str) -> list[str]:
        names = set(await self._load(user_id))
        names.update(str(name) for name in self.configured_cards)
        return sorted(names, key=str.casefold)

    async def get(self, user_id: str, name: str) -> dict[str, Any] | None:
        cards = await self._load(user_id)
        card = cards.get(name)
        if card is None:
            card = next((value for key, value in cards.items() if key.casefold() == name.casefold()), None)
        if card is None:
            card = self._configured_card(name)
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
