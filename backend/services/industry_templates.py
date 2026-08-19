"""行业规格模板（需求单 Section C）的单一事实源。

同一套字段定义要被四个地方使用，各自散一份必然漂移：
  - 1.1 创建页表单（frontend/requirement-create.js 的 RC_*_SPECS，键名必须与此处一致）
  - AI 需求抽取的可抽取/必填字段集合（services/requirement_extract.py）
  - 需求单 PDF 的「三、产品技术规格」章节（services/requirement_pdf.py）
  - 需求确定性完整性检查（main.py::_requirement_precheck）

因此这里只描述「有哪些字段、叫什么、必填与否」，渲染方式各端自理。
tests/test_industry_templates.py 会比对前端 JS 中的键名，防止两边跑偏。
"""
from __future__ import annotations

from typing import Iterable, NamedTuple

# 平台当前支持的行业模板。与 storage/da_mock.py 的 INDUSTRIES 键保持一致，
# 需求单选的行业能直接对上知识库里那一套物料/工序/费率。
INDUSTRIES: tuple[str, ...] = ("semiconductor", "battery", "appliance")
DEFAULT_INDUSTRY = "semiconductor"

INDUSTRY_LABELS = {
    "semiconductor": "半导体",
    "battery": "电池",
    "appliance": "电器",
    # 历史草稿可能仍是「灵活（AI 生成字段）」模板。它已不在可选项里，
    # 但旧需求单必须还能打开，所以保留标签与渲染兜底。
    "flexible": "灵活",
}


class SpecField(NamedTuple):
    key: str
    label: str
    required: bool = False


class SpecBlock(NamedTuple):
    section: str      # 3.1 / 3.2 / ...
    title: str
    fields: tuple[SpecField, ...]


# --------------------------------------------------------------------------- #
# 半导体（静电吸盘等精密零部件）
# --------------------------------------------------------------------------- #
SEMICONDUCTOR_SPEC: tuple[SpecBlock, ...] = (
    SpecBlock("3.1", "基础参数", (
        SpecField("product_name", "产品名称", True),
        SpecField("product_model", "产品型号"),
        SpecField("wafer_size", "晶圆尺寸", True),
        SpecField("chuck_type", "静电吸盘类型", True),
        SpecField("temperature_zones", "温区数量", True),
        SpecField("ceramic_material", "陶瓷基体材料", True),
        SpecField("electrode_material", "电极材料"),
        SpecField("base_material", "金属基座材质", True),
        SpecField("product_weight", "产品重量"),
        SpecField("overall_dimensions", "外形尺寸"),
    )),
    SpecBlock("3.2", "精度与性能参数", (
        SpecField("ttv", "平面度（TTV）要求", True),
        SpecField("roughness", "表面粗糙度（Ra）要求", True),
        SpecField("micro_hole_diameter", "微孔孔径", True),
        SpecField("micro_hole_diameter_tolerance", "微孔孔径公差"),
        SpecField("micro_hole_depth_tolerance", "微孔深度公差"),
        SpecField("mesa_height", "微凸台（mesa）高度"),
        SpecField("adsorption_uniformity", "吸附力均匀性", True),
        SpecField("temperature_range", "工作温度范围"),
        SpecField("max_voltage", "最高使用电压"),
        SpecField("leakage_current", "漏电流要求"),
        SpecField("helium_leak_rate", "氦气漏率要求", True),
        SpecField("cleanliness", "洁净度等级", True),
        SpecField("service_life", "使用寿命要求"),
    )),
    SpecBlock("3.3", "应用场景", (
        SpecField("target_equipment", "目标设备类型", True),
        SpecField("process_stage", "适用工艺环节", True),
        SpecField("vacuum_environment", "真空环境要求"),
        SpecField("heating", "是否含加热功能"),
    )),
)

# --------------------------------------------------------------------------- #
# 电池（电芯 / 模组）
# --------------------------------------------------------------------------- #
BATTERY_SPEC: tuple[SpecBlock, ...] = (
    SpecBlock("3.1", "基本电性能参数", (
        SpecField("battery_model", "电芯型号", True),
        SpecField("cathode_material", "正极材料", True),
        SpecField("anode_material", "负极材料", True),
        SpecField("nominal_voltage", "标称电压", True),
        SpecField("gravimetric_energy_density", "质量能量密度"),
        SpecField("volumetric_energy_density", "体积能量密度"),
        SpecField("dcir", "直流内阻（DCIR）"),
    )),
    SpecBlock("3.2", "安全与可靠性参数", (
        SpecField("battery_operating_temperature", "工作温度范围"),
        SpecField("thermal_runaway_temperature", "热失控触发温度"),
        SpecField("crush_puncture_safety", "挤压/针刺安全"),
        SpecField("cycle_life", "循环寿命"),
        SpecField("calendar_life", "日历寿命"),
    )),
    SpecBlock("3.3", "核心工艺特点", (
        SpecField("stacking_process", "叠片工艺"),
        SpecField("minimalist_packaging", "极简封装"),
        SpecField("battery_process_other", "其他核心工艺特点"),
    )),
    SpecBlock("3.4", "形状与尺寸", (
        SpecField("vda_dimensions", "VDA标准尺寸"),
        SpecField("slim_cell_dimensions", "长薄化尺寸"),
        SpecField("battery_form_factor", "形状"),
    )),
)

