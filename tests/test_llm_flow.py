import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _astrbot_stub import load_plugin  # noqa: E402

from core.artist_presets import ARTIST_PRESETS, PRESET_CHOICES, artist_for_preset  # noqa: E402
from core.draw_flow import DrawError, resolve_cost, resolve_model, resolve_size, resolve_steps  # noqa: E402
from core.llm_prompt import (  # noqa: E402
    compose_negative,
    detect_conditionals,
    merge_character,
    normalize_tags,
)
from core.prompt_builder import NEG_BASE, NEG_BUBBLE, NEG_NO_SEX, NEG_XRAY, base_negative  # noqa: E402


class ArtistPresetTests(unittest.TestCase):
    def test_every_choice_resolves(self):
        for name in PRESET_CHOICES:
            with self.subTest(preset=name):
                value = artist_for_preset(name, "my artist string")
                if name in {"custom", "none"}:
                    continue
                self.assertTrue(value.strip(), f"{name} 必须是真实画师串，不能为空")

    def test_none_and_unknown_send_empty_artist(self):
        self.assertEqual(artist_for_preset("none"), "")
        self.assertEqual(artist_for_preset(""), "")
        self.assertEqual(artist_for_preset("not-a-preset"), "")
        self.assertEqual(artist_for_preset("custom", "  1.2::wlop::  "), "1.2::wlop::")

    def test_preset_values_keep_weight_syntax(self):
        doujin = artist_for_preset("doujin")
        self.assertIn("1.4::asanagi::", doujin)
        self.assertIn("{{{{{artist:asanagi}}}}}", doujin)
        self.assertIn("-2::green ::", doujin)
        self.assertNotIn(",,", doujin)

    def test_no_preset_carries_youth_weights(self):
        for name, value in ARTIST_PRESETS.items():
            with self.subTest(preset=name):
                lowered = value.casefold()
                for banned in ("loli", "child", "petite"):
                    self.assertNotIn(banned, lowered, f"{name} 含幼态权重，违反只画成年人约束")

    def test_escape_residue_is_tidied_away(self):
        self.assertNotIn("\\n", artist_for_preset("2.5d"))
        self.assertNotIn("\n", artist_for_preset("galgame"))


class LlmPromptTests(unittest.TestCase):
    def test_normalize_tags_flattens_full_width_and_blank_entries(self):
        self.assertEqual(
            normalize_tags("1girl， solo ，\nmonochrome, , masterpiece"),
            "1girl, solo, monochrome, masterpiece",
        )
        self.assertEqual(normalize_tags(None), "")

    def test_merge_character_appends_look_without_duplicates(self):
        card = {
            "ref": "the rabbit-eared woman",
            "look": "monochrome, greyscale, adult woman, rabbit ears",
            "uc": "fox ears, fox tail",
        }
        prompt, negative = merge_character("1girl, monochrome, greyscale, masterpiece", card)
        # 角色外貌与称呼接在提示词末尾，重复标签只保留第一次出现
        self.assertEqual(prompt.count("monochrome"), 1)
        self.assertTrue(prompt.startswith("1girl, monochrome, greyscale, masterpiece"))
        self.assertIn("the rabbit-eared woman", prompt)
        self.assertIn("adult woman, rabbit ears", prompt)
        self.assertEqual(negative, "fox ears, fox tail")

    def test_negative_always_keeps_adult_guard(self):
        for extra in ("", "extra words", "loli, child"):
            with self.subTest(extra=extra):
                negative = compose_negative(extra)
                self.assertIn(NEG_BASE, negative)
                self.assertIn("loli, child, aged down, petite, flat chest", negative)

    def test_conditional_and_extra_negatives_stack(self):
        negative = compose_negative("multiple tails", no_sex=True, bubble=True, xray=True)
        for expected in (NEG_NO_SEX, NEG_BUBBLE, NEG_XRAY, "multiple tails"):
            self.assertIn(expected, negative)

    def test_color_mode_drops_colorful_guard_but_keeps_base(self):
        negative = compose_negative(bw=False)
        self.assertNotIn("colorful", negative)
        self.assertIn("loli, child, aged down", negative)
        self.assertEqual(compose_negative(bw=True), base_negative(True))

    def test_detect_conditionals_matches_prompt_text(self):
        flags = detect_conditionals("Panel 1: An empty white speech bubble with no text in it.")
        self.assertTrue(flags["bubble"])
        self.assertTrue(detect_conditionals("cross-section, x-ray view")["xray"])
        self.assertTrue(detect_conditionals("cross-section, x-ray view")["section"])
        self.assertFalse(detect_conditionals("1girl, solo")["bubble"])


