"""几何自检：用引擎自己的引脚几何取绝对坐标，核对走线。

用法:  python pin_check.py            # 全部预设
       python pin_check.py ldo_1117   # 指定预设，并打印坐标表
"""
import sys

sys.path.insert(0, "D:/AutoDraw/app/engine")
import engine as E

lib_pins = E.lib_pins


def report(key, verbose=False):
    circ = E.CIRCUITS[key]()
    pins = {}          # (x, y) -> (ref, number, type)
    for s in circ["symbols"]:
        for num, (dx, dy, ptype) in lib_pins(s["lib_id"]).items():
            pins[(round(s["x"] + dx, 2), round(s["y"] + dy, 2))] = (
                s["ref"], num, ptype)

    segs = [tuple(round(v, 2) for v in w) for w in circ["wires"]]
    ends = {}          # 端点 -> 线数
    corners = set()
    for x1, y1, x2, y2 in segs:
        for a, b in (((x1, y1), (x2, y2)), ((x2, y2), (x1, y1))):
            ends[a] = ends.get(a, 0) + 1
        # 同向共线的相邻线段端点可视为拐角/中继点
        corners.add((x1, y1))
        corners.add((x2, y2))

    issues = []
    for pt, n in sorted(ends.items()):
        if pt in pins:
            continue
        on_mid = any(x1 < pt[0] < x2 and (y1 == y2 == pt[1])
                     or y1 < pt[1] < y2 and (x1 == x2 == pt[0])
                     for (x1, y1, x2, y2) in segs)
        if on_mid:
            continue        # T 型接触：KiCad 自动连通
        if n == 1:
            issues.append("死端 (%.2f,%.2f)：不接引脚也没别的线头在此" % (pt[0], pt[1]))

    for a in range(len(segs)):
        for b in range(a + 1, len(segs)):
            x1, y1, x2, y2 = segs[a]
            x3, y3, x4, y4 = segs[b]
            if y1 == y2 == y3 == y4 and min(x1, x2) < max(x3, x4) \
                    and min(x3, x4) < max(x1, x2) and \
                    not ({(x1, y1), (x2, y2)} & {(x3, y3), (x4, y4)}):
                issues.append("水平线重叠 y=%.2f: %s 与 %s" % (y1, segs[a], segs[b]))
            if x1 == x2 == x3 == x4 and min(y1, y2) < max(y3, y4) \
                    and min(y3, y4) < max(y1, y2) and \
                    not ({(x1, y1), (x2, y2)} & {(x3, y3), (x4, y4)}):
                issues.append("垂直线重叠 x=%.2f: %s 与 %s" % (x1, segs[a], segs[b]))

    for pt, (ref, num, ptype) in sorted(pins.items(), key=lambda kv: str(kv)):
        if pt not in ends:
            issues.append("%s 引脚 %s (%s) 没线接: (%.2f, %.2f)"
                          % (ref, num, ptype, pt[0], pt[1]))

    if verbose:
        print("  引脚: " + ", ".join("%s.%s@(%.2f,%.2f)%s"
                                     % (r, n, p[0], p[1], "!" if t == "power_in" else "")
                                     for p, (r, n, t) in sorted(
                                         pins.items(), key=lambda kv: (kv[0][1], kv[0][0]))))
        print("  线端: " + ", ".join("(%.2f,%.2f)×%d" % (p[0], p[1], c)
                                     for p, c in sorted(ends.items())))
    return issues


keys = sys.argv[1:] or sorted(E.CIRCUITS)
verbose = "-v" in keys
keys = [k for k in keys if k != "-v"]
bad = 0
for k in keys:
    iss = report(k, verbose or len(keys) == 1)
    if iss:
        bad += 1
        print("## %s: %d 处" % (k, len(iss)))
        for i in iss:
            print("   " + i)
    else:
        print("## %s: OK" % k)
print("\n%d/%d 条有几何问题" % (bad, len(keys)))
