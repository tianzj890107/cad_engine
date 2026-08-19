"""行业模板（半导体 / 电池 / 电器）的一致性回归测试。

同一套 Section C 字段定义存在于两处实现：Python 侧 services/industry_templates.py
（供 AI 抽取、PDF、完整性检查使用）与前端 requirement-create.js 的 RC_*_SPECS
（供表单渲染使用）。两边键名一旦漂移，就会出现「页面填了、PDF 空白」或
「AI 推荐的字段表单里没有」这类难查的问题，所以在这里直接比对。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from backend.services import industry_templates, requirement_extract

ROOT = Path(__file__).resolve().parents[1]
CREATE_JS = (ROOT / "frontend" / "requirement-create.js").read_text(encoding="utf-8")
HOME_JS = (ROOT / "frontend" / "home.js").read_text(encoding="utf-8")
CONFIRM_JS = (ROOT / "frontend" / "requirement-confirm-page.js").read_text(encoding="utf-8")

JS_SPEC_CONSTANTS = {
    "semiconductor": "RC_SEMI_SPECS",
    "battery": "RC_BATTERY_SPECS",
    "appliance": "RC_APPLIANCE_SPECS",
}


# --------------------------------------------------------------------------- #
# 极简 JS 字面量解析：只够读出 RC_*_SPECS 的字段定义，不做通用 JS 解析。
# --------------------------------------------------------------------------- #
def _split_top_level(body: str, open_ch: str = "[", close_ch: str = "]") -> list[str]:
    """按最外层括号切分，忽略嵌套（选项数组）与字符串内的括号。"""
    entries: list[str] = []
    depth = 0
    start = 0
    in_string = False
    for index, char in enumerate(body):
        if char == "'":
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == open_ch:
            if depth == 0:
                start = index + 1
            depth += 1
        elif char == close_ch:
            depth -= 1
            if depth == 0:
                entries.append(body[start:index])
    return entries


def _split_commas(entry: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    in_string = False
    current = ""
    for char in entry:
        if char == "'":
            in_string = not in_string
            current += char
            continue
        if not in_string:
            if char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
            elif char == "," and depth == 0:
                parts.append(current.strip())
                current = ""
                continue
        current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def parse_js_spec(constant: str) -> list[tuple[str, str, str, bool, str, list[str]]]:
    """返回 [(section, label, key, required, kind, option_values)]。"""
    match = re.search(rf"const {constant}=\{{(.*?)\n\}};", CREATE_JS, re.S)
    assert match, f"未在 requirement-create.js 中找到 {constant}"
    parsed: list[tuple[str, str, str, bool, str, list[str]]] = []
    for line in match.group(1).splitlines():
        line = line.strip()
        section_match = re.match(r"'(\d\.\d)':\{title:'([^']*)',fields:\[(.*)\]\},?$", line)
        if not section_match:
            continue
        section = section_match.group(1)
        for entry in _split_top_level(section_match.group(3)):
            parts = _split_commas(entry)
            label = parts[0].strip("'")
            key = parts[1].strip("'")
            required = len(parts) > 3 and parts[3] == "true"
            kind = parts[4].strip("'") if len(parts) > 4 else "field"
            options: list[str] = []
            if len(parts) > 5:
                inner = parts[5].strip()
                if inner.startswith("[") and inner.endswith("]"):
                    inner = inner[1:-1]   # 去掉最外层数组，留下逐项
                if kind == "tags":        # 标签是扁平数组：['压缩机','风机']
                    options = [item.strip().strip("'") for item in _split_commas(inner)]
                else:                     # 下拉是二元组数组：[['value','中文']]
                    options = [
                        _split_commas(pair)[0].strip().strip("'")
                        for pair in _split_top_level(inner)
                    ]
            parsed.append((section, label, key, required, kind, options))
    return parsed


class IndustryTemplateParityTests(unittest.TestCase):
    def test_parser_reads_the_appliance_template(self):
        # 解析器本身出错会让下面所有比对变成空断言，先确认它真的读到了东西。
        parsed = parse_js_spec("RC_APPLIANCE_SPECS")
        self.assertGreaterEqual(len(parsed), 20)
        self.assertIn(("3.1", "产品品类", "appliance_category", True, "select"),
                      [item[:5] for item in parsed])

    def test_field_keys_and_order_match_python_templates(self):
        for industry, constant in JS_SPEC_CONSTANTS.items():
            with self.subTest(industry=industry):
                js_keys = [item[2] for item in parse_js_spec(constant)]
                self.assertEqual(js_keys, industry_templates.field_keys(industry))

    def test_required_flags_match_python_templates(self):
        for industry, constant in JS_SPEC_CONSTANTS.items():
            with self.subTest(industry=industry):
                js_required = {item[2] for item in parse_js_spec(constant) if item[3]}
                self.assertEqual(js_required, industry_templates.required_keys(industry))

    def test_section_titles_match_python_templates(self):
        for industry, constant in JS_SPEC_CONSTANTS.items():
            with self.subTest(industry=industry):
                match = re.search(rf"const {constant}=\{{(.*?)\n\}};", CREATE_JS, re.S)
                js_titles = re.findall(r"'(\d\.\d)':\{title:'([^']*)'", match.group(1))
                py_titles = [(block.section, block.title)
                             for block in industry_templates.blocks(industry)]
                self.assertEqual(js_titles, py_titles)

    def test_field_labels_match_python_templates(self):
        for industry, constant in JS_SPEC_CONSTANTS.items():
            with self.subTest(industry=industry):
                js_labels = {item[2]: item[1] for item in parse_js_spec(constant)}
                self.assertEqual(js_labels, industry_templates.labels(industry))


class IndustryOptionTests(unittest.TestCase):
    def test_home_page_offers_exactly_the_supported_industries(self):
        picker = re.search(r'id="homeIndustry".*?</select>', HOME_JS, re.S)
        self.assertIsNotNone(picker, "首页行业选择器不存在")
        options = re.findall(r'<option value="([^"]+)">', picker.group(0))
        self.assertEqual(options, list(industry_templates.INDUSTRIES))
        # 「AI 自动生成」已被电器行业取代，首页不应再出现该入口。
        self.assertNotIn("flexible", options)
        self.assertIn("appliance", options)

    def test_appliance_select_options_are_registered_as_enums(self):
        """页面下拉的 value 必须在服务端枚举里，否则 AI 推荐值会被静默丢弃。"""
        for section, label, key, required, kind, options in parse_js_spec("RC_APPLIANCE_SPECS"):
            if kind not in ("select", "tags"):
                continue
            with self.subTest(field=key):
                self.assertIn(key, requirement_extract._RECOMMENDATION_ENUMS)
                registered = requirement_extract._RECOMMENDATION_ENUMS[key]
                # select 的首项是「请选择」占位，不入枚举。
                values = [item for item in options if item and item != "请选择"]
                self.assertTrue(values)
                for value in values:
                    self.assertIn(value, registered)

    def test_every_enum_has_a_usable_default(self):
        for key in ("appliance_category", "energy_efficiency_grade", "housing_material",
                    "surface_process", "certification_region", "core_components",
                    "forming_process", "safety_standard"):
            with self.subTest(field=key):
                default = requirement_extract._RECOMMENDATION_ENUM_DEFAULTS[key]
                self.assertIn(default, requirement_extract._RECOMMENDATION_ENUMS[key])

    def test_confirm_page_can_label_every_appliance_field(self):
        for key in industry_templates.field_keys("appliance"):
            with self.subTest(field=key):
                self.assertIn(f"{key}:'", CONFIRM_JS)


class IndustryBehaviourTests(unittest.TestCase):
    def test_normalize_falls_back_to_default(self):
        self.assertEqual(industry_templates.normalize("appliance"), "appliance")
        self.assertEqual(industry_templates.normalize("APPLIANCE"), "appliance")
        self.assertEqual(industry_templates.normalize(""), "semiconductor")
        # 已下线的历史模板不是受支持模板，取字段时回落到默认。
        self.assertEqual(industry_templates.normalize("flexible"), "semiconductor")
        self.assertEqual(industry_templates.label("flexible"), "灵活")

    def test_appliance_fields_are_extractable_and_required_sets_are_scoped(self):
        extractable = requirement_extract._extractable_fields_for_industry("appliance")
        for key in industry_templates.field_keys("appliance"):
            self.assertIn(key, extractable)
        # 行业之间不串味：电器需求不该被要求填电芯型号或晶圆尺寸。
        required = requirement_extract._required_recommendation_fields_for_industry("appliance")
        self.assertNotIn("battery_model", required)
        self.assertNotIn("wafer_size", required)
        self.assertIn("appliance_category", required)
        self.assertIn("safety_standard", required)

    def test_model_output_keys_outside_the_form_are_dropped(self):
        for key in industry_templates.all_field_keys():
            self.assertIn(key, requirement_extract.EXTRACTABLE_FIELDS)

    def test_file_block_section_follows_template_length(self):
        self.assertEqual(industry_templates.FILE_BLOCK_SECTION["semiconductor"], "3.4")
        self.assertEqual(industry_templates.FILE_BLOCK_SECTION["battery"], "3.5")
        self.assertEqual(industry_templates.FILE_BLOCK_SECTION["appliance"], "3.5")

    def test_section_checks_cover_every_block(self):
        checks = industry_templates.section_checks("appliance")
        labels = [item[0] for item in checks]
        self.assertEqual(labels[0], "三、产品技术规格（Section C）")
        self.assertEqual(labels[1:], ["3.1 整机基础参数", "3.2 性能与可靠性参数",
                                      "3.3 结构与材料", "3.4 安规与认证"])
        for _label, fields, _ok in checks[1:]:
            for key in fields:
                self.assertIn(key, industry_templates.required_keys("appliance"))


class RequirementPdfTests(unittest.TestCase):
    def test_product_section_follows_the_selected_industry(self):
        from backend.services import requirement_pdf

        for industry in industry_templates.INDUSTRIES:
            with self.subTest(industry=industry):
                title, keys = requirement_pdf._product_section({"industry": industry})
                self.assertEqual(title, "三、产品技术规格")
                self.assertEqual(keys, industry_templates.field_keys(industry))

    def test_pdf_renders_appliance_values(self):
        from backend.services import requirement_pdf

        content = requirement_pdf.build_requirement_pdf({
            "requirement_no": "REQ-202608-1003", "title": "冰箱门体总成工艺评估",
            "status": "approved", "created_by": "工艺技术经理",
            "data": {"industry": "appliance", "appliance_category": "refrigerator",
                     "rated_power": "180 W", "safety_standard": "GB 4706.1,CCC"},
        }, {})
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertGreater(len(content), 2000)

    def test_legacy_flexible_draft_still_renders(self):
        """历史草稿的动态字段不在 data 顶层，PDF 仍须能取到值。"""
        from backend.services import requirement_pdf

        title, keys = requirement_pdf._product_section({
            "industry": "flexible",
            "flexible_spec": {"fields": [{"key": "custom_a", "label": "自定义项", "value": "X"}]},
        })
        self.assertEqual(keys, ["custom_a"])
        self.assertEqual(requirement_pdf._LABELS["custom_a"], "自定义项")


if __name__ == "__main__":
    unittest.main()
