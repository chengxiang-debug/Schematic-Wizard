# -*- coding: utf-8 -*-
"""生成 AutoDraw 的 exe 图标：紫粉渐变圆角方块 + 走线 + 过孔（和界面左上角标志一致）。"""
import os

from PIL import Image, ImageDraw

S = 1024
VIOLET = (157, 107, 255)
PINK = (255, 61, 127)
INK = (13, 13, 18)


def gradient(size):
    step = 256
    img = Image.new("RGB", (step, step))
    px = img.load()
    denom = float(2 * step - 2)
    for y in range(step):
        for x in range(step):
            t = float(x + y) / denom
            px[x, y] = tuple(int(round(a + (b - a) * t))
                             for a, b in zip(VIOLET, PINK))
    return img.resize((size, size), Image.BICUBIC)


def main():
    base = gradient(S).convert("RGBA")
    mask = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.215), fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(base, (0, 0), mask)

    d = ImageDraw.Draw(img)
    trace = int(S * 0.075)
    y = S // 2
    pad = int(S * 0.13)
    ink = INK + (200,)
    d.rounded_rectangle([pad, y - trace // 2, S - pad, y + trace // 2],
                        radius=trace // 2, fill=ink)
    r = int(S * 0.155)
    d.ellipse([S // 2 - r, y - r, S // 2 + r, y + r], fill=ink)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "icon.ico")
    small = img.resize((256, 256), Image.LANCZOS)
    small.save(out, format="ICO",
               sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print("wrote", out, os.path.getsize(out), "bytes")


if __name__ == "__main__":
    main()
