"""
生成一张示例"设备需求原图"(简化工程图)用于测试解析流程。
输出: samples/sample_bracket.png

画的是一个带 4 个安装孔的底板 + 一个导向衬套，附尺寸标注与标题栏，
足以让 Claude 解析出零件/特征/尺寸。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 850
BG = (255, 255, 255)
INK = (20, 20, 20)
DIM = (180, 30, 30)


def _font(size: int):
    # 优先含中日韩(CJK)字形的字体，否则中文会显示成方块
    for name in ("msyh.ttc", "msyhbd.ttc", "simsun.ttc", "simhei.ttf",
                 "arial.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f = _font(20)
    fs = _font(16)
    fbig = _font(26)

    # ---- 标题 ----
    d.text((30, 20), "传动支架总成  TRANSMISSION BRACKET ASSY", font=fbig, fill=INK)
    d.line((30, 60, W - 30, 60), fill=INK, width=2)

    # ============ 主视图: 底板 (俯视) ============
    d.text((90, 90), "件1 底板 (俯视图)  材料: Q235  厚 12", font=f, fill=INK)
    px, py, pw, ph = 120, 130, 520, 300   # 板在画布上的像素框 (代表 320 x 180)
    d.rectangle((px, py, px + pw, py + ph), outline=INK, width=3)

    # 4 个安装孔 (矩形阵列, 间距 280 x 140, Φ9)
    margin_x, margin_y = 120, 80   # 像素边距(对应 (520-? )/?), 仅示意
    holes = [
        (px + margin_x, py + margin_y),
        (px + pw - margin_x, py + margin_y),
        (px + margin_x, py + ph - margin_y),
        (px + pw - margin_x, py + ph - margin_y),
    ]
    for (hx, hy) in holes:
        r = 12
        d.ellipse((hx - r, hy - r, hx + r, hy + r), outline=INK, width=2)
        d.line((hx - r - 5, hy, hx + r + 5, hy), fill=INK, width=1)
        d.line((hx, hy - r - 5, hx, hy + r + 5), fill=INK, width=1)

    # 尺寸标注 320 (总长)
    yb = py + ph + 40
    d.line((px, yb, px + pw, yb), fill=DIM, width=2)
    d.line((px, yb - 6, px, yb + 6), fill=DIM, width=2)
    d.line((px + pw, yb - 6, px + pw, yb + 6), fill=DIM, width=2)
    d.text((px + pw / 2 - 20, yb + 6), "320", font=f, fill=DIM)

    # 尺寸标注 180 (总宽)
    xb = px + pw + 40
    d.line((xb, py, xb, py + ph), fill=DIM, width=2)
    d.line((xb - 6, py, xb + 6, py), fill=DIM, width=2)
    d.line((xb - 6, py + ph, xb + 6, py + ph), fill=DIM, width=2)
    d.text((xb + 6, py + ph / 2 - 10), "180", font=f, fill=DIM)

    # 孔间距 280
    yh = holes[0][1] - 40
    d.line((holes[0][0], yh, holes[1][0], yh), fill=DIM, width=2)
    d.text(((holes[0][0] + holes[1][0]) / 2 - 20, yh - 24), "280", font=fs, fill=DIM)
    # 孔间距 140
    xh = holes[0][0] - 35
    d.line((xh, holes[0][1], xh, holes[2][1]), fill=DIM, width=2)
    d.text((xh - 34, (holes[0][1] + holes[2][1]) / 2 - 8), "140", font=fs, fill=DIM)
    # 孔径
    d.text((holes[1][0] + 18, holes[1][1] - 10), "4×Φ9  M8安装孔", font=fs, fill=INK)

    # ============ 件2: 导向衬套 (剖视) ============
    d.text((760, 90), "件2 导向衬套  材料: 6061-T6  数量×2", font=f, fill=INK)
    cx, cy = 920, 320
    outer, inner, height = 70, 35, 150
    # 外轮廓(矩形代表圆柱侧视) + 中心轴孔
    d.rectangle((cx - outer, cy - height / 2, cx + outer, cy + height / 2), outline=INK, width=3)
    d.line((cx - inner, cy - height / 2, cx - inner, cy + height / 2), fill=INK, width=2)
    d.line((cx + inner, cy - height / 2, cx + inner, cy + height / 2), fill=INK, width=2)
    # 中心线
    d.line((cx, cy - height / 2 - 15, cx, cy + height / 2 + 15), fill=(120, 120, 120), width=1)
    # 标注 Φ40 外径 / Φ20 内径 / 高 30
    d.text((cx - 20, cy - height / 2 - 30), "Φ40", font=fs, fill=DIM)
    d.text((cx - 18, cy - 8), "Φ20", font=fs, fill=DIM)
    d.text((cx + outer + 12, cy - 8), "高 30", font=fs, fill=DIM)

    # ============ 标题栏 ============
    tb_y = H - 110
    d.rectangle((30, tb_y, W - 30, H - 30), outline=INK, width=2)
    d.line((30, tb_y + 40, W - 30, tb_y + 40), fill=INK, width=1)
    d.text((40, tb_y + 8), "设备: 传动支架总成   设计意图: 承载电机并固定到底板, 需可拆卸",
           font=f, fill=INK)
    d.text((40, tb_y + 48), "总体外形: 320 × 180 × 95 mm   一般公差: ISO 2768-m   "
                            "紧固件: GB/T 5783 M8×25 ×4", font=fs, fill=INK)

    out_dir = Path(__file__).resolve().parent.parent / "samples"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / "sample_bracket.png"
    img.save(out)
    print("已生成示例原图:", out)


if __name__ == "__main__":
    main()
