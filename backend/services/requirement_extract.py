"""技术文档 -> 1.1 工艺评估需求草稿的纯文本提取服务。

先在本地从 TXT/CSV/Markdown/PDF/DOCX 抽取文本，再调用 Qwen 的文本模型。
不把工程图或图片混入本请求，避免本来可用纯文本模型的场景被错误地切到 VL。
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable
from xml.etree import ElementTree

from ..models.workflow import RequirementDocumentExtraction, RequirementDynamicSpecField
from ..config import LLM_MAX_DOCUMENT_CHARS, LLM_MAX_DOCUMENTS, LLM_MAX_TOTAL_DOCUMENT_CHARS
# 走统一分派层，而不是直接绑死 qwen —— 直接 import 具体提供商的客户端，
# 就会绕过「模型设置」，出现"配了 opus5 却报 Qwen 调用失败"。
from . import llm_client

_TEXT_SUFFIXES = (".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".ini")
_MAX_DOCUMENTS = LLM_MAX_DOCUMENTS
_MAX_CHARS_PER_DOCUMENT = LLM_MAX_DOCUMENT_CHARS
_MAX_TOTAL_CHARS = LLM_MAX_TOTAL_DOCUMENT_CHARS

from . import industry_templates

# 与 1.1 表单字段一一对应；模型返回其它键会被服务端丢弃，防止污染业务数据。
EXTRACTABLE_FIELDS = {
    "priority", "bu", "disclosure", "description",
    "customer_industry", "account_manager", "final_customer_name",
    "transaction_customer_name", "project_name", "project_code",
    "project_manager", "technical_contact", "product_name", "product_model", "wafer_size",
    "chuck_type", "temperature_zones", "ceramic_material", "electrode_material",
    "base_material", "product_weight", "overall_dimensions", "ttv", "roughness",
    "micro_hole_diameter", "micro_hole_diameter_tolerance", "micro_hole_depth_tolerance",
    "mesa_height", "adsorption_uniformity", "temperature_range", "max_voltage",
    "leakage_current", "helium_leak_rate", "cleanliness", "service_life",
    "target_equipment", "process_stage", "vacuum_environment", "heating",
    "annual_forecast", "lifetime_forecast", "first_sample_due", "mass_production_due",
    "target_price", "competitors", "current_situation", "project_k0", "evaluation_due",
    "project_start_due", "milestones", "category_a", "category_b", "product_type",
    "complexity", "new_technology", "technology_source", "notes", "related_requirement",
    # 电池行业固定规格模板（1.1 Section C）。
    "battery_model", "cathode_material", "anode_material", "nominal_voltage",
    "gravimetric_energy_density", "volumetric_energy_density", "dcir",
    "battery_operating_temperature", "thermal_runaway_temperature", "crush_puncture_safety",
    "cycle_life", "calendar_life", "stacking_process", "minimalist_packaging",
    "battery_process_other", "vda_dimensions", "slim_cell_dimensions", "battery_form_factor",
    # 电器行业固定规格模板（1.1 Section C）。
    *industry_templates.field_keys("appliance"),
}

# 只有必填字段允许使用兜底推荐；非必填字段没有资料时保持空白。
_RECOMMENDATION_FALLBACK = "待人工确认"

_COMMON_RECOMMENDATION_FIELDS = {
    "priority", "bu", "disclosure", "description", "customer_industry",
    "account_manager", "final_customer_name", "transaction_customer_name",
    "project_name", "project_code", "project_manager", "technical_contact",
    "annual_forecast", "lifetime_forecast", "first_sample_due", "mass_production_due",
    "target_price", "competitors", "current_situation", "project_k0", "evaluation_due",
    "project_start_due", "milestones", "category_a", "category_b", "product_type",
    "complexity", "new_technology", "technology_source", "notes", "related_requirement",
}
_SEMICONDUCTOR_RECOMMENDATION_FIELDS = _COMMON_RECOMMENDATION_FIELDS | {
    "product_name", "product_model", "wafer_size", "chuck_type", "temperature_zones",
    "ceramic_material", "electrode_material", "base_material", "product_weight",
    "overall_dimensions", "ttv", "roughness", "micro_hole_diameter",
    "micro_hole_diameter_tolerance", "micro_hole_depth_tolerance", "mesa_height",
    "adsorption_uniformity", "temperature_range", "max_voltage", "leakage_current",
    "helium_leak_rate", "cleanliness", "service_life", "target_equipment", "process_stage",
    "vacuum_environment", "heating",
}
_BATTERY_RECOMMENDATION_FIELDS = _COMMON_RECOMMENDATION_FIELDS | {
    "battery_model", "cathode_material", "anode_material", "nominal_voltage",
    "gravimetric_energy_density", "volumetric_energy_density", "dcir",
    "battery_operating_temperature", "thermal_runaway_temperature", "crush_puncture_safety",
    "cycle_life", "calendar_life", "stacking_process", "minimalist_packaging",
    "battery_process_other", "vda_dimensions", "slim_cell_dimensions", "battery_form_factor",
}

_APPLIANCE_RECOMMENDATION_FIELDS = _COMMON_RECOMMENDATION_FIELDS | set(
    industry_templates.field_keys("appliance")
)

_COMMON_REQUIRED_RECOMMENDATION_FIELDS = {
    "requirement_type", "priority", "bu", "disclosure", "description",
    "customer_type", "customer_industry", "account_manager", "final_customer_name",
    "project_name", "project_code", "product_iteration", "annual_forecast",
    "first_sample_due", "mass_production_due", "evaluation_due", "category_a", "product_type",
}
_SEMICONDUCTOR_REQUIRED_RECOMMENDATION_FIELDS = _COMMON_REQUIRED_RECOMMENDATION_FIELDS | {
    "product_name", "wafer_size", "chuck_type", "temperature_zones", "ceramic_material",
    "base_material", "ttv", "roughness", "micro_hole_diameter", "adsorption_uniformity",
    "helium_leak_rate", "cleanliness", "target_equipment", "process_stage",
}
_BATTERY_REQUIRED_RECOMMENDATION_FIELDS = _COMMON_REQUIRED_RECOMMENDATION_FIELDS | {
    "battery_model", "cathode_material", "anode_material", "nominal_voltage",
}
_APPLIANCE_REQUIRED_RECOMMENDATION_FIELDS = (
    _COMMON_REQUIRED_RECOMMENDATION_FIELDS | industry_templates.required_keys("appliance")
)

# 这些字段对应 1.1 页面已有的 select/tag 选项。AI 只能返回这里的 value，
# 服务端和前端都不允许把模型生成的其它字符串变成新的下拉条目。
_RECOMMENDATION_ENUMS = {
    "requirement_type": {"new", "iteration", "change"},
    "priority": {"urgent", "high", "medium", "low"},
    "bu": {"bu1", "bu2"},
    "disclosure": {"secret", "public", "internal"},
    "customer_type": {"new", "old"},
    "customer_industry": {"foundry", "idm", "equipment", "other"},
    "account_manager": {"li", "wang"},
    "project_code": {"P001", "P002"},
    "product_iteration": {"new", "iteration"},
    "wafer_size": {"6", "8", "12", "custom"},
    "chuck_type": {"coulomb", "jr"},
    "temperature_zones": {"1", "2", "4", "multi"},
    "ceramic_material": {"al2o3", "aln", "other"},
    "base_material": {"al", "alalloy", "other"},
    "cleanliness": {"1", "10", "100", "custom"},
    "vacuum_environment": {"hv", "uhv", "plasma"},
    "heating": {"yes", "no"},
    "category_a": {"A1", "A2"},
    "category_b": {"B1", "B2"},
    "product_type": {"esc", "target", "other"},
    "complexity": {"simple", "medium", "complex", "verycomplex"},
    "new_technology": {"yes", "no"},
    "technology_source": {"self", "kste", "customer", "joint"},
    "target_equipment": {"刻蚀机", "PVD设备", "CVD设备", "离子注入机", "光刻机", "量测设备"},
    "process_stage": {"刻蚀", "薄膜沉积", "离子注入", "光刻", "量测"},
    # 电器行业（1.1 Section C）。
    "appliance_category": {"refrigerator", "washer", "ac", "kitchen", "small", "other"},
    "energy_efficiency_grade": {"g1", "g2", "g3", "tbd"},
    "housing_material": {"abs", "hips", "vcm", "spcc", "glass", "other"},
    "surface_process": {"brushed", "spray", "laminated", "ecoat", "none"},
    "certification_region": {"ccc", "ce", "ul", "multi"},
    "core_components": {"压缩机", "变频电机", "PCBA控制板", "风机", "热交换器", "显示模组"},
    "forming_process": {"注塑", "冲压", "折弯", "吸塑", "发泡", "绕线", "焊接"},
    "safety_standard": {"GB 4706.1", "GB 4343.1", "GB 17625.1", "CCC", "CE", "UL"},
}
_RECOMMENDATION_ENUM_DEFAULTS = {
    key: next(iter(sorted(values))) for key, values in _RECOMMENDATION_ENUMS.items()
}
# 对业务上更合适的默认值单独覆盖集合排序带来的任意顺序。
_RECOMMENDATION_ENUM_DEFAULTS.update({
    "requirement_type": "new", "priority": "medium", "bu": "bu1", "disclosure": "secret",
    "customer_type": "new", "customer_industry": "other", "account_manager": "li",
    "project_code": "P001", "product_iteration": "new", "wafer_size": "12",
    "chuck_type": "coulomb", "temperature_zones": "1", "ceramic_material": "aln",
    "base_material": "al", "cleanliness": "10", "vacuum_environment": "hv", "heating": "no",
    "category_a": "A1", "category_b": "B1", "product_type": "esc", "complexity": "medium",
    "new_technology": "no", "technology_source": "self", "target_equipment": "刻蚀机",
    "process_stage": "刻蚀",
    "appliance_category": "refrigerator", "energy_efficiency_grade": "g1",
    "housing_material": "spcc", "surface_process": "laminated",
    "certification_region": "ccc", "core_components": "压缩机",
    "forming_process": "注塑", "safety_standard": "GB 4706.1",
})
_RECOMMENDATION_DATE_OFFSETS = {
    "project_k0": 7, "project_start_due": 10, "evaluation_due": 14,
    "first_sample_due": 30, "mass_production_due": 90,
}


def _recommendation_fields_for_industry(industry: str) -> set[str]:
    """返回本次允许生成 AI 推荐的字段；非必填字段不进入推荐集合。"""
    return _required_recommendation_fields_for_industry(industry)


def _extractable_fields_for_industry(industry: str) -> set[str]:
    """字段明确出现在技术文档中时仍可带入，不能因它不是必填项而丢弃。"""
    if industry == "battery":
        return _BATTERY_RECOMMENDATION_FIELDS | _BATTERY_REQUIRED_RECOMMENDATION_FIELDS
    if industry == "appliance":
        return _APPLIANCE_RECOMMENDATION_FIELDS | _APPLIANCE_REQUIRED_RECOMMENDATION_FIELDS
    if industry == "flexible":
        # 历史草稿：规格字段由 AI 动态生成，只保留跨行业通用字段。
        return _COMMON_RECOMMENDATION_FIELDS | _COMMON_REQUIRED_RECOMMENDATION_FIELDS
    return _SEMICONDUCTOR_RECOMMENDATION_FIELDS | _SEMICONDUCTOR_REQUIRED_RECOMMENDATION_FIELDS


def _required_recommendation_fields_for_industry(industry: str) -> set[str]:
    if industry == "battery":
        return _BATTERY_REQUIRED_RECOMMENDATION_FIELDS
    if industry == "appliance":
        return _APPLIANCE_REQUIRED_RECOMMENDATION_FIELDS
    if industry == "flexible":
        return _COMMON_REQUIRED_RECOMMENDATION_FIELDS
    return _SEMICONDUCTOR_REQUIRED_RECOMMENDATION_FIELDS


def _recommendation_fallback_value(key: str) -> str:
    if key in _RECOMMENDATION_ENUM_DEFAULTS:
        return _RECOMMENDATION_ENUM_DEFAULTS[key]
    if key in _RECOMMENDATION_DATE_OFFSETS:
        return (date.today() + timedelta(days=_RECOMMENDATION_DATE_OFFSETS[key])).isoformat()
    if key == "description":
        return "待人工确认需求背景"
    if key == "final_customer_name":
        return "待人工确认客户"
    if key == "project_name":
        return "待人工确认项目"
    if key == "annual_forecast":
        return "待人工确认数量"
    return _RECOMMENDATION_FALLBACK


def _normalize_recommendation_value(key: str, value: object) -> str:
    raw = str(value or "").strip()
    if key in _RECOMMENDATION_ENUMS:
        if key in {"target_equipment", "process_stage"}:
            values = [item.strip() for item in re.split(r"[,，、]", raw) if item.strip()]
            valid = list(dict.fromkeys(item for item in values if item in _RECOMMENDATION_ENUMS[key]))
            return ",".join(valid) or _RECOMMENDATION_ENUM_DEFAULTS[key]
        return raw if raw in _RECOMMENDATION_ENUMS[key] else _RECOMMENDATION_ENUM_DEFAULTS[key]
    return raw[:2000] or _recommendation_fallback_value(key)

_SYSTEM_PROMPT = """你是工艺评估需求受理专员。请从技术文档中提取能够明确映射到
《工艺评估需求单 1.1 创建》字段的信息，并对资料未明确给出的适用字段给出 AI 推荐默认值，输出 JSON。

