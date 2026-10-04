"""Build one-page prompts following the nai-doujin skill conventions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .placeholders import PlaceholderError, replace_placeholders

TAGS_EXPLICIT = "nsfw, rating:explicit, 1boy, faceless male, hetero, adult, uncensored"
TAGS_GENERAL = "rating:general"
TAGS_STYLE = (
    "monochrome, greyscale, comic, manga, screentone, halftone, lineart, "
    "speech bubble, sound effects, emphasis lines, dramatic shadows, detailed background"
)
TAGS_STYLE_COLOR = (
    "comic, manga, lineart, speech bubble, sound effects, emphasis lines, "
    "dramatic shadows, detailed background"
)
TAGS_QUALITY = "very aesthetic, masterpiece, best quality, absurdres"
NEG_BASE = (
    "loli, child, aged down, petite, flat chest, censored, mosaic censoring, "
    "bar censor, pubic hair, multiple girls, 2boys, male face, half-closed eyes, "
    "bad anatomy, bad hands, extra digits, fewer digits, extra legs, lowres, "
    "artistic error, worst quality, bad quality, jpeg artifacts, very displeasing, "
    "logo, watermark, signature, furigana, colorful"
)
NEG_BUBBLE = "text in speech bubble, chinese text, english text"
NEG_NO_SEX = "penis, sex, vaginal, penis in pussy"
NEG_XRAY = "see-through body, full body x-ray, skeleton, bones, transparent skin, 2 penises"
NEG_SECTION = "see-through body, x-ray on body, transparent skin, penis on stomach, 2 penises"


def base_negative(bw: bool = True) -> str:
    return NEG_BASE if bw else NEG_BASE.replace(", colorful", "")


LAYOUTS = {
    "四格": "A manga page read from right to left with four panels. The main panel at the top shows the main action; three small panels of about the same size run along the bottom, separated by clean thin borders.",
    "主格+下排三格": "A manga page read from right to left with four panels. The main panel at the top shows the main action; three small panels of about the same size run along the bottom, separated by clean thin borders.",  # 四格的常用别名
    "三格主格下": "A manga page read from right to left with three panels: two small panels side by side across the top, and the main panel filling the bottom two thirds of the page, separated by clean thin borders.",
    "两格上下": "A manga page read from right to left with two panels stacked one above the other, separated by a clean thin border.",
    "两格斜线": "A manga page read from right to left with two wide panels separated by one diagonal border slanting across the middle of the page.",
    "五格": "A manga page read from right to left with five panels: one wide panel across the top, two panels side by side in the middle, and two panels side by side at the bottom, separated by clean thin borders.",
    "六格": "A manga page read from right to left with six panels in three rows of two, separated by clean thin borders.",
    "整页大格": "A manga page that is one single full-page splash panel with no borders, the art bleeding off every edge.",
}
POSITIONS = {
    "四格": ["top, main panel", "bottom-right", "bottom-middle", "bottom-left"],
    "主格+下排三格": ["top, main panel", "bottom-right", "bottom-middle", "bottom-left"],
    "三格主格下": ["top-right", "top-left", "bottom, main panel"],
    "两格上下": ["top", "bottom"],
    "五格": ["top, wide", "middle-right", "middle-left", "bottom-right", "bottom-left, tall"],
    "六格": ["top-right", "top-left", "middle-right", "middle-left", "bottom-right", "bottom-left"],
}


def build_page_prompt(
    *,
    layout: str,
    panels: Sequence[Mapping[str, Any]],
    characters: list[Mapping[str, Any]],
    explicit: bool = False,
    behavior_tags: str = "",
    style_tags: str = TAGS_STYLE,
    quality_tags: str = TAGS_QUALITY,
    bw: bool = True,
    no_sex: bool = False,
    char_boxes: bool = False,
) -> tuple[str, str]:
    if layout not in LAYOUTS:
        raise ValueError(f"未知版式：{layout}；可用版式：{'、'.join(LAYOUTS)}")
    if not panels:
        raise ValueError("至少需要一个分镜格")
    if len(panels) > 6:
        raise ValueError("一期最多支持六格版式")
    positions = POSITIONS.get(layout)
    if positions and len(panels) != len(positions):
        raise ValueError(f"{layout}需要 {len(positions)} 格，当前提供 {len(panels)} 格")
    if not characters:
        raise ValueError("至少需要一张角色卡")

    lead_tags = TAGS_EXPLICIT if explicit else TAGS_GENERAL
    selected_style = style_tags if bw else TAGS_STYLE_COLOR
    header = ", ".join(part for part in (lead_tags, behavior_tags.strip(), "【1:look】", "【style】", "【quality】") if part)
    blocks: list[str] = []
    negative_parts = [base_negative(bw)]
    if no_sex:
        negative_parts.append(NEG_NO_SEX)
    for char in characters:
        uc = str(char.get("uc") or "").strip()
        if uc:
            negative_parts.append(uc)
    has_bubble = False
    for index, panel in enumerate(panels, start=1):
        number = int(panel.get("no", index))
        if number != index:
            raise ValueError("分镜格编号必须从 1 连续递增")
        pos = str(panel.get("pos") or (positions[index - 1] if positions else f"panel {index}"))
        action = str(panel.get("action") or panel.get("description") or "").strip()
        if not action:
            raise ValueError(f"第 {index} 格缺少画面描述")
        shot = str(panel.get("shot") or "").strip()
        sfx = str(panel.get("sfx") or "").strip()
        moan = str(panel.get("moan") or "").strip()
        dialogue = bool(panel.get("blank_bubble", False)) or bool(panel.get("dialogue"))
        pieces = [item for item in (shot, action) if item]
        if sfx:
            pieces.append(f'the sound effect "{sfx}" in bold letters')
        if moan:
            pieces.append(f'her moan "{moan}" written in bold letters')
        if dialogue:
            pieces.append("An empty white speech bubble with no text in it.")
            has_bubble = True
        blocks.append(f"Panel {index} ({pos}): " + ", ".join(pieces) + ".")
    if has_bubble:
        negative_parts.append(NEG_BUBBLE)
    if any("x-ray" in str(p.get("action", "")).casefold() for p in panels):
        negative_parts.append(NEG_XRAY)
    if any("cross-section" in str(p.get("action", "")).casefold() for p in panels):
        negative_parts.append(NEG_SECTION)
    if char_boxes:
        if len(characters) < 2:
            raise ValueError("角色框模式仅用于多人页")
        for char in characters:
            uc = str(char.get("uc") or "").strip()
            if uc:
                negative_parts.append(uc)

    raw_prompt = f"{header}. {LAYOUTS[layout]} " + " ".join(blocks)
    prompt_characters = characters
    if not bw:
        prompt_characters = [
            dict(char, look=", ".join(
                term.strip() for term in str(char.get("look", "")).split(",")
                if term.strip().casefold() not in {"monochrome", "greyscale", "grayscale"}
            ))
            for char in characters
        ]
    try:
        prompt = replace_placeholders(
            raw_prompt,
            prompt_characters,
            style_tags=selected_style,
            quality_tags=quality_tags,
            char_boxes=char_boxes,
        )
    except PlaceholderError as exc:
        raise ValueError(str(exc)) from exc
    negative = ", ".join(dict.fromkeys(part for part in negative_parts if part))
    return prompt, negative