# --------------------------------------------------------------------------- #
# 电器（白电整机与关键部件）
#
# 字段取向与前两个行业不同:半导体看精度、电池看电性能,电器看的是
# 「能效 + 安规认证 + 成型工艺」—— 这三项决定整机能不能上市、成本落在哪里。
# --------------------------------------------------------------------------- #
APPLIANCE_SPEC: tuple[SpecBlock, ...] = (
    SpecBlock("3.1", "整机基础参数", (
        SpecField("appliance_category", "产品品类", True),
        SpecField("appliance_model", "产品型号"),
        SpecField("rated_voltage", "额定电压/频率", True),
        SpecField("rated_power", "额定功率", True),
        SpecField("energy_efficiency_grade", "能效等级", True),
        SpecField("appliance_dimensions", "整机外形尺寸"),
        SpecField("appliance_weight", "整机净重"),
    )),
    SpecBlock("3.2", "性能与可靠性参数", (
        SpecField("key_performance", "关键性能指标", True),
        SpecField("noise_limit", "噪声限值"),
        SpecField("standby_power", "待机功耗"),
        SpecField("appliance_operating_temperature", "工作环境温度"),
        SpecField("appliance_service_life", "整机使用寿命"),
        SpecField("reliability_test", "可靠性试验要求"),
    )),
    SpecBlock("3.3", "结构与材料", (
        SpecField("housing_material", "主体结构材料", True),
        SpecField("surface_process", "外观表面工艺"),
        SpecField("insulation_requirement", "保温与密封要求"),
        SpecField("core_components", "核心部件", True),
        SpecField("forming_process", "关键成型工艺", True),
    )),
    SpecBlock("3.4", "安规与认证", (
        SpecField("safety_standard", "安规标准", True),
        SpecField("hipot_requirement", "耐压测试要求"),
        SpecField("ground_resistance", "接地电阻要求"),
        SpecField("emc_requirement", "EMC 要求"),
        SpecField("certification_region", "认证区域"),
    )),
)

SPECS: dict[str, tuple[SpecBlock, ...]] = {
    "semiconductor": SEMICONDUCTOR_SPEC,
    "battery": BATTERY_SPEC,
    "appliance": APPLIANCE_SPEC,
}

# 各行业 Section C 之后「图纸与技术资料」块的编号:模板块数 + 1。
FILE_BLOCK_SECTION = {key: f"3.{len(blocks) + 1}" for key, blocks in SPECS.items()}


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def normalize(industry: object) -> str:
    """把任意来源的行业值收敛成受支持的模板键。"""
    value = str(industry or "").strip().lower()
    return value if value in SPECS else DEFAULT_INDUSTRY


def label(industry: object) -> str:
    value = str(industry or "").strip().lower()
    return INDUSTRY_LABELS.get(value, INDUSTRY_LABELS[DEFAULT_INDUSTRY])


def blocks(industry: object) -> tuple[SpecBlock, ...]:
    return SPECS[normalize(industry)]


def field_keys(industry: object) -> list[str]:
    """按页面顺序返回该行业 Section C 的全部字段键。"""
    return [field.key for block in blocks(industry) for field in block.fields]


def required_keys(industry: object) -> set[str]:
    return {field.key for block in blocks(industry) for field in block.fields if field.required}


def labels(industry: object) -> dict[str, str]:
    return {field.key: field.label for block in blocks(industry) for field in block.fields}


def all_labels() -> dict[str, str]:
    """全行业字段标签合集,供 PDF / 确认页等需要跨行业展示的地方使用。"""
    merged: dict[str, str] = {}
    for industry in SPECS:
        merged.update(labels(industry))
    return merged


def all_field_keys() -> set[str]:
    return {key for industry in SPECS for key in field_keys(industry)}


def section_checks(industry: object) -> list[tuple[str, list[str], str]]:
    """需求完整性检查用的 (章节名, 必填字段, 通过语) 列表。"""
    industry = normalize(industry)
    spec = blocks(industry)
    head_fields = sorted(required_keys(industry) & {f.key for f in spec[0].fields})
    checks = [(
        "三、产品技术规格（Section C）", head_fields,
        f"{label(industry)}产品基础规格已录入",
    )]
    for block in spec:
        required = [field.key for field in block.fields if field.required]
        checks.append((f"{block.section} {block.title}", required, f"{block.title}已录入"))
    return checks


def iter_fields(industry: object) -> Iterable[SpecField]:
    for block in blocks(industry):
        yield from block.fields
