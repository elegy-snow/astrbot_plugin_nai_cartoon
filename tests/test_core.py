import asyncio
import json
import unittest

from core.character_store import CharacterStore, CharacterStoreError
from core.placeholders import PlaceholderError, replace_placeholders
from core.pricing import cost_for_size
from core.prompt_builder import NEG_BASE, NEG_BUBBLE, NEG_NO_SEX, base_negative, build_page_prompt


class PricingTests(unittest.TestCase):
    def test_step_thresholds_for_standard_model(self):
        expected = {28: 1, 29: 6, 35: 6, 36: 8, 45: 8, 46: 10, 50: 10}
        for steps, cost in expected.items():
            with self.subTest(steps=steps):
                self.assertEqual(cost_for_size("竖图", "nai-diffusion-4-5-full", steps), cost)

    def test_size_and_model_interaction(self):
        self.assertEqual(cost_for_size("竖图", "nai-diffusion-5-full", 28), 8)
        self.assertEqual(cost_for_size("2K竖图", "nai-diffusion-5-full", 28), 15)
        self.assertEqual(cost_for_size("4K横图", "nai-diffusion-4-5-full", 46), 33)
        self.assertEqual(cost_for_size("竖图", "nai-diffusion-4-5-full", 0), 1)
        self.assertEqual(cost_for_size("竖图", "nai-diffusion-4-5-full", 500), 10)


class PlaceholderTests(unittest.TestCase):
    def setUp(self):
        self.character = {
            "slot": 1,
            "name_zh": "兔耳娘",
            "ref": "the rabbit-eared woman",
            "tag": "rabbit-eared woman",
            "look": "monochrome, greyscale, adult woman, rabbit ears",
            "parts": {"ears": "her long rabbit ears"},
        }

    def test_replaces_all_supported_tokens(self):
        result = replace_placeholders(
            "【1:look】, 【1】, 【1.ears】, 【1:name】, 【style】, 【quality】",
            [self.character],
            style_tags="monochrome, manga",
            quality_tags="best quality",
        )
        self.assertIn("the rabbit-eared woman, rabbit-eared woman, monochrome", result)
        self.assertIn("her long rabbit ears", result)
        self.assertIn("rabbit-eared", result)
        self.assertIn("monochrome, manga", result)
        self.assertIn("best quality", result)
        self.assertNotIn("【", result)

    def test_missing_character_or_unknown_placeholder_fails(self):
        with self.assertRaises(PlaceholderError):
            replace_placeholders("【2】", [self.character])
        with self.assertRaises(PlaceholderError):
            replace_placeholders("【unknown】", [self.character])

    def test_character_box_mode_uses_ref_only(self):
        result = replace_placeholders("【1:look】", [self.character], char_boxes=True)
        self.assertEqual(result, "the rabbit-eared woman")