规则：
1. fields 只填写文档中明确出现或可直接换算的信息；不得把猜测写入 fields。
2. recommendations 只允许输出必填字段：当前行业模板中所有必填字段，只要 fields 没有明确带入，就必须给出推荐；非必填字段不要输出 recommendations，也不要为了填满页面写“待人工确认”。每个推荐字段还必须在 recommendation_confidence 中给出 0 到 1 的置信度。
3. fields 和 recommendations 的 key 只能来自下列列表；非必填字段若资料没有明确内容就保持空白。下拉/标签枚举字段只能使用页面已有 value，绝对不能创造新选项：
recommendations 还可以使用 requirement_type、customer_type、product_iteration，但这三个字段禁止写入 fields：
priority, bu, disclosure, description, customer_industry,
account_manager, final_customer_name, transaction_customer_name, project_name, project_code,
project_manager, technical_contact, product_name, product_model, wafer_size,
chuck_type, temperature_zones, ceramic_material, electrode_material, base_material, product_weight,
overall_dimensions, ttv, roughness, micro_hole_diameter, micro_hole_diameter_tolerance,
micro_hole_depth_tolerance, mesa_height, adsorption_uniformity, temperature_range, max_voltage,
leakage_current, helium_leak_rate, cleanliness, service_life, target_equipment, process_stage,
vacuum_environment, heating, annual_forecast, lifetime_forecast, first_sample_due,
mass_production_due, target_price, competitors, current_situation, project_k0, evaluation_due,
project_start_due, milestones, category_a, category_b, product_type, complexity, new_technology,
technology_source, notes, related_requirement。
电池行业还可使用：battery_model, cathode_material, anode_material, nominal_voltage,
gravimetric_energy_density, volumetric_energy_density, dcir, battery_operating_temperature,
thermal_runaway_temperature, crush_puncture_safety, cycle_life, calendar_life, stacking_process,
minimalist_packaging, battery_process_other, vda_dimensions, slim_cell_dimensions, battery_form_factor。
4. 所有值必须是字符串；target_equipment 只能从“刻蚀机、PVD设备、CVD设备、离子注入机、光刻机、量测设备”中选择并用英文逗号分隔；process_stage 只能从“刻蚀、薄膜沉积、离子注入、光刻、量测”中选择并用英文逗号分隔。
5. fields 中的日期使用 YYYY-MM-DD；不能从文档确定完整日期时不要把猜测写入 fields。必填字段的 recommendations 日期即使资料没有明确给出，也要给出合法的 YYYY-MM-DD 行业/计划默认值，并降低置信度。需求类型、新旧客户、全新/迭代
由平台根据历史需求单自动比对，禁止在 fields 中输出这三个字段。其余可枚举字段尽量使用表单值：
wafer_size=6/8/12/custom；ceramic_material=al2o3/aln/other；base_material=al/alalloy/other；
heating=yes/no；new_technology=yes/no。
6. recommendations 中的枚举字段必须使用这些页面 value：requirement_type=new/iteration/change；priority=urgent/high/medium/low；bu=bu1/bu2；disclosure=secret/public/internal；customer_type=new/old；customer_industry=foundry/idm/equipment/other；account_manager=li/wang；project_code=P001/P002；product_iteration=new/iteration；wafer_size=6/8/12/custom；chuck_type=coulomb/jr；temperature_zones=1/2/4/multi；ceramic_material=al2o3/aln/other；base_material=al/alalloy/other；cleanliness=1/10/100/custom；vacuum_environment=hv/uhv/plasma；heating=yes/no；category_a=A1/A2；category_b=B1/B2；product_type=esc/target/other；complexity=simple/medium/complex/verycomplex；new_technology=yes/no；technology_source=self/kste/customer/joint。不能输出中文标签、自然语言选项或其它自造值。
7. title 是建议的需求名称；summary 不超过 120 字；open_questions 只列真实缺失项。
8. 即使 fields 没有提取到内容，也必须为当前模板所有必填字段输出 recommendations；非必填字段不要输出 recommendations。只输出一个合法 JSON 对象，不要 Markdown。JSON 顶层必须包含 title、fields、recommendations、recommendation_confidence、summary、open_questions、industry、industry_confidence、industry_reason；recommendations 的格式为 {"字段 key":"AI 推荐默认值"}，recommendation_confidence 的格式为 {"字段 key":0.0到1.0}。"""

_FLEXIBLE_SPEC_PROMPT = """
当前项目的行业模板为“灵活”。除 fields 外，你还必须先根据需求文档、需求描述、产品名称/型号，
判断它所属的细分行业和产品类别；再结合该细分行业通常需要关注的规格维度，为产品技术规格生成
flexible_spec_fields 数组。每一项格式如下：
{"section":"3.1"或"3.2"或"3.3", "key":"英文下划线字段名", "label":"中文字段名",
 "value":"文档中明确的初始值；无则空字符串", "placeholder":"填写提示", "input_type":"text 或 textarea", "required":true或false}。
