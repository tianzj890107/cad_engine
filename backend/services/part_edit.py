"""零件参数的**受控**改写：AI 对话唯一被允许写业务数据的口子。

平台里有两个会话入口 —— 老的单轮 `workbench-chat` 和 2.1 页的 open-claude Agent。
它们都可能被用户要求"把底板厚度改成 3mm"。改写逻辑只能有一份：两份实现意味着
白名单会漂移，某一天其中一条路允许改另一条不允许，而这类差异只有出事才会被发现。

约束（服务端强制，不依赖模型自觉）：

  - 只能改 **name / quantity / material.spec**，以及**已有**特征的**数值**字段；
  - 特征字段按 `type` 逐类白名单，不在表内一律拒绝；
  - 不能增删特征、不能改特征 type、不能碰别的零件；
  - 尺寸必须为正（`x` / `y` 是相对坐标，可为负）；
  - 导入的精确 STEP/STP 实体禁止改 IR 特征 —— 改了 IR 也改不动真实实体，
    只会让两者悄悄脱节。

本模块只做校验与就地改写，不落盘、不审计、不认识 HTTP —— 调用方负责保存版本、
写审计。失败一律抛 `PartEditError`，由调用方翻成各自的错误形态。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from ..models.ir import Material

#: 每类特征允许改写的数值字段。不在表内的字段一律拒绝 —— 白名单而不是黑名单，
#: 因为新增特征类型时"忘了禁"比"忘了放行"危险得多。
FEATURE_FIELDS: dict[str, set[str]] = {
    "plate": {"length", "width", "thickness"},
    "box": {"length", "width", "height"},
    "cylinder": {"diameter", "height"},
    "hole": {"diameter", "x", "y"},
    "hole_pattern": {"diameter", "count_x", "count_y", "spacing_x", "spacing_y"},
    "fillet": {"radius"},
    "chamfer": {"distance"},
}

#: 这两个是相对坐标，允许为负或 0；其余尺寸必须为正。
_SIGNED_FIELDS = {"x", "y"}

_IMPORTED_3D_SUFFIXES = (".step", ".stp")


class PartEditError(ValueError):
    """改写被拒绝。消息面向用户，可直接展示。"""


def blocks_feature_edit(meta: dict | None) -> str:
    """导入的精确 3D 实体不接受文本改参；返回拒绝理由，空串表示可以改。"""
    source = str((meta or {}).get("source_filename") or "").lower()
    if source.endswith(_IMPORTED_3D_SUFFIXES):
        return ("当前项目是导入的精确 3D 模型（STEP/STP），改 IR 不会改动真实实体，"
                "只会让两者脱节。请在原 CAD 中修改后重新导入。")
    return ""


def _coerce(field: str, value: Any) -> float | int:
    try:
        number = int(value) if field.startswith("count_") else float(value)
    except (TypeError, ValueError) as exc:
        raise PartEditError(f"{field} 需要一个数值，收到 {value!r}") from exc
    if field not in _SIGNED_FIELDS and number <= 0:
        raise PartEditError(f"{field} 必须大于 0，收到 {number}")
    return number


def apply_edit(
    part: Any,
    *,
    name: Optional[str] = None,
    quantity: Optional[int] = None,
    material_spec: Optional[str] = None,
    feature_updates: Sequence[Any] = (),
) -> tuple[list[dict], bool]:
    """就地改写一个零件，返回 (变更清单, 是否需要重生几何)。

    `feature_updates` 的每一项需带 `feature_index` / `field` / `value`，
    dict 与对象都接受（老的 workbench-chat 传 pydantic 模型，Agent 传 dict）。
    值没变的字段不会进变更清单 —— 「AI 改了什么」必须如实反映，不能把
    "确认了一遍原值"记成一次修改。
    """
    changes: list[dict] = []
    geometry_changed = False

    if name is not None and name.strip() and name.strip() != part.name:
        before, part.name = part.name, name.strip()
        changes.append({"field": "name", "old": before, "new": part.name})

    if quantity is not None and quantity != part.quantity:
        before, part.quantity = part.quantity, quantity
        changes.append({"field": "quantity", "old": before, "new": part.quantity})

    if material_spec is not None and material_spec.strip():
        spec = material_spec.strip()
        before = part.material.spec if part.material else ""
        if spec != before:
            if part.material:
                part.material.spec = spec
            else:
                part.material = Material(spec=spec)
            changes.append({"field": "material.spec", "old": before, "new": spec})

    for update in _as_updates(feature_updates):
        index, field, raw = update
        if index < 0 or index >= len(part.features):
            raise PartEditError(
                f"特征序号 {index + 1} 不存在（{part.part_id} 共 {len(part.features)} 个特征）")
        feature = part.features[index]
        kind = feature.type.value if hasattr(feature.type, "value") else str(feature.type)
        allowed = FEATURE_FIELDS.get(kind, set())
        if field not in allowed:
            raise PartEditError(
                f"特征 #{index + 1}（{kind}）不允许修改字段 {field}；"
                f"可改的是：{'、'.join(sorted(allowed)) or '无'}")
        value = _coerce(field, raw)
        before = getattr(feature, field)
        if before != value:
            setattr(feature, field, value)
            changes.append({"field": f"features[{index}].{field}",
                            "old": before, "new": value})
            geometry_changed = True

    return changes, geometry_changed


def _as_updates(items: Iterable[Any]) -> list[tuple[int, str, Any]]:
    """统一 dict / 对象两种写法。整条读不出来就拒绝，不猜。"""
    out: list[tuple[int, str, Any]] = []
    for item in items or []:
        if isinstance(item, dict):
            index, field, value = (item.get("feature_index"), item.get("field"),
                                   item.get("value"))
        else:
            index, field, value = (getattr(item, "feature_index", None),
                                   getattr(item, "field", None),
                                   getattr(item, "value", None))
        if index is None or not field:
            raise PartEditError(
                "特征修改需要同时给出 feature_index 与 field，收到不完整的一项")
        try:
            index = int(index)
        except (TypeError, ValueError) as exc:
            raise PartEditError(f"feature_index 需要整数，收到 {index!r}") from exc
        out.append((index, str(field), value))
    return out