class PromptBuilderTests(unittest.TestCase):
    character = {
        "slot": 1,
        "name_zh": "兔耳娘",
        "ref": "the rabbit-eared woman",
        "look": "monochrome, greyscale, adult woman, rabbit ears",
        "uc": "fox ears, fox tail",
        "parts": {},
    }

    def test_page_prompt_and_conditional_negatives(self):
        prompt, negative = build_page_prompt(
            layout="两格上下",
            panels=[
                {"no": 1, "action": "close-up portrait"},
                {"no": 2, "action": "looking toward the viewer", "dialogue": "hello"},
            ],
            characters=[self.character],
        )
        self.assertIn("two panels stacked one above the other", prompt)
        self.assertIn("Panel 1 (top)", prompt)
        self.assertIn("monochrome, greyscale, comic", prompt)
        self.assertIn(NEG_BASE, negative)
        self.assertIn("fox ears, fox tail", negative)
        self.assertIn(NEG_BUBBLE, negative)
        self.assertNotIn("【", prompt)

    def test_layout_mismatch_fails(self):
        with self.assertRaises(ValueError):
            build_page_prompt(layout="四格", panels=[{"action": "one panel"}], characters=[self.character])

    def test_bw_switch_changes_style_and_color_negative(self):
        panels = [{"action": "adult woman standing"}]
        prompt_bw, negative_bw = build_page_prompt(
            layout="整页大格", panels=panels, characters=[self.character], bw=True
        )
        prompt_color, negative_color = build_page_prompt(
            layout="整页大格", panels=panels, characters=[self.character], bw=False
        )
        self.assertNotEqual(prompt_bw, prompt_color)
        self.assertIn("monochrome, greyscale", prompt_bw)
        self.assertNotIn("monochrome, greyscale", prompt_color)
        self.assertNotIn("black and white", prompt_color.casefold())
        self.assertNotIn("black and white", prompt_bw.casefold())
        self.assertIn("colorful", negative_bw)
        self.assertNotIn("colorful", negative_color)
        self.assertIn("bad anatomy", negative_color)
        self.assertIn("loli, child, aged down", negative_color)
        self.assertIn("bad anatomy", base_negative(False))
        self.assertNotIn("colorful", base_negative(False))

    def test_no_sex_negative_is_opt_in(self):
        _, negative = build_page_prompt(
            layout="整页大格",
            panels=[{"action": "adult woman holding an object"}],
            characters=[self.character],
            no_sex=True,
        )
        self.assertIn(NEG_NO_SEX, negative)


class FakeKVPlugin:
    def __init__(self):
        self.values = {}

    async def get_kv_data(self, key, default):
        return self.values.get(key, default)

    async def put_kv_data(self, key, value):
        self.values[key] = value


class CharacterStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_card_accepts_single_object_and_list(self):
        plugin = FakeKVPlugin()
        card = {"ref": "configured woman", "look": "adult woman, long hair"}
        single = CharacterStore(plugin, json.dumps({"name_zh": "Single", **card}))
        listed = CharacterStore(plugin, json.dumps([{"name": "Listed", **card}]))
        self.assertEqual((await single.get("user", "Single"))["ref"], "configured woman")
        self.assertEqual((await listed.get("user", "Listed"))["ref"], "configured woman")

    async def test_settings_cards_are_available_and_kv_card_overrides(self):
        plugin = FakeKVPlugin()
        configured = {"Card": {"ref": "configured woman", "look": "adult woman, configured hair"}}
        store = CharacterStore(plugin, configured)
        configured_card = await store.get("alice", "Card")
        self.assertEqual(configured_card["ref"], "configured woman")
        self.assertEqual(await store.list("alice"), ["Card"])
        await store.put("alice", "Card", {"ref": "kv woman", "look": "adult woman, kv hair"})
        self.assertEqual((await store.get("alice", "Card"))["ref"], "kv woman")

    async def test_descriptions_are_not_truncated(self):
        plugin = FakeKVPlugin()
        store = CharacterStore(plugin)
        look = "adult woman, " + "long hair, " * 300
        part = "distinctive feature " * 50
        await store.put("alice", "LongDescription", {
            "ref": "the adult woman", "look": look, "parts": {"detail": part}
        })
        stored = await store.get("alice", "LongDescription")
        self.assertEqual(stored["look"], look.strip())
        self.assertEqual(stored["parts"]["detail"], part.strip())

    async def test_crud_is_scoped_per_user_and_requires_adult_detail(self):
        plugin = FakeKVPlugin()
        store = CharacterStore(plugin)
        card = {"ref": "the adult woman", "look": "adult woman, long hair", "parts": {"ears": "her rabbit ears"}}
        await store.put("alice", "兔耳娘", card)
        self.assertEqual(await store.list("alice"), ["兔耳娘"])
        self.assertEqual((await store.get("alice", "兔耳娘"))["slot"], 1)
        self.assertEqual(await store.list("bob"), [])
        self.assertTrue(await store.delete("alice", "兔耳娘"))
        self.assertFalse(await store.delete("alice", "兔耳娘"))
        with self.assertRaises(CharacterStoreError):
            await store.put("alice", "未成年", {"ref": "person", "look": "long hair"})


if __name__ == "__main__":
    unittest.main()
