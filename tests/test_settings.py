import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SettingsSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

    def test_every_runtime_config_key_has_schema_entry(self):
        expected = {
            "station_base", "image_mode", "user_key", "character_cards",
            "queue_timeout", "max_queue", "daily_quota", "default_model",
            "default_artist_preset", "custom_artist", "send_preview", "bw_default",
            "default_layout", "default_explicit", "default_behavior_tags", "no_sex_default",
            "default_size", "default_steps", "quota_precheck",
        }
        self.assertTrue(expected.issubset(self.schema.keys()))
        self.assertNotIn("fallback_key", self.schema)
        self.assertNotIn("user_key_max_length", self.schema)
        self.assertNotIn("character_look_max_length", self.schema)
        self.assertNotIn("max_characters", self.schema)

    def test_per_page_defaults_are_documented_in_schema(self):
        self.assertFalse(self.schema["no_sex_default"]["default"])
        self.assertEqual(self.schema["default_layout"]["default"], "四格")
        self.assertFalse(self.schema["default_explicit"]["default"])
        self.assertEqual(self.schema["default_behavior_tags"]["default"], "")


if __name__ == "__main__":
    unittest.main()