三个 section 都应根据资料分别考虑：3.1 基础参数、3.2 精度与性能要求、3.3 应用场景；
只创建与当前产品实际有关的字段，最多 18 个。可以根据通用行业知识决定“需要什么字段”，
但 value 只能填写资料明确给出的内容，资料未给出的 value 必须为空并通过 placeholder 说明填写口径。
key 只能含小写字母、数字和下划线。
"""

_INDUSTRY_PROMPT = """
你还必须输出 industry、industry_confidence、industry_reason：
- industry 只能是 semiconductor、battery、appliance；
- semiconductor 仅用于半导体制造、晶圆、真空腔体、静电吸盘、半导体设备及其明确零部件；
- battery 仅用于锂电池、动力电池、储能电池、电芯、模组、PACK 与其明确零部件；
- appliance 仅用于家用电器整机及其明确部件（冰箱、洗衣机、空调、厨电、小家电、
  以及压缩机、家电电机、控制板、箱体门体等）；
- 三者都不匹配或依据不足时，选最接近的一个并把 industry_confidence 压低到 0.4 以下，
  由人工在页面上改选，不要臆造行业；
- industry_confidence 为 0 到 1 的数字，industry_reason 不超过 80 字。
"""


@dataclass(frozen=True)
class PreparedDocuments:
    text: str
    processed_files: list[str]
    skipped_files: list[str]


def _decode_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").replace("\x00", "").strip()


def _extract_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        raw = archive.read("word/document.xml")
    root = ElementTree.fromstring(raw)
    paragraphs = []
    for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        text = "".join(node.text or "" for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
        if text.strip():
            paragraphs.append(text.strip())
    return "\n".join(paragraphs)


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("PDF 文本提取组件未安装") from exc
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "").strip() for page in reader.pages[:80]).strip()


def _text_from_file(name: str, data: bytes) -> str:
    lower = (name or "").lower()
    if lower.endswith(_TEXT_SUFFIXES):
        return _decode_text(data)
    if lower.endswith(".docx"):
        return _extract_docx(data)
    if lower.endswith(".pdf"):
        return _extract_pdf(data)
    raise ValueError("不支持提取文本")


def extract_document_text(name: str, data: bytes) -> str:
    """提取单个技术文档的可读文本，供图纸解析等服务复用。

    这个函数只做本地文件解析，不会调用模型。PDF/DOCX 因此既能用于 1.1
    的需求字段提取，也能作为 2.1 图纸解析的工程约束上下文。
    """
    return _text_from_file(name, data)


def prepare_documents(attachments: Iterable[tuple[str, bytes]]) -> PreparedDocuments:
    """本地提取并限制上下文；此函数不调用模型，便于离线测试。"""
    segments: list[str] = []
    processed: list[str] = []
    skipped: list[str] = []
    remaining = _MAX_TOTAL_CHARS or None
    for index, (name, data) in enumerate(attachments):
        if index >= _MAX_DOCUMENTS or (remaining is not None and remaining <= 0):
            skipped.append(f"{name}（超出本次文档数量或文本预算）")
            continue
        try:
            extracted = extract_document_text(name, data)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            skipped.append(f"{name}（{exc}）")
            continue
        if not extracted:
            skipped.append(f"{name}（未提取到可读文本）")
            continue
        budget = _MAX_CHARS_PER_DOCUMENT or len(extracted)
        if remaining is not None:
            budget = min(budget, remaining)
        excerpt = extracted[:budget]
        if remaining is not None:
            remaining -= len(excerpt)
        processed.append(name)
        suffix = "\n【该文件内容已按上下文预算截断】" if len(extracted) > len(excerpt) else ""
        segments.append(f"【技术文档：{name}】\n{excerpt}{suffix}")
    return PreparedDocuments("\n\n".join(segments), processed, skipped)


def _normalize_flexible_spec_fields(raw_fields: list[RequirementDynamicSpecField]) -> list[RequirementDynamicSpecField]:
    """将模型建议限制为稳定、可存储、可渲染的表单定义。"""
    normalized: list[RequirementDynamicSpecField] = []
    used: set[str] = set()
    for item in raw_fields[:18]:
        section = str(item.section or "").strip()
        if section not in {"3.1", "3.2", "3.3"}:
            continue
        key = re.sub(r"[^a-z0-9_]", "_", str(item.key or "").lower()).strip("_")[:48]
        label = str(item.label or "").strip()[:80]
        if not key or not label or key in used:
            continue
        used.add(key)
        normalized.append(RequirementDynamicSpecField(
            section=section, key=key, label=label,
            value=str(item.value or "").strip()[:1000],
            placeholder=str(item.placeholder or "").strip()[:160],
            input_type="textarea" if str(item.input_type or "").lower() == "textarea" else "text",
            required=bool(item.required),
        ))
    return normalized


def _normalized_industry(value: str) -> str:
    """模型识别出的行业。不在受支持模板内时回落到默认模板，由人工在页面上改选。"""
    return industry_templates.normalize(value)


def extract_requirement_fields(prepared: PreparedDocuments, industry_selection: str = "semiconductor") -> RequirementDocumentExtraction:
    """只发送本地抽出的文字，因此 qwen_client 会选择 QWEN_TEXT_MODEL。"""
    if not prepared.text:
        raise ValueError("没有可供解析的技术文档文本")
    selection = str(industry_selection or "semiconductor").strip().lower()
    # flexible 是历史草稿模板，仍允许沿用；新建需求只会传三个受支持行业之一。
    manual_industry = selection if selection in (*industry_templates.INDUSTRIES, "flexible") else ""
    system_prompt = _SYSTEM_PROMPT + _INDUSTRY_PROMPT
    if manual_industry:
        system_prompt += f"\n用户已人工指定行业模板为 {manual_industry}。仍请输出识别建议用于留痕，但必须按该人工模板组织字段。"
    if manual_industry == "flexible":
        system_prompt += _FLEXIBLE_SPEC_PROMPT
    result = llm_client.complete_to_model(
        system_prompt,
        "请从以下技术文档提取 1.1 需求创建字段：\n\n" + prepared.text,
        RequirementDocumentExtraction,
        # 1.1 字段与灵活行业规格可能同时返回几十个字段，不能再使用早期
        # 个人 API 的 2200 token 小预算，否则合法 JSON 会在末尾被截断。
        max_tokens=12000,
    )
    result.title = result.title.strip()[:160] or "待人工确认需求"
    result.summary = result.summary.strip()[:240]
    detected_industry = _normalized_industry(str(result.industry or "").strip().lower())
    effective_industry = manual_industry or detected_industry
    recommendation_fields = _recommendation_fields_for_industry(effective_industry)
    extractable_fields = _extractable_fields_for_industry(effective_industry) & EXTRACTABLE_FIELDS
    invalid_field_recommendations = {}
    normalized_fields = {}
    for key, value in result.fields.items():
        if key not in extractable_fields or not str(value).strip():
            continue
        normalized = _normalize_recommendation_value(key, value)
        # 文档事实如果不是页面已有枚举值，不能写成蓝色“AI 带入”；
        # 统一转成合法的黄色推荐。这个规则对所有枚举字段生效。
        if key in _RECOMMENDATION_ENUMS and normalized != str(value).strip():
            invalid_field_recommendations[key] = normalized
            continue
        normalized_fields[key] = str(value).strip()[:2000]
    result.fields = normalized_fields
    if invalid_field_recommendations:
        result.recommendations = {
            **invalid_field_recommendations,
            **(result.recommendations or {}),
        }
    result.fields = {
        key: value for key, value in result.fields.items()
    }
    model_recommendation_keys = {
        key for key, value in result.recommendations.items()
        if key in recommendation_fields and str(value).strip() and key not in result.fields
    }
    result.recommendations = {
        key: _normalize_recommendation_value(key, value)
        for key, value in result.recommendations.items()
        if key in model_recommendation_keys
    }
    # 模型偶尔会因为 token 或结构化输出限制漏掉推荐项。这里只补齐必填字段；
    # 非必填字段没有文档事实时保持空白。
    result.recommendations.update({
        key: _recommendation_fallback_value(key)
        for key in recommendation_fields
        if key not in result.fields and key not in result.recommendations
    })
    raw_confidence = result.recommendation_confidence or {}
    result.recommendation_confidence = {}
    for key in result.recommendations:
        try:
            confidence = float(raw_confidence.get(key))
        except (TypeError, ValueError):
            confidence = 0.65 if key in model_recommendation_keys else 0.35
        result.recommendation_confidence[key] = max(0.0, min(1.0, confidence))
    result.industry = detected_industry
    result.industry_confidence = max(0.0, min(1.0, float(result.industry_confidence or 0.0)))
    result.industry_reason = str(result.industry_reason or "").strip()[:160]
    result.flexible_spec_fields = _normalize_flexible_spec_fields(result.flexible_spec_fields) if effective_industry == "flexible" else []
    result.open_questions = [str(item).strip()[:240] for item in result.open_questions if str(item).strip()][:12]
    return result
