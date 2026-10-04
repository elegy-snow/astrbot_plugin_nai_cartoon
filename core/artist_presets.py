"""画风预设名 → 站点真实画师串。

设置页里的 `fresh / comicDoujin / 2.5d / doujin / galgame` 只是**预设名**，
真正的长文本画师串在站点前端 `artistPresets`（`site-frontend/app.js:77`）里。
历史实现把预设名本身当画师串发给站点，等于往正向提示词里塞 "doujin" 这种
无意义词；API核实报告 §0.3 实测 `artist` 取值会让出图亮度差 3–5 倍，必须发真串。

约定：
- `none` → 空串（不传画师串，由站点自己决定）
- `custom` → 设置页 `custom_artist` 的原文
- 未知预设 → 空串（宁可让站点用默认，也不发垃圾词）

站点的 `lolita25d` 预设带 `{{petite,loli}}` 权重，与「只画成年人」硬约束冲突，
**故意不收录**。
"""

from __future__ import annotations

import re

# 与站点前端逐字对齐；`\n` 是站点串里真实存在的转义残留，由 _tidy 收成标签分隔。
ARTIST_PRESETS: dict[str, str] = {
    "fresh": (
        "masterpiece, best quality,[[[artist:dishwasher1910]]], {{yd_(orange_maru)}}, "
        "[artist:ciloranko], [artist:sho_(sho_lwlw)], [ningen mame], soft lighting,year 2024"
    ),
    "comicDoujin": (
        "masterpiece, best quality, very aesthetic, modern Japanese anime, official anime art, "
        "anime key visual, anime screencap, soft cel shading, soft anime coloring, "
        "smooth color transitions, natural skin tones, restrained color palette, "
        "slightly desaturated, muted colors, soft ambient lighting, gentle contrast, "
        "subtle gradients, subtle bloom, detailed anime background"
    ),
    "2.5d": (
        "0.9::misaka_12003-gou ::, dino_(dinoartforame), wanke, liduke, year 2025, realistic, 4k, "
        "-2::green ::, textless version, The image is highly intricate finished drawn. "
        "Only the character's face is in anime style, but their body is in realistic style. "
        "1.35::A highly finished photo-style artwork that has lively color, graphic texture, "
        "realistic skin surface, and lifelike flesh with little obliques::. "
        "1.63::photorealistic::, 1.63::photo(medium)::, \\n"
        "20::best quality, absurdres, very aesthetic, detailed, masterpiece::,, "
        "very aesthetic, masterpiece, no text,"
    ),
    "doujin": (
        "1.4::asanagi::,{{{{{artist:asanagi}}}}},1.2::xiaoluo_xl::,"
        "1.3::Artist: misaka_12003-gou::,1.2::Artist:shexyo::,0.7::Artist:b.sa_(bbbs)::,"
        "1::Artist:qiandaiyiyu::,1.05::artist:natedecock::,1.05::artist:kunaboto::,"
        "0.75::artist:kandata_nijou::,1.05::artist:zer0.zer0 ::,1.05::artist:jasony::,"
        "0.75::misaka_12003-gou ::, dino_(dinoartforame), wanke, liduke, year 2025, "
        "realistic, 4k, -2::green ::, {textless version, The image is highly intricate "
        "finished drawn,write realistically,true to life}, 1.35::A highly finished "
        "photo-style artwork that has lively color, graphic texture, realistic skin surface, "
        "and lifelike flesh with little obliques::, 1.63::photorealistic::,3::age slider::,"
        "1.63::photo(medium)::, 2::best quality, absurdres, very aesthetic, detailed, "
        "masterpiece::,-4::Muscle definition, abs::"
    ),
    "galgame": (
        "artist:ningen_mame,, noyu_(noyu23386566),, toosaka asagi,, location,\n"
        "20::best quality, absurdres, very aesthetic, detailed, masterpiece::,:,, "
        "very aesthetic, masterpiece, no text,"
    ),
}

# 允许出现在设置页 / 工具参数里的取值（含两个非预设分支）。
PRESET_CHOICES: tuple[str, ...] = (*ARTIST_PRESETS, "custom", "none")

_WHITESPACE = re.compile(r"(?:\\n|\s)+")
_COMMA = re.compile(r"\s*,\s*")


def _tidy(value: str) -> str:
    """把站点串里的换行转义收成逗号分隔，并压掉重复逗号。

    只动分隔符，不动标签本身（权重 `1.4::asanagi::`、`-2::green ::`、
    花括号 `{{{{{artist:asanagi}}}}}` 原样保留）。
    """
    cleaned = _WHITESPACE.sub(" ", value)
    cleaned = _COMMA.sub(", ", cleaned)
    cleaned = _COMMA.sub(", ", cleaned)
    return cleaned.strip(" ,")


for _name, _value in tuple(ARTIST_PRESETS.items()):
    ARTIST_PRESETS[_name] = _tidy(_value)
del _name, _value


def artist_for_preset(preset: str, custom: str = "") -> str:
    """把设置页/工具参数的画风取值解析成要发给站点的画师串。"""
    key = str(preset or "").strip()
    if key == "custom":
        return str(custom or "").strip()
    if key in {"", "none"}:
        return ""
    return ARTIST_PRESETS.get(key, "")
