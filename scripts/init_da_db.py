"""建立 DA 数据库(SQLite)与知识库图库目录。

用法:
    python scripts/init_da_db.py                  # 建库 + 建图库目录
    python scripts/init_da_db.py --seed           # 顺带写入知识库种子数据(通用机加工)
    python scripts/init_da_db.py --mock           # 写入半导体/电池/电器三行业模拟源表数据
    python scripts/init_da_db.py --mock battery   # 只写某一个行业
    python scripts/init_da_db.py --scan           # 扫描图库文件夹,登记图纸索引
    python scripts/init_da_db.py --import-legacy  # 导入既有 equipment.json / suppliers.json
    python scripts/init_da_db.py --stats          # 只看各表行数
    python scripts/init_da_db.py --db D:/tmp/da.db --seed
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 控制台默认可能是 GBK/cp1252,直接打印中文表名会抛 UnicodeEncodeError。
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 DA 数据库与图库目录")
    parser.add_argument("--db", help="库文件路径(默认 DATA_DIR/da.db)")
    parser.add_argument("--kb-dir", help="图库根目录(默认 DATA_DIR/kb)")
    parser.add_argument("--seed", action="store_true", help="写入知识库种子数据(通用机加工)")
    parser.add_argument("--mock", nargs="?", const="all", metavar="行业",
                        help="写入行业模拟源表数据。可选 semiconductor,battery,appliance;"
                             "不带值或 all 表示全部")
    parser.add_argument("--no-mock-projects", action="store_true",
                        help="模拟数据只写知识库,不建评估需求(src_ 层)")
    parser.add_argument("--overwrite", action="store_true", help="种子/模拟数据覆盖已有记录")
    parser.add_argument("--scan", action="store_true", help="扫描图库文件夹并登记图纸索引")
    parser.add_argument("--import-legacy", action="store_true",
                        help="导入既有 data/equipment.json 与 data/suppliers.json")
    parser.add_argument("--stats", action="store_true", help="只输出各表行数")
    args = parser.parse_args()

    # config 在导入时读取环境变量,故覆盖必须发生在导入之前。
    if args.db:
        os.environ["DA_DB_PATH"] = str(Path(args.db).resolve())
    if args.kb_dir:
        os.environ["KB_DIR"] = str(Path(args.kb_dir).resolve())

    from backend.storage import da_db, da_mock, da_seed, kb_library, kb_repo

    if args.stats:
        _print_stats(da_db)
        return 0

    path = da_db.init_db()
    kb_root = kb_library.ensure_kb_dirs()
    print(f"[建库] {path}")
    print(f"[图库] {kb_root}")
    print(f"[表数] {len(da_db.table_names())}")

    if args.import_legacy:
        _import_legacy(da_db, kb_repo)

    if args.seed:
        counts = da_seed.seed_all(overwrite=args.overwrite)
        print("[种子] " + ", ".join(f"{k}={v}" for k, v in counts.items()))

    if args.mock:
        selected = None if args.mock == "all" else [
            item.strip() for item in args.mock.split(",") if item.strip()
        ]
        results = da_mock.load(selected, overwrite=args.overwrite,
                               with_projects=not args.no_mock_projects)
        for industry, counts in results.items():
            label = da_mock.INDUSTRIES[industry]["name"]
            print(f"[模拟·{label}] " + ", ".join(f"{k}={v}" for k, v in counts.items()))

    if args.scan:
        for result in kb_repo.sync_all_drawings():
            if result.get("error"):
                print(f"[扫描] {result['component_code']}: 跳过 —— {result['error']}")
            else:
                print(f"[扫描] {result['component_code']}: "
                      f"新增 {result['added']} / 更新 {result['updated']} / 移除 {result['removed']}")

    _print_stats(da_db)
    return 0


def _import_legacy(da_db, kb_repo) -> None:
    """把现有 JSON 里的设备与供应商搬进 kb_ 表。字段缺失的按空处理,不臆造。"""
    from backend.config import DATA_DIR

    equipment_file = Path(DATA_DIR) / "equipment.json"
    if equipment_file.exists():
        records = json.loads(equipment_file.read_text(encoding="utf-8"))
        for rec in records if isinstance(records, list) else []:
            kb_repo.save_equipment({
                "equipment_id": rec.get("id") or None,
                "equipment_code": rec.get("code"),
                "name": rec.get("name") or "未命名设备",
                "model_no": rec.get("model") or rec.get("model_no"),
                "manufacturer": rec.get("manufacturer"),
                "workshop": rec.get("workshop"),
                "note": rec.get("note"),
                "capability": rec.get("capability") or rec.get("spec"),
            })
        print(f"[导入] equipment.json -> kb_equipment: {len(records)} 条")

    supplier_file = Path(DATA_DIR) / "suppliers.json"
    if supplier_file.exists():
        records = json.loads(supplier_file.read_text(encoding="utf-8"))
        for rec in records if isinstance(records, list) else []:
            # 旧结构把"可供物料+纯度+粒径"平铺在供应商上,这里拆成能力行。
            capability = {
                "material_name": rec.get("material"),
                "max_purity_pct": rec.get("max_purity_pct"),
                "d50_min_um": rec.get("d50_min_um"),
                "d50_max_um": rec.get("d50_max_um"),
                "moq": rec.get("moq"),
                "lead_time": rec.get("lead_time"),
            }
            kb_repo.save_supplier({
                "supplier_id": rec.get("id") or None,
                "name": rec.get("name") or "未命名供应商",
                "contact": rec.get("contact"),
                "note": rec.get("note"),
            }, capabilities=[capability] if rec.get("material") else [])
        print(f"[导入] suppliers.json -> kb_supplier: {len(records)} 条")


def _print_stats(da_db) -> None:
    conn = da_db.get_conn()
    rows = []
    for table in da_db.table_names(conn):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if count:
            rows.append((table, count))
    if not rows:
        print("[统计] 所有表为空")
        return
    width = max(len(t) for t, _ in rows)
    print("[统计] 非空表:")
    for table, count in rows:
        print(f"  {table.ljust(width)}  {count}")


if __name__ == "__main__":
    raise SystemExit(main())
