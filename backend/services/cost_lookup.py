"""成本测算前的价格与费率检索。

成本测算最怕的是**数字没有出处**。模型可以联网查行情，但企业自己的合同价、
车间人工费率、设备折旧率只在库里 —— 查不到就只能编，编出来的报价没法拿去谈。

这里在调模型之前先把三样东西钉死，全程不经模型：
  1. 物料价：按牌号反查物料编码，再按**测算时点**取价（kb_repo.current_price
     已经保证最新价优先）。带上 price_id 与来源，事后能复现这次测算用的是哪条价。
  2. 费率：按工艺检索给出的设备类逐个取人工/折旧费率，指定作用域没有就回退
     global —— 回退这件事必须显式标出来，否则拿一条通用费率当专机费率用，
     成本会低估一大截。
  3. 计价系数：良率/废品率/毛利/税率，按零件类别与材料类别取。

查不到的都进 gaps，直接变成需要询价或补录费率的待办，而不是被默默填一个默认值。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..storage import kb_repo, store
from ..time_utils import now_cst_str

ProgressFn = Optional[Callable[[str], None]]

# 与成本口径对应的费率类型。前两类跟着设备类走，后几类是全厂口径。
_SCOPED_RATES = ("labor", "equipment_dep")
_GLOBAL_RATES = ("energy", "overhead", "logistics")
_FACTORS = ("yield", "scrap", "margin", "tax")


def _material_row(spec: Optional[str]) -> Optional[dict]:
    """按牌号反查物料；查不到留空，不猜。"""
    text = str(spec or "").strip()
    if not text:
        return None
    for material in kb_repo.list_materials(keyword=text):
        grade = str(material.get("grade") or "").strip()
        if grade and (grade in text or text in grade):
            return material
    return None


def _equipment_classes(process_report: Optional[dict]) -> list[str]:
    """从工艺检索结果里取出这条路线会用到的设备类。"""
    if not process_report:
        return []
    steps = list((process_report.get("route") or {}).get("steps") or [])
    steps += list(process_report.get("extra_steps") or [])
    classes: list[str] = []
    for step in steps:
        code = step.get("equipment_class")
        if code and code not in classes:
            classes.append(code)
    return classes


# --------------------------------------------------------------------------- #
# 检索
# --------------------------------------------------------------------------- #
def lookup_part(part: dict, *, quantity: int = 1, match: Optional[dict] = None,
                process_report: Optional[dict] = None, at: Optional[str] = None,
                progress: ProgressFn = None) -> dict:
    """检索单个零件的成本依据。返回物料价 / 费率 / 系数 / 缺口，不写库。"""
    qty = max(1, int(quantity or 1))
    moment = at or now_cst_str()
    label = f"{part.get('part_id') or '?'} {part.get('name') or ''}".strip()
    _report(progress, f"检索成本库：{label}（批量 {qty}，取价时点 {moment}）")

    gaps: list[str] = []
    material_info = _lookup_material(part, match, moment, gaps, progress)
    category = (process_report or {}).get("query", {}).get("category")
    rates = _lookup_rates(_equipment_classes(process_report), moment, gaps, progress)
    factors = _lookup_factors(category, material_info.get("category"), moment, gaps, progress)

    fallback = [item for item in rates if item["fallback"]]
    _report(progress, f"成本库检索完成：物料价 {'已取到' if material_info.get('price') else '缺失'}、"
                      f"费率 {len(rates)} 条（回退 global {len(fallback)} 条）、"
                      f"系数 {len(factors)} 条、待补 {len(gaps)} 项")

    return {
        "generated_at": now_cst_str(),
        "priced_at": moment,
        "part_id": part.get("part_id"),
        "part_name": part.get("name") or "",
        "quantity": qty,
        "material": material_info,
        "rates": rates,
        "factors": factors,
        "gaps": gaps,
        "summary": {
            "has_price": bool(material_info.get("price")),
            "rates": len(rates),
            "rates_fallback": len(fallback),
            "factors": len(factors),
            "gaps": len(gaps),
        },
    }


def _lookup_material(part: dict, match: Optional[dict], moment: str,
                     gaps: list[str], progress: ProgressFn) -> dict:
    material = part.get("material")
    spec = material.get("spec") if isinstance(material, dict) else material
    row = _material_row(spec)
    # 图纸没写牌号时，退而用库内匹配到的同类零部件的默认物料 —— 这是有依据的推断，
    # 不是猜；来源会在 source 字段里标明。
    source = "按图纸牌号反查"
    if not row and match and match.get("matched"):
        component = kb_repo.get_component(match.get("component_code") or "")
        if component and component.get("default_material_code"):
            row = kb_repo.get_material(component["default_material_code"])
            source = f"图纸未标牌号，取同类件 {component['component_code']} 的默认物料"
    if not row:
        gaps.append(f"物料「{spec or '未标注'}」不在物料库中，需补录或询价")
        _report(progress, f"  ↳ 物料「{spec or '未标注'}」库内无记录，需询价")
        return {"spec": spec, "matched": False, "source": "", "price": None}

    price = kb_repo.current_price(row["material_code"], at=moment)
    if price:
        _report(progress, f"  ↳ 物料 {row['material_code']} {row.get('name') or ''}"
                          f" 现价 {price['price']} {price.get('currency')}/{price.get('unit')}"
                          f"（{price.get('price_type')}，{price.get('valid_from')} 起）")
    else:
        gaps.append(f"物料 {row['material_code']} 在 {moment} 无有效价格，需询价")
        _report(progress, f"  ↳ 物料 {row['material_code']} 在该时点无有效价格，需询价")

    return {
        "spec": spec,
        "matched": True,
        "source": source,
        "material_code": row["material_code"],
        "name": row.get("name"),
        "grade": row.get("grade"),
        "category": row.get("category"),
        "density": row.get("density"),
        "base_unit": row.get("base_unit"),
        "standard_loss_rate": row.get("standard_loss_rate"),
        "price": {
            "price_id": price["price_id"], "price": price["price"],
            "currency": price.get("currency"), "unit": price.get("unit"),
            "price_type": price.get("price_type"), "valid_from": price.get("valid_from"),
            "source_name": price.get("source_name"), "confidence": price.get("confidence"),
        } if price else None,
    }


def _lookup_rates(equipment_classes: list[str], moment: str,
                  gaps: list[str], progress: ProgressFn) -> list[dict]:
    rates: list[dict] = []
    seen: set[str] = set()

    def take(rate_type: str, scope_type: str, scope_ref: Optional[str]) -> None:
        row = kb_repo.effective_rate(rate_type, scope_type=scope_type,
                                     scope_ref=scope_ref, at=moment)
        if not row:
            gaps.append(f"费率 {rate_type}"
                        + (f"（{scope_ref}）" if scope_ref else "")
                        + " 库内缺失，需补录")
            return
        if row["rate_code"] in seen:
            return
        seen.add(row["rate_code"])
        # 回退必须显式标出：拿通用费率当专机费率用会把成本压低一大截。
        fallback = scope_ref is not None and row.get("scope_type") != scope_type
        rates.append({
            "rate_type": rate_type, "rate_code": row["rate_code"], "name": row.get("name"),
            "value": row["value"], "unit": row.get("unit"), "currency": row.get("currency"),
            "scope_type": row.get("scope_type"), "scope_ref": row.get("scope_ref"),
            "requested_scope": scope_ref, "fallback": fallback,
            "effective_from": row.get("effective_from"),
        })
        note = f"（回退 global，请求作用域 {scope_ref}）" if fallback else ""
        _report(progress, f"  ↳ 费率 {row['rate_code']} {row['value']} {row.get('unit')}{note}")

    for class_code in equipment_classes:
        for rate_type in _SCOPED_RATES:
            take(rate_type, "equipment_class", class_code)
    if not equipment_classes:
        for rate_type in _SCOPED_RATES:
            take(rate_type, "global", None)
    for rate_type in _GLOBAL_RATES:
        take(rate_type, "global", None)
    return rates


def _lookup_factors(category: Optional[str], material_category: Optional[str],
                    moment: str, gaps: list[str], progress: ProgressFn) -> list[dict]:
    factors: list[dict] = []
    for factor_type in _FACTORS:
        # 良率跟零件类别走，废品率跟材料类别走，毛利/税率是全公司口径。
        scope = category if factor_type == "yield" else (
            material_category if factor_type == "scrap" else None)
        row = kb_repo.effective_factor(factor_type, at=moment, scope=scope)
        if not row:
            gaps.append(f"计价系数 {factor_type} 库内缺失，需补录")
            continue
        factors.append({
            "factor_type": factor_type, "factor_code": row["factor_code"],
            "name": row.get("name"), "value": row["value"],
            "applicable_scope": row.get("applicable_scope"),
            "requested_scope": scope,
            "effective_from": row.get("effective_from"),
        })
    if factors:
        _report(progress, "  ↳ 计价系数：" + "、".join(
            f"{item['factor_type']}={item['value']}" for item in factors))
    return factors


def _report(progress: ProgressFn, message: str) -> None:
    if progress:
        try:
            progress(message)
        except Exception:      # 进度上报失败不能影响检索本身
            pass


# --------------------------------------------------------------------------- #
# 给模型看的摘要
# --------------------------------------------------------------------------- #
def as_prompt(report: dict) -> str:
    """把检索结果压成一段提示词。库里有的一律以库为准，模型不得另行估价。"""
    lines: list[str] = [f"【企业成本库检索结果】取价时点 {report.get('priced_at')}，"
                        f"核算批量 {report.get('quantity')}"]
    material = report.get("material") or {}
    price = material.get("price")
    if price:
        lines.append(
            f"物料 {material.get('material_code')} {material.get('name') or ''}"
            f"（{material.get('grade') or ''}，密度 {material.get('density')} g/cm³，"
            f"标准损耗率 {material.get('standard_loss_rate')}）："
            f"{price['price']} {price.get('currency')}/{price.get('unit')}"
            f"，价格类型 {price.get('price_type')}，{price.get('valid_from')} 起生效"
            f"，price_id={price['price_id']}"
        )
    else:
        lines.append(f"物料「{material.get('spec') or '未标注'}」库内无有效价格，"
                     "请联网检索行情并在 search_sources 里标明来源。")
    if report.get("rates"):
        lines.append("费率（元为单位，直接采用，不要另行估算）：")
        for item in report["rates"]:
            mark = "（注意：库内无该作用域费率，已回退全厂通用值）" if item["fallback"] else ""
            lines.append(f"  {item['rate_code']} {item.get('name') or item['rate_type']}"
                         f" = {item['value']} {item.get('unit')}{mark}")
    if report.get("factors"):
        lines.append("计价系数：" + "、".join(
            f"{item['factor_code']}({item['factor_type']})={item['value']}"
            for item in report["factors"]))
    if report.get("gaps"):
        lines.append("库内缺口（这些项必须在 assumptions/risks 里显式说明，不得静默取默认值）：")
        lines.extend(f"  - {gap}" for gap in report["gaps"])
    lines.append("要求：库内已给出的价格与费率必须原值采用；仅当库内缺失时才联网估算，"
                 "且要标注来源与可信度。")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 持久化
# --------------------------------------------------------------------------- #
def save_report(project_id: str, part_id: str, report: dict) -> None:
    store.save_cost_lookup(project_id, part_id, report)
