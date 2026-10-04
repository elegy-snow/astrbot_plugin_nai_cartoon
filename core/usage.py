"""Per-user daily generation accounting."""

from __future__ import annotations

from datetime import date
from typing import Any

USAGE_KEY_PREFIX = "usage:"


def today_key() -> str:
    return date.today().isoformat()


class UsageStore:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def _key(self, user_id: str, day: str | None = None) -> str:
        return f"{USAGE_KEY_PREFIX}{user_id}:{day or today_key()}"

    async def count(self, user_id: str) -> int:
        raw = await self.plugin.get_kv_data(self._key(user_id), 0)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0

    async def record(self, user_id: str) -> int:
        used = await self.count(user_id) + 1
        await self.plugin.put_kv_data(self._key(user_id), used)
        return used

    async def remaining(self, user_id: str, limit: int) -> int | None:
        """Return remaining draws today, or None when unlimited."""
        if limit <= 0:
            return None
        return max(0, limit - await self.count(user_id))
