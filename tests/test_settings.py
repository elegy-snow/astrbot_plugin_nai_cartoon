import ast
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.config_utils import config_value


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

    def test_sensitive_and_json_settings_use_supported_string_type(self):
        self.assertEqual(self.schema["user_key"]["type"], "string")
        self.assertTrue(self.schema["user_key"]["secret"])
        self.assertEqual(self.schema["character_cards"]["type"], "string")

    def test_all_schema_types_match_known_supported_set(self):
        supported = {"string", "int", "float", "bool", "file"}
        for key, value in self.schema.items():
            with self.subTest(key=key):
                self.assertIn(value.get("type"), supported)

    def test_per_page_defaults_are_documented_in_schema(self):
        self.assertFalse(self.schema["no_sex_default"]["default"])
        self.assertEqual(self.schema["default_layout"]["default"], "四格")
        self.assertFalse(self.schema["default_explicit"]["default"])
        self.assertEqual(self.schema["default_behavior_tags"]["default"], "")

    def test_plugin_constructor_uses_injected_config_dict(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        plugin_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "NaiDoujinPlugin")
        init = next(node for node in plugin_class.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
        args = [arg.arg for arg in init.args.args]
        self.assertEqual(args, ["self", "context", "config"])
        self.assertEqual(len(init.args.defaults), 1, "config 应有默认值以便旧版 AstrBot 仍可加载")
        config_fn = next(node for node in plugin_class.body if isinstance(node, ast.FunctionDef) and node.name == "_config")
        config_source = ast.get_source_segment((ROOT / "main.py").read_text(encoding="utf-8"), config_fn)
        self.assertIn("_plugin_config", config_source)

    def test_get_config_is_only_used_as_fallback(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        plugin_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "NaiDoujinPlugin")
        config_fn = next(
            (node for node in plugin_class.body if isinstance(node, ast.FunctionDef) and node.name == "_config"), None
        )
        self.assertIsNotNone(config_fn)
        for node in ast.walk(plugin_class):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get_config":
                self.assertTrue(
                    config_fn.lineno <= node.lineno <= (config_fn.end_lineno or config_fn.lineno),
                    "get_config 只应出现在 _config 回退路径中",
                )

    def test_every_schema_setting_is_actually_read_by_code(self):
        sources = [(ROOT / "main.py").read_text(encoding="utf-8")]
        sources += [path.read_text(encoding="utf-8") for path in sorted((ROOT / "core").glob("*.py"))]
        code = "\n".join(sources)
        for key in self.schema:
            with self.subTest(key=key):
                self.assertIn(f'"{key}"', code, f"设置项 {key} 在设置页中，但代码从未读取")


class RuntimeSettingsReadTests(unittest.TestCase):
    def test_reads_dict_and_nested_setting_value(self):
        config = {"user_key": "secret", "foo": SimpleNamespace(value="bar")}
        self.assertEqual(config_value(config, "user_key", ""), "secret")
        self.assertEqual(config_value(config, "foo", ""), "bar")

    def test_reads_mapping_and_attribute_config_objects(self):
        class MappingConfig(dict):
            pass

        self.assertEqual(config_value(MappingConfig(user_key="secret"), "user_key", ""), "secret")
        self.assertEqual(config_value(SimpleNamespace(user_key="secret"), "user_key", ""), "secret")


if __name__ == "__main__":
    unittest.main()
