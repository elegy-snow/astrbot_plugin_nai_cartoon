"""Pricing rules mirrored from nai.sta1n.cn frontendGenerationCost."""

SIZE_COSTS = {
    "竖图": 1,
    "横图": 1,
    "方图": 1,
    "2K竖图": 15,
    "2K横图": 15,
    "2K方图": 15,
    "4K竖图": 25,
    "4K横图": 25,
    "4K方图": 25,
}
MODELS = {"nai-diffusion-4-5-full", "nai-diffusion-5-full"}


def normalize_steps(steps: int) -> int:
    """Clamp steps exactly as the station UI does."""
    return max(1, min(50, int(steps)))


def generation_cost(size_cost: int, model: str, steps: int) -> int:
    """Return frontend cost for a single image.

    The model surcharge only applies when size_cost <= 1. High-resolution
    size costs bypass the model base price but still receive step surcharges.
    """
    if size_cost < 1:
        raise ValueError("size_cost must be positive")
    if model not in MODELS:
        raise ValueError(f"unsupported model: {model}")
    steps = normalize_steps(steps)
    extra = 8 if steps > 45 else 6 if steps > 35 else 4 if steps > 28 else 0
    if size_cost > 1:
        return size_cost + extra
    if model == "nai-diffusion-5-full":
        return 8 + extra
    return 2 + extra if extra else 1


def cost_for_size(size: str, model: str, steps: int) -> int:
    try:
        size_cost = SIZE_COSTS[size]
    except KeyError as exc:
        raise ValueError(f"unsupported size: {size}") from exc
    return generation_cost(size_cost, model, steps)
