"""Helpers for reading AstrBot plugin configuration values."""

from __future__ import annotations

from typing import Any


def config_value(config: Any, key: str, default: Any) -> Any:
    if isinstance(config, dict):
        value = config.get(key, default)
    else:
        getter = getattr(config, "get", None)
        if callable(getter):
            try:
                value = getter(key, default)
            except TypeError:
                value = getter(key)
        else:
            value = getattr(config, key, default)
    if hasattr(value, "value"):
        value = value.value
    return default if value is None else value