class DrawFlowTests(unittest.TestCase):
    def test_size_and_model_resolution(self):
        self.assertEqual(resolve_size("", "竖图"), "竖图")
        self.assertEqual(resolve_size(" 2K横图 ", "竖图"), "2K横图")
        self.assertEqual(resolve_model("", "nai-diffusion-4-5-full"), "nai-diffusion-4-5-full")
        with self.assertRaises(DrawError):
            resolve_size("4K", "竖图")
        with self.assertRaises(DrawError):
            resolve_model("sd-xl", "nai-diffusion-4-5-full")

    def test_steps_resolution_defaults_clamps_and_rejects(self):
        self.assertEqual(resolve_steps(0, 28), 28)
        self.assertEqual(resolve_steps("", 28), 28)
        self.assertEqual(resolve_steps(30, 28), 30)
        self.assertEqual(resolve_steps(500, 28), 50)
        with self.assertRaises(DrawError):
            resolve_steps("三十", 28)
        with self.assertRaises(DrawError):
            resolve_steps(-3, 28)

    def test_cost_matches_pricing_module(self):
        self.assertEqual(resolve_cost("竖图", "nai-diffusion-4-5-full", 28), 1)
        self.assertEqual(resolve_cost("2K竖图", "nai-diffusion-4-5-full", 28), 15)


class FakeEvent:
    def __init__(self, sender="alice"):
        self.sender = sender
        self.extras = {}
        self.sent = []

    def get_sender_id(self):
        return self.sender

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    async def send(self, message):
        self.sent.append(message)

    def plain_result(self, text):
        return ("plain", text)

    def chain_result(self, chain):
        return ("chain", chain)


class FakeClient:
    def __init__(self, image=b"\x89PNG\r\n\x1a\nbytes", balance=100):
        self.image = image
        self.balance = balance
        self.calls = []
        self.me_calls = 0

    async def me(self, token):
        self.me_calls += 1
        return {"balance": self.balance}

    async def generate(self, token, **kwargs):
        self.calls.append({"token": token, **kwargs})
        return {"id": "job-1"}, self.image


class FakeQueue:
    def __init__(self):
        self.submitted = 0

    async def submit(self, operation):
        self.submitted += 1
        return operation()


async def collect(generator):
    return [item async for item in generator]


class ToolFlowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = load_plugin()

    def make_plugin(self, **overrides):
        config = {
            "user_key": "test-token",
            "default_size": "竖图",
            "default_steps": 28,
            "default_model": "nai-diffusion-4-5-full",
            "default_artist_preset": "doujin",
            "daily_quota": 0,
            "quota_precheck": True,
            "bw_default": True,
        }
        config.update(overrides)
        plugin = self.main.NaiDoujinPlugin(context=None, config=config)
        store = {}
        plugin._kv = store

        async def get_kv_data(key, default=None):
            return store.get(key, default)

        async def put_kv_data(key, value):
            store[key] = value

        async def delete_kv_data(key):
            store.pop(key, None)

        plugin.get_kv_data = get_kv_data
        plugin.put_kv_data = put_kv_data
        plugin.delete_kv_data = delete_kv_data
        plugin._queue = FakeQueue()
        self.client = FakeClient()
        plugin._client = lambda: self.client
        return plugin

    async def test_tool_refines_prompt_sends_image_and_uses_real_artist(self):
        plugin = self.make_plugin()
        event = FakeEvent()
        results = await collect(
            plugin.NAI_Generate_Image(
                event, prompt="1girl， solo ,\nmonochrome, masterpiece", size="2K横图", steps=30
            )
        )
        self.assertEqual(len(results), 1)
        self.assertIn("图片已生成并发送给用户", results[0])
        self.assertEqual(len(event.sent), 1)
        chain = event.sent[0]
        self.assertEqual(len(chain.chain), 1)
        self.assertTrue(chain.chain[0].file.startswith("base64://"))
        sent = self.client.calls[0]
        self.assertEqual(sent["tag"], "1girl, solo, monochrome, masterpiece")
        self.assertEqual(sent["size"], "2K横图")
        self.assertEqual(sent["steps"], 30)
        self.assertEqual(sent["model"], "nai-diffusion-4-5-full")
        # §7 修复：发出去的是真实画师串，不再是 "doujin" 这个预设名
        self.assertEqual(sent["artist"], ARTIST_PRESETS["doujin"])
        self.assertNotEqual(sent["artist"], "doujin")
        self.assertIn("loli, child, aged down", sent["negative"])
        self.assertEqual(self.client.me_calls, 1)

    async def test_tool_merges_character_card_look_and_uc(self):
        plugin = self.make_plugin()
        await plugin._character_store().put(
            "alice",
            "兔耳娘",
            {
                "ref": "the rabbit-eared woman",
                "look": "adult woman, rabbit ears, long drooping rabbit ears",
                "uc": "fox ears, fox tail",
            },
        )
        event = FakeEvent()
        results = await collect(
            plugin.NAI_Generate_Image(
                event, prompt="1girl, monochrome, adult woman, rabbit ears", character="兔耳娘"
            )
        )
        self.assertIn("图片已生成并发送给用户", results[0])
        sent = self.client.calls[0]
        self.assertEqual(
            sent["tag"],
            "1girl, monochrome, adult woman, rabbit ears, the rabbit-eared woman, "
            "long drooping rabbit ears",
        )
        self.assertIn("fox ears, fox tail", sent["negative"])

    async def test_tool_reports_missing_character(self):
        plugin = self.make_plugin()
        event = FakeEvent()
        results = await collect(plugin.NAI_Generate_Image(event, prompt="1girl", character="不存在"))
        self.assertIn("未找到角色卡", results[0])
        self.assertEqual(self.client.calls, [])

    async def test_tool_only_generates_once_per_message(self):
        plugin = self.make_plugin()
        event = FakeEvent()
        first = await collect(plugin.NAI_Generate_Image(event, prompt="1girl, solo"))
        second = await collect(plugin.NAI_Generate_Image(event, prompt="1boy, solo"))
        self.assertIn("图片已生成并发送给用户", first[0])
        self.assertIn("本轮消息已经执行过一次", second[0])
        self.assertEqual(plugin._queue.submitted, 1)
        self.assertEqual(len(event.sent), 1)

    async def test_validation_error_does_not_consume_the_attempt(self):
        plugin = self.make_plugin()
        event = FakeEvent()
        bad_style = await collect(plugin.NAI_Generate_Image(event, prompt="1girl", style="bogus"))
        bad_size = await collect(plugin.NAI_Generate_Image(event, prompt="1girl", size="4K"))
        empty = await collect(plugin.NAI_Generate_Image(event, prompt="   "))
        good = await collect(plugin.NAI_Generate_Image(event, prompt="1girl, solo"))
        self.assertIn("未知画风", bad_style[0])
        self.assertIn("未知尺寸", bad_size[0])
        self.assertIn("prompt 为空", empty[0])
        self.assertIn("图片已生成并发送给用户", good[0])
        self.assertEqual(plugin._queue.submitted, 1)

    async def test_tool_can_be_disabled_from_settings(self):
        plugin = self.make_plugin(enable_llm_tool=False)
        event = FakeEvent()
        results = await collect(plugin.NAI_Generate_Image(event, prompt="1girl"))
        self.assertIn("enable_llm_tool", results[0])
        self.assertEqual(plugin._queue.submitted, 0)
        self.assertEqual(event.sent, [])

    async def test_prompt_only_mode_returns_prompt_without_drawing(self):
        plugin = self.make_plugin(image_mode="prompt_only")
        event = FakeEvent()
        results = await collect(
            plugin.NAI_Generate_Image(event, prompt="1girl, solo, monochrome, speech bubble")
        )
        self.assertIn("1girl, solo, monochrome, speech bubble", results[0])
        self.assertEqual(plugin._queue.submitted, 0)
        self.assertEqual(event.sent, [])

    async def test_conditional_negatives_follow_the_prompt(self):
        plugin = self.make_plugin()
        event = FakeEvent()
        await collect(
            plugin.NAI_Generate_Image(
                event,
                prompt='x-ray view, cross-section, "An empty white speech bubble with no text in it."',
                no_sex=True,
            )
        )
        negative = self.client.calls[0]["negative"]
        for expected in (NEG_BUBBLE, NEG_NO_SEX, NEG_XRAY):
            self.assertIn(expected, negative)

    async def test_color_switch_drops_colorful_guard(self):
        plugin = self.make_plugin()
        event = FakeEvent()
        await collect(plugin.NAI_Generate_Image(event, prompt="1girl, solo, vivid color", color=True))
        negative = self.client.calls[0]["negative"]
        self.assertNotIn("colorful", negative)
        self.assertIn("loli, child, aged down", negative)

    async def test_quota_precheck_blocks_before_queue(self):
        plugin = self.make_plugin()
        self.client.balance = 0
        event = FakeEvent()
        results = await collect(plugin.NAI_Generate_Image(event, prompt="1girl", size="4K竖图"))
        self.assertIn("额度不足", results[0])
        self.assertEqual(plugin._queue.submitted, 0)

    async def test_failed_attempt_does_not_retry_in_same_message(self):
        plugin = self.make_plugin()
        self.client.balance = 0
        event = FakeEvent()
        first = await collect(plugin.NAI_Generate_Image(event, prompt="1girl"))
        second = await collect(plugin.NAI_Generate_Image(event, prompt="1girl"))
        self.assertIn("生成失败", first[0])
        self.assertIn("本轮消息已经执行过一次", second[0])

    async def test_page_tool_assembles_layout_and_bubble_negative(self):
        plugin = self.make_plugin()
        await plugin._character_store().put(
            "alice",
            "兔耳娘",
            {
                "ref": "the rabbit-eared woman",
                "look": "adult woman, rabbit ears",
                "uc": "fox ears",
            },
        )
        event = FakeEvent()
        panels = [
            {"no": 1, "action": "the rabbit-eared woman standing in the rain", "shot": "wide shot"},
            {"no": 2, "action": "close-up of her face", "dialogue": True, "sfx": "ザアッ"},
        ]
        results = await collect(
            plugin.NAI_Generate_Comic_Page(
                event,
                layout="两格上下",
                panels=json.dumps(panels, ensure_ascii=False),
                character="兔耳娘",
            )
        )
        self.assertIn("图片已生成并发送给用户", results[0])
        sent = self.client.calls[0]
        self.assertIn("two panels stacked one above the other", sent["tag"])
        self.assertIn("Panel 1 (top)", sent["tag"])
        self.assertIn("Panel 2 (bottom)", sent["tag"])
        self.assertNotIn("【", sent["tag"])
        self.assertIn(NEG_BUBBLE, sent["negative"])
        self.assertIn("fox ears", sent["negative"])

    async def test_page_tool_rejects_bad_panels(self):
        plugin = self.make_plugin()
        event = FakeEvent()
        broken = await collect(
            plugin.NAI_Generate_Comic_Page(event, layout="四格", panels="not-json", character="x")
        )
        wrong_count = await collect(
            plugin.NAI_Generate_Comic_Page(
                event, layout="四格", panels=json.dumps([{"no": 1, "action": "a"}]), character="x"
            )
        )
        unknown_layout = await collect(
            plugin.NAI_Generate_Comic_Page(event, layout="七格", panels="[]", character="x")
        )
        self.assertIn("不是合法 JSON", broken[0])
        self.assertIn("参数错误", wrong_count[0])
        self.assertIn("未知版式", unknown_layout[0])
        self.assertEqual(plugin._queue.submitted, 0)

    async def test_list_characters_returns_cards_and_options(self):
        plugin = self.make_plugin()
        await plugin._character_store().put(
            "alice", "兔耳娘", {"ref": "the rabbit-eared woman", "look": "adult woman, rabbit ears"}
        )
        event = FakeEvent()
        results = await collect(plugin.NAI_List_Characters(event))
        payload = json.loads(results[0])
        self.assertEqual(payload["characters"][0]["name"], "兔耳娘")
        self.assertIn("四格", payload["layouts"])
        self.assertIn("doujin", payload["presets"])

    async def test_chat_commands_share_the_same_artist_and_negative(self):
        plugin = self.make_plugin()
        event = FakeEvent()
        draw_results = await collect(plugin.draw(event, "1girl, solo, monochrome"))
        self.assertEqual(draw_results[0][1], "任务已提交到本机队列，预计费用 1 点。")
        self.assertEqual(self.client.calls[0]["artist"], ARTIST_PRESETS["doujin"])
        self.assertIn("colorful", self.client.calls[0]["negative"])

        await plugin._character_store().put(
            "alice", "兔耳娘", {"ref": "the rabbit-eared woman", "look": "adult woman, rabbit ears"}
        )
        page_event = FakeEvent()
        payload = {
            "character": "兔耳娘",
            "layout": "两格上下",
            "panels": [
                {"no": 1, "action": "the rabbit-eared woman standing"},
                {"no": 2, "action": "close-up of her face", "dialogue": True},
            ],
        }
        await collect(plugin.page(page_event, json.dumps(payload, ensure_ascii=False)))
        page_call = self.client.calls[-1]
        self.assertIn(NEG_BUBBLE, page_call["negative"])
        self.assertIn("two panels stacked", page_call["tag"])


if __name__ == "__main__":
    unittest.main()
