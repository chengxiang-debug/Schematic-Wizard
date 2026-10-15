# -*- coding: utf-8 -*-
"""
AutoDraw 引擎 — KiCad 10 一键电路生成，无界面。
运行: <python> engine.py   （stdin 收一个 JSON 请求, stdout 吐 NDJSON 事件）
请求: {"action": "list" | "detail" | "env" | "generate",
       "key": "led_3v3",              # detail 用
       "keys": ["led_3v3"], "outdir": "D:\\...", "sch": true, "pcb": true,
       "jlc": true,
       "paths": {"kicad_root": "D:\\KiCad", "kicad_py": "D:\\KiCad\\bin\\python.exe"}}
事件: {"ev": "log"|"progress"|"presets"|"detail"|"env"|"result"|"error"|"done", ...}
原理图拓扑规则(KiCad 10 实测): 纯"引脚-导线-引脚"链, 无结点, 无死端。
"""
import csv
import io
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import uuid
import zipfile

# ============================ 路径配置 ============================
KICAD_ROOT = r"D:\KiCad"


def set_kicad_root(root):
    """KiCad 安装根目录可改；派生 CLI / 符号库 / 封装库路径。"""
    global KICAD_ROOT, KICAD_BIN, KICAD_SYM, KICAD_FPS, KICAD_CLI
    KICAD_ROOT = root
    KICAD_BIN = os.path.join(root, "bin")
    KICAD_SYM = os.path.join(root, "share", "kicad", "symbols")
    KICAD_FPS = os.path.join(root, "share", "kicad", "footprints")
    KICAD_CLI = os.path.join(KICAD_BIN, "kicad-cli.exe")


set_kicad_root(KICAD_ROOT)

KICAD_PY = r"D:\KiCad\bin\python.exe"
DEFAULT_OUT = r"D:\AutoDraw_Output"


def check_env():
    """返回 {"ok":bool,"missing":[...],"version":str,"paths":{...}}。"""
    probes = {
        "kicad_cli": KICAD_CLI,
        "kicad_python": KICAD_PY,
        "symbols": KICAD_SYM,
        "footprints": KICAD_FPS,
    }
    missing = [k for k, p in probes.items() if not os.path.exists(p)]
    version = ""
    if "kicad_cli" not in missing:
        try:
            r = subprocess.run([KICAD_CLI, "version"],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=20)
            version = (r.stdout or r.stderr or "").strip().splitlines()[0]
        except Exception:
            version = ""
    return {"ok": not missing, "missing": missing, "version": version,
            "paths": probes}
# ============================ BOM / 报告 ============================
def export_bom(circuit, outdir):
    """导出 CSV BOM。"""
    rows = [("Reference", "Value", "Footprint", "Qty")]
    for s in circuit["symbols"]:
        if s["ref"].startswith("#"):
            continue
        rows.append((s["ref"], s["val"], s["fp"], "1"))
    bom_path = os.path.join(outdir, circuit["name"] + "_bom.csv")
    with io.open(bom_path, "w", encoding="utf-8-sig", newline="\n") as f:
        for r in rows:
            f.write(",".join('"%s"' % c for c in r) + "\n")
    return bom_path


def export_html_report(circuit, outdir, erc_ok, results, jlc_files=None):
    """生成单电路 HTML 报告, 内嵌 SVG 原理图 + PCB 渲染图 + BOM 表。"""
    name = circuit["name"]
    svg_path = os.path.join(outdir, name + ".svg")
    png_path = os.path.join(outdir, name + "_pcb_3d.png")
    svg_html = ""
    if os.path.exists(svg_path):
        with io.open(svg_path, encoding="utf-8") as f:
            svg_html = f.read()
    png_html = ""
    if os.path.exists(png_path):
        png_html = ('<img src="%s" style="max-width:480px;border:1px solid #ccc;'
                    'border-radius:6px">' % (name + "_pcb_3d.png"))
    bom_rows = ""
    for s in circuit["symbols"]:
        if s["ref"].startswith("#"):
            continue
        bom_rows += ("<tr><td>%s</td><td>%s</td><td><code>%s</code></td></tr>"
                     % (s["ref"], s["val"], s["fp"]))
    checks = "".join(
        "<li>%s: <b style='color:%s'>%s</b></li>" % (
            k, "#2f6fd0" if v else "#c33", "通过" if v else "未通过/跳过")
        for k, v in results.items())
    names = [name + s for s in (
        ".kicad_sch", ".pdf", ".net", "_erc.rpt", ".kicad_pcb", "_drc.rpt",
        "_pcb_3d.png", "_bom.csv", ".svg", "_report.html")]
    for p in (jlc_files or {}).values():
        names.append(os.path.basename(p))
    file_rows = "".join(
        '<li><a href="%s">%s</a></li>' % (n, n)
        for n in names if os.path.exists(os.path.join(outdir, n)))
    html = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>AutoDraw 报告 — %s</title>
