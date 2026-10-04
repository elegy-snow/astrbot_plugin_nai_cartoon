"""出图参数归一化：会话 LLM 工具与聊天指令共用同一套校验。

LLM 传来的尺寸/步数/模型是不可信输入（可能写 "4K"、"1024x1536"、"30 步"），
这里统一解析成站点认的字面量，失败时抛 `DrawError`，文案可直接回给用户。
"""

from __future__ import annotations

from .pricing import MODELS, SIZE_COSTS, cost_for_size, normalize_steps


class DrawError(RuntimeError):
    """出图流程中可以直接展示给用户的失败原因。"""


def resolve_size(value: object, default: str) -> str:
    candidate = str(value or "").strip() or str(default or "").strip()
    if candidate not in SIZE_COSTS:
        raise DrawError(f"未知尺寸：{value or '(空)'}；可选：{'、'.join(SIZE_COSTS)}")
    return candidate


def resolve_model(value: object, default: str) -> str:
    candidate = str(value or "").strip() or str(default or "").strip()
    if candidate not in MODELS:
        raise DrawError(f"未知模型：{value or '(空)'}；可选：{'、'.join(sorted(MODELS))}")
    return candidate


def resolve_steps(value: object, default: object) -> int:
    raw = str(value).strip() if value is not None else ""
    if raw in {"", "0", "0.0"}:
        raw = str(default).strip() if default is not None else ""
    try:
        steps = int(float(raw))
    except (TypeError, ValueError):
        raise DrawError(f"步数必须是 1-50 的整数，收到：{value}") from None
    if steps <= 0:
        raise DrawError(f"步数必须是 1-50 的整数，收到：{value}")
    return normalize_steps(steps)


def resolve_cost(size: str, model: str, steps: int) -> int:
    try:
        return cost_for_size(size, model, steps)
    except ValueError as exc:
        raise DrawError(str(exc)) from None
