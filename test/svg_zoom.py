"""把 KiCad 导出的 SVG 按局部区域放大截图，用来看清值文本有没有互相压字。

整页截图里电路只占左上角一小块，1200px 下字全糊在一起；这里改写 SVG 的
viewBox 做真正的放大，再交给无头 Chrome 出图。

用法：python test/svg_zoom.py <key> [<key> ...] [--box x y w h] [--outdir DIR]
"""
import io
import os
import re
import subprocess
import sys

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BOX = (70.0, 26.0, 80.0, 56.0)


def zoom(svg_path, png_path, box, size=1400):
    s = io.open(svg_path, encoding="utf-8").read()
    w, h = size, int(size * box[3] / box[2])
    s = re.sub(r'width="[0-9.]+mm" height="[0-9.]+mm"',
               'width="%d" height="%d"' % (w, h), s, count=1)
    s = re.sub(r'viewBox="[^"]+"',
               'viewBox="%g %g %g %g"' % box, s, count=1)
    tmp = png_path[:-4] + ".viewbox.svg"
    io.open(tmp, "w", encoding="utf-8", newline="\n").write(s)
    subprocess.check_call(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--window-size=%d,%d" % (w, h), "--force-device-scale-factor=1",
         "--screenshot=" + png_path, "file:///" + tmp.replace("\\", "/")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.remove(tmp)
    return png_path


def main():
    argv = sys.argv[1:]
    box, outdir, keys = BOX, "D:/AutoDraw_Test/preview", []
    if "--box" in argv:
        i = argv.index("--box")
        box = tuple(float(t) for t in argv[i + 1:i + 5])
        del argv[i:i + 5]
    if "--outdir" in argv:
        i = argv.index("--outdir")
        outdir = argv[i + 1]
        del argv[i:i + 2]
    keys = argv
    for k in keys:
        src = os.path.join(outdir, k + ".svg")
        if not os.path.exists(src):
            print("缺 SVG:", src)
            continue
        dst = os.path.join(outdir, "z_" + k + ".png")
        zoom(src, dst, box)
        print(dst)


if __name__ == "__main__":
    main()