<style>
body{font-family:'Segoe UI',sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#222}
h1{border-bottom:2px solid #4a90d9;padding-bottom:8px}
.card{background:#f8f9fa;border-radius:8px;padding:16px;margin:16px 0}
svg{max-width:100%%;height:auto;background:#fff}
table{border-collapse:collapse;width:100%%}
td,th{border:1px solid #ddd;padding:6px 10px;text-align:left}
th{background:#4a90d9;color:#fff}
code{background:#eee;padding:1px 4px;border-radius:3px}
</style></head><body>
<h1>AutoDraw 生成报告</h1>
<p><b>电路:</b> %s &nbsp;|&nbsp; <b>标题:</b> %s</p>
<div class="card"><h2>检查结果</h2><ul>%s</ul></div>
<div class="card"><h2>原理图</h2>%s</div>
<div class="card"><h2>PCB 3D 渲染</h2>%s</div>
<div class="card"><h2>BOM</h2>
<table><tr><th>位号</th><th>值</th><th>封装</th></tr>%s</table></div>
<div class="card"><h2>文件清单</h2><ul>
%s
</ul></div>
<p style="color:#888;font-size:12px">Generated by AutoDraw</p>
</body></html>""" % (name, name, circuit["title"], checks, svg_html,
                     png_html, bom_rows, file_rows)
    html_path = os.path.join(outdir, name + "_report.html")
    with io.open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    return html_path


# ============================ 原理图核心 ============================
def uid():
    return str(uuid.uuid4())


def extract_symbol(path, name):
    with io.open(path, "r", encoding="utf-8") as f:
        text = f.read()
    key = '(symbol "%s"' % name
    idx = text.find(key)
    if idx < 0:
        raise RuntimeError("symbol %r not found in %s" % (name, path))
    depth, i, in_str = 0, idx, False
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return text[idx: i + 1]
        i += 1
    raise RuntimeError("unbalanced parens for %r" % name)


def find_extends(block):
    """从符号块中提取 (extends "父符号") 的父符号名，无则返回 None。"""
    import re
    m = re.search(r'\(extends\s+"([^"]+)"\)', block)
    return m.group(1) if m else None


def _props_of(block):
    return re.findall(r'\(property\s+"[^"]+".*?\n\t\t\)', block, re.S)


def _prop_name(p):
    return re.match(r'\(property\s+"([^"]+)"', p).group(1)


def _flatten_symbol(path, name, _depth=0):
    """沿 extends 链把符号展开成自带引脚与全部字段的独立定义。

    ERC 要求嵌入副本至少是库展开结果的超集：同名字段整块替换（含位置与字体
    effects），子符号独有字段（如 ki_fp_filters）必须一并保留。
    """
    if _depth > 8:
        raise RuntimeError("extends 链过深: %r" % name)
    block = extract_symbol(path, name)
    parent = find_extends(block)
    if not parent:
        return block

    body = _flatten_symbol(path, parent, _depth + 1)
    child_props = {}
    for p in _props_of(block):
        child_props[_prop_name(p)] = p

    merged = []
    for p in _props_of(body):
        n = _prop_name(p)
        merged.append(child_props.pop(n, None) or p)
    merged += list(child_props.values())

    for p in _props_of(body):
        body = body.replace("\n\t\t" + p, "", 1)
    m = re.search(r'\n\t\t\(symbol "', body)
    anchor = m.start() if m else body.rfind("\n\t\t(embedded_fonts")
    body = body[:anchor] + "\n\t\t" + "\n\t\t".join(merged) + body[anchor:]

    body = body.replace('(symbol "%s"' % parent, '(symbol "%s"' % name, 1)
    body = body.replace('"%s_' % parent, '"%s_' % name)
    return body


def lib_symbol(nickname, path, name):
    """返回嵌入 .kicad_sch 的 lib_symbol 块；extends 链已展开成独立定义。"""
    block = _flatten_symbol(path, name)
    block = block.replace('(symbol "%s"' % name,
                          '(symbol "%s:%s"' % (nickname, name), 1)
    return "\n\t\t" + block.replace("\n", "\n\t")


# ==================== 符号几何（界面预览与自动布局共用） ====================
# 符号本地坐标系 Y 轴朝上，图纸 Y 轴朝下：sheet = (x + dx, y - dy)。
# 引脚 (at X Y A) 里的 (X, Y) 就是电气连接点，线从这一点朝本体画 length 长。

_LIB_CACHE = {}
_GEO_CACHE = {}
_XY_RE = re.compile(r'\((?:xy|start|end|mid|center)\s+(-?[\d.]+)\s+(-?[\d.]+)\)')


def sexp_blocks(text, token_re):
    """括号配平地取出所有匹配 token_re 的 s-表达式（含最外层括号）。"""
    out = []
    for m in re.finditer(token_re, text):
        depth, i, in_str = 0, m.start(), False
        while i < len(text):
            c = text[i]
            if in_str:
                if c == "\\":
                    i += 2
                    continue
                if c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        out.append(text[m.start(): i + 1])
                        break
            i += 1
    return out


def lib_block(lib_id):
    """取展开后的库符号定义文本（缓存，反复预览不重复读盘）。"""
    if lib_id not in _LIB_CACHE:
        nick, name = lib_id.split(":", 1)
        path = os.path.join(KICAD_SYM, nick + ".kicad_sym")
        _LIB_CACHE[lib_id] = _flatten_symbol(path, name)
    return _LIB_CACHE[lib_id]


def unit_blocks(block):
    """→ [(单元号, 变体号, 文本)]，即 lib_symbol 里的 NAME_单元_变体 子块。"""
    out = []
    for m in re.finditer(r'\(symbol "[^"]*_(\d+)_(\d+)"', block):
        depth, i, in_str = 0, m.start(), False
        while i < len(block):
            c = block[i]
            if in_str:
                if c == "\\":
                    i += 2
                    continue
                if c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        out.append((int(m.group(1)), int(m.group(2)),
                                    block[m.start(): i + 1]))
                        break
            i += 1
    return out


def lib_geometry(lib_id, unit=1):
    """→ (本体图元, 引脚表)，坐标为符号本地系（Y 向上）。"""
    cache_key = (lib_id, unit)
    if cache_key in _GEO_CACHE:
        return _GEO_CACHE[cache_key]
    block = lib_block(lib_id)
    subs = unit_blocks(block)

    body = []
    for u, _v, sub in subs:
        if u != 0:
            continue
        for item in sexp_blocks(sub, r'\((?:rectangle|polyline|circle|arc)'):
            kind = re.match(r'\((\w+)', item).group(1)
            at = re.search(
                r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?\s*\)', item)
            body.append({
                "kind": kind,
                "pts": [[float(a), float(b)] for a, b in _XY_RE.findall(item)],
                "at": [float(at.group(1)), float(at.group(2)),
                       float(at.group(3) or 0)] if at else None,
            })
    if not body:      # 运放这类把本体画在 unit 1 里，unit 0 只有引脚
        for _u, _v, sub in subs:
            if _u != unit:
                continue
            for item in sexp_blocks(sub, r'\((?:rectangle|polyline|circle|arc)'):
                kind = re.match(r'\((\w+)', item).group(1)
                at = re.search(
                    r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?\s*\)', item)
                body.append({
                    "kind": kind,
                    "pts": [[float(a), float(b)] for a, b in _XY_RE.findall(item)],
                    "at": [float(at.group(1)), float(at.group(2)),
                           float(at.group(3) or 0)] if at else None,
                })

    # 引脚通常在 _1_N；单只引脚的电源符号(如 PWR_FLAG)放在 _0_N，取不到就全扫。
    # 隐藏电源脚另占一个 unit（NE555 的 1/8、LM358 的 4/8）：那种 unit 没有图元、
    # 引脚全是 power_in/power_out。嵌入的 lib_symbol 是整块超集，这些脚在 ERC 里
    # 真实存在，不当成可用引脚就会漏接而报 pin_not_connected。
    main = [s for _u, _v, s in subs if _u == unit] or \
           [sub for _u, _v, sub in subs]

    def _power_unit(sub):
        if re.search(r'\((?:rectangle|polyline|circle|arc)\b', sub):
            return False
        types = [m.group(1) for m in
                 re.finditer(r'\(pin\s+(\w+)', sub)]
        return bool(types) and all(t in ("power_in", "power_out")
                                   for t in types)

    extra = [s for _u, _v, s in subs
             if s not in main and _power_unit(s)]
    pin_src = main + extra
    pins = []
    got = set()
    for sub in pin_src:
        for item in sexp_blocks(sub, r'\(pin\s'):
            head = re.search(r'\(pin\s+(\S+)\s+(\S+)', item)
            at = re.search(
                r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\)', item)
            ln = re.search(r'\(length\s+(-?[\d.]+)\)', item)
            nm = re.search(r'\(name\s+"([^"]*)"', item)
            nu = re.search(r'\(number\s+"([^"]*)"', item)
            if not (head and at):
                continue
            num = nu.group(1) if nu else "?"
            if num in got:          # 同号引脚只认第一个（跳线/多位置件）
                continue
            got.add(num)
            pins.append({
                "num": num,
                "name": nm.group(1) if nm else "",
                "type": head.group(1),
                "shape": head.group(2),
                "x": float(at.group(1)), "y": float(at.group(2)),
                "angle": float(at.group(3)),
                "len": float(ln.group(1) if ln else 0),
            })
    _GEO_CACHE[cache_key] = (body, pins)
    return body, pins


def rot_pt(px, py, angle):
    r = math.radians(angle)
    c, s = math.cos(r), math.sin(r)
    return (px * c - py * s, px * s + py * c)


def to_sheet(x, y, dx, dy, rot=0):
    """符号本地偏移 → 图纸绝对坐标（先转本地旋转，再翻 Y 轴）。"""
    rx, ry = rot_pt(dx, dy, rot)
    return (round(x + rx, 2), round(y - ry, 2))


def lib_pins(lib_id, rot=0):
    """{引脚号: (dx, dy, 类型)}，偏移已是图纸方向（可直接加到元件坐标上）。"""
    out = {}
    for p in lib_geometry(lib_id)[1]:
        rx, ry = rot_pt(p["x"], p["y"], rot)
        out[p["num"]] = (round(rx, 2), round(-ry, 2), p["type"])
    return out


def symbol_geometry(lib_id, x, y, rot=0):
    """元件在图纸上的图元与引脚，界面拿来直接画。"""
    body_src, pin_src = lib_geometry(lib_id)
    body = []
    for item in body_src:
        pts = []
        for px, py in item["pts"]:
            if item["at"]:
                ax, ay, aa = item["at"]
                qx, qy = rot_pt(px, py, aa)
                px, py = qx + ax, qy + ay
            pts.append(list(to_sheet(x, y, px, py, rot)))
        if pts:
            body.append({"kind": item["kind"], "pts": pts})
    pins = []
    for p in pin_src:
        cx, cy = to_sheet(x, y, p["x"], p["y"], rot)
        # 引脚线段从电气端朝本体方向延伸，本地角度在图纸上是取负
        tx, ty = rot_pt(p["len"], 0.0, -(p["angle"] + rot))
        pins.append({"num": p["num"], "name": p["name"], "type": p["type"],
                     "x": cx, "y": cy, "tx": round(cx + tx, 2),
                     "ty": round(cy + ty, 2)})
    return {"body": body, "pins": pins}


def circuit_geometry(circuit):
    """把预设摊成预览数据：元件图元 + 引脚 + 导线 + 包围盒。"""
    symbols, pins = [], []
    for s in circuit["symbols"]:
        g = symbol_geometry(s["lib_id"], s["x"], s["y"], s.get("rot", 0))
        for p in g["pins"]:
            pins.append(dict(p, ref=s["ref"]))
        symbols.append({"ref": s["ref"], "val": s["val"], "lib": s["lib_id"],
                        "x": s["x"], "y": s["y"], "body": g["body"],
                        "rx": s["ref_xy"][0], "ry": s["ref_xy"][1],
                        "vx": s["val_xy"][0], "vy": s["val_xy"][1],
                        "rh": bool(s.get("ref_hide")),
                        "vh": bool(s.get("val_hide"))})
    wires = [[round(a, 2), round(b, 2), round(c, 2), round(d, 2)]
             for a, b, c, d in circuit["wires"]]
    extras = []
    for sy in symbols:
        for item in sy["body"]:
            extras.extend(item["pts"])
        extras.append([sy["rx"], sy["ry"]])
        extras.append([sy["vx"], sy["vy"]])
    return {"symbols": symbols, "pins": pins, "wires": wires,
            "bbox": _geometry_bbox(pins, wires, extras)}


def _geometry_bbox(pins, wires, extras=()):
    xs, ys = [p["x"] for p in pins], [p["y"] for p in pins]
    for w in wires:
        xs += [w[0], w[2]]
        ys += [w[1], w[3]]
    for ex in extras:
        xs.append(ex[0])
        ys.append(ex[1])
    if not xs:
        return [90.0, 30.0, 120.0, 70.0]
    return [round(min(xs) - 6, 2), round(min(ys) - 6, 2),
            round(max(xs) + 6, 2), round(max(ys) + 6, 2)]


def circuit_nets(circuit):
    """导线+引脚做并查集 → 连通铜皮带标号，预览配色和网络检查都用它。"""
    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    points = {}
    pwr_name = {}
    for s in circuit["symbols"]:
        if s["ref"].startswith("#PWR"):
            pwr_name[s["ref"]] = s["val"]
        for num, (dx, dy, _t) in lib_pins(s["lib_id"], s.get("rot", 0)).items():
            points.setdefault((round(s["x"] + dx, 2),
                               round(s["y"] + dy, 2)), []).append(
                "%s.%s" % (s["ref"], num))
    segs = [(round(a, 2), round(b, 2), round(c, 2), round(d, 2))
            for a, b, c, d in circuit["wires"]]
    for i, (x1, y1, x2, y2) in enumerate(segs):
        union((x1, y1), (x2, y2))
        for (px, py) in points:
            if _pt_seg((px, py), (x1, y1), (x2, y2)) == 0:
                union((px, py), (x1, y1))
        # 线头搭在别的线身上（T 型结）KiCad 也算连通，只靠引脚会漏判
        for j in range(i + 1, len(segs)):
            x3, y3, x4, y4 = segs[j]
            if (_pt_seg((x1, y1), (x3, y3), (x4, y4)) == 0
                    or _pt_seg((x2, y2), (x3, y3), (x4, y4)) == 0
                    or _pt_seg((x3, y3), (x1, y1), (x2, y2)) == 0
                    or _pt_seg((x4, y4), (x1, y1), (x2, y2)) == 0):
                union((x1, y1), (x3, y3))
    groups = {}
    for pt, names in points.items():
        groups.setdefault(find(pt), {"pins": [], "wires": []})
        groups[find(pt)]["pins"].extend(names)
    for i, (x1, y1, x2, y2) in enumerate(segs):
        root = find((x1, y1))
        groups.setdefault(root, {"pins": [], "wires": []})
        groups[root]["wires"].append(i)

    out = []
    for i, g in enumerate(sorted(groups.values(),
                                 key=lambda v: str(v["pins"]))):
        members = sorted(set(g["pins"]))
        label = next((pwr_name[m.split(".")[0]] for m in members
                      if m.split(".")[0] in pwr_name), None)
        out.append({"id": i,
                    "name": label or ("Net-" + members[0].replace(".", "-")
                                      if members else "悬空铜皮"),
                    "pins": members, "wires": sorted(g["wires"])})
    return out


def _pt_seg(p, a, b):
    """点到线段距离（0 = 落在线上）。"""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return round(math.hypot(px - ax, py - ay), 4)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return round(math.hypot(px - (ax + t * dx), py - (ay + t * dy)), 4)


def wire(x1, y1, x2, y2):
    return (
        "\t(wire\n\t\t(pts\n\t\t\t(xy %s %s) (xy %s %s)\n\t\t)\n"
        "\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n"
        '\t\t(uuid "%s")\n\t)\n' % (x1, y1, x2, y2, uid())
    )


def prop(name, value, x, y, hide=False, justify=None, angle=0):
    eff = "\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)"
    if justify:
        eff += "\n\t\t\t\t(justify %s)" % justify
    if hide:
        eff += "\n\t\t\t\t(hide yes)"
    eff += "\n\t\t\t)"
    return ('\t\t(property "%s" "%s"\n\t\t\t(at %s %s %s)\n%s\n\t\t)\n'
            % (name, value, x, y, angle, eff))


def symbol_inst(project, root_uuid, lib_id, x, y, rot, ref, value, footprint,
                pins, ref_xy, val_xy, ref_hide=False, val_hide=False):
    s = "\t(symbol\n"
    s += '\t\t(lib_id "%s")\n' % lib_id
    s += "\t\t(at %s %s %s)\n\t\t(unit 1)\n" % (x, y, rot)
    s += "\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n"
    s += "\t\t(on_board yes)\n\t\t(dnp no)\n"
    s += '\t\t(uuid "%s")\n' % uid()
    s += prop("Reference", ref, ref_xy[0], ref_xy[1], hide=ref_hide, justify="left")
    s += prop("Value", value, val_xy[0], val_xy[1], hide=val_hide, justify="left")
    s += prop("Footprint", footprint, x, y, hide=True)
    s += prop("Datasheet", "~", x, y, hide=True)
    for p in pins:
        s += '\t\t(pin "%s"\n\t\t\t(uuid "%s")\n\t\t)\n' % (p, uid())
    s += ("\t\t(instances\n\t\t\t(project \"%s\"\n\t\t\t\t(path \"/%s\"\n"
          "\t\t\t\t\t(reference %s)\n\t\t\t\t\t(unit 1)\n\t\t\t\t)\n"
          "\t\t\t)\n\t\t)\n\t)\n" % (project, root_uuid, ref))
    return s


# ============================ 电路预设 ============================
# 每条预设 = {symbols, wires, libs}  几何全部落在 1.27mm 网格
# 拓扑铁律: 每根导线两端都必须落在引脚上(可经角点拐弯), 禁止结点/死端。

def _circuit_led_3v3():
    """3V3 -> R1(330) -> LED -> GND, 双 PWR_FLAG。"""
    def S(lib_id, x, y, ref, val, fp, pins, rx, ry, vx, vy, rh=False, vh=False):
        return dict(lib_id=lib_id, x=x, y=y, rot=0, ref=ref, val=val,
                    fp=fp, pins=pins, ref_xy=(rx, ry), val_xy=(vx, vy),
                    ref_hide=rh, val_hide=vh)

    return dict(
        name="led_3v3",
        title="3V3 Power LED",
        symbols=[
            S("power:+3V3", 101.6, 35.56, "#PWR01", "+3V3", "", ["1"],
              101.6, 39.37, 101.6, 31.75, True, False),
            S("Device:R", 101.6, 45.72, "R1", "330",
              "Resistor_SMD:R_0603_1608Metric", ["1", "2"],
              103.156, 44.45, 103.156, 46.99),
            S("Device:LED", 101.6, 55.88, "D1", "LED",
              "LED_SMD:LED_0603_1608Metric", ["1", "2"],
              103.156, 54.61, 103.156, 57.15),
            S("power:GND", 101.6, 64.77, "#PWR02", "GND", "", ["1"],
              101.6, 71.12, 101.6, 68.58, True, False),
            S("power:PWR_FLAG", 111.76, 35.56, "#FLG01", "PWR_FLAG", "", ["1"],
              111.76, 33.02, 111.76, 29.21, True, False),
            S("power:PWR_FLAG", 111.76, 64.77, "#FLG02", "PWR_FLAG", "", ["1"],
              111.76, 62.23, 111.76, 59.69, True, False),
        ],
        wires=[
            (101.6, 35.56, 101.6, 41.91),   # +3V3 -> R1.1
            (101.6, 35.56, 111.76, 35.56),  # +3V3 -> FLG01
            (101.6, 49.53, 105.41, 49.53),  # R1.2 -> corner
            (105.41, 49.53, 105.41, 55.88), # corner -> D1.A
            (97.79, 55.88, 97.79, 64.77),   # D1.K -> corner
            (97.79, 64.77, 101.6, 64.77),   # corner -> GND
            (101.6, 64.77, 111.76, 64.77),  # GND -> FLG02
        ],
        libs=[("Device", "R"), ("Device", "LED"),
              ("power", "+3V3"), ("power", "GND"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("R1", "Resistor_SMD", "R_0603_1608Metric", 20, 15, 0),
                ("D1", "LED_SMD", "LED_0603_1608Metric", 30, 15, 180),
            ],
            tracks=[
                ("R1", "2", "D1", "2", "Net-(D1-A)"),
            ],
        ),
    )


def _circuit_divider():
    """分压: 3V3 -> R1(10k) -> 节点 -> R2(10k) -> GND。节点出线接FLG? 无源不接。
    拓扑: +3V3 pin—线—R1.1 ; R1.2—线—R2.1 ; R2.2—线—GND pin ; 电源/地各挂 FLG。
    """
    def S(lib_id, x, y, ref, val, fp, pins, rx, ry, vx, vy, rh=False, vh=False):
        return dict(lib_id=lib_id, x=x, y=y, rot=0, ref=ref, val=val,
                    fp=fp, pins=pins, ref_xy=(rx, ry), val_xy=(vx, vy),
                    ref_hide=rh, val_hide=vh)

    RFP = "Resistor_SMD:R_0603_1608Metric"
    return dict(
        name="divider",
        title="Resistive Divider 1:1 (3V3)",
        symbols=[
            S("power:+3V3", 101.6, 30.48, "#PWR01", "+3V3", "", ["1"],
              101.6, 34.29, 101.6, 26.67, True, False),
            S("power:PWR_FLAG", 111.76, 30.48, "#FLG01", "PWR_FLAG", "", ["1"],
              111.76, 27.94, 111.76, 25.4, True, False),
            S("Device:R", 101.6, 38.1, "R1", "10k", RFP, ["1", "2"],
              103.156, 36.83, 103.156, 39.37),
            S("Device:R", 101.6, 48.26, "R2", "10k", RFP, ["1", "2"],
              103.156, 46.99, 103.156, 49.53),
            S("power:GND", 101.6, 55.88, "#PWR02", "GND", "", ["1"],
              101.6, 62.23, 101.6, 59.69, True, False),
            S("power:PWR_FLAG", 111.76, 55.88, "#FLG02", "PWR_FLAG", "", ["1"],
              111.76, 53.34, 111.76, 50.8, True, False),
        ],
        wires=[
            (101.6, 30.48, 101.6, 34.29),   # +3V3 -> R1.1
            (101.6, 30.48, 111.76, 30.48),  # +3V3 -> FLG01
            (101.6, 41.91, 101.6, 44.45),   # R1.2 -> R2.1 (分压节点)
            (101.6, 52.07, 101.6, 55.88),   # R2.2 -> GND
            (101.6, 55.88, 111.76, 55.88),  # GND -> FLG02
        ],
        libs=[("Device", "R"), ("power", "+3V3"), ("power", "GND"),
              ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("R1", "Resistor_SMD", "R_0603_1608Metric", 20, 15, 0),
                ("R2", "Resistor_SMD", "R_0603_1608Metric", 30, 15, 0),
            ],
            tracks=[
                ("R1", "2", "R2", "1", "Net-(R1-R2)"),
            ],
        ),
    )


def _circuit_led_dual():
    """并联双 LED: 3V3 -> R1 -> LED1 -> GND 且 3V3 -> R2 -> LED2 -> GND。
    实测: 电源引脚可挂多根线(无结点), ERC 0 违规。
    """
    def S(lib_id, x, y, ref, val, fp, pins, rx, ry, vx, vy, rh=False, vh=False):
        return dict(lib_id=lib_id, x=x, y=y, rot=0, ref=ref, val=val,
                    fp=fp, pins=pins, ref_xy=(rx, ry), val_xy=(vx, vy),
                    ref_hide=rh, val_hide=vh)

    RFP = "Resistor_SMD:R_0603_1608Metric"
    DFP = "LED_SMD:LED_0603_1608Metric"
    return dict(
        name="led_dual",
        title="Dual Parallel LED (3V3)",
        symbols=[
            S("power:+3V3", 91.44, 30.48, "#PWR01", "+3V3", "", ["1"],
              91.44, 34.29, 91.44, 26.67, True, False),
            S("power:PWR_FLAG", 81.28, 30.48, "#FLG01", "PWR_FLAG", "", ["1"],
              81.28, 27.94, 81.28, 25.4, True, False),
            S("Device:R", 91.44, 38.1, "R1", "330", RFP, ["1", "2"],
              92.996, 36.83, 92.996, 39.37),
            S("Device:LED", 91.44, 48.26, "D1", "LED", DFP, ["1", "2"],
              92.996, 46.99, 92.996, 49.53),
            S("Device:R", 106.68, 38.1, "R2", "330", RFP, ["1", "2"],
              108.236, 36.83, 108.236, 39.37),
            S("Device:LED", 106.68, 48.26, "D2", "LED", DFP, ["1", "2"],
              108.236, 46.99, 108.236, 49.53),
            S("power:GND", 99.06, 54.61, "#PWR02", "GND", "", ["1"],
              99.06, 60.96, 99.06, 58.42, True, False),
            S("power:PWR_FLAG", 109.22, 54.61, "#FLG02", "PWR_FLAG", "", ["1"],
              109.22, 52.07, 109.22, 49.53, True, False),
        ],
        wires=[
            (91.44, 30.48, 91.44, 34.29),    # +3V3 -> R1.1
            (91.44, 30.48, 106.68, 30.48),   # +3V3 -> 角
            (106.68, 30.48, 106.68, 34.29),  # 角 -> R2.1
            (91.44, 30.48, 81.28, 30.48),    # +3V3 -> FLG01
            (91.44, 41.91, 95.25, 41.91),    # R1.2 -> 角
            (95.25, 41.91, 95.25, 48.26),    # 角 -> D1.A
            (87.63, 48.26, 87.63, 54.61),    # D1.K -> 角
            (87.63, 54.61, 99.06, 54.61),    # 角 -> GND
            (106.68, 41.91, 110.49, 41.91),  # R2.2 -> 角
            (110.49, 41.91, 110.49, 48.26),  # 角 -> D2.A
            (102.87, 48.26, 102.87, 54.61),  # D2.K -> 角
            (102.87, 54.61, 99.06, 54.61),   # 角 -> GND
            (99.06, 54.61, 109.22, 54.61),   # GND -> FLG02
        ],
        libs=[("Device", "R"), ("Device", "LED"),
              ("power", "+3V3"), ("power", "GND"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("R1", "Resistor_SMD", "R_0603_1608Metric", 18, 12, 0),
                ("D1", "LED_SMD", "LED_0603_1608Metric", 28, 12, 180),
                ("R2", "Resistor_SMD", "R_0603_1608Metric", 18, 20, 0),
                ("D2", "LED_SMD", "LED_0603_1608Metric", 28, 20, 180),
            ],
            tracks=[
                ("R1", "2", "D1", "2", "Net-(D1-A)"),
                ("R2", "2", "D2", "2", "Net-(D2-A)"),
            ],
        ),
    )


def _circuit_rc_filter():
    """RC 低通: 3V3 -> C1(100n) -> 节点 -> R1(1k) -> GND。
    Device:C 引脚与 R 相同(垂直 ±3.81)。
    """
    def S(lib_id, x, y, ref, val, fp, pins, rx, ry, vx, vy, rh=False, vh=False):
        return dict(lib_id=lib_id, x=x, y=y, rot=0, ref=ref, val=val,
                    fp=fp, pins=pins, ref_xy=(rx, ry), val_xy=(vx, vy),
                    ref_hide=rh, val_hide=vh)

    RFP = "Resistor_SMD:R_0603_1608Metric"
    CFP = "Capacitor_SMD:C_0603_1608Metric"
    return dict(
        name="rc_filter",
        title="RC Low-Pass (3V3)",
        symbols=[
            S("power:+3V3", 101.6, 30.48, "#PWR01", "+3V3", "", ["1"],
              101.6, 34.29, 101.6, 26.67, True, False),
            S("power:PWR_FLAG", 111.76, 30.48, "#FLG01", "PWR_FLAG", "", ["1"],
              111.76, 27.94, 111.76, 25.4, True, False),
            S("Device:C", 101.6, 38.1, "C1", "100n", CFP, ["1", "2"],
              103.156, 36.83, 103.156, 39.37),
            S("Device:R", 101.6, 48.26, "R1", "1k", RFP, ["1", "2"],
              103.156, 46.99, 103.156, 49.53),
            S("power:GND", 101.6, 55.88, "#PWR02", "GND", "", ["1"],
              101.6, 62.23, 101.6, 59.69, True, False),
            S("power:PWR_FLAG", 111.76, 55.88, "#FLG02", "PWR_FLAG", "", ["1"],
              111.76, 53.34, 111.76, 50.8, True, False),
        ],
        wires=[
            (101.6, 30.48, 101.6, 34.29),   # +3V3 -> C1.1
            (101.6, 30.48, 111.76, 30.48),  # +3V3 -> FLG01
            (101.6, 41.91, 101.6, 44.45),   # C1.2 -> R1.1
            (101.6, 52.07, 101.6, 55.88),   # R1.2 -> GND
            (101.6, 55.88, 111.76, 55.88),  # GND -> FLG02
        ],
        libs=[("Device", "C"), ("Device", "R"), ("power", "+3V3"),
              ("power", "GND"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("C1", "Capacitor_SMD", "C_0603_1608Metric", 20, 15, 0),
                ("R1", "Resistor_SMD", "R_0603_1608Metric", 30, 15, 0),
            ],
            tracks=[
                ("C1", "2", "R1", "1", "Net-(C1-R1)"),
            ],
        ),
    )


def _circuit_led_5v():
    """5V 电源指示灯: +5V -> R1(1k) -> LED -> GND。拓扑与 led_3v3 相同。"""
    def S(lib_id, x, y, ref, val, fp, pins, rx, ry, vx, vy, rh=False, vh=False):
        return dict(lib_id=lib_id, x=x, y=y, rot=0, ref=ref, val=val,
                    fp=fp, pins=pins, ref_xy=(rx, ry), val_xy=(vx, vy),
                    ref_hide=rh, val_hide=vh)

    return dict(
        name="led_5v",
        title="5V Power LED",
        symbols=[
            S("power:+5V", 101.6, 35.56, "#PWR01", "+5V", "", ["1"],
              101.6, 39.37, 101.6, 31.75, True, False),
            S("Device:R", 101.6, 45.72, "R1", "1k",
              "Resistor_SMD:R_0603_1608Metric", ["1", "2"],
              103.156, 44.45, 103.156, 46.99),
            S("Device:LED", 101.6, 55.88, "D1", "LED",
              "LED_SMD:LED_0603_1608Metric", ["1", "2"],
              103.156, 54.61, 103.156, 57.15),
            S("power:GND", 101.6, 64.77, "#PWR02", "GND", "", ["1"],
              101.6, 71.12, 101.6, 68.58, True, False),
            S("power:PWR_FLAG", 111.76, 35.56, "#FLG01", "PWR_FLAG", "", ["1"],
              111.76, 33.02, 111.76, 29.21, True, False),
            S("power:PWR_FLAG", 111.76, 64.77, "#FLG02", "PWR_FLAG", "", ["1"],
              111.76, 62.23, 111.76, 59.69, True, False),
        ],
        wires=[
            (101.6, 35.56, 101.6, 41.91),
            (101.6, 35.56, 111.76, 35.56),
            (101.6, 49.53, 105.41, 49.53),
            (105.41, 49.53, 105.41, 55.88),
            (97.79, 55.88, 97.79, 64.77),
            (97.79, 64.77, 101.6, 64.77),
            (101.6, 64.77, 111.76, 64.77),
        ],
        libs=[("Device", "R"), ("Device", "LED"),
              ("power", "+5V"), ("power", "GND"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("R1", "Resistor_SMD", "R_0603_1608Metric", 20, 15, 0),
                ("D1", "LED_SMD", "LED_0603_1608Metric", 30, 15, 180),
            ],
            tracks=[
                ("R1", "2", "D1", "2", "Net-(D1-A)"),
            ],
        ),
    )


def _circuit_decouple():
    """去耦电容: +3V3 -> C1(100n) -> GND。"""
    def S(lib_id, x, y, ref, val, fp, pins, rx, ry, vx, vy, rh=False, vh=False):
        return dict(lib_id=lib_id, x=x, y=y, rot=0, ref=ref, val=val,
                    fp=fp, pins=pins, ref_xy=(rx, ry), val_xy=(vx, vy),
                    ref_hide=rh, val_hide=vh)

    return dict(
        name="decouple",
        title="Decoupling Cap 100n (3V3)",
        symbols=[
            S("power:+3V3", 101.6, 35.56, "#PWR01", "+3V3", "", ["1"],
              101.6, 39.37, 101.6, 31.75, True, False),
            S("Device:C", 101.6, 45.72, "C1", "100n",
              "Capacitor_SMD:C_0603_1608Metric", ["1", "2"],
              103.156, 44.45, 103.156, 46.99),
            S("power:GND", 101.6, 53.34, "#PWR02", "GND", "", ["1"],
              101.6, 59.69, 101.6, 57.15, True, False),
            S("power:PWR_FLAG", 111.76, 35.56, "#FLG01", "PWR_FLAG", "", ["1"],
              111.76, 33.02, 111.76, 29.21, True, False),
            S("power:PWR_FLAG", 111.76, 53.34, "#FLG02", "PWR_FLAG", "", ["1"],
              111.76, 50.8, 111.76, 48.26, True, False),
        ],
        wires=[
            (101.6, 35.56, 101.6, 41.91),   # +3V3 -> C1.1
            (101.6, 35.56, 111.76, 35.56),  # +3V3 -> FLG01
            (101.6, 49.53, 101.6, 53.34),   # C1.2 -> GND
            (101.6, 53.34, 111.76, 53.34),  # GND -> FLG02
        ],
        libs=[("Device", "C"), ("power", "+3V3"), ("power", "GND"),
              ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("C1", "Capacitor_SMD", "C_0603_1608Metric", 25, 15, 0),
            ],
            tracks=[],
        ),
    )




# ---- 通用 S 辅助（新预设共用） ----
def _S(lib_id, x, y, ref, val, fp, pins, rx, ry, vx, vy, rh=False, vh=False):
    return dict(lib_id=lib_id, x=x, y=y, rot=0, ref=ref, val=val,
                fp=fp, pins=pins, ref_xy=(rx, ry), val_xy=(vx, vy),
                ref_hide=rh, val_hide=vh)


RFP = "Resistor_SMD:R_0603_1608Metric"
CFP = "Capacitor_SMD:C_0603_1608Metric"
DFP = "LED_SMD:LED_0603_1608Metric"
TO92 = "Package_TO_SOT_THT:TO-92_Inline"
SOT223 = "Package_TO_SOT_SMD:SOT-223-3_TabPin2"
SOD123 = "Diode_SMD:D_SOD-123"
SWFPS = "Button_Switch_SMD:SW_SPST_PTS810"
FUSEFP = "Fuse:Fuse_1206_3216Metric"
XTALFP = "Crystal:Crystal_SMD_5032-2Pin_5.0x3.2mm"
HDR2 = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
HDR4 = "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"
TPFP = "TestPoint:TestPoint_Pad_D1.5mm"


def _circuit_npn_switch():
    """NPN Low-Side LED Switch"""
    return dict(
        name="npn_switch",
        title="NPN Low-Side LED Switch",
        symbols=[
            _S("power:+3V3", 100.33, 33.02, "#PWR01", "+3V3",
               "", ["1"],
               100.33, 36.83, 100.33, 29.21, True, False),
            _S("Device:R", 100.33, 41.91, "R1", "1k",
               "Resistor_SMD:R_0603_1608Metric", ["1", "2"],
               101.886, 40.64, 101.886, 43.18, False, False),
            _S("Transistor_BJT:BC547", 105.41, 50.8, "Q1", "BC547",
               "Package_TO_SOT_THT:TO-92_Inline", ["1", "2", "3"],
               107.95, 54.61, 107.95, 46.99, False, False),
            _S("Device:R", 115.57, 38.1, "R2", "330",
               "Resistor_SMD:R_0603_1608Metric", ["1", "2"],
               117.126, 36.83, 117.126, 39.37, False, False),
            _S("Device:LED", 115.57, 48.26, "D1", "LED",
               "LED_SMD:LED_0603_1608Metric", ["1", "2"],
               117.126, 46.99, 117.126, 49.53, False, False),
            _S("power:GND", 107.95, 63.5, "#PWR02", "GND",
               "", ["1"],
               107.95, 69.85, 107.95, 67.31, True, False),
            _S("power:PWR_FLAG", 88.9, 33.02, "#FLG01", "PWR_FLAG",
               "", ["1"],
               88.9, 30.48, 88.9, 27.94, True, False),
            _S("power:PWR_FLAG", 118.11, 63.5, "#FLG02", "PWR_FLAG",
               "", ["1"],
               118.11, 60.96, 118.11, 58.42, True, False),
        ],
        wires=[
            (100.33, 33.02, 100.33, 38.1),
            (100.33, 33.02, 115.57, 33.02),
            (115.57, 33.02, 115.57, 34.29),
            (100.33, 33.02, 88.9, 33.02),
            (100.33, 45.72, 100.33, 50.8),
            (115.57, 41.91, 119.38, 41.91),
            (119.38, 41.91, 119.38, 48.26),
            (111.76, 48.26, 107.95, 48.26),
            (107.95, 48.26, 107.95, 45.72),
            (107.95, 55.88, 107.95, 63.5),
            (107.95, 63.5, 118.11, 63.5),
        ],
        libs=[("Device", "R"), ("Device", "LED"), ("Transistor_BJT", "BC547"), ("power", "+3V3"), ("power", "GND"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("R1", "Resistor_SMD", "R_0603_1608Metric", 18, 12, 0),
                ("Q1", "Package_TO_SOT_THT", "TO-92_Inline", 28, 15, 0),
                ("R2", "Resistor_SMD", "R_0603_1608Metric", 38, 12, 0),
                ("D1", "LED_SMD", "LED_0603_1608Metric", 48, 15, 180),
            ],
            tracks=[
                ("R1", "2", "Q1", "2", "Net-(R1-B)"),
                ("R2", "2", "D1", "2", "Net-(D1-A)"),
                ("D1", "1", "Q1", "1", "Net-(D1-K)"),
            ],
        ),
    )


def _circuit_pnp_high():
    """PNP High-Side LED Switch"""
    return dict(
        name="pnp_high",
        title="PNP High-Side LED Switch",
        symbols=[
            _S("power:+3V3", 115.57, 33.02, "#PWR01", "+3V3",
               "", ["1"],
               115.57, 36.83, 115.57, 29.21, True, False),
            _S("Transistor_BJT:BC557", 105.41, 50.8, "Q1", "BC557",
               "Package_TO_SOT_THT:TO-92_Inline", ["1", "2", "3"],
               107.95, 54.61, 107.95, 46.99, False, False),
            _S("Device:R", 107.95, 38.1, "R2", "330",
               "Resistor_SMD:R_0603_1608Metric", ["1", "2"],
               109.506, 36.83, 109.506, 39.37, False, False),
            _S("Device:LED", 107.95, 27.94, "D1", "LED",
               "LED_SMD:LED_0603_1608Metric", ["1", "2"],
               109.506, 26.67, 109.506, 29.21, False, False),
            _S("Device:R", 100.33, 58.42, "R1", "10k",
               "Resistor_SMD:R_0603_1608Metric", ["1", "2"],
               101.886, 57.15, 101.886, 59.69, False, False),
            _S("power:GND", 104.14, 22.86, "#PWR02", "GND",
               "", ["1"],
               104.14, 29.21, 104.14, 26.67, True, False),
            _S("power:GND", 100.33, 67.31, "#PWR03", "GND",
               "", ["1"],
               100.33, 73.66, 100.33, 71.12, True, False),
            _S("power:PWR_FLAG", 125.73, 33.02, "#FLG01", "PWR_FLAG",
               "", ["1"],
               125.73, 30.48, 125.73, 27.94, True, False),
            _S("power:PWR_FLAG", 114.3, 22.86, "#FLG02", "PWR_FLAG",
               "", ["1"],
               114.3, 20.32, 114.3, 17.78, True, False),
        ],
        wires=[
            (115.57, 33.02, 115.57, 55.88),
            (115.57, 55.88, 107.95, 55.88),
            (115.57, 33.02, 125.73, 33.02),
            (107.95, 45.72, 107.95, 41.91),
            (107.95, 34.29, 111.76, 34.29),
            (111.76, 34.29, 111.76, 27.94),
            (104.14, 27.94, 104.14, 22.86),
            (104.14, 22.86, 114.3, 22.86),
            (100.33, 50.8, 100.33, 54.61),
            (100.33, 62.23, 100.33, 67.31),
        ],
        libs=[("Device", "R"), ("Device", "LED"), ("Transistor_BJT", "BC557"), ("power", "+3V3"), ("power", "GND"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("Q1", "Package_TO_SOT_THT", "TO-92_Inline", 25, 18, 0),
                ("R2", "Resistor_SMD", "R_0603_1608Metric", 25, 10, 0),
                ("D1", "LED_SMD", "LED_0603_1608Metric", 35, 10, 180),
                ("R1", "Resistor_SMD", "R_0603_1608Metric", 15, 18, 0),
            ],
            tracks=[
                ("Q1", "1", "R2", "2", "Net-(Q1-C)"),
                ("R2", "1", "D1", "2", "Net-(D1-A)"),
                ("Q1", "2", "R1", "1", "Net-(R1-B)"),
            ],
        ),
    )


def _circuit_mosfet_switch():
    """NMOS Low-Side Switch (IRLZ44N)"""
    return dict(
        name="mosfet_switch",
        title="NMOS Low-Side Switch (IRLZ44N)",
        symbols=[
            _S("power:+3V3", 100.33, 33.02, "#PWR01", "+3V3",
               "", ["1"],
               100.33, 36.83, 100.33, 29.21, True, False),
            _S("Device:R", 100.33, 41.91, "R1", "10k",
               "Resistor_SMD:R_0603_1608Metric", ["1", "2"],
               101.886, 40.64, 101.886, 43.18, False, False),
            _S("Transistor_FET:IRLZ44N", 105.41, 50.8, "Q1", "IRLZ44N",
               "Package_TO_SOT_THT:TO-220-3_Vertical", ["1", "2", "3"],
               107.95, 54.61, 107.95, 46.99, False, False),
            _S("Device:R", 115.57, 38.1, "R2", "330",
               "Resistor_SMD:R_0603_1608Metric", ["1", "2"],
               117.126, 36.83, 117.126, 39.37, False, False),
            _S("Device:LED", 115.57, 48.26, "D1", "LED",
               "LED_SMD:LED_0603_1608Metric", ["1", "2"],
               117.126, 46.99, 117.126, 49.53, False, False),
            _S("power:GND", 107.95, 63.5, "#PWR02", "GND",
               "", ["1"],
               107.95, 69.85, 107.95, 67.31, True, False),
            _S("power:PWR_FLAG", 88.9, 33.02, "#FLG01", "PWR_FLAG",
               "", ["1"],
               88.9, 30.48, 88.9, 27.94, True, False),
            _S("power:PWR_FLAG", 118.11, 63.5, "#FLG02", "PWR_FLAG",
               "", ["1"],
               118.11, 60.96, 118.11, 58.42, True, False),
        ],
        wires=[
            (100.33, 33.02, 100.33, 38.1),
            (100.33, 33.02, 115.57, 33.02),
            (115.57, 33.02, 115.57, 34.29),
            (100.33, 33.02, 88.9, 33.02),
            (100.33, 45.72, 100.33, 50.8),
            (115.57, 41.91, 119.38, 41.91),
            (119.38, 41.91, 119.38, 48.26),
            (111.76, 48.26, 107.95, 48.26),
            (107.95, 48.26, 107.95, 45.72),
            (107.95, 55.88, 107.95, 63.5),
            (107.95, 63.5, 118.11, 63.5),
        ],
        libs=[("Device", "R"), ("Device", "LED"), ("Transistor_FET", "IRLZ44N"), ("power", "+3V3"), ("power", "GND"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("R1", "Resistor_SMD", "R_0603_1608Metric", 18, 12, 0),
                ("Q1", "Package_TO_SOT_THT", "TO-220-3_Vertical", 30, 18, 0),
                ("R2", "Resistor_SMD", "R_0603_1608Metric", 38, 12, 0),
                ("D1", "LED_SMD", "LED_0603_1608Metric", 48, 15, 180),
            ],
            tracks=[
                ("R1", "2", "Q1", "1", "Net-(R1-G)"),
                ("R2", "2", "D1", "2", "Net-(D1-A)"),
                ("D1", "1", "Q1", "2", "Net-(D1-K)"),
            ],
        ),
    )


def _circuit_ldo_1117():
    """AMS1117-3.3 LDO 5V to 3.3V"""
    return dict(
        name="ldo_1117",
        title="AMS1117-3.3 LDO 5V to 3.3V",
        symbols=[
            _S("power:+5V", 97.79, 33.02, "#PWR01", "+5V",
               "", ["1"],
               97.79, 36.83, 97.79, 29.21, True, False),
            _S("Device:C", 91.44, 38.1, "C1", "10u",
               "Capacitor_SMD:C_0603_1608Metric", ["1", "2"],
               93.276, 36.83, 93.276, 39.37, False, False),
            _S("power:GND", 91.44, 45.72, "#PWR02", "GND",
               "", ["1"],
               91.44, 52.07, 91.44, 49.53, True, False),
            _S("Regulator_Linear:AMS1117-3.3", 105.41, 45.72, "U1", "AMS1117-3.3",
               "Package_TO_SOT_SMD:SOT-223-3_TabPin2", ["1", "2", "3"],
               108.585, 50.8, 99.06, 39.37, False, False),
            _S("power:+3V3", 113.03, 35.56, "#PWR03", "+3V3",
               "", ["1"],
               113.03, 39.37, 113.03, 31.75, True, False),
            _S("Device:C", 120.65, 40.64, "C2", "10u",
               "Capacitor_SMD:C_0603_1608Metric", ["1", "2"],
               122.206, 39.37, 122.206, 41.91, False, False),
            _S("power:GND", 120.65, 50.8, "#PWR04", "GND",
               "", ["1"],
               120.65, 57.15, 120.65, 54.61, True, False),
            _S("power:GND", 105.41, 60.96, "#PWR05", "GND",
               "", ["1"],
               105.41, 67.31, 105.41, 64.77, True, False),
            _S("power:PWR_FLAG", 87.63, 33.02, "#FLG01", "PWR_FLAG",
               "", ["1"],
               87.63, 30.48, 87.63, 27.94, True, False),
            _S("power:PWR_FLAG", 125.73, 60.96, "#FLG03", "PWR_FLAG",
               "", ["1"],
               125.73, 58.42, 125.73, 55.88, True, False),
        ],
        wires=[
            (87.63, 33.02, 91.44, 33.02),
            (91.44, 33.02, 97.79, 33.02),
            (97.79, 33.02, 97.79, 45.72),
            (91.44, 33.02, 91.44, 34.29),
            (91.44, 41.91, 91.44, 45.72),
            (113.03, 45.72, 113.03, 35.56),
            (113.03, 35.56, 120.65, 35.56),
            (120.65, 35.56, 120.65, 36.83),
            (120.65, 44.45, 120.65, 50.8),
            (105.41, 53.34, 105.41, 60.96),
            (105.41, 60.96, 125.73, 60.96),
        ],
        libs=[("Device", "C"), ("Regulator_Linear", "AMS1117-3.3"), ("power", "+5V"), ("power", "+3V3"), ("power", "GND"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("C1", "Capacitor_SMD", "C_0603_1608Metric", 18, 12, 90),
                ("U1", "Package_TO_SOT_SMD", "SOT-223-3_TabPin2", 30, 15, 0),
                ("C2", "Capacitor_SMD", "C_0603_1608Metric", 42, 8, 90),
            ],
            tracks=[
                ("C1", "1", "U1", "3", "Net-(C1-VI)"),
                ("U1", "2", "C2", "1", "Net-(U1-VO)"),
            ],
        ),
    )


def _circuit_schottky_rev():
    """Schottky Reverse Polarity Protection"""
    return dict(
        name="schottky_rev",
        title="Schottky Reverse Polarity Protection",
        symbols=[
            _S("power:+12V", 109.22, 35.56, "#PWR01", "+12V",
               "", ["1"],
               109.22, 39.37, 109.22, 31.75, True, False),
            _S("Device:D_Schottky", 105.41, 45.72, "D1", "SS34",
               "Diode_SMD:D_SOD-123", ["1", "2"],
               106.966, 44.45, 106.966, 46.99, False, False),
            _S("power:VCC", 101.6, 35.56, "#PWR02", "VCC",
               "", ["1"],
               101.6, 39.37, 101.6, 31.75, True, False),
            _S("power:PWR_FLAG", 119.38, 35.56, "#FLG01", "PWR_FLAG",
               "", ["1"],
               119.38, 33.02, 119.38, 30.48, True, False),
            _S("power:PWR_FLAG", 91.44, 35.56, "#FLG02", "PWR_FLAG",
               "", ["1"],
               91.44, 33.02, 91.44, 30.48, True, False),
        ],
        wires=[
            (109.22, 35.56, 109.22, 45.72),
            (109.22, 35.56, 119.38, 35.56),
            (101.6, 35.56, 101.6, 45.72),
            (101.6, 35.56, 91.44, 35.56),
        ],
        libs=[("Device", "D_Schottky"), ("power", "+12V"), ("power", "VCC"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("D1", "Diode_SMD", "D_SOD-123", 25, 15, 0),
            ],
            tracks=[

            ],
        ),
    )


def _circuit_i2c_pullup():
    """I2C Pull-Up (4.7k x2 + Header)"""
    return dict(
        name="i2c_pullup",
        title="I2C Pull-Up (4.7k x2 + Header)",
        symbols=[
            _S("power:+3V3", 96.52, 33.02, "#PWR01", "+3V3",
               "", ["1"],
               96.52, 36.83, 96.52, 29.21, True, False),
            _S("Device:R", 96.52, 39.37, "R1", "4.7k",
               "Resistor_SMD:R_0603_1608Metric", ["1", "2"],
               98.076, 38.10, 98.076, 40.64, False, False),
            _S("Device:R", 91.44, 41.91, "R2", "4.7k",
               "Resistor_SMD:R_0603_1608Metric", ["1", "2"],
               87.63, 40.64, 87.63, 43.18, False, False),
            _S("Connector_Generic:Conn_01x04", 111.76, 43.18, "J1", "I2C",
               "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", ["1", "2", "3", "4"],
               114.3, 41.91, 114.3, 40.64, False, False),
            _S("power:GND", 106.68, 52.07, "#PWR02", "GND",
               "", ["1"],
               106.68, 58.42, 106.68, 55.88, True, False),
            _S("power:PWR_FLAG", 86.36, 33.02, "#FLG01", "PWR_FLAG",
               "", ["1"],
               86.36, 30.48, 86.36, 27.94, True, False),
            _S("power:PWR_FLAG", 116.84, 52.07, "#FLG02", "PWR_FLAG",
               "", ["1"],
               116.84, 49.53, 116.84, 46.99, True, False),
        ],
        wires=[
            (86.36, 33.02, 91.44, 33.02),
            (91.44, 33.02, 96.52, 33.02),
            (96.52, 33.02, 96.52, 35.56),
            (91.44, 33.02, 91.44, 38.10),
            (96.52, 33.02, 101.6, 33.02),
            (101.6, 33.02, 101.6, 40.64),
            (101.6, 40.64, 106.68, 40.64),
            (96.52, 43.18, 106.68, 43.18),
            (91.44, 45.72, 106.68, 45.72),
            (106.68, 48.26, 106.68, 52.07),
            (106.68, 52.07, 116.84, 52.07),
        ],
        libs=[("Device", "R"), ("Connector_Generic", "Conn_01x04"), ("power", "+3V3"), ("power", "GND"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("R1", "Resistor_SMD", "R_0603_1608Metric", 20, 12, 0),
                ("R2", "Resistor_SMD", "R_0603_1608Metric", 20, 18, 0),
                ("J1", "Connector_PinHeader_2.54mm", "PinHeader_1x04_P2.54mm_Vertical", 35, 15, 0),
            ],
            tracks=[
                ("R1", "2", "J1", "2", "Net-(SDA)"),
                ("R2", "2", "J1", "3", "Net-(SCL)"),
            ],
        ),
    )


def _circuit_button_pullup():
    """Push Button with Pull-Up"""
    return dict(
        name="button_pullup",
        title="Push Button with Pull-Up",
        symbols=[
            _S("power:+3V3", 101.6, 33.02, "#PWR01", "+3V3",
               "", ["1"],
               101.6, 36.83, 101.6, 29.21, True, False),
            _S("Device:R", 101.6, 40.64, "R1", "10k",
               "Resistor_SMD:R_0603_1608Metric", ["1", "2"],
               103.156, 39.37, 103.156, 41.91, False, False),
            _S("Switch:SW_Push", 101.6, 50.8, "SW1", "SW_Push",
               "Button_Switch_SMD:SW_SPST_PTS810", ["1", "2"],
               99.06, 45.72, 96.52, 54.61, False, False),
            _S("Connector:TestPoint", 91.44, 50.8, "TP1", "OUT",
               "TestPoint:TestPoint_Pad_D1.5mm", ["1"],
               91.44, 48.26, 91.44, 45.72, False, False),
            _S("power:GND", 106.68, 58.42, "#PWR02", "GND",
               "", ["1"],
               106.68, 64.77, 106.68, 62.23, True, False),
            _S("power:PWR_FLAG", 111.76, 33.02, "#FLG01", "PWR_FLAG",
               "", ["1"],
               111.76, 30.48, 111.76, 27.94, True, False),
            _S("power:PWR_FLAG", 116.84, 58.42, "#FLG02", "PWR_FLAG",
               "", ["1"],
               116.84, 55.88, 116.84, 53.34, True, False),
        ],
        wires=[
            (101.6, 33.02, 101.6, 36.83),
            (101.6, 33.02, 111.76, 33.02),
            (101.6, 44.45, 96.52, 44.45),
            (96.52, 44.45, 96.52, 50.8),
            (96.52, 50.8, 91.44, 50.8),
            (106.68, 50.8, 106.68, 58.42),
            (106.68, 58.42, 116.84, 58.42),
        ],
        libs=[("Device", "R"), ("Switch", "SW_Push"), ("Connector", "TestPoint"), ("power", "+3V3"), ("power", "GND"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("R1", "Resistor_SMD", "R_0603_1608Metric", 20, 12, 0),
                ("SW1", "Button_Switch_SMD", "SW_SPST_PTS810", 32, 15, 0),
                ("TP1", "TestPoint", "TestPoint_Pad_D1.5mm", 20, 18, 0),
            ],
            tracks=[
                ("R1", "2", "SW1", "1", "Net-(OUT)"),
                ("SW1", "1", "TP1", "1", "Net-(OUT)"),
            ],
        ),
    )


def _circuit_xtal_load():
    """Crystal with Load Capacitors"""
    return dict(
        name="xtal_load",
        title="Crystal with Load Capacitors",
        symbols=[
            _S("Device:Crystal", 105.41, 40.64, "X1", "16MHz",
               "Crystal:Crystal_SMD_5032-2Pin_5.0x3.2mm", ["1", "2"],
               105.41, 36.83, 105.41, 44.45, False, False),
            _S("Device:C", 101.6, 49.53, "C1", "22p",
               "Capacitor_SMD:C_0603_1608Metric", ["1", "2"],
               103.156, 48.26, 103.156, 50.8, False, False),
            _S("Device:C", 109.22, 49.53, "C2", "22p",
               "Capacitor_SMD:C_0603_1608Metric", ["1", "2"],
               110.776, 48.26, 110.776, 50.8, False, False),
            _S("power:GND", 101.6, 58.42, "#PWR01", "GND",
               "", ["1"],
               101.6, 64.77, 101.6, 62.23, True, False),
            _S("power:GND", 109.22, 58.42, "#PWR02", "GND",
               "", ["1"],
               109.22, 64.77, 109.22, 62.23, True, False),
            _S("Connector:TestPoint", 93.98, 40.64, "TP1", "XTAL1",
               "TestPoint:TestPoint_Pad_D1.5mm", ["1"],
               93.98, 38.1, 93.98, 35.56, False, False),
            _S("Connector:TestPoint", 116.84, 40.64, "TP2", "XTAL2",
               "TestPoint:TestPoint_Pad_D1.5mm", ["1"],
               116.84, 38.1, 116.84, 35.56, False, False),
            _S("power:PWR_FLAG", 111.76, 58.42, "#FLG01", "PWR_FLAG",
               "", ["1"],
               111.76, 55.88, 111.76, 53.34, True, False),
        ],
        wires=[
            (101.6, 40.64, 101.6, 45.72),
            (109.22, 40.64, 109.22, 45.72),
            (101.6, 53.34, 101.6, 58.42),
            (109.22, 53.34, 109.22, 58.42),
            (93.98, 40.64, 101.6, 40.64),
            (109.22, 40.64, 116.84, 40.64),
            (101.6, 58.42, 109.22, 58.42),
            (109.22, 58.42, 111.76, 58.42),
        ],
        libs=[("Device", "Crystal"), ("Device", "C"), ("Connector", "TestPoint"), ("power", "GND"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("X1", "Crystal", "Crystal_SMD_5032-2Pin_5.0x3.2mm", 25, 12, 0),
                ("C1", "Capacitor_SMD", "C_0603_1608Metric", 18, 18, 0),
                ("C2", "Capacitor_SMD", "C_0603_1608Metric", 32, 18, 0),
            ],
            tracks=[
                ("X1", "1", "C1", "1", "Net-(XTAL1)"),
                ("X1", "2", "C2", "1", "Net-(XTAL2)"),
            ],
        ),
    )


def _circuit_battery_div():
    """Battery Voltage Divider"""
    return dict(
        name="battery_div",
        title="Battery Voltage Divider",
        symbols=[
            _S("Device:Battery", 88.9, 45.72, "BT1", "9V",
               "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", ["+", "-"],
               91.44, 44.45, 91.44, 46.99, False, False),
            _S("Device:R", 101.6, 38.1, "R1", "100k",
               "Resistor_SMD:R_0603_1608Metric", ["1", "2"],
               103.156, 36.83, 103.156, 39.37, False, False),
            _S("Device:R", 101.6, 50.8, "R2", "100k",
               "Resistor_SMD:R_0603_1608Metric", ["1", "2"],
               103.156, 49.53, 103.156, 52.07, False, False),
            _S("Connector:TestPoint", 111.76, 44.45, "TP1", "VMID",
               "TestPoint:TestPoint_Pad_D1.5mm", ["1"],
               113.03, 41.91, 113.03, 39.37, False, False),
            _S("power:PWR_FLAG", 101.6, 30.48, "#FLG01", "PWR_FLAG",
               "", ["1"],
               101.6, 27.94, 101.6, 25.4, True, False),
            _S("power:PWR_FLAG", 101.6, 59.69, "#FLG02", "PWR_FLAG",
               "", ["1"],
               101.6, 57.15, 104.14, 58.42, True, False),
        ],
        wires=[
            (88.9, 40.64, 93.98, 40.64),
            (93.98, 40.64, 93.98, 34.29),
            (93.98, 34.29, 101.6, 34.29),
            (101.6, 34.29, 101.6, 30.48),
            (101.6, 41.91, 101.6, 46.99),
            (101.6, 41.91, 111.76, 41.91),
            (111.76, 41.91, 111.76, 44.45),
            (88.9, 50.8, 93.98, 50.8),
            (93.98, 50.8, 93.98, 54.61),
            (93.98, 54.61, 101.6, 54.61),
            (101.6, 54.61, 101.6, 59.69),
        ],
        libs=[("Device", "Battery"), ("Device", "R"), ("Connector", "TestPoint"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("BT1", "Connector_PinHeader_2.54mm", "PinHeader_1x02_P2.54mm_Vertical", 15, 15, 0),
                ("R1", "Resistor_SMD", "R_0603_1608Metric", 28, 12, 0),
                ("R2", "Resistor_SMD", "R_0603_1608Metric", 28, 18, 0),
            ],
            tracks=[
                ("R1", "2", "R2", "1", "Net-(VMID)"),
                ("R2", "1", "TP1", "1", "Net-(VMID)"),
            ],
        ),
    )


def _circuit_polyfuse_in():
    """Polyfuse Input Protection"""
    return dict(
        name="polyfuse_in",
        title="Polyfuse Input Protection",
        symbols=[
            _S("power:+12V", 101.6, 30.48, "#PWR01", "+12V",
               "", ["1"],
               101.6, 34.29, 101.6, 26.67, True, False),
            _S("Device:Polyfuse", 101.6, 40.64, "F1", "500mA",
               "Fuse:Fuse_1206_3216Metric", ["1", "2"],
               103.156, 39.37, 103.156, 41.91, False, False),
            _S("power:VCC", 101.6, 50.8, "#PWR02", "VCC",
               "", ["1"],
               101.6, 54.61, 101.6, 46.99, True, False),
            _S("power:PWR_FLAG", 111.76, 30.48, "#FLG01", "PWR_FLAG",
               "", ["1"],
               111.76, 27.94, 111.76, 25.4, True, False),
            _S("power:PWR_FLAG", 111.76, 50.8, "#FLG02", "PWR_FLAG",
               "", ["1"],
               111.76, 48.26, 111.76, 45.72, True, False),
        ],
        wires=[
            (101.6, 30.48, 101.6, 36.83),
            (101.6, 30.48, 111.76, 30.48),
            (101.6, 44.45, 101.6, 50.8),
            (101.6, 50.8, 111.76, 50.8),
        ],
        libs=[("Device", "Polyfuse"), ("power", "+12V"), ("power", "VCC"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("F1", "Fuse", "Fuse_1206_3216Metric", 25, 15, 0),
            ],
            tracks=[

            ],
        ),
    )


def _circuit_tvs_clamp():
    """TVS Clamp Protection"""
    return dict(
        name="tvs_clamp",
        title="TVS Clamp Protection",
        symbols=[
            _S("power:+5V", 101.6, 33.02, "#PWR01", "+5V",
               "", ["1"],
               101.6, 36.83, 101.6, 29.21, True, False),
            _S("Device:D_TVS", 105.41, 43.18, "D1", "SMBJ5.0A",
               "Diode_SMD:D_SOD-123", ["1", "2"],
               106.966, 41.91, 106.966, 44.45, False, False),
            _S("power:GND", 109.22, 50.8, "#PWR02", "GND",
               "", ["1"],
               109.22, 57.15, 109.22, 54.61, True, False),
            _S("power:PWR_FLAG", 111.76, 33.02, "#FLG01", "PWR_FLAG",
               "", ["1"],
               111.76, 30.48, 111.76, 27.94, True, False),
            _S("power:PWR_FLAG", 119.38, 50.8, "#FLG02", "PWR_FLAG",
               "", ["1"],
               119.38, 48.26, 119.38, 45.72, True, False),
        ],
        wires=[
            (101.6, 33.02, 101.6, 43.18),
            (101.6, 33.02, 111.76, 33.02),
            (109.22, 43.18, 109.22, 50.8),
            (109.22, 50.8, 119.38, 50.8),
        ],
        libs=[("Device", "D_TVS"), ("power", "+5V"), ("power", "GND"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("D1", "Diode_SMD", "D_SOD-123", 25, 15, 0),
            ],
            tracks=[

            ],
        ),
    )


def _circuit_npn_inverter():
    """NPN Common-Emitter Inverter"""
    return dict(
        name="npn_inverter",
        title="NPN Common-Emitter Inverter",
        symbols=[
            _S("power:+3V3", 107.95, 30.48, "#PWR01", "+3V3",
               "", ["1"],
               107.95, 34.29, 107.95, 26.67, True, False),
            _S("Device:R", 107.95, 38.1, "R2", "1k",
               "Resistor_SMD:R_0603_1608Metric", ["1", "2"],
               109.506, 36.83, 109.506, 39.37, False, False),
            _S("Transistor_BJT:BC547", 105.41, 50.8, "Q1", "BC547",
               "Package_TO_SOT_THT:TO-92_Inline", ["1", "2", "3"],
               107.95, 54.61, 107.95, 46.99, False, False),
            _S("Connector:TestPoint", 115.57, 45.72, "TP2", "OUT",
               "TestPoint:TestPoint_Pad_D1.5mm", ["1"],
               115.57, 43.18, 115.57, 40.64, False, False),
            _S("Device:R", 100.33, 43.18, "R1", "10k",
               "Resistor_SMD:R_0603_1608Metric", ["1", "2"],
               101.886, 41.91, 101.886, 44.45, False, False),
            _S("Connector:TestPoint", 100.33, 33.02, "TP1", "IN",
               "TestPoint:TestPoint_Pad_D1.5mm", ["1"],
               100.33, 30.48, 100.33, 27.94, False, False),
            _S("power:GND", 107.95, 63.5, "#PWR02", "GND",
               "", ["1"],
               107.95, 69.85, 107.95, 67.31, True, False),
            _S("power:PWR_FLAG", 95.25, 30.48, "#FLG01", "PWR_FLAG",
               "", ["1"],
               95.25, 27.94, 95.25, 25.4, True, False),
            _S("power:PWR_FLAG", 118.11, 63.5, "#FLG02", "PWR_FLAG",
               "", ["1"],
               118.11, 60.96, 118.11, 58.42, True, False),
        ],
        wires=[
            (107.95, 30.48, 107.95, 34.29),
            (107.95, 30.48, 95.25, 30.48),
            (107.95, 41.91, 107.95, 45.72),
            (107.95, 45.72, 115.57, 45.72),
            (100.33, 33.02, 100.33, 39.37),
            (100.33, 46.99, 100.33, 50.8),
            (107.95, 55.88, 107.95, 63.5),
            (107.95, 63.5, 118.11, 63.5),
        ],
        libs=[("Device", "R"), ("Transistor_BJT", "BC547"), ("Connector", "TestPoint"), ("power", "+3V3"), ("power", "GND"), ("power", "PWR_FLAG")],
        pcb=dict(
            footprints=[
                ("R2", "Resistor_SMD", "R_0603_1608Metric", 20, 10, 0),
                ("Q1", "Package_TO_SOT_THT", "TO-92_Inline", 30, 15, 0),
                ("R1", "Resistor_SMD", "R_0603_1608Metric", 18, 18, 0),
            ],
            tracks=[
                ("R2", "2", "Q1", "1", "Net-(Q1-C)"),
                ("TP2", "1", "Q1", "1", "Net-(Q1-C)"),
                ("R1", "2", "Q1", "2", "Net-(R1-B)"),
            ],
        ),
    )


CIRCUITS = {
    "led_3v3": _circuit_led_3v3,
    "led_5v": _circuit_led_5v,
    "led_dual": _circuit_led_dual,
    "divider": _circuit_divider,
    "rc_filter": _circuit_rc_filter,
    "decouple": _circuit_decouple,
    "npn_switch": _circuit_npn_switch,
    "pnp_high": _circuit_pnp_high,
    "mosfet_switch": _circuit_mosfet_switch,
    "ldo_1117": _circuit_ldo_1117,
    "schottky_rev": _circuit_schottky_rev,
    "i2c_pullup": _circuit_i2c_pullup,
    "button_pullup": _circuit_button_pullup,
    "xtal_load": _circuit_xtal_load,
    "battery_div": _circuit_battery_div,
    "polyfuse_in": _circuit_polyfuse_in,
    "tvs_clamp": _circuit_tvs_clamp,
    "npn_inverter": _circuit_npn_inverter,
}
CIRCUIT_LABELS = {
    "led_3v3": "3V3 电源指示灯 (R+LED)",
    "led_5v": "5V 电源指示灯 (R+LED)",
    "led_dual": "并联双 LED 指示灯 (2×R+2×LED)",
    "divider": "电阻分压 1:1 (R+R)",
    "rc_filter": "RC 低通滤波 (C+R)",
    "decouple": "去耦电容 100n (C)",
    "npn_switch": "NPN Low-Side LED Switch",
    "pnp_high": "PNP High-Side LED Switch",
    "mosfet_switch": "NMOS Low-Side Switch (IRLZ44N)",
    "ldo_1117": "AMS1117-3.3 LDO 5V to 3.3V",
    "schottky_rev": "Schottky Reverse Polarity Protection",
    "i2c_pullup": "I2C Pull-Up (4.7k x2 + Header)",
    "button_pullup": "Push Button with Pull-Up",
    "xtal_load": "Crystal with Load Capacitors",
    "battery_div": "Battery Voltage Divider",
    "polyfuse_in": "Polyfuse Input Protection",
    "tvs_clamp": "TVS Clamp Protection",
    "npn_inverter": "NPN Common-Emitter Inverter",
}

CAT_ORDER = ["电源变换", "基准电压", "电源保护", "指示", "开关驱动", "滤波去耦", "分压测量",
             "上拉与总线", "时钟谐振", "接口连接", "复位与时序", "放大与比较",
             "电平转换", "LED 驱动", "电机驱动", "电池管理", "电流检测",
             "传感器接口", "其他"]
CIRCUIT_CATS = {
    "led_3v3": "指示",
    "led_5v": "指示",
    "led_dual": "指示",
    "divider": "分压测量",
    "battery_div": "分压测量",
    "rc_filter": "滤波去耦",
    "decouple": "滤波去耦",
    "npn_switch": "开关驱动",
    "npn_inverter": "开关驱动",
    "pnp_high": "开关驱动",
    "mosfet_switch": "开关驱动",
    "button_pullup": "上拉与总线",
    "i2c_pullup": "上拉与总线",
    "ldo_1117": "电源变换",
    "schottky_rev": "电源保护",
    "polyfuse_in": "电源保护",
    "tvs_clamp": "电源保护",
    "xtal_load": "时钟谐振",
}


_CAT = None


def _catalog():
    """按需载入预设目录。

    builder.py 里是 `import engine`：本文件以 __main__ 跑时那会再生成一份
    engine 模块，符号库缓存和注册表都翻倍，所以先把名字指回当前实例。
    """
    global _CAT
    if _CAT is None:
        sys.modules.setdefault("engine", sys.modules[__name__])
        import catalog
        _CAT = catalog
    return _CAT


_PRESETS = None
_LABELS = None


def _relaid(fn):
    """手写的旧预设：文字位置交给和 catalog 同一套摆放器重排。

    builder 反过来 import engine，所以这里懒加载，避免循环。
    """
    def build():
        c = fn()
        sys.modules.setdefault("engine", sys.modules[__name__])
        import builder
        builder.layout_labels(c["symbols"])
        return c
    build.__doc__ = fn.__doc__
    return build


def presets():
    """手写 18 条 + catalog 家族摊出来的全部预设，key 冲突时保留手写的。"""
    global _PRESETS
    if _PRESETS is None:
        merged = dict(_catalog().CIRCUITS)
        merged.update((k, _relaid(fn)) for k, fn in CIRCUITS.items())
        _PRESETS = merged
    return _PRESETS


def preset_label(key):
    global _LABELS
    if _LABELS is None:
        merged = dict(_catalog().CIRCUIT_LABELS)
        merged.update(CIRCUIT_LABELS)
        _LABELS = merged
    return _LABELS.get(key, key)


def erc_key(circuit):
    """ERC 等价类：几何与引脚类型一致就能沿用同一次 ERC 结论。"""
    try:
        _catalog()            # 先把 engine 的模块别名铺好，避免 builder 二次加载
        import builder as _B
        return _B.erc_class(circuit)
    except Exception:
        return circuit.get("tpl") or circuit["name"]


def generate_schematic(circuit, outdir, log):
    """生成 .kicad_sch → upgrade → ERC → SVG/PDF/网表。返回 (sch路径, erc_ok, ERC 计数)。"""
    project = circuit["name"]
    root_uuid = "aaaaaaaa-0000-0000-0000-%012x" % (abs(hash(project)) % 10 ** 12)

    lib_cache = {}
    libs = []
    for nick, sname in circuit["libs"]:
        key = (nick, sname)
        if key not in lib_cache:
            path = os.path.join(KICAD_SYM, nick + ".kicad_sym")
            lib_cache[key] = lib_symbol(nick, path, sname)
        libs.append(lib_cache[key])

    items = []
    for w in circuit["wires"]:
        items.append(wire(*w))
    for sym in circuit["symbols"]:
        items.append(symbol_inst(
            project, root_uuid, sym["lib_id"], sym["x"], sym["y"], sym["rot"],
            sym["ref"], sym["val"], sym["fp"], sym["pins"],
            sym["ref_xy"], sym["val_xy"], sym["ref_hide"], sym["val_hide"]))

    sch = (
        "(kicad_sch\n\t(version 20231120)\n\t(generator \"eeschema\")\n"
        "\t(generator_version \"8.0\")\n\t(uuid \"%s\")\n\t(paper \"A4\")\n"
        "\t(title_block\n\t\t(title \"%s\")\n\t\t(date \"%s\")\n"
        '\t\t(rev "1.0")\n\t\t(comment 1 "Generated by AutoDraw")\n\t)\n'
        "\t(lib_symbols\n%s\n\t)\n%s"
        "\t(sheet_instances\n\t\t(path \"/\"\n\t\t\t(page \"1\")\n\t\t)\n\t)\n)\n"
        % (root_uuid, circuit["title"], "2026-10-11", "".join(libs), "".join(items))
    )
    sch_path = os.path.join(outdir, project + ".kicad_sch")
    with io.open(sch_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(sch)
    log("原理图已生成: %s" % sch_path)

    # upgrade to KiCad 10 format
    r = subprocess.run([KICAD_CLI, "sch", "upgrade", sch_path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    log("upgrade: " + ("OK" if r.returncode == 0 else (r.stderr or r.stdout).strip()))

    # ERC
    erc_rpt = os.path.join(outdir, project + "_erc.rpt")
    r = subprocess.run([KICAD_CLI, "sch", "erc", "--output", erc_rpt, sch_path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    erc_ok = False
    erc_info = {"msgs": 0, "errors": 0, "warns": 0}
    if os.path.exists(erc_rpt):
        txt = io.open(erc_rpt, encoding="utf-8").read()
        erc_ok = "ERC messages: 0" in txt
        for line in txt.splitlines():
            if "ERC messages" in line:
                log("ERC: " + line.strip())
                m = re.search(r"ERC messages:\s*(\d+)\s+Errors\s*(\d+)"
                              r"\s+Warnings\s*(\d+)", line)
                if m:
                    erc_info = {"msgs": int(m.group(1)),
                                "errors": int(m.group(2)),
                                "warns": int(m.group(3))}
                break
    else:
        log("ERC 失败: " + (r.stderr or r.stdout).strip())

    # exports
    r = subprocess.run([KICAD_CLI, "sch", "export", "svg", "--output", outdir, sch_path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    log("导出 SVG: " + ("OK" if r.returncode == 0 else "FAIL"))
    pdf_path = os.path.join(outdir, project + ".pdf")
    r = subprocess.run([KICAD_CLI, "sch", "export", "pdf", "--output", pdf_path, sch_path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    log("导出 PDF: " + ("OK" if r.returncode == 0 else "FAIL"))
    net_path = os.path.join(outdir, project + ".net")
    r = subprocess.run([KICAD_CLI, "sch", "export", "netlist", "--output", net_path, sch_path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    log("导出网表: " + ("OK" if r.returncode == 0 else "FAIL"))
    return sch_path, erc_ok, erc_info


PCB_SCRIPT = r'''
import json, math, os, sys, heapq
import pcbnew
cfg = json.load(open(sys.argv[2], "r", encoding="utf-8"))
out = sys.argv[1]
W, H = (float(v) for v in (cfg.get("board") or [50.0, 30.0])[:2])
HOLE_R = 1.35
MARGIN = 3.5
mm = pcbnew.FromMM
vec = lambda x, y: pcbnew.VECTOR2I(mm(x), mm(y))
board = pcbnew.CreateEmptyBoard()
# 先装封装、按真实外框尺寸排布，再反过来定板框：
# 固定 10mm 间距会让蜂鸣器/电位器/保险丝座这类大件互相压外框。
fp_root = cfg.get("fp_root") or r"D:\KiCad\share\kicad\footprints"
items = []
for ref, lib, name, _x, _y, rot in cfg.get("footprints", []):
    f = pcbnew.FootprintLoad(os.path.join(fp_root, lib + ".pretty"), name)
    if f is None:
        print("FP_FAIL", lib, name)
        continue
    f.SetReference(ref)
    f.SetValue(cfg.get("values", {}).get(ref, ""))
    if rot:
        f.SetOrientationDegrees(rot)
    board.Add(f)
    items.append((ref, f))


def fp_box(f):
    """外框优先；没画 Courtyard 的库退回焊盘+图形的包围盒。"""
    try:
        b = f.GetBoundingBox(pcbnew.F_Courtyard)
        if b.GetWidth() > 0 and b.GetHeight() > 0:
            return b
    except Exception:
        pass
    return f.GetBoundingBox()


SIDE = 6.0      # 元件区到板边的最小距离（要容得下安装孔）
CPAD = 2.0      # 外框之间的间隙
MAXW = 70.0     # 一行最宽，超了就换行
cx, cy, row_h, right, bottom = mm(SIDE), mm(SIDE), 0, mm(SIDE), mm(SIDE)
fp_map = {}
for ref, f in items:
    b = fp_box(f)
    if cx > mm(SIDE) and (cx - mm(SIDE) + b.GetWidth()) > mm(MAXW):
        cx, cy, row_h = mm(SIDE), cy + row_h + mm(CPAD), 0
    f.SetPosition(pcbnew.VECTOR2I(int(cx - b.GetX()), int(cy - b.GetY())))
    fp_map[ref] = f
    cx += b.GetWidth() + mm(CPAD)
    row_h = max(row_h, b.GetHeight())
    right = max(right, cx - mm(CPAD))
    bottom = max(bottom, cy + b.GetHeight())
W = max(W, pcbnew.ToMM(right) + SIDE)
H = max(H, pcbnew.ToMM(bottom) + SIDE)
corners = [(0, 0), (W, 0), (W, H), (0, H)]
for i in range(4):
    seg = pcbnew.PCB_SHAPE(board)
    seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
    seg.SetStart(vec(*corners[i]))
    seg.SetEnd(vec(*corners[(i + 1) % 4]))
    seg.SetLayer(pcbnew.Edge_Cuts)
    seg.SetWidth(mm(0.1))
    board.Add(seg)
# 安装孔
for x, y in [(MARGIN, MARGIN), (W - MARGIN, MARGIN),
             (MARGIN, H - MARGIN), (W - MARGIN, H - MARGIN)]:
    circ = pcbnew.PCB_SHAPE(board)
    circ.SetShape(pcbnew.SHAPE_T_CIRCLE)
    circ.SetCenter(vec(x, y))
    circ.SetEnd(vec(x + HOLE_R, y))
    circ.SetLayer(pcbnew.Edge_Cuts)
    circ.SetWidth(mm(0.1))
    board.Add(circ)
# 丝印
for text, x, y, size in [("AUTODRAW", W / 2.0, 4.0, 1.5),
                         (cfg.get("title", ""), W / 2.0, H - 4.0, 1.1)]:
    t = pcbnew.PCB_TEXT(board)
    t.SetText(text)
    t.SetPosition(vec(x, y))
    t.SetLayer(pcbnew.F_SilkS)
    t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
    t.SetTextThickness(mm(0.2))
    t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_CENTER)
    board.Add(t)
# 布线：先试直连与 L / Z 形，不通再上 0.5mm 网格 A*，正面挤满就换背面
INFL = mm(0.35)   # 焊盘避让余量（0.2 间距 + 半线宽）
GAP = mm(0.45)    # 走线间距余量
GS = mm(0.5)      # A* 网格步长
HALF_W = mm(0.125)  # 0.25 线宽的一半
VIA_R = mm(0.4)     # 换层过孔半径
LAYERS = (pcbnew.F_Cu, pcbnew.B_Cu)   # 正面挤满就打过孔换到背面
TURN = 0.4        # A* 拐弯罚分（步），让线尽量走直
BOUND = (mm(1), mm(1), mm(W - 1), mm(H - 1))
lines = []
vias = []   # 换层补的过孔：两面都算铜皮


def pads_named(fp, num):
    """同名引脚可能有多个（SOT-223 散热片=2 脚、按键两极），全部都要挂上网络。"""
    return [p for p in fp.Pads() if p.GetName() == num]


def pxy(p):
    return (p.GetPosition().x, p.GetPosition().y)


def pad_boxes():
    """焊盘包围盒 + 网络 + 钻孔尺寸和孔心：插孔焊盘两面都是铜，贴片只有一面。

    散热片上那种没名字、又整个被有网络的父焊盘盖住的小焊盘不算障碍——那是
    KiCad 画 TO-263/D2PAK 的 TAB 时拆出来的几块，本来就连在一起。"""
    out = []
    for f in board.GetFootprints():
        pads = list(f.Pads())
        owned = [p.GetBoundingBox() for p in pads if p.GetNetCode() != 0]
        for p in pads:
            bb = p.GetBoundingBox()
            code = p.GetNetCode()
            if code == 0 and not p.GetName() and any(
                    o.GetX() <= bb.GetX() and o.GetY() <= bb.GetY()
                    and o.GetRight() >= bb.GetRight()
                    and o.GetBottom() >= bb.GetBottom() for o in owned):
                continue
            pos = p.GetPosition()
            d = p.GetDrillSize()
            out.append((bb.GetX(), bb.GetY(), bb.GetRight(), bb.GetBottom(),
                        code, d.x, d.y, pos.x, pos.y))
    return out


def _cross(o, a, b):
    return (b[0] - o[0]) * (a[1] - o[1]) - (a[0] - o[0]) * (b[1] - o[1])


def _seg_seg(a, b, c, d):
    return ((_cross(a, b, c) > 0) != (_cross(a, b, d) > 0)
            and (_cross(c, d, a) > 0) != (_cross(c, d, b) > 0))


def _pt_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        return ((p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2) ** 0.5
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / float(dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return ((p[0] - a[0] - t * dx) ** 2 + (p[1] - a[1] - t * dy) ** 2) ** 0.5


def _dist(a, b):
    return ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5


def _rect_hit(p, q, r, infl):
    """Liang-Barsky：线段 pq 是否碰到外扩 infl 的轴对齐矩形。"""
    x1, y1 = float(r[0] - infl), float(r[1] - infl)
    x2, y2 = float(r[2] + infl), float(r[3] + infl)
    px, py = float(p[0]), float(p[1])
    dx, dy = float(q[0] - p[0]), float(q[1] - p[1])
    t0, t1 = 0.0, 1.0
    for pc, qc in ((-dx, px - x1), (dx, x2 - px), (-dy, py - y1), (dy, y2 - py)):
        if pc == 0:
            if qc < 0:
                return False
        elif pc < 0:
            t = qc / pc
            if t > t1:
                return False
            if t > t0:
                t0 = t
        else:
            t = qc / pc
            if t < t0:
                return False
            if t < t1:
                t1 = t
    return True


def blocked(a, b, code, boxes, layer):
    """直连/L 形快判：只跟同一铜层的铜皮抢地方——背面的线可以从贴片焊盘
    底下穿过，但插孔焊盘两面都是铜，躲不开。过孔落点另由 via_clear 严查。"""
    for r in boxes:
        if (r[4] != code and (layer == pcbnew.F_Cu or r[5] > 0)
                and _rect_hit(a, b, r, INFL)):
            return True
    for x, y, c2 in vias:
        if c2 != code and _rect_hit(a, b, (x - VIA_R, y - VIA_R,
                                           x + VIA_R, y + VIA_R), INFL):
            return True
    for s, e, c2, l2 in lines:
        if c2 == code or l2 != layer:
            continue
        if (_seg_seg(a, b, s, e)
                or min(_pt_seg(a, s, e), _pt_seg(b, s, e),
                       _pt_seg(s, a, b), _pt_seg(e, a, b)) < GAP):
            return True
    return False


def candidates(a, b):
    # 斜线一律不给：一条斜线在后面的 A* 眼里会变成一大块假障碍，
    # 直角线本来就是更好的画法，斜的交给 astar 绕。
    if a[0] == b[0] or a[1] == b[1]:
        yield [a, b]
    yield [a, (b[0], a[1]), b]
    yield [a, (a[0], b[1]), b]
    for off in (mm(1.2), mm(2), mm(3), mm(4.5), mm(6), mm(8)):
        for s in (1, -1):
            yield [a, (a[0], a[1] + s * off), (b[0], a[1] + s * off), b]
            yield [a, (a[0] + s * off, a[1]), (a[0] + s * off, b[1]), b]


def fits(pt):
    return BOUND[0] <= pt[0] <= BOUND[2] and BOUND[1] <= pt[1] <= BOUND[3]


def obstacles(code, layer, boxes):
    """别的网络的铜皮：('r' 矩形 / 's' 线段, 起点, 终点, 避让余量)。

    焊盘只挡同层：背面的线可以从贴片焊盘底下走，但插孔焊盘两面都是铜。
    走线也只挡本层——正面已有的线正是换到背面要甩开的拥挤源。
    背面那条路两端的过孔落点由 via_clear 单独按两层查。
    """
    out = []
    for r in boxes:
        if r[4] != code and (layer == pcbnew.F_Cu or r[5] > 0):
            out.append(("r", (r[0], r[1], r[2], r[3]), None, INFL))
    out += [("s", s, e, GAP) for s, e, c2, l2 in lines
            if c2 != code and l2 == layer]
    out += [("r", (x - VIA_R, y - VIA_R, x + VIA_R, y + VIA_R), None, INFL)
            for x, y, c2 in vias if c2 != code]
    return out


def astar(a, b, obs, pad, chk1=None, chk2=None):
    """0.5mm 网格 A*：绕开 obs 里的铜皮。四邻域走直角，拐弯罚一点让线尽量直。

    两端不从焊盘中心起跳——引出线最容易蹭到的就是同封装隔壁那个网络的焊盘
    （SOT-23 这类 0.9mm 脚距的），所以先在中心四个方向上各找一个干净的落点，
    再从这些落点做多源 A*，收到对面的落点就停。chk1/chk2 是两端的附加条件
    （那一端要放过孔时得按过孔的规矩再查；插孔焊盘本身穿透两层，不用放）。
    只在两端点外扩 pad 的窗口里搜，格子按障碍栅格化，一张板几十条线也是毫秒级。
    找不到路返回 None，由调用方换层或放大窗口再来。
    """
    x0 = max(BOUND[0], min(a[0], b[0]) - pad)
    x1 = min(BOUND[2], max(a[0], b[0]) + pad)
    y0 = max(BOUND[1], min(a[1], b[1]) - pad)
    y1 = min(BOUND[3], max(a[1], b[1]) + pad)
    if x0 >= x1 or y0 >= y1:
        return None
    nx = int((x1 - x0) / GS) + 1
    ny = int((y1 - y0) / GS) + 1
    solid = bytearray(nx * ny)

    def mark(i, j):
        if 0 <= i < nx and 0 <= j < ny:
            solid[j * nx + i] = 1

    for ob in obs:
        kind, s, e, clr = ob
        infl = clr + HALF_W + GS / 2.0
        rad = int(math.ceil(infl / GS))
        if kind == "r":
            r = s
            for j in range(int(math.floor((r[1] - infl - y0) / GS)),
                           int(math.ceil((r[3] + infl - y0) / GS)) + 1):
                for i in range(int(math.floor((r[0] - infl - x0) / GS)),
                               int(math.ceil((r[2] + infl - x0) / GS)) + 1):
                    mark(i, j)
            continue
        n = max(2, int(math.ceil(_dist(s, e) / (GS / 4.0))))
        for t in range(n + 1):
            px, py = s[0] + (e[0] - s[0]) * t / n, s[1] + (e[1] - s[1]) * t / n
            ci = int(round((px - x0) / GS))
            cj = int(round((py - y0) / GS))
            for j in range(cj - rad, cj + rad + 1):
                for i in range(ci - rad, ci + rad + 1):
                    mark(i, j)
    DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))

    def cell(p):
        i, j = int(round((p[0] - x0) / GS)), int(round((p[1] - y0) / GS))
        return None if (i < 0 or j < 0 or i >= nx or j >= ny) else j * nx + i

    def xy(c):
        return (x0 + (c % nx) * GS, y0 + (c // nx) * GS)

    def dirty(p, q):
        """引出线段本身能不能真落地：判据和 blocked 完全一致。

        这里曾经多让了半个格子（clr + GS），结果焊盘中心只要离某条既有走线
        不到 0.95mm，四个方向就全被判死——那个距离是焊盘自己的位置，挪多远
        都改不了，引脚等于被彻底封住。引出线的起点本来就在焊盘上，不该再
        多要这一份余量。"""
        for kind, s, e, clr in obs:
            if kind == "r":
                if _rect_hit(p, q, s, clr):
                    return True
            elif (_seg_seg(p, q, s, e)
                  or min(_pt_seg(p, s, e), _pt_seg(q, s, e),
                         _pt_seg(s, p, q), _pt_seg(e, p, q)) < clr):
                return True
        return False

    def stubs(p, chk):
        """中心往四个方向挪，直到「引出线不碰铜皮、落点本身也干净」为止。"""
        out = []
        for dx, dy in DIRS:
            d = GS
            while d <= mm(6):
                q = (p[0] + dx * d, p[1] + dy * d)
                c = cell(q)
                if c is None:
                    break
                # 落点按真实几何判（dirty 和 blocked 同一套标准），不再要求它在
                # 栅格里也是空的：栅格把障碍胀过半个格子，焊盘旁边那点正经能落地
                # 的位置会被这一圈胀掉，引脚等于被封死。每个方向都一路找到够不着
                # 为止，近处那个落点可能是个死口袋，远处还得留几个备选。
                if not dirty(p, q) and (not chk or chk(q)):
                    out.append((c, dx, dy, q))
                d += GS
        return out

    starts, ends = stubs(a, chk1), stubs(b, chk2)
    if not starts or not ends:
        return None
    kof = {(1, 0): 0, (-1, 0): 1, (0, 1): 2, (0, -1): 3}
    goals = dict((c, q) for c, _dx, _dy, q in ends)
    seedq = dict((c, q) for c, _dx, _dy, q in starts)
    gij = [(c % nx, c // nx) for c in goals]
    h = lambda c: min(abs(c % nx - i) + abs(c // nx - j) for i, j in gij)
    dist, parent = {}, {}
    pq = []
    for c, dx, dy, q in starts:
        if c not in dist:
            dist[c] = 1.0
            parent[c] = None
            heapq.heappush(pq, (1.0 + h(c), c, kof[(dx, dy)]))
    done = set()
    hit = None
    while pq:
        _f, cur, _d = heapq.heappop(pq)
        if cur in done:
            continue
        done.add(cur)
        if cur in goals:
            hit = cur
            break
        ci, cj = cur % nx, cur // nx
        for k, (di, dj) in enumerate(DIRS):
            ni, nj = ci + di, cj + dj
            if not (0 <= ni < nx and 0 <= nj < ny) or solid[nj * nx + ni]:
                continue
            nxt = nj * nx + ni
            cost = dist[cur] + 1.0 + (TURN if _d != k else 0.0)
            if cost < dist.get(nxt, 1e18):
                dist[nxt] = cost
                parent[nxt] = cur
                heapq.heappush(pq, (cost + h(nxt), nxt, k))
    if hit is None:
        return None
    chain = []
    c = hit
    while c is not None:
        chain.append(c)
        c = parent[c]
    chain.reverse()
    # 两端的落点用真实引出点，不用格子中心，这样引出线保持直角
    pts = [a, seedq[chain[0]]] + [xy(v) for v in chain[1:-1]] + [goals[hit], b]
    out = []
    for p in pts:
        if out and abs(p[0] - out[-1][0]) < 1 and abs(p[1] - out[-1][1]) < 1:
            continue
        out.append(p)
    path, i = [out[0]], 1
    while i < len(out) - 1:
        if (path[-1][0] == out[i][0] == out[i + 1][0]
                or path[-1][1] == out[i][1] == out[i + 1][1]):
            i += 1
            continue
        path.append(out[i])
        i += 1
    path.append(out[-1])
    return path


def via_clear(pt, code, boxes):
    """过孔是穿透两层的铜环加一个钻孔：落点要离别的网络的焊盘、离任何带孔的
    焊盘（孔碰孔也算违规）、离任一面的走线和已有过孔都留出间距。"""
    need = VIA_R + mm(0.2)
    for r in boxes:
        # 同网络的贴片焊盘可以放过孔压在上面（via-in-pad），带孔的可不行
        if (r[4] != code or r[5] > 0) and r[0] - need <= pt[0] <= r[2] + need \
                and r[1] - need <= pt[1] <= r[3] + need:
            return False
    for s, e, c2, l2 in lines:
        if c2 != code and _pt_seg(pt, s, e) < need + HALF_W:
            return False
    for x, y, _c2 in vias:
        if _dist(pt, (x, y)) < 2 * VIA_R + mm(0.2):
            return False
    return True


def route(a, b, code, layer, boxes, pth1, pth2):
    """找一条能落地的一通路，返回 (折点, 每段的铜层, 要补的过孔)。

    背面的路只在两头各引出一小段到正面（贴片焊盘只有一面有铜），中间走背面
    甩开正面的拥挤；插孔焊盘本来就穿透两层，那一端可以直接从背面接上，
    不用过孔。一段线不能同时属于两层，所以两端正都是贴片时至少要四个折点。
    """
    back = layer == pcbnew.B_Cu
    via1, via2 = back and not pth1, back and not pth2
    # 背面的路两头各有一小段是在正面上引出来的：落点既要过孔自己放得下，
    # 也要这一段引出线在正面不碰铜。只按背面查会挑出一个正面挤死的方向。
    vc = lambda p: via_clear(p, code, boxes)
    chk1 = (lambda q: vc(q) and not blocked(a, q, code, boxes,
                                            pcbnew.F_Cu)) if via1 else None
    chk2 = (lambda q: vc(q) and not blocked(b, q, code, boxes,
                                            pcbnew.F_Cu)) if via2 else None

    def plan(path):
        if not all(fits(pt) for pt in path[1:-1]):
            return None
        n = len(path)
        segs = []
        for i in range(n - 1):
            front = (i == 0 and via1) or (i == n - 2 and via2)
            if i == 0 and i == n - 2 and (via1 or via2):
                return None
            segs.append(pcbnew.F_Cu if front or not back else layer)
        pts = ([path[1]] if via1 else []) + ([path[n - 2]] if via2 else [])
        if len(set(pts)) != len(pts):
            return None
        for i in range(n - 1):
            if blocked(path[i], path[i + 1], code, boxes, segs[i]):
                return None
        for pt in pts:
            if not via_clear(pt, code, boxes):
                return None
        return segs, pts

    for path in candidates(a, b):
        got = plan(path)
        if got:
            return path, got
    for pad in (mm(6), mm(16), mm(30)):
        path = astar(a, b, obstacles(code, layer, boxes), pad, chk1, chk2)
        if path:
            got = plan(path)
            if got:
                return path, got
    return None


def add_via(pt, net, code):
    via = pcbnew.PCB_VIA(board)
    via.SetPosition(pcbnew.VECTOR2I(int(pt[0]), int(pt[1])))
    # KiCad 10 的 VIA 尺寸全在 padstack 里，裸 VIA 是个零直径孔
    ps = pcbnew.PADSTACK(via)
    for cl in (pcbnew.F_Cu, pcbnew.B_Cu):
        ps.SetShape(pcbnew.PAD_SHAPE_CIRCLE, cl)
        ps.SetSize(pcbnew.VECTOR2I(mm(0.8), mm(0.8)), cl)
    ps.SetDrillShape(pcbnew.PAD_DRILL_SHAPE_CIRCLE)
    via.SetPadstack(ps)
    via.SetDrill(mm(0.4))
    via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    via.SetNet(net)
    board.Add(via)
    vias.append((int(pt[0]), int(pt[1]), code))


def connect(p1, p2, net, code, boxes):
    """正面走不通就换背面，焊盘钻孔与否决定那一端要不要补过孔。

    长线先铺背面：一条横跨整排的正面线等于把板子从中切开，后面每一根要从
    这一排里引出来的引脚都得翻过它，越晚走越没有落脚的地方。
    """
    a, b = pxy(p1), pxy(p2)
    pth1, pth2 = p1.GetDrillSize().x > 0, p2.GetDrillSize().x > 0
    order = LAYERS if _dist(a, b) < mm(10) else (pcbnew.B_Cu, pcbnew.F_Cu)
    for lay in order:
        got = route(a, b, code, lay, boxes, pth1, pth2)
        if not got:
            continue
        path, (segs, pts) = got
        for pt in pts:
            add_via(pt, net, code)
        for i in range(len(path) - 1):
            s, e = path[i], path[i + 1]
            tr = pcbnew.PCB_TRACK(board)
            tr.SetStart(pcbnew.VECTOR2I(int(s[0]), int(s[1])))
            tr.SetEnd(pcbnew.VECTOR2I(int(e[0]), int(e[1])))
            tr.SetWidth(mm(0.25))
            tr.SetLayer(segs[i])
            tr.SetNet(net)
            board.Add(tr)
            lines.append((s, e, code, segs[i]))
        return True
    # 两层都堵死：宁可留一条会报短路的直线，也别把焊盘甩成未连接
    print("FALLBACK %s (%.2f,%.2f)->(%.2f,%.2f)"
          % (net.GetNetname(), pcbnew.ToMM(a[0]), pcbnew.ToMM(a[1]),
             pcbnew.ToMM(b[0]), pcbnew.ToMM(b[1])))
    tr = pcbnew.PCB_TRACK(board)
    tr.SetStart(pcbnew.VECTOR2I(int(a[0]), int(a[1])))
    tr.SetEnd(pcbnew.VECTOR2I(int(b[0]), int(b[1])))
    tr.SetWidth(mm(0.25))
    tr.SetLayer(pcbnew.F_Cu)
    tr.SetNet(net)
    board.Add(tr)
    lines.append((a, b, code, pcbnew.F_Cu))
    return False


nets = {}
links = []
for ref1, pad1, ref2, pad2, netname in cfg.get("tracks", []):
    if ref1 not in fp_map or ref2 not in fp_map:
        continue
    net = nets.get(netname)
    if net is None:
        net = pcbnew.NETINFO_ITEM(board, netname)
        board.Add(net)
        nets[netname] = net
    hits1 = pads_named(fp_map[ref1], pad1)
    hits2 = pads_named(fp_map[ref2], pad2)
    if not hits1 or not hits2:
        continue
    for p in hits1 + hits2:
        p.SetNet(net)
    links.append((hits1, hits2, net))

# 只有一个焊盘的净（对端是没封装的电源符号）也要挂上网络：这块铜该是谁的
# 就是谁的，布线才知道要躲它，而不是当它不存在。
lonely = []
for ref, num, netname in cfg.get("net_ends", []):
    if ref not in fp_map:
        continue
    net = nets.get(netname)
    if net is None:
        net = pcbnew.NETINFO_ITEM(board, netname)
        board.Add(net)
        nets[netname] = net
    hits = pads_named(fp_map[ref], num)
    for p in hits:
        p.SetNet(net)
    lonely.append((netname, hits))

# 先把网络全部分好，再开始布线：这时还没网络的焊盘才是真跟电路无关的铜
boxes = pad_boxes()
tied = {}

# 最远的那一对先走。长线要是留到最后，中间一排元件的焊盘和别人的走线早已把
# 通道占死，它只能直愣愣横穿整块板，那种线是最容易绕不开的。
links.sort(key=lambda lk: (pxy(lk[0][0])[0] - pxy(lk[1][0])[0]) ** 2
                         + (pxy(lk[0][0])[1] - pxy(lk[1][0])[1]) ** 2,
           reverse=True)


def near(p, pool):
    pos = p.GetPosition()
    return min(pool, key=lambda q: (q.GetPosition().x - pos.x) ** 2
                              + (q.GetPosition().y - pos.y) ** 2)


for hits1, hits2, net in links:
    code = net.GetNetCode()
    connect(hits1[0], hits2[0], net, code, boxes)
    for extra in hits1[1:] + hits2[1:]:
        home = hits1[0] if extra in hits1 else hits2[0]
        connect(extra, home, net, code, boxes)
    tied.setdefault(net.GetNetname(), []).extend(hits1 + hits2)

# 挂了网络却没被任何一条连线带到的焊盘（对端是电源符号，或者按钮那种一个
# 引脚拆成两块铜的封装）也得连上，不然 DRC 报 unconnected_items：邻里有组
# 织就并进组织，整条网络只剩自己人就把它们自己串起来。
for netname, pads in lonely:
    net = nets[netname]
    code = net.GetNetCode()
    pool = tied.setdefault(netname, [])
    for p in pads:
        if pool:
            connect(p, near(p, pool), net, code, boxes)
        pool.append(p)

board.Save(out)
print("PCB_WROTE", out, "fps=%d tracks=%d" % (len(fp_map), len(cfg.get("tracks", []))))
'''


def generate_pcb(circuit, outdir, log):
    """pcbnew 放件+布线 → DRC → 3D渲染。临时脚本放系统 temp，不污染输出目录。"""
    import json as _json
    pcb_path = os.path.join(outdir, circuit["name"] + ".kicad_pcb")
    cfg = circuit.get("pcb", {})
    values = {s["ref"]: s["val"] for s in circuit["symbols"]
              if not s["ref"].startswith("#")}
    cfg = dict(cfg, title=circuit["title"], values=values, fp_root=KICAD_FPS)
    tmpdir = tempfile.mkdtemp(prefix="autodraw_")
    cfg_path = os.path.join(tmpdir, "pcb_cfg.json")
    with io.open(cfg_path, "w", encoding="utf-8") as f:
        _json.dump(cfg, f)
    script = os.path.join(tmpdir, "pcb_gen.py")
    with io.open(script, "w", encoding="utf-8") as f:
        f.write(PCB_SCRIPT)
    # 先清掉上一轮的产物，否则脚本中途崩了会留下旧文件冒充新结果
    for stale in (pcb_path, os.path.join(outdir, circuit["name"] + "_drc.rpt")):
        if os.path.exists(stale):
            os.remove(stale)
    r = subprocess.run([KICAD_PY, script, pcb_path, cfg_path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not os.path.exists(pcb_path):
        log("PCB 生成失败: " + (r.stderr or r.stdout).strip())
        return None
    wrote = (r.stdout or "").strip()
    for ln in wrote.splitlines():
        if ln.startswith("FALLBACK"):
            log("走线绕不开: " + ln.split(" ", 1)[1])
    if "PCB_WROTE" not in wrote:
        log("PCB 脚本中断: " + ((r.stderr or wrote).strip().splitlines() or ["无输出"])[-1])
    else:
        log("PCB: " + wrote.splitlines()[-1])

    drc_rpt = os.path.join(outdir, circuit["name"] + "_drc.rpt")
    subprocess.run([KICAD_CLI, "pcb", "drc", "--output", drc_rpt, pcb_path],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
    if os.path.exists(drc_rpt):
        txt = io.open(drc_rpt, encoding="utf-8").read()
        for line in txt.splitlines():
            if "Found" in line or "violation" in line.lower():
                log("DRC: " + line.strip())

    png_path = os.path.join(outdir, circuit["name"] + "_pcb_3d.png")
    r = subprocess.run([KICAD_CLI, "pcb", "render", "--output", png_path, pcb_path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    log("3D渲染: " + ("OK -> " + os.path.basename(png_path) if r.returncode == 0 else "FAIL"))
    return pcb_path

# ============================ 嘉立创导出 ============================
# 2 层板；KiCad 默认输出的就是 Protel 扩展名(.gtl/.gbl/...)，嘉立创下单页直接认。
JLC_LAYERS = ("F.Cu,F.Mask,F.Paste,F.Silkscreen,"
              "B.Cu,B.Mask,B.Paste,B.Silkscreen,Edge.Cuts")
GERBER_EXT = (".gbr", ".gbrjob", ".gtl", ".gbl", ".gts", ".gbs", ".gtp",
              ".gbp", ".gto", ".gbo", ".gml", ".gko", ".drl")


def kicad_cli(args, timeout=300):
    return subprocess.run([KICAD_CLI] + list(args), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def cli_fail(r):
    return (r.stderr or r.stdout or "").strip().replace("\n", " ")[:240]


def export_jlc_bom(circuit, path):
    """按嘉立创 BOM 模板出四列：Comment,Designator,Footprint,LCSC(料号待填)。"""
    groups = []
    index = {}
    for s in circuit["symbols"]:
        if s["ref"].startswith("#"):
            continue
        key = (s["val"], s["fp"])
        if key not in index:
            index[key] = len(groups)
            groups.append([s["val"], [], s["fp"]])
        groups[index[key]][1].append(s["ref"])
    with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(u'"Comment","Designator","Footprint","LCSC"\r\n')
        for val, refs, fp in groups:
            f.write(u'"%s","%s","%s",""\r\n'
                    % (val, ",".join(refs), fp.replace('"', "'")))
    return len(groups)


def board_bounds(pcb_path):
    """从 .kicad_pcb 文本里取板框(Edge.Cuts)的 X/Y 范围，单位 mm。"""
    try:
        with io.open(pcb_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except IOError:
        return None
    pts = []
    num = re.compile(r"\((?:start|end|center)\s+(-?[\d.]+)\s+(-?[\d.]+)\)")
    for n, ln in enumerate(lines):
        if '"Edge.Cuts"' not in ln:
            continue
        for back in range(1, 14):
            if n - back < 0:
                break
            prev = lines[n - back]
            m = num.search(prev)
            if m:
                pts.append((float(m.group(1)), float(m.group(2))))
            if prev.lstrip().startswith("(gr_"):
                break
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def fix_cpl_origin(cpl_path, bounds):
    """KiCad 出的坐标 Y 轴向下且为负；嘉立创要板框左下角原点、Y 向上为正。"""
    x0, _, x1, y1 = bounds
    with io.open(cpl_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return 0
    hdr = rows[0]
    if "PosX" not in hdr or "PosY" not in hdr:
        return 0
    ix, iy = hdr.index("PosX"), hdr.index("PosY")
    moved = 0
    for row in rows[1:]:
        if len(row) <= max(ix, iy):
            continue
        try:
            row[ix] = "%.6f" % (float(row[ix]) - x0)
            row[iy] = "%.6f" % (float(row[iy]) + y1)
        except ValueError:
            continue
        moved += 1
    with io.open(cpl_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, lineterminator="\r\n")
        for row in rows:
            w.writerow(row)
    return moved


def export_jlc_files(circuit, outdir, log):
    """Gerber+钻孔 -> zip，另出征位 CSV 和嘉立创格式 BOM，凑齐下单三件套。"""
    name = circuit["name"]
    pcb = os.path.join(outdir, name + ".kicad_pcb")
    out = {}
    if not os.path.exists(pcb):
        log(u"嘉立创: 缺 .kicad_pcb，跳过 Gerber/坐标")
        return out
    gdir = os.path.join(outdir, name + "_gerber")
    os.makedirs(gdir, exist_ok=True)
    r = kicad_cli(["pcb", "export", "gerbers", "--output", gdir,
                   "--layers", JLC_LAYERS, "--use-drill-file-origin",
                   "--subtract-soldermask", "--precision", "6", pcb])
    if r.returncode != 0:
        log(u"Gerber 导出失败: " + cli_fail(r))
        return out
    r = kicad_cli(["pcb", "export", "drill", "--output", gdir, "--format",
                   "excellon", "--drill-origin", "plot", "--excellon-units",
                   "mm", "--excellon-zeros-format", "decimal", pcb])
    if r.returncode != 0:
        log(u"钻孔导出失败: " + cli_fail(r))
    gbr_zip = os.path.join(outdir, name + "_jlc_gerber.zip")
    count = 0
    with zipfile.ZipFile(gbr_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for fname in sorted(os.listdir(gdir)):
            p = os.path.join(gdir, fname)
            if os.path.isfile(p) and fname.lower().endswith(GERBER_EXT):
                z.write(p, fname)
                count += 1
    out["gbr"] = gbr_zip
    log(u"嘉立创 Gerber: %d 个文件(含钻孔) -> %s"
        % (count, os.path.basename(gbr_zip)))

    cpl = os.path.join(outdir, name + "_cpl.csv")
    r = kicad_cli(["pcb", "export", "pos", "--output", cpl, "--format", "csv",
                   "--units", "mm", "--side", "both", "--use-drill-file-origin",
                   "--exclude-dnp", pcb])
    if os.path.exists(cpl):
        out["cpl"] = cpl
        bounds = board_bounds(pcb)
        if bounds:
            moved = fix_cpl_origin(cpl, bounds)
            log(u"嘉立创坐标: %d 个位号, 原点=板框左下角 %.1fx%.1fmm -> %s"
                % (moved, bounds[2] - bounds[0], bounds[3] - bounds[1],
                   os.path.basename(cpl)))
        else:
            log(u"嘉立创坐标: -> " + os.path.basename(cpl) +
                u" (未找到板框, Y 轴未翻转)")
    else:
        log(u"坐标导出失败: " + cli_fail(r))

    jbom = os.path.join(outdir, name + "_jlc_bom.csv")
    n = export_jlc_bom(circuit, jbom)
    out["jbom"] = jbom
    log(u"嘉立创 BOM: %d 项 -> %s" % (n, os.path.basename(jbom)))
    return out


def export_lceda_package(circuit, outdir, log):
    """嘉立创EDA 专业版「导入 KiCad」只收 zip：工程文件放压缩包根目录；
    用到的封装做成 .pretty 一起打进去(全局库在对方机器上不存在)，再附 fp-lib-table。"""
    name = circuit["name"]
    zip_path = os.path.join(outdir, name + "_lceda.zip")
    entries = []
    for suffix in (".kicad_pro", ".kicad_sch", ".kicad_pcb", ".net"):
        p = os.path.join(outdir, name + suffix)
        if os.path.exists(p):
            entries.append((p, name + suffix))
    if not any(e[1].endswith(".kicad_sch") for e in entries):
        log(u"嘉立创导入包: 没有 .kicad_sch，跳过")
        return None
    pretty = {}
    missing = 0
    for row in circuit.get("pcb", {}).get("footprints", []):
        lib, mod = row[1], row[2]
        p = os.path.join(KICAD_FPS, lib + ".pretty", mod + ".kicad_mod")
        if os.path.exists(p):
            pretty.setdefault(lib, {})[mod] = p
        else:
            missing += 1
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p, arc in entries:
            z.write(p, arc)
        for lib in sorted(pretty):
            for mod in sorted(pretty[lib]):
                z.write(pretty[lib][mod], "%s.pretty/%s.kicad_mod" % (lib, mod))
        if pretty:
            table = [u"(fp_lib_table\n"]
            for lib in sorted(pretty):
                table.append(u'  (lib (name "%s")(type "KiCad")'
                             u'(uri "${KIPRJMOD}/%s.pretty")(options "")'
                             u'(descr ""))\n' % (lib, lib))
            table.append(u")\n")
            z.writestr("fp-lib-table", u"".join(table))
    nmods = sum(len(v) for v in pretty.values())
    log(u"嘉立创导入包: %d 个工程文件 + %d 个封装%s -> %s"
        % (len(entries), nmods,
           (u" (缺失 %d)" % missing) if missing else u"",
           os.path.basename(zip_path)))
    return zip_path


LCEDA_README = u"""嘉立创 EDA / 嘉立创下单 —— 用法说明
========================================

一、把电路导进嘉立创 EDA 专业版
1. 打开嘉立创 EDA 专业版(或官方的「格式转换助手 / 文件迁移助手」)。
2. 菜单：文件 -> 导入 -> KiCad，选中本目录里的 <电路名>_lceda.zip。
   官方只接受 zip；不要自己右键压缩文件夹，也不要只选单个 .kicad_sch。
   注意：专业版的导入跑在客户端内嵌的「格式转换助手」页面里，需要登录嘉立创
   账号并联网，图纸会上传到嘉立创服务器转换后再落回本地。不想上传就只在
   KiCad 里改图，下单时用下面的三件套。
   导入包已把用到的封装(.pretty 目录)和 fp-lib-table 一起打进 zip，
   因为 KiCad 的全局封装库在对方机器上是读不到的。
3. 原理图里内嵌了 lib_symbols，元件图形与引脚能直接还原；
   专业版的 KiCad 解析器按文件名后缀分派、按封装名绑定器件，
   所以 zip 里的 .pretty/*.kicad_mod 必须一起留着。
   若个别元件报"封装未找到"，在其属性里手选同名 0603 封装即可。
4. 立创商城料号(LCSC)不会自动匹配，需要按 <电路名>_jlc_bom.csv 的
   LCSC 列自己补齐(专业版里可以右键器件去匹配工厂料号)。

二、不进 EDA，直接在嘉立创下单
- <电路名>_jlc_gerber.zip : Gerber(Protel 扩展名) + 钻孔，传"Gerber 文件"栏。
- <电路名>_cpl.csv        : 贴片机坐标，单位 mm，原点已换算成板框左下角、
  Y 轴向上为正(与嘉立创贴片预览一致)。若预览里元件仍整体上下颠倒，
  在坐标设置里勾一次"Y 轴镜像/翻转 Y"即可。
- <电路名>_jlc_bom.csv    : BOM(Comment/Designator/Footprint/LCSC 四列)。
- 工艺参数：2 层、板厚 1.6mm、板框 50x30mm、四角 2.7mm 安装孔、
  线宽线距 0.25mm，常规工艺即可做。

三、继续改图
- 想换库或补料号，就在嘉立创 EDA 里改；
- 想回 KiCad 改，本目录的 .kicad_sch / .kicad_pcb 就是源文件，
  AutoDraw 也能重新生成(会覆盖同名文件)。

由 AutoDraw 生成。
"""


def export_lceda_readme(outdir):
    path = os.path.join(outdir, u"嘉立创EDA导入说明.txt")
    with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(LCEDA_README)
    return path


# ============================ 命令行协议 ============================
def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def log(msg):
    emit({"ev": "log", "text": unicode_str(msg)})


def unicode_str(s):
    if isinstance(s, bytes):
        return s.decode("utf-8", "replace")
    return s if isinstance(s, str) else repr(s)


def apply_paths(req):
    p = req.get("paths") or {}
    if p.get("kicad_root"):
        set_kicad_root(p["kicad_root"])
    if p.get("kicad_py"):
        set_kicad_py(p["kicad_py"])


def set_kicad_py(path):
    global KICAD_PY
    KICAD_PY = path


def _desc_of(fn):
    doc = (fn.__doc__ or "").strip().split("\n")[0]
    return doc.replace("·", "").strip()


# ==================== 预设元数据缓存 ====================
# presets_meta() 要把每条预设真的摆一遍，2300+ 条约 16 秒，是冷启动的主要开销。
# 元数据只由 engine/catalog/builder 三个源文件决定，所以按源码指纹缓存到磁盘：
# 源码没变直接读 JSON，改过一行自动作废重建。指纹只看内容不看 mtime，
# 免得开发目录和打包目录拿着同一份代码却各算各的哈希，互相冲掉缓存。
_META_FILES = ("engine.py", "catalog.py", "builder.py")


def _meta_signature():
    h = hashlib.sha256()
    for name in _META_FILES:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
        h.update(name.encode("utf-8"))
        try:
            with open(path, "rb") as f:
                blob = f.read()
            h.update(blob)
        except OSError:
            blob = b"missing"
            h.update(blob)
        # 长度单独进摘要，防止不同切分方式撞出同一个哈希
        h.update(b"|=%d" % len(blob))
    return h.hexdigest()[:16]


def _meta_cache_paths():
    out = []
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        out.append(os.path.join(base, "AutoDraw"))
    out.append(os.path.dirname(os.path.abspath(__file__)))
    return [os.path.join(d, "presets_meta.json") for d in out]


def presets_meta():
    """把所有预设摊成轻量元数据：1000+ 条也能一次发给界面。

    结果按源码指纹缓存；缓存不可写或过期时回落到现算，不报错。
    """
    sig = _meta_signature()

    def load_cache():
        for path in _meta_cache_paths():
            try:
                with io.open(path, encoding="utf-8") as f:
                    blob = json.load(f)
            except Exception:
                continue
            if blob.get("sig") == sig and isinstance(blob.get("items"), list):
                return blob["items"], path
        return None, None

    cached, _ = load_cache()
    if cached is not None:
        return cached
    items = []
    broken = []
    for key, fn in presets().items():
        try:
            c = fn()
        except Exception as e:
            # 单条预设摆位失败只丢这一条，别让整份列表变成空界面。
            broken.append((key, "%s: %s" % (type(e).__name__, e)))
            continue
        real = [s for s in c["symbols"] if not s["ref"].startswith("#")]
        pcb = c.get("pcb", {})
        items.append({
            "key": key,
            "label": preset_label(key),
            "title": c["title"],
            "cat": c.get("cat") or CIRCUIT_CATS.get(key, "其他"),
            "desc": _desc_of(fn),
            "params": c.get("params") or [],
            "tpl": c.get("tpl") or key,
            "bom": ", ".join("%s %s" % (s["ref"], s["val"]) for s in real),
            "parts": len(real),
            "power": sorted(set(s["val"] for s in c["symbols"]
                                if s["ref"].startswith("#PWR"))),
            "wires": len(c["wires"]),
            "footprints": len(pcb.get("footprints", [])),
            "tracks": len(pcb.get("tracks", [])),
            "libs": len(set(c["libs"])),
        })
    if broken:
        emit({"ev": "log",
              "text": "有 %d 条预设摆位失败，已从列表里跳过：%s"
                      % (len(broken),
                         "; ".join("%s(%s)" % x for x in broken[:5]))})
    for path in _meta_cache_paths():
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with io.open(path, "w", encoding="utf-8") as f:
                json.dump({"sig": sig, "items": items}, f,
                          ensure_ascii=False)
            break
        except Exception:
            continue
    return items


def preset_detail(key):
    """单条预设的完整预览数据（几何 + 网络 + 清单），界面即时出图用。"""
    c = presets()[key]()
    geo = circuit_geometry(c)
    return {
        "key": key,
        "label": preset_label(key),
        "title": c["title"],
        "cat": c.get("cat") or CIRCUIT_CATS.get(key, "其他"),
        "desc": _desc_of(presets()[key]),
        "params": c.get("params") or [],
        "tpl": c.get("tpl") or key,
        "power": sorted(set(s["val"] for s in c["symbols"]
                            if s["ref"].startswith("#PWR"))),
        "bom": [{"ref": s["ref"], "val": s["val"], "fp": s["fp"],
                 "lib": s["lib_id"]}
                for s in c["symbols"] if not s["ref"].startswith("#")],
        "nets": circuit_nets(c),
        "pcb": c.get("pcb", {}),
        **geo
    }


def run_generate(req):
    keys = req.get("keys") or []
    outdir = (req.get("outdir") or DEFAULT_OUT).strip()
    sch_on = bool(req.get("sch", True))
    pcb_on = bool(req.get("pcb", True))
    jlc_on = bool(req.get("jlc", True))
    strict_erc = bool(req.get("strict_erc", False))
    steps = ((1 if sch_on else 0) + (1 if pcb_on else 0)
             + (1 if (jlc_on and pcb_on) else 0)) or 1
    total = len(keys)
    proven = set()
    stats = {"total": total, "ok": 0, "erc_bad": 0, "pcb_bad": 0}
    if outdir:
        os.makedirs(outdir, exist_ok=True)
    log("输出目录: %s" % outdir)
    for idx, key in enumerate(keys):
        if key not in presets():
            emit({"ev": "error", "text": "未知预设: %r" % key})
            continue
        circuit = presets()[key]()
        tpl = circuit.get("tpl") or key
        emit({"ev": "start", "key": key, "tpl": tpl, "index": idx,
              "total": total, "label": preset_label(key)})
        log("=" * 52)
        log("[%d/%d] %s" % (idx + 1, total, preset_label(key)))
        erc_ok, pcb_ok = True, False
        erc_info = {"msgs": 0, "errors": 0, "warns": 0, "trusted": False}
        if sch_on:
            # 同 ERC 等价类（几何+引脚类型一致）的变体只需真跑一次 ERC。
            trust = (not strict_erc and erc_key(circuit) in proven)
            if trust:
                erc_info["trusted"] = True
                log("几何与前面某条同拓扑，沿用已通过的 ERC 结果。")
            else:
                _, erc_ok, erc_info = generate_schematic(circuit, outdir, log)
                erc_info.setdefault("trusted", False)
                if erc_ok:
                    proven.add(erc_key(circuit))
            emit({"ev": "progress", "pct": int((idx * steps + 1) /
                                               float(total * steps) * 100),
                  "label": "%s 原理图" % key})
        if pcb_on:
            pcb_ok = generate_pcb(circuit, outdir, log) is not None
            emit({"ev": "progress", "pct": int((idx * steps + 2) /
                                               float(total * steps) * 100),
                  "label": "%s PCB" % key})
        bom = export_bom(circuit, outdir)
        jlc_files = {}
        if jlc_on and pcb_on:
            try:
                jlc_files = export_jlc_files(circuit, outdir, log)
                lp = export_lceda_package(circuit, outdir, log)
                if lp:
                    jlc_files["lczip"] = lp
            except Exception as e:
                log("嘉立创导出失败: %r" % e)
            emit({"ev": "progress", "pct": int((idx * steps + 3) /
                                               float(total * steps) * 100),
                  "label": "%s 嘉立创文件" % key})
        erc_note = ("原理图 ERC: 0 错误 0 告警" if erc_ok else
                    "原理图 ERC: %d 错误 %d 告警" % (erc_info["errors"],
                                                    erc_info["warns"]))
        rpt = export_html_report(circuit, outdir, erc_ok,
                                 {erc_note: erc_ok, "PCB DRC": pcb_ok},
                                 jlc_files)
        files = {}
        for suffix, tag in ((".kicad_sch", "sch"), (".pdf", "pdf"), (".net", "net"),
                            ("_erc.rpt", "erc"), (".kicad_pcb", "pcb"),
                            ("_drc.rpt", "drc"), ("_pcb_3d.png", "png"),
                            ("_bom.csv", "bom"), ("_report.html", "report"),
                            (".svg", "svg")):
            p = os.path.join(outdir, key + suffix)
            if os.path.exists(p):
                files[tag] = p.replace("\\", "/")
        for tag, p in jlc_files.items():
            files[tag] = p.replace("\\", "/")
        emit({"ev": "result", "key": key, "tpl": tpl,
              "label": preset_label(key),
              "erc_ok": erc_ok, "erc_errors": erc_info["errors"],
              "erc_warns": erc_info["warns"],
              "erc_trusted": bool(erc_info.get("trusted")),
              "pcb_ok": pcb_ok,
              "outdir": outdir, "files": files})
        stats["ok"] += 1 if (erc_ok and (pcb_ok or not pcb_on)) else 0
        stats["erc_bad"] += 0 if erc_ok else 1
        stats["pcb_bad"] += 0 if (pcb_ok or not pcb_on) else 1
        log("[%d/%d] %s 完成 — ERC %s%s, PCB %s"
            % (idx + 1, total, key,
               "通过" if erc_ok else
               ("%d 错误 %d 告警" % (erc_info["errors"], erc_info["warns"])),
               "(沿用)" if erc_info.get("trusted") else "",
               "已生成" if pcb_ok else "未生成"))
    readme = None
    if jlc_on:
        readme = export_lceda_readme(outdir).replace("\\", "/")
        log("导入说明: " + readme)
    emit({"ev": "progress", "pct": 100, "label": "完成"})
    emit({"ev": "done", "total": total, "jlc": jlc_on, "readme": readme,
          "stats": stats})


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except Exception:
        raw = "{}"
    try:
        req = json.loads(raw or "{}")
    except Exception as e:
        emit({"ev": "error", "text": "请求 JSON 解析失败: %r" % e})
        emit({"ev": "done"})
        return
    action = req.get("action", "env")
    try:
        apply_paths(req)
        if action == "list":
            emit({"ev": "presets", "items": presets_meta()})
        elif action == "detail":
            key = req.get("key")
            if key not in presets():
                emit({"ev": "error", "text": "未知预设: %r" % key})
            else:
                emit({"ev": "detail", "data": preset_detail(key)})
        elif action == "env":
            emit({"ev": "env", "data": check_env()})
        elif action == "generate":
            run_generate(req)
        else:
            emit({"ev": "error", "text": "未知 action: %r" % action})
    except Exception as e:
        import traceback
        emit({"ev": "error", "text": "%r" % e,
              "trace": traceback.format_exc()})
    if action != "generate":
        emit({"ev": "done"})


if __name__ == "__main__":
    main()
