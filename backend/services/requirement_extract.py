"""技术文档 -> 1.1 工艺评估需求草稿的纯文本提取服务。

先在本地从 TXT/CSV/Markdown/PDF/DOCX 抽取文本，再调用 Qwen 的文本模型。
不把工程图或图片混入本请求，避免本来可用纯文本模型的场景被错误地切到 VL。
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from typing import Iterable
from xml.etree import ElementTree

from ..models.workflow import RequirementDocumentExtraction
from . import qwen_client

_TEXT_SUFFIXES = (".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".ini")
_MAX_DOCUMENTS = 8
_MAX_CHARS_PER_DOCUMENT = 12000
_MAX_TOTAL_CHARS = 50000

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
}

_SYSTEM_PROMPT = """你是半导体零部件工艺评估需求受理专员。请从技术文档中提取能够明确映射到
《工艺评估需求单 1.1 创建》字段的信息，输出 JSON。

规则：
1. 只填写文档中明确出现或可直接换算的信息；不得猜测、补全或编造。
2. fields 的 key 只能来自下列列表；没有值的字段不要输出：
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
3. 所有值必须是字符串；多选 target_equipment、process_stage 使用英文逗号分隔。
4. 日期使用 YYYY-MM-DD；不能确定完整日期就不要填写。需求类型、新旧客户、全新/迭代
由平台根据历史需求单自动比对，禁止在 fields 中输出这三个字段。其余可枚举字段尽量使用表单值：
wafer_size=6/8/12/custom；ceramic_material=al2o3/aln/other；base_material=al/alalloy/other；
heating=yes/no；new_technology=yes/no。
5. title 是建议的需求名称；summary 不超过 120 字；open_questions 只列真实缺失项。
只输出一个合法 JSON 对象，不要 Markdown。"""


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
    remaining = _MAX_TOTAL_CHARS
    for index, (name, data) in enumerate(attachments):
        if index >= _MAX_DOCUMENTS or remaining <= 0:
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
        excerpt = extracted[:min(_MAX_CHARS_PER_DOCUMENT, remaining)]
        remaining -= len(excerpt)
        processed.append(name)
        suffix = "\n【该文件内容已按上下文预算截断】" if len(extracted) > len(excerpt) else ""
        segments.append(f"【技术文档：{name}】\n{excerpt}{suffix}")
    return PreparedDocuments("\n\n".join(segments), processed, skipped)


def extract_requirement_fields(prepared: PreparedDocuments) -> RequirementDocumentExtraction:
    """只发送本地抽出的文字，因此 qwen_client 会选择 QWEN_TEXT_MODEL。"""
    if not prepared.text:
        raise ValueError("没有可供解析的技术文档文本")
    result = qwen_client.complete_to_model(
        _SYSTEM_PROMPT,
        "请从以下技术文档提取 1.1 需求创建字段：\n\n" + prepared.text,
        RequirementDocumentExtraction,
        max_tokens=2200,
    )
    result.title = result.title.strip()[:160]
    result.summary = result.summary.strip()[:240]
    result.fields = {
        key: str(value).strip()[:2000]
        for key, value in result.fields.items()
        if key in EXTRACTABLE_FIELDS and str(value).strip()
    }
    result.open_questions = [str(item).strip()[:240] for item in result.open_questions if str(item).strip()][:12]
    return result
