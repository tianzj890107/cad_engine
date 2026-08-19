"""知识库图库(2D/3D 图纸、文档)的文件夹布局与文件读写。

设计取舍:**图库以文件夹为准,数据库只是索引**。
工艺/设计人员可以直接把图纸拷进对应文件夹,再调 scan_component_files 扫描登记;
不需要先在系统里建记录才能放文件。这样图库在没有平台的情况下依然可用。

目录布局(相对 DATA_DIR):

    kb/
    ├── components/<component_code>/
    │   ├── 2d/<rev>/      *.dxf *.dwg *.pdf
    │   ├── 3d/<rev>/      *.step *.stp *.stl *.sldprt
    │   ├── doc/<rev>/     规格书 检验规范 工艺卡
    │   └── thumb/         preview.svg preview.png
    ├── standard_parts/<standard_no>/<designation>/{2d,3d}/
    ├── materials/<material_code>/     MSDS、检测报告、性能曲线
    └── routes/<route_code>/           工艺路线卡
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Iterator, Optional

DRAWING_KINDS = ("2d", "3d", "doc", "thumb")

# 各类目录接受的扩展名;扫描登记时据此判定 file_format 与是否收录。
KIND_FORMATS: dict[str, tuple[str, ...]] = {
    "2d": ("dxf", "dwg", "pdf", "png", "jpg", "jpeg", "svg"),
    "3d": ("step", "stp", "stl", "igs", "iges", "sldprt", "x_t", "3mf", "obj"),
    "doc": ("pdf", "docx", "doc", "xlsx", "xls", "md", "txt"),
    "thumb": ("svg", "png", "jpg", "jpeg", "webp"),
}

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_SEPARATORS = re.compile(r"[\\/\s]+")


# --------------------------------------------------------------------------- #
# 根目录
# --------------------------------------------------------------------------- #
def blob_root() -> Path:
    from ..config import DATA_DIR

    return Path(DATA_DIR)


def kb_root() -> Path:
    from ..config import KB_DIR

    return Path(KB_DIR)


def rel_path(path: Path) -> str:
    """转成入库用的相对路径(相对 DATA_DIR,posix 分隔符)。"""
    p = Path(path).resolve()
    try:
        return p.relative_to(blob_root().resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def abs_path(relative: str) -> Path:
    return blob_root() / relative


def ensure_kb_dirs() -> Path:
    """创建图库骨架目录。"""
    root = kb_root()
    for sub in ("components", "standard_parts", "materials", "routes"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


# --------------------------------------------------------------------------- #
# 路径构造
# --------------------------------------------------------------------------- #
def component_dir(component_code: str, *, create: bool = False) -> Path:
    d = kb_root() / "components" / safe_segment(component_code)
    if create:
        for kind in DRAWING_KINDS:
            (d / kind).mkdir(parents=True, exist_ok=True)
    return d


def component_kind_dir(component_code: str, kind: str, rev: str = "A", *, create: bool = False) -> Path:
    _check_kind(kind)
    base = component_dir(component_code)
    # thumb 不分版本:它只是当前版的预览图。
    d = base / kind if kind == "thumb" else base / kind / safe_segment(rev)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def standard_part_dir(standard_no: str, designation: str, *, create: bool = False) -> Path:
    d = kb_root() / "standard_parts" / safe_segment(standard_no or "misc") / safe_segment(designation)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def material_dir(material_code: str, *, create: bool = False) -> Path:
    d = kb_root() / "materials" / safe_segment(material_code)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def route_dir(route_code: str, *, create: bool = False) -> Path:
    d = kb_root() / "routes" / safe_segment(route_code)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
# 文件名规范化
# --------------------------------------------------------------------------- #
def safe_segment(name: str) -> str:
    """目录名规范化。用于编码类标识(component_code / 'GB/T 97.1' 等)。

    与 safe_name 的区别:目录名里的 '.' 不是扩展名分隔符 —— 'GB/T 97.1' 必须
    整体保留成 'GB-T-97.1',不能被切成 'GB-T-97' + '.1'。
    """
    text = str(name).strip()
    if not text:
        return "unnamed"
    normalized = _SEPARATORS.sub("-", text)
    cleaned = _SAFE_CHARS.sub("_", normalized).strip("_-.")
    if cleaned != normalized.strip("_-."):
        # 含中文等非安全字符:补一段哈希,避免不同名字塌缩成同一目录。
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        cleaned = f"{cleaned}_{digest}" if cleaned else f"d_{digest}"
    return cleaned


def safe_name(filename: str) -> str:
    """落盘名不含中文与空格;原名由调用方存进库的 file_name 列。

    中文名整体替换成短哈希而不是逐字丢弃,避免 '盖板.dxf' 与 '底板.dxf' 都变成 '_.dxf'。
    """
    name = Path(str(filename)).name.strip() or "unnamed"
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    cleaned = _SAFE_CHARS.sub("_", stem).strip("_.")
    if not cleaned or cleaned != stem:
        digest = hashlib.sha1(stem.encode("utf-8")).hexdigest()[:8]
        cleaned = f"{cleaned}_{digest}" if cleaned else f"f_{digest}"
    ext = _SAFE_CHARS.sub("", ext).lower()
    return f"{cleaned}.{ext}" if ext else cleaned


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_format(path: Path) -> str:
    return path.suffix.lstrip(".").lower()


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #
def put_component_file(
    component_code: str,
    kind: str,
    filename: str,
    data: bytes | None = None,
    *,
    src: Optional[Path] = None,
    rev: str = "A",
) -> dict:
    """把一份图纸/模型/文档放进图库,返回可直接入库的元数据。"""
    if data is None and src is None:
        raise ValueError("put_component_file 需要 data 或 src 之一")
    target_dir = component_kind_dir(component_code, kind, rev, create=True)
    target = target_dir / safe_name(filename)
    if data is not None:
        target.write_bytes(data)
    else:
        shutil.copyfile(src, target)  # type: ignore[arg-type]
    return describe(target, kind=kind, rev=rev, original_name=Path(filename).name)


def describe(path: Path, *, kind: str, rev: str = "A", original_name: str = "") -> dict:
    """把一个图库文件描述成 kb_component_drawing 的行。"""
    return {
        "drawing_kind": kind,
        "file_format": file_format(path),
        "rev": rev,
        "file_path": rel_path(path),
        "file_name": original_name or path.name,
        "file_sha256": sha256_file(path),
        "file_size": path.stat().st_size,
    }


# --------------------------------------------------------------------------- #
# 扫描(文件夹 -> 索引)
# --------------------------------------------------------------------------- #
def scan_component_files(component_code: str) -> list[dict]:
    """扫描某零部件的图库目录,返回全部可登记文件(按 kind/rev 归类)。"""
    base = component_dir(component_code)
    if not base.exists():
        return []
    found: list[dict] = []
    for kind in DRAWING_KINDS:
        kind_dir = base / kind
        if not kind_dir.is_dir():
            continue
        for path, rev in _walk_kind(kind_dir, kind):
            fmt = file_format(path)
            if fmt and fmt not in KIND_FORMATS[kind]:
                continue  # 目录里的临时文件/说明不登记
            found.append(describe(path, kind=kind, rev=rev))
    return found


def _walk_kind(kind_dir: Path, kind: str) -> Iterator[tuple[Path, str]]:
    if kind == "thumb":
        for path in sorted(kind_dir.iterdir()):
            if path.is_file():
                yield path, "A"
        return
    for rev_dir in sorted(kind_dir.iterdir()):
        if rev_dir.is_dir():
            for path in sorted(rev_dir.rglob("*")):
                if path.is_file():
                    yield path, rev_dir.name
        elif rev_dir.is_file():
            # 兼容没分版本目录直接丢文件的情况,归入默认版本 A。
            yield rev_dir, "A"


def list_component_codes() -> list[str]:
    """图库里实际存在的零部件目录(用于全量扫描登记)。"""
    base = kb_root() / "components"
    if not base.is_dir():
        return []
    return sorted(d.name for d in base.iterdir() if d.is_dir())


def latest_rev(component_code: str, kind: str) -> Optional[str]:
    """某类图纸的最新版本目录名(按字典序,A<B<C / V1.0<V1.1)。"""
    _check_kind(kind)
    if kind == "thumb":
        return "A"
    d = component_dir(component_code) / kind
    if not d.is_dir():
        return None
    revs = sorted(x.name for x in d.iterdir() if x.is_dir())
    return revs[-1] if revs else None


def _check_kind(kind: str) -> None:
    if kind not in DRAWING_KINDS:
        raise ValueError(f"未知的图纸类别: {kind}(应为 {DRAWING_KINDS})")
