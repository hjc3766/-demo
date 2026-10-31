"""utils/yamllite.py 的测试：零依赖 YAML 子集解析。

配置解析错比配置缺失更危险——所以这里既测"能读通"，也测"读不懂就报错"。
"""
from __future__ import annotations

import unittest

from asset_suggestion.utils.yamllite import YamlError, load_yaml


class TestYamlSubset(unittest.TestCase):
    def test_flat_mapping(self):
        data = load_yaml("a: 1\nb: two\nc: true\nd: null\n")
        self.assertEqual(data, {"a": 1, "b": "two", "c": True, "d": None})

    def test_nested_mapping(self):
        data = load_yaml("run:\n  db_path: data/x.db\n  seed_assets: 2000\n")
        self.assertEqual(data, {"run": {"db_path": "data/x.db", "seed_assets": 2000}})

    def test_block_list(self):
        data = load_yaml("scopes:\n  - 设施地图\n  - 照片\n")
        self.assertEqual(data, {"scopes": ["设施地图", "照片"]})

    def test_nested_mapping_with_list(self):
        """config/default.yaml 里 material_scopes 的形状。"""
        data = load_yaml(
            "material_scopes:\n"
            "  社区服务代表:\n"
            "    - 历史维修\n"
            "  维修协调员:\n"
            "    - 设施地图\n"
            "    - 验收凭证\n"
        )
        self.assertEqual(data["material_scopes"]["社区服务代表"], ["历史维修"])
        self.assertEqual(data["material_scopes"]["维修协调员"], ["设施地图", "验收凭证"])

    def test_comments_and_blank_lines(self):
        data = load_yaml("# 注释\n\na: 1   # 行内注释\n\nb: 2\n")
        self.assertEqual(data, {"a": 1, "b": 2})

    def test_hash_inside_quotes_is_kept(self):
        data = load_yaml('a: "带 # 号"\n')
        self.assertEqual(data, {"a": "带 # 号"})

    def test_flow_list(self):
        data = load_yaml("a: [1, 2, 3]\n")
        self.assertEqual(data, {"a": [1, 2, 3]})

    def test_float_and_negative(self):
        data = load_yaml("a: 0.15\nb: -3\n")
        self.assertEqual(data, {"a": 0.15, "b": -3})

    def test_bad_indent_raises(self):
        with self.assertRaises(YamlError):
            load_yaml("a: 1\n   b: 2\n")

    def test_missing_colon_raises(self):
        with self.assertRaises(YamlError):
            load_yaml("这不是映射项\n")

    def test_tab_indent_raises(self):
        with self.assertRaises(YamlError):
            load_yaml("a:\n\tb: 1\n")

    def test_empty_document(self):
        self.assertEqual(load_yaml(""), {})
        self.assertEqual(load_yaml("# 只有注释\n"), {})


if __name__ == "__main__":
    unittest.main()
