"""IR 模型的离线兼容性回归：不调用任何模型或网络。"""
from __future__ import annotations

import unittest

from backend.models.ir import DesignIR


class IRNormalizationTests(unittest.TestCase):
    def test_common_model_variants_are_normalized_without_losing_the_ir(self):
        ir = DesignIR.model_validate({
            "device_name": "测试装置",
            "design_intent": "验证常见字段变体",
            "overall_dims": {"length": 320, "width": 180, "height": 95, "unit": "mm"},
            "standard_parts": [
                {"name": "GB/T 5783 M8x25", "category": "bolt", "quantity": 4},
                {"category": "washer", "quantity": 4},
            ],
            "open_questions": ["板厚在图中未标注"],
            "parts": [{
                "part_id": "P-001", "name": "底板", "features": [],
                "provenance": "明细表第 1 项及爆炸图标注",
            }, {
                "part_id": "P-002", "name": "接头", "features": [
                    {"type": "cylinder", "diameter": 6, "length": 20},
                    {"type": "box", "length": 60, "width": 60, "thickness": 20},
                ],
            }],
        })

        self.assertEqual(ir.overall_dims, "320 × 180 × 95 mm")
        self.assertEqual(ir.standard_parts[0].spec, "GB/T 5783 M8x25")
        self.assertEqual(ir.standard_parts[1].spec, "washer")
        self.assertEqual(ir.open_questions[0].field, "待确认项")
        self.assertEqual(ir.open_questions[0].reason, "板厚在图中未标注")
        self.assertEqual(ir.parts[0].provenance.note, "明细表第 1 项及爆炸图标注")
        self.assertEqual(ir.parts[1].features[0].height, 20)
        self.assertEqual(ir.parts[1].features[1].height, 20)


class StrListCoercionTests(unittest.TestCase):
    """`List[str]` 字段收到单个字符串时不能让整份结果作废。

    实际发生过：模型只想说一条假设，于是给了
    `"assumptions": "材料按家电常规选…"` 而不是 `["…"]`，pydantic 判 list_type
    失败，零件、尺寸、证据台账全都解析对了却一起被丢掉，还白花一次模型钱
    （claude_client 带着报错重试一次，模型照旧给字符串）。
    """

    BASE = {"device_name": "测试装置", "design_intent": "验证容错"}

    def ir(self, assumptions):
        return DesignIR.model_validate({**self.BASE, "assumptions": assumptions})

    def test_single_string_becomes_one_item(self):
        text = "材料按家电常规选用冷轧板，文档未给出牌号"
        self.assertEqual(self.ir(text).assumptions, [text])

    def test_list_input_is_untouched(self):
        self.assertEqual(self.ir(["甲", "乙"]).assumptions, ["甲", "乙"])

    def test_none_and_blank_become_empty(self):
        self.assertEqual(self.ir(None).assumptions, [])
        self.assertEqual(self.ir("   ").assumptions, [])
        self.assertEqual(self.ir(["", "  ", "有效"]).assumptions, ["有效"])

    def test_wrapped_items_are_flattened(self):
        """模型偶尔包一层 [{"assumption": "…"}]；字段是 List[str]，没有别处可放。"""
        self.assertEqual(self.ir([{"assumption": "按常规选材"}]).assumptions, ["按常规选材"])

    def test_json_schema_is_unchanged(self):
        """给模型看的 schema 必须还是 array of string —— 这是容错，不是放宽约定。"""
        field = DesignIR.model_json_schema()["properties"]["assumptions"]
        self.assertEqual(field["type"], "array")
        self.assertEqual(field["items"], {"type": "string"})

    def test_every_str_list_field_is_tolerant(self):
        """新加的 List[str] 字段容易漏掉容错，这里全量扫一遍。"""
        import importlib
        import pkgutil
        import typing

        import backend.models as models_pkg
        from backend.models.coercion import as_str_list

        offenders = []
        for info in pkgutil.iter_modules(models_pkg.__path__):
            module = importlib.import_module(f"backend.models.{info.name}")
            for name in dir(module):
                model = getattr(module, name)
                if not (isinstance(model, type) and hasattr(model, "model_fields")):
                    continue
                for field_name, field in model.model_fields.items():
                    if field.annotation != typing.List[str]:
                        continue
                    metadata = getattr(field, "metadata", []) or []
                    if not any(getattr(m, "func", None) is as_str_list for m in metadata):
                        offenders.append(f"{info.name}.{name}.{field_name}")
        self.assertEqual(sorted(set(offenders)), [],
                         "这些字段还是裸 List[str]，请改用 StrList")


if __name__ == "__main__":
    unittest.main()
