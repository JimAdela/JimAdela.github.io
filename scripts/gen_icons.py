# -*- coding: utf-8 -*-
"""生成站点图标：套色底 + 纸色衬线「无」。

输出 img/favicon.ico（16/32/48）、img/apple-touch-icon.png（180）、
pwa/icons/pwa_icon_{128,192,512}.png。全部铺满方块不留圆角，
字落在中心 62% 的安全区里，iOS 圆角与 Android maskable 裁切都切不到字。

用法：python scripts/gen_icons.py
"""
import pathlib

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRESS = (30, 91, 78)       # --press  #1E5B4E
PAPER = (252, 251, 248)    # --paper  #FCFBF8
GLYPH = "无"
FONTS = [
    r"C:\Windows\Fonts\NotoSerifSC-VF.ttf",
    r"C:\Windows\Fonts\STSONG.TTF",
    r"C:\Windows\Fonts\simsun.ttc",
]


def load_font(size, weight):
    for path in FONTS:
        if not pathlib.Path(path).exists():
            continue
        font = ImageFont.truetype(path, size)
        try:
            font.set_variation_by_axes([weight])
        except Exception:
            pass
        return font
    raise SystemExit("找不到中文衬线字体")


def render(px):
    # 宋体横画细，小尺寸要更粗更大才不糊；先画 4 倍再缩，边缘更干净
    small = px <= 64
    big = px * 4
    img = Image.new("RGB", (big, big), PRESS)
    draw = ImageDraw.Draw(img)
    font = load_font(int(big * (0.74 if small else 0.62)), 900 if small else 700)
    l, t, r, b = draw.textbbox((0, 0), GLYPH, font=font)
    x = (big - (r - l)) / 2 - l
    y = (big - (b - t)) / 2 - t
    draw.text((x, y), GLYPH, font=font, fill=PAPER)
    return img.resize((px, px), Image.LANCZOS)


def main():
    icons = ROOT / "pwa" / "icons"
    for size in (128, 192, 512):
        render(size).save(icons / f"pwa_icon_{size}.png", optimize=True)
    render(180).save(ROOT / "img" / "apple-touch-icon.png", optimize=True)
    ico = [render(s) for s in (48, 32, 16)]
    ico[0].save(ROOT / "img" / "favicon.ico", sizes=[(48, 48), (32, 32), (16, 16)], append_images=ico[1:])
    print("ok")


if __name__ == "__main__":
    main()
