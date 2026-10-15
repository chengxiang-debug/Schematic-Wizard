# -*- coding: utf-8 -*-
"""全目录的文字压字体检：把每条预设的 Reference/Value 当成矩形，找互相压字的对子。

包围盒模型和摆放器共用一份（builder._text_box + TEXT_TOL/BODY_TOL），
所以这里报出来的都是摆放器自己承认「实在没位置」的那几处。
另外也查文字压到别家本体：KiCad 会把图形画在文字上面，读不出来。

用法（在 D:/AutoDraw 下跑）:
  D:/KiCad/bin/python.exe test/text_check.py                # 全量
  D:/KiCad/bin/python.exe test/text_check.py tl431 xtal     # 只看 key 含这些串的
  D:/KiCad/bin/python.exe test/text_check.py -v             # 打印每类前 8 处
"""
import sys
import time
from collections import defaultdict

sys.path.insert(0, "D:/AutoDraw/app/engine")
import engine as E          # noqa: E402
import builder as B         # noqa: E402

box = B._text_box          # 和摆放器同一套包围盒模型，免得两边判断对不上


def bodies(circ):
    """每个实体符号的本体包围盒：文字压在图形上一样读不出来。"""
    out = []
    for s in circ["symbols"]:
        if s["ref"].startswith("#"):
            continue
        g = E.symbol_geometry(s["lib_id"], s["x"], s["y"], s.get("rot", 0))
        pts = [p for item in g["body"] for p in item["pts"]]
        if not pts:
            continue
        out.append((s["ref"], (round(min(p[0] for p in pts), 2),
                               round(max(p[0] for p in pts), 2),
                               round(min(p[1] for p in pts), 2),
                               round(max(p[1] for p in pts), 2))))
    return out


def hits(circ):
    """一条电路里所有压字对：[(a方, b方, 重叠宽, 重叠高)]。"""
    items = []
    for s in circ["symbols"]:
        if s["ref"].startswith("#"):
            continue
        rot = s.get("rot", 0) % 360
        for kind, xy, txt in (("ref", s["ref_xy"], s["ref"]),
                              ("val", s["val_xy"], s["val"])):
            if not txt or s.get("ref_hide" if kind == "ref" else "val_hide"):
                continue
            items.append((s["ref"], kind, txt, box(xy[0], xy[1], txt, rot)))
    for ref, bd in bodies(circ):
        items.append((ref, "body", "", bd))
    out = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if a[1] == "body" and b[1] == "body":
                continue
            # 本体只按包围盒算，斜边/圆弧的空白角会造假阳性，容忍量放宽
            tol = B.TEXT_TOL if "body" not in (a[1], b[1]) else B.BODY_TOL
            ox = min(a[3][1], b[3][1]) - max(a[3][0], b[3][0])
            oy = min(a[3][3], b[3][3]) - max(a[3][2], b[3][2])
            if ox > tol and oy > tol:
                out.append((a, b, round(ox, 2), round(oy, 2)))
    return out


def main():
    args = [a for a in sys.argv[1:]]
    verbose = "-v" in args
    pats = [a for a in args if not a.startswith("-")]
    t0 = time.time()
    reg = E.presets()
    tpl_bad, rows, n = defaultdict(int), [], 0
    for key, fn in sorted(reg.items()):
        if pats and not any(p in key for p in pats):
            continue
        try:
            circ = fn()
        except Exception:
            continue
        n += 1
        hs = hits(circ)
        if not hs:
            continue
        tpl = circ.get("tpl") or key
        tpl_bad[tpl] += 1
        if len(rows) < 400:
            rows.append((key, tpl, hs))
    bad = sum(tpl_bad.values())
    print("预设 %d 条，%.0fs；有压字的 tpl %d 个，累计 %d 条预设"
          % (n, time.time() - t0, len(tpl_bad), bad))
    for tpl, c in sorted(tpl_bad.items(), key=lambda kv: -kv[1]):
        print("  %-22s %5d 条" % (tpl, c))
    if verbose or not rows:
        pass
    for key, tpl, hs in rows[:60 if verbose else 12]:
        print("## %s (%s)" % (key, tpl))
        for a, b, ox, oy in hs[:8]:
            print("   %s.%s %-12s × %s.%s %-12s  重叠 %.2f×%.2f"
                  % (a[0], a[1], a[2], b[0], b[1], b[2], ox, oy))
    sys.exit(1 if rows else 0)


if __name__ == "__main__":
    main()
