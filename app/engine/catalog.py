# -*- coding: utf-8 -*-
"""预设目录：拓扑家族 × 参数网格 → 1000+ 条常用模块。

1000+ 条不可能手写坐标，所以这里只写「家族」：
  * 一个家族 = 一个 layout(b) 函数，用 builder 在格点上摆出拓扑；
  * 参数网格（电源轨 / 阻容取值 / 型号 / 封装）负责把家族摊成几百条预设；
  * 每条预设的几何都由 builder 现算并自检（网表==图纸），不放心就不出图；
  * 同家族只有取值不同的预设共享 ERC 等价类（builder.erc_class），
    校验脚本每个拓扑只跑一次真 KiCad ERC。

加一条预设：找对家族往网表里加一行；没有对应家族就新写一个 f_xxx() 并 @family。
"""

import io
import math
import os
import re

import builder as B
from builder import Block

E = B.E

# ------------------------------------------------------------------ 标量表
E24 = [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
       3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1]


def nearest_e24(v):
    """算出来的值贴到最近的 E24 标准值。"""
    if v <= 0:
        return 0.0
    e = int(math.floor(math.log10(v)))
    best = None
    for m in (e - 1, e, e + 1):
        for base in E24:
            c = base * (10 ** m)
            if best is None or abs(c - v) / max(v, c) < abs(best - v) / max(v, best):
                best = c
    return best


def fmt_r(v):
    """1000 → '1k'，4700 → '4k7'，220 → '220R'。"""
    if v >= 1e6:
        return _trim(v / 1e6) + "M"
    if v >= 1e3:
        return _trim(v / 1e3, "k")
    return _trim(v) + "R"


def fmt_c(v):
    """1e-9 → '1n'，100e-6 → '100u'。"""
    if v >= 1e-3:
        return _trim(v * 1e3) + "m"
    if v >= 1e-6:
        return _trim(v * 1e6, "u")
    if v >= 1e-9:
        return _trim(v * 1e9, "n")
    return _trim(v * 1e12) + "p"


def fmt_u(v):
    return _trim(v) + "V"


def fmt_l(v):
    """33e-6 → '33u'，1e-3 → '1m'（和电容同一套量级缩写）。"""
    return fmt_c(v)


def _trim(x, unit=""):
    s = "%.2f" % x
    if s.endswith("00"):
        s = s[:-2]
    elif s.endswith("0"):
        s = s[:-1]
    if s.endswith("."):
        s = s[:-1]
    if unit:
        if "." in s:                              # 4.7k 写成 4k7
            a, b = s.split(".")
            s = a + unit + b
        else:
            s += unit
    return s


def tok(s):
    """把 '4k7' / '+3V3' / '100n' 变成可当文件名 key 的片段。"""
    out = str(s).replace("+", "p").replace(" ", "")
    for ch in "-./*():;<>|\\\"'":
        out = out.replace(ch, "p")
    while "pp" in out:
        out = out.replace("pp", "p")
    return out.strip("p").lower()


# ------------------------------------------------------------------ 电源轨
VOLT = {
    "+1V0": 1.0, "+1V1": 1.1, "+1V2": 1.2, "+1V35": 1.35, "+1V5": 1.5,
    "+1V8": 1.8, "+2V5": 2.5, "+2V8": 2.8, "+3V0": 3.0, "+3V3": 3.3,
    "+3V8": 3.8, "+4V": 4.0, "+5V": 5.0, "+6V": 6.0, "+7.5V": 7.5,
    "+8V": 8.0, "+9V": 9.0, "+10V": 10.0, "+12V": 12.0, "+15V": 15.0,
    "+24V": 24.0, "+28V": 28.0, "+36V": 36.0, "+48V": 48.0,
    "VBUS": 5.0, "+BATT": 7.4, "VCC": 5.0, "VDD": 3.3, "VAA": 12.0,
    "-2V5": -2.5, "-3V3": -3.3, "-5V": -5.0, "-6V": -6.0, "-8V": -8.0,
    "-9V": -9.0, "-10V": -10.0, "-12V": -12.0, "-15V": -15.0,
    "-24V": -24.0, "-36V": -36.0, "-48V": -48.0,
}


def rname(net):
    """电源轨的中文名：+3V3 → 3.3V，-12V → -12V，VBUS → USB 5V。"""
    if net == "VBUS":
        return "USB 5V"
    if net == "+BATT":
        return "电池"
    if net in ("VCC", "VDD", "VAA"):
        return net
    sign = "-" if net.startswith("-") else ""
    a, _, b = net.lstrip("+-").rstrip("V").partition("V")
    return sign + (a + "." + b if b else a) + "V"


LED_RAILS = ["+3V3", "+5V", "+6V", "+9V", "+12V", "+24V", "+48V", "VBUS"]
ANY_RAILS = ["+1V8", "+2V5", "+3V0", "+3V3", "+5V", "+6V", "+9V",
             "+12V", "+15V", "+24V", "+48V", "VBUS", "+BATT"]

# ------------------------------------------------------------------ 封装表
R_FP = {"0402": "Resistor_SMD:R_0402_1005Metric",
        "0603": "Resistor_SMD:R_0603_1608Metric",
        "0805": "Resistor_SMD:R_0805_2012Metric",
        "1206": "Resistor_SMD:R_1206_3216Metric",
        "THT": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal"}
C_FP = {"0402": "Capacitor_SMD:C_0402_1005Metric",
        "0603": "Capacitor_SMD:C_0603_1608Metric",
        "0805": "Capacitor_SMD:C_0805_2012Metric",
        "1206": "Capacitor_SMD:C_1206_3216Metric",
        "THT": "Capacitor_THT:CP_Radial_D6.3mm_P2.50mm"}
LED_FP = {"0603": "LED_SMD:LED_0603_1608Metric",
          "0805": "LED_SMD:LED_0805_2012Metric",
          "1206": "LED_SMD:LED_1206_3216Metric",
          "THT": "LED_THT:LED_D3.0mm"}
D_FP = {"0603": "Diode_SMD:D_0603_1608Metric",
        "0805": "Diode_SMD:D_0805_2012Metric",
        "1206": "Diode_SMD:D_1206_3216Metric",
        "SOD123": "Diode_SMD:D_SOD-123",
        "SMA": "Diode_SMD:D_SMA",
        "SMB": "Diode_SMD:D_SMB",
        "THT": "Diode_THT:D_DO-35_SOD27_P10.16mm_Horizontal"}
L_FP = {"0603": "Inductor_SMD:L_0603_1608Metric",
        "0805": "Inductor_SMD:L_0805_2012Metric",
        "1210": "Inductor_SMD:L_1210_3225Metric"}
FB_FP = {"0603": "Inductor_SMD:L_0603_1608Metric",
         "0805": "Inductor_SMD:L_0805_2012Metric",
         "1206": "Inductor_SMD:L_1206_3216Metric"}
CP_FP = {"3216": "Capacitor_Tantalum_SMD:CP_EIA-3216-18_Kemet-A",
         "ELEC5": "Capacitor_SMD:CP_Elec_5x5.4",
         "THT63": "Capacitor_THT:CP_Radial_D6.3mm_P2.50mm"}
XTAL_FP = "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm"
OSC_FP = "Oscillator:Oscillator_SMD_SeikoEpson_SG210-4Pin_2.5x2.0mm"
SW_FP = "Button_Switch_THT:SW_PUSH_6mm"
BZ_FP = "Buzzer_Beeper:Buzzer_12x9.5RM7.6"
HDR = {n: "Connector_PinHeader_2.54mm:PinHeader_1x%02d_P2.54mm_Vertical" % n
       for n in range(1, 16)}
SOT23 = "Package_TO_SOT_SMD:SOT-23-3"
SOT223 = "Package_TO_SOT_SMD:SOT-223-3_TabPin2"
TO220 = "Package_TO_SOT_THT:TO-220-3_Vertical"
TO92 = "Package_TO_SOT_THT:TO-92_Inline_Wide"
SO8 = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
MSOP8 = "Package_SO:MSOP-8-1EP_3x3mm_P0.65mm_EP1.68x1.88mm"
TSSOP10 = "Package_SO:TSSOP-10_3x3mm_P0.5mm"
DIP8 = "Package_DIP:DIP-8_W7.62mm"
DIP14 = "Package_DIP:DIP-14_W7.62mm"
DIP16 = "Package_DIP:DIP-16_W7.62mm"
SOT235 = "Package_TO_SOT_SMD:SOT-23-5"
TP_FP = "TestPoint:TestPoint_Pad_D2.0mm"

# ------------------------------------------------------------------ 常用件
RES_STD = [10, 22, 47, 100, 220, 330, 470, 1000, 2200, 4700, 10000,
           22000, 47000, 100000, 220000, 470000, 1000000]
CAP_STD = [10e-12, 100e-12, 1e-9, 10e-9, 100e-9, 1e-6, 4.7e-6, 10e-6,
           22e-6, 47e-6, 100e-6, 220e-6, 470e-6]
LED_V = {"红": 1.9, "黄": 2.0, "绿": 2.1, "蓝": 3.1, "白": 3.1}
LED_EN = {"红": "red", "黄": "yellow", "绿": "green", "蓝": "blue", "白": "white"}


# ------------------------------------------------------------------ 器件接线
_FP_PROP_RE = re.compile(r'property "Footprint" "([^"]*)"')
_FP_CACHE = {}


def fp_of(lib_id, fallback=""):
    """器件库自己标注的推荐封装；库里没写或盘上没有就退回 fallback。"""
    if lib_id in _FP_CACHE:
        return _FP_CACHE[lib_id]
    out = ""
    lib, name = lib_id.split(":", 1)
    p = os.path.join(E.KICAD_SYM, lib + ".kicad_sym")
    try:
        txt = io.open(p, encoding="utf-8", errors="replace").read()
    except IOError:
        txt = ""
    i = txt.find('\n\t(symbol "%s"' % name)
    if i >= 0:
        m = _FP_PROP_RE.search(txt[i:i + 4000])
        if m and m.group(1) and B.footprint_exists(m.group(1)):
            out = m.group(1)
    out = out or fallback
    _FP_CACHE[lib_id] = out
    return out


NC_NAMES = ("NC", "~{NC}")


def pin_names(lib_id):
    """{引脚名: [引脚号]}：同名多脚（PGND/PVDD/VO）算一组。"""
    out = {}
    for p in E.lib_geometry(lib_id)[1]:
        out.setdefault(p["name"], []).append(p["num"])
    return out


def byname(lib_id, *pairs):
    """按引脚名分配网络 → ({引脚号: 网络}, [该悬空的脚号])。

    写错名字、或者漏接了非 NC 引脚都直接报错 —— 免得"少写一脚"被静默
    变成"这脚本来就该悬空"。
    """
    names = pin_names(lib_id)
    want = {}
    for nm, net in pairs:
        if nm not in names:
            raise B.BuildError("%s 没有叫 %s 的引脚（有 %s）"
                               % (lib_id, nm, "/".join(sorted(names))))
        want.update((num, net) for num in names[nm])
    nc = []
    for nm, nums in names.items():
        for num in nums:
            if num in want:
                continue
            if nm not in NC_NAMES:
                raise B.BuildError("%s 的 %s 脚(%s) 没接线"
                                   % (lib_id, nm, num))
            nc.append(num)
    return want, sorted(set(nc), key=lambda t: (len(t), t))


def pick(lib_id, cands):
    """在候选引脚名里挑这个器件真有的第一个（各家命名不统一）。"""
    names = pin_names(lib_id)
    for nm in cands:
        if nm in names:
            return nm
    return None


def place(b, ref, lib_id, val, *pairs, **kw):
    """放一个多脚 IC：按引脚名接线，NC 脚自动声明悬空，封装取库里推荐值。"""
    pinmap, nc = byname(lib_id, *pairs)
    fp = kw.pop("fp", None) or fp_of(lib_id)
    s = b.dev(ref, lib_id, val, fp, pinmap, **kw)
    if nc:
        b.nc(ref, *nc)
    return s


# ------------------------------------------------------------------ 注册机制
FAMILIES = []


def family(fn):
    FAMILIES.append(fn)
    return fn


def spec(key, label, cat, tpl, desc, params, layout, **kw):
    """把「家族 + 参数」压成一条预设：返回 (key, label, 建图闭包)。

    闭包被调用时才真的摆一次 builder，自检不过抛 BuildError ——
    宁可这条出不了图，也不交一份看着像对的电路。
    """
    def build():
        b = Block(key, label, **kw)
        layout(b)
        return b.finish(cat=cat, desc=desc, params=list(params), tpl=tpl)
    build.__doc__ = desc
    return key, label, build


def _cached(build):
    """同一条预设的几何只算一次；预览和出图共用。"""
    box = {}

    def get():
        if not box:
            box["c"] = build()
        return box["c"]
    get.__doc__ = build.__doc__
    return get


def combos(**lists):
    """候选参数笛卡儿积 → variant 字典列表；_blk 里的键交给 Block 构造函数。"""
    keys = sorted(lists)
    out = [{}]
    for k in keys:
        nxt = []
        for v in out:
            for x in lists[k]:
                w = dict(v)
                if k == "blk":
                    w.setdefault("_blk", {}).update(x)
                else:
                    w[k] = x
                nxt.append(w)
        out = nxt
    return out


def solved(key, label, cat, tpl, desc, params, layout, variants):
    """多脚 IC 家族：几何敏感，按候选摆位逐个试，自检通过就定案。

    摆位不同（旋转角、线间距、轨道高度）只是画法差别，拓扑不变；
    第一个能通过对账的候选会被缓存进闭包，所以每次进程结果稳定。
    """
    box = {}

    def build():
        if not box:
            err = None
            for p in variants:
                blk = dict(p.get("_blk", {}))
                rest = dict((k, v) for k, v in p.items() if k != "_blk")
                try:
                    b = Block(key, label, **blk)
                    layout(b, **rest)
                    box["c"] = b.finish(cat=cat, desc=desc,
                                        params=list(params), tpl=tpl)
                    box["used"] = p
                except B.BuildError as e:
                    err = e
                    continue
            if not box:
                raise err or B.BuildError("没有可用摆位")
        return box["c"]
    build.__doc__ = desc
    return key, label, build


def collect():
    """摊开所有家族 → (CIRCUITS, CIRCUIT_LABELS, 家族统计)。"""
    circuits, labels, tpl_of = {}, {}, {}
    for fn in FAMILIES:
        for key, label, build in fn():
            if key in circuits:
                raise ValueError("预设 key 重复: %s" % key)
            circuits[key] = _cached(build)
            labels[key] = label
    return circuits, labels, tpl_of


def cats():
    out = {}
    for key, fn in CIRCUITS.items():
        out[key] = fn()["cat"]
    return out


# ================================================================== 指示
def _led_top(b, rail, rv, color, pkg):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("A")
    b.shunt("R1", "Device:R", fmt_r(rv), R_FP[pkg], "A", rail)
    b.shunt("D1", "Device:LED", "LED_" + LED_EN[color], LED_FP[pkg], "A", "GND")


@family
def f_led_top():
    """限流电阻在上：电源轨 → R → LED → GND，按目标电流自动选阻值。"""
    for rail in LED_RAILS:
        v = VOLT[rail]
        for color, vf in LED_V.items():
            if v - vf < 0.4:
                continue
            for ma in (2, 5, 10):
                r = nearest_e24((v - vf) * 1000.0 / ma)
                real = (v - vf) / (r / 1000.0)
                pkg = "0603"
                key = "led_%s_%s_%s" % (tok(rail), LED_EN[color], fmt_r(r))
                yield spec(
                    key, "%s %s LED 指示灯" % (rname(rail), color),
                    "指示", "led_top",
                    "%s 经 %s 限流点亮%s LED，约 %.1fmA。"
                    % (rname(rail), fmt_r(r), color, real),
                    [["电源", rname(rail)], ["颜色", color],
                     ["限流", fmt_r(r)], ["电流", "%.1fmA" % real],
                     ["封装", pkg]],
                    lambda b, rail=rail, r=r, color=color, pkg=pkg:
                    _led_top(b, rail, r, color, pkg))


def _led_bottom(b, rail, rv, color):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("A")
    b.shunt("D1", "Device:LED", "LED_" + LED_EN[color], LED_FP["0603"],
            "A", rail)
    b.shunt("R1", "Device:R", fmt_r(rv), R_FP["0603"], "A", "GND")


@family
def f_led_bottom():
    """LED 在上、电阻在下（阴极侧限流），共阴接法时用。"""
    for rail in LED_RAILS:
        v = VOLT[rail]
        for color, vf in LED_V.items():
            if v - vf < 0.4:
                continue
            for rv in (470, 1000, 2200, 4700):
                key = "ledn_%s_%s_%s" % (tok(rail), LED_EN[color], fmt_r(rv))
                yield spec(
                    key, "%s %s 指示灯（LED 在上）" % (rname(rail), color),
                    "指示", "led_bottom",
                    "%s 先经%s LED 再经 %s 到地。"
                    % (rname(rail), color, fmt_r(rv)),
                    [["电源", rname(rail)], ["颜色", color],
                     ["限流", fmt_r(rv)]],
                    lambda b, rail=rail, rv=rv, color=color:
                    _led_bottom(b, rail, rv, color))


def _led_stack(b, rail, rv, color):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("A", "M")
    b.shunt("R1", "Device:R", fmt_r(rv), R_FP["0805"], "A", rail)
    b.series("D1", "Device:LED", "LED_" + LED_EN[color], LED_FP["0805"],
             "A", "M", pin_a="1", pin_b="2", row_off=0)
    b.shunt("D2", "Device:LED", "LED_" + LED_EN[color], LED_FP["0805"],
            "M", "GND")


@family
def f_led_stack():
    """两只同色 LED 串联，抬高压降、让 12/24V 下的限流损耗减半。"""
    for rail in ("+9V", "+12V", "+24V", "+48V", "+BATT"):
        v = VOLT[rail]
        for color, vf in LED_V.items():
            if v - 2 * vf < 0.5:
                continue
            for ma in (5, 10):
                r = nearest_e24((v - 2 * vf) * 1000.0 / ma)
                key = "led2_%s_%s_%s" % (tok(rail), LED_EN[color], fmt_r(r))
                yield spec(
                    key, "%s 双 LED 串联指示灯" % rname(rail),
                    "指示", "led_stack",
                    "%s → %s → 2×%s LED 串联 → GND，约 %dmA。"
                    % (rname(rail), fmt_r(r), color, ma),
                    [["电源", rname(rail)], ["颜色", color],
                     ["限流", fmt_r(r)], ["电流", "%dmA" % ma]],
                    lambda b, rail=rail, r=r, color=color:
                    _led_stack(b, rail, r, color))


def _led_pair(b, rail, c1, c2, r1, r2):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("A1", "A2")
    b.shunt("R1", "Device:R", fmt_r(r1), R_FP["0603"], "A1", rail)
    b.shunt("D1", "Device:LED", "LED_" + LED_EN[c1], LED_FP["0603"],
            "A1", "GND")
    b.shunt("R2", "Device:R", fmt_r(r2), R_FP["0603"], "A2", rail)
    b.shunt("D2", "Device:LED", "LED_" + LED_EN[c2], LED_FP["0603"],
            "A2", "GND")


@family
def f_led_pair():
    """同轨两路指示灯（双色并放）。"""
    for rail in ("+3V3", "+5V", "+12V", "+24V"):
        v = VOLT[rail]
        for c1, c2 in (("红", "绿"), ("红", "蓝"), ("绿", "蓝"),
                       ("红", "黄"), ("黄", "绿")):
            if min(v - LED_V[c1], v - LED_V[c2]) < 0.4:
                continue
            r1 = nearest_e24((v - LED_V[c1]) / 0.005)
            r2 = nearest_e24((v - LED_V[c2]) / 0.005)
            key = "ledp_%s_%s%s" % (tok(rail), LED_EN[c1], LED_EN[c2])
            yield spec(
                key, "%s 双灯指示（%s+%s）" % (rname(rail), c1, c2),
                "指示", "led_pair",
                "%s 下两路 5mA 指示灯：%s %s / %s %s。"
                % (rname(rail), fmt_r(r1), c1, fmt_r(r2), c2),
                [["电源", rname(rail)], ["灯1", "%s %s" % (c1, fmt_r(r1))],
                 ["灯2", "%s %s" % (c2, fmt_r(r2))]],
                lambda b, rail=rail, c1=c1, c2=c2, r1=r1, r2=r2:
                _led_pair(b, rail, c1, c2, r1, r2))


def _neon(b, rail, rv):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("A")
    b.shunt("R1", "Device:R", fmt_r(rv), R_FP["1206"], "A", rail)
    b.shunt("L1", "Device:Lamp_Neon", "氖灯", LED_FP["THT"], "A", "GND")


@family
def f_neon():
    """高压指示：氖灯 + 高阻限流，常见于电源开关板。"""
    for rail in ("+12V", "+24V", "+48V"):
        for rv in (100000, 220000, 470000):
            key = "neon_%s_%s" % (tok(rail), fmt_r(rv))
            yield spec(
                key, "%s 氖灯指示" % rname(rail),
                "指示", "neon",
                "%s → %s → 氖灯 → GND，起辉后微亮常通。"
                % (rname(rail), fmt_r(rv)),
                [["电源", rname(rail)], ["限流", fmt_r(rv)]],
                lambda b, rail=rail, rv=rv: _neon(b, rail, rv),
                pitch=12.7)


def _buzzer(b, rail):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("A")
    b.shunt("SW1", "Switch:SW_Push", "使能", SW_FP, "A", rail)
    b.shunt("BZ1", "Device:Buzzer", "有源蜂鸣器", BZ_FP, "A", "GND",
            pin_a="1", pin_b="2")
    b.header("J1", "A", "left", "EN")


@family
def f_buzzer():
    """有源蜂鸣器直驱：给电就响，配一个使能接线柱。"""
    for rail in ("+3V3", "+5V", "+9V", "+12V", "+24V"):
        key = "bzr_%s" % tok(rail)
        yield spec(
            key, "%s 有源蜂鸣器" % rname(rail),
            "指示", "buzzer",
            "%s 经接线柱 J1 接到有源蜂鸣器，另一脚落地。" % rname(rail),
            [["电源", rname(rail)]],
            lambda b, rail=rail: _buzzer(b, rail))


def _rgb(b, rail):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("RA", "GA", "BA")
    for color, x in (("红", "RA"), ("绿", "GA"), ("蓝", "BA")):
        v = VOLT[rail] - LED_V[color]
        rv = nearest_e24(v / 0.005)
        i = ("RA", "GA", "BA").index(x) + 1
        b.shunt("R%d" % i, "Device:R", fmt_r(rv), R_FP["0603"], x, rail)
        b.shunt("D%d" % i, "Device:LED", "LED_" + LED_EN[color],
                LED_FP["0603"], x, "GND")
    b.header("J1", "RA", "left", "R")
    b.header("J3", "BA", "right", "B")


@family
def f_led_tricolor():
    """三色指示灯（等效共阴 RGB）：红绿蓝各一路限流。"""
    for rail in ("+3V3", "+5V", "+9V", "+12V", "+24V"):
        if min(VOLT[rail] - LED_V[c] for c in ("蓝", "白")) < 0.4:
            continue
        key = "rgb_%s" % tok(rail)
        rs = " ".join(fmt_r(nearest_e24((VOLT[rail] - LED_V[c]) / 0.005))
                      for c in ("红", "绿", "蓝"))
        yield spec(
            key, "%s 三色指示灯" % rname(rail),
            "指示", "led_tricolor",
            "红/绿/蓝三路并联指示，阴极共点接地，限流依次 %s（各约 5mA）。" % rs,
            [["电源", rname(rail)], ["限流", rs], ["电流", "5mA×3"]],
            lambda b, rail=rail: _rgb(b, rail),
            pitch=15.24, lane=20.32)


# ================================================================== 分压测量
def _div_adc(b, rail, vout, rb, rt, real):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("MID")
    b.shunt("R1", "Device:R", fmt_r(rt), R_FP["0805"], "MID", rail)
    b.shunt("R2", "Device:R", fmt_r(rb), R_FP["0805"], "MID", "GND")
    b.header("J1", "MID", "right", "ADC")


@family
def f_divider_adc():
    """把高压轨分压到 ADC 量程，自动算上偏电阻。"""
    for rail in ("+5V", "+6V", "+9V", "+12V", "+15V", "+24V", "+48V",
                 "VBUS", "+BATT"):
        v = VOLT[rail]
        for vout in (1.8, 2.5, 3.0, 3.3):
            if vout >= v:
                continue
            for rb in (10000, 22000, 100000, 1000000):
                rt = nearest_e24(rb * (v / vout - 1.0))
                if rt / rb < 0.2:
                    continue
                real = v * rb / (rt + rb)
                key = "div_%s_to_%s_rb%s" % (tok(rail), tok(fmt_u(vout)),
                                             fmt_r(rb))
                yield spec(
                    key, "%s → %s 分压采样" % (rname(rail), fmt_u(vout)),
                    "分压测量", "div_adc",
                    "%s 经 %s/%s 分压得 %.2fV，满量程 %.1f%%。"
                    % (rname(rail), fmt_r(rt), fmt_r(rb), real,
                       real / vout * 100),
                    [["输入", rname(rail)], ["目标", fmt_u(vout)],
                     ["上偏", fmt_r(rt)], ["下偏", fmt_r(rb)],
                     ["实际", "%.2fV" % real]],
                    lambda b, rail=rail, vout=vout, rb=rb, rt=rt, real=real:
                    _div_adc(b, rail, vout, rb, rt, real))


def _div_rc(b, rail, rb, rt, vout):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("MID", "F")
    b.shunt("R1", "Device:R", fmt_r(rt), R_FP["0805"], "MID", rail)
    b.shunt("R2", "Device:R", fmt_r(rb), R_FP["0805"], "F", "GND")
    b.series("R3", "Device:R", "1k", R_FP["0603"], "MID", "F")
    b.shunt("C1", "Device:C", "100n", C_FP["0402"], "F", "GND", col=1.5)
    b.header("J1", "F", "right", "ADC")


@family
def f_divider_rc():
    """分压 + RC 滤波：ADC 采样的标准前端。"""
    for rail in ("+5V", "+9V", "+12V", "+24V", "+48V", "VBUS", "+BATT"):
        v = VOLT[rail]
        for vout in (2.5, 3.3):
            if vout >= v:
                continue
            for rb in (10000, 100000):
                rt = nearest_e24(rb * (v / vout - 1.0))
                key = "divrc_%s_%s_rb%s" % (tok(rail), tok(fmt_u(vout)),
                                            fmt_r(rb))
                yield spec(
                    key, "%s → %s 分压滤波" % (rname(rail), fmt_u(vout)),
                    "分压测量", "div_rc",
                    "%s/%s 分压后再用 1k+100n 滤到 %.0fHz 附近。"
                    % (fmt_r(rt), fmt_r(rb), 1 / (2 * math.pi * 1000 * 100e-9)),
                    [["输入", rname(rail)], ["目标", fmt_u(vout)],
                     ["上偏", fmt_r(rt)], ["下偏", fmt_r(rb)],
                     ["滤波", "1k+100n"]],
                    lambda b, rail=rail, rb=rb, rt=rt, vout=vout:
                    _div_rc(b, rail, rb, rt, vout),
                    pitch=12.7)


def _pot(b, rail, rv):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("TOP", "WIP")
    b.dev("RV1", "Device:R_Potentiometer", fmt_r(rv),
          "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
          {"1": rail, "2": "WIP", "3": "GND"}, col="TOP", rot=90)
    b.header("J1", "WIP", "right", "OUT")


@family
def f_potentiometer():
    """电位器三分压：旋转取 0～满量程。"""
    for rail in ("+3V3", "+5V", "+9V", "+12V", "+24V", "VCC"):
        for rv in (10000, 50000, 100000):
            key = "pot_%s_%s" % (tok(rail), fmt_r(rv))
            yield spec(
                key, "%s 电位器调压" % rname(rail),
                "分压测量", "pot",
                "%s 跨接 %s 电位器，滑动端输出 0～%s。"
                % (rname(rail), fmt_r(rv), rname(rail)),
                [["电源", rname(rail)], ["电位器", fmt_r(rv)]],
                lambda b, rail=rail, rv=rv: _pot(b, rail, rv),
                pitch=15.24, lane=20.32)


def _bat_div(b, cells, rb, rt, v):
    net = "+BATT"
    b.rail(net, side="up")
    b.rail("GND")
    b.cols("MID")
    b.dev("B1", "Device:Battery_Cell", "%dS 锂电" % cells, "",
          {"1": net, "2": "GND"}, col="MID", rot=0, x_off=-10.16)
    b.shunt("R1", "Device:R", fmt_r(rt), R_FP["0805"], "MID", net)
    b.shunt("R2", "Device:R", fmt_r(rb), R_FP["0805"], "MID", "GND")
    b.header("J1", "MID", "right", "VSEN")


@family
def f_battery_div():
    """锂电池组电压采样：1S～4S 各配一档分压。"""
    for cells, vc in ((1, 4.2), (2, 8.4), (3, 12.6), (4, 16.8)):
        for rb in (10000, 100000):
            rt = nearest_e24(rb * (vc / 3.3 - 1.0))
            key = "battdiv_%ds_rb%s" % (cells, fmt_r(rb))
            yield spec(
                key, "%dS 电池电压采样" % cells,
                "分压测量", "batt_div",
                "%dS 锂电满电 %.1fV，用 %s/%s 分压到 3.3V 量程。"
                % (cells, vc, fmt_r(rt), fmt_r(rb)),
                [["电池", "%dS %.1fV" % (cells, vc)],
                 ["上偏", fmt_r(rt)], ["下偏", fmt_r(rb)]],
                lambda b, cells=cells, rb=rb, rt=rt, vc=vc:
                _bat_div(b, cells, rb, rt, vc))


# ================================================================== 上拉与总线
def _pull(b, rail, rv, up):
    b.cols("SIG")
    if up:
        b.rail(rail, side="up")
        b.rail("GND")
        b.shunt("R1", "Device:R", fmt_r(rv), R_FP["0603"], "SIG", rail)
    else:
        b.rail(rail, side="up")
        b.rail("GND")
        b.shunt("R1", "Device:R", fmt_r(rv), R_FP["0603"], "SIG", "GND")
    b.header("J1", "SIG", "left", "SIG")


@family
def f_pull_single():
    """单线上/下拉：开关量输入最常见的保命电阻。"""
    for rail in ANY_RAILS:
        for rv in (1000, 2200, 4700, 10000, 47000, 100000):
            for up in (True, False):
                key = "%s_%s_%s" % ("pu" if up else "pd", tok(rail), fmt_r(rv))
                yield spec(
                    key, "%s %s（%s）" % (rname(rail),
                                         "上拉" if up else "下拉", fmt_r(rv)),
                    "上拉与总线", "pull_up" if up else "pull_down",
                    "信号线经 %s %s到%s，静态无电流路径。"
                    % (fmt_r(rv), "" if up else "落地、另一端接", rname(rail)),
                    [["电源", rname(rail)], ["方式", "上拉" if up else "下拉"],
                     ["阻值", fmt_r(rv)]],
                    lambda b, rail=rail, rv=rv, up=up: _pull(b, rail, rv, up))


def _pull_array(b, rail, rv, n):
    nets = ["L%d" % i for i in range(1, n + 1)]
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols(*nets)
    for i, net in enumerate(nets):
        b.shunt("R%d" % (i + 1), "Device:R", fmt_r(rv), R_FP["0603"],
                net, rail)
        b.header("J%d" % (i + 1), net, "right", net)


@family
def f_pull_array():
    """多路并排上拉：一次把整条总线拉住。"""
    for n in (4, 8):
        for rail in ("+3V3", "+5V", "+12V"):
            for rv in (1000, 4700, 10000):
                key = "purn%d_%s_%s" % (n, tok(rail), fmt_r(rv))
                yield spec(
                    key, "%s %d 路上拉" % (rname(rail), n),
                    "上拉与总线", "pull_array%d" % n,
                    "%d 条线各用 %s 上拉到 %s，右端逐线引出。"
                    % (n, fmt_r(rv), rname(rail)),
                    [["电源", rname(rail)], ["线数", "%d" % n],
                     ["阻值", fmt_r(rv)]],
                    lambda b, rail=rail, rv=rv, n=n:
                    _pull_array(b, rail, rv, n),
                    pitch=20.32, lane=20.32)


def _i2c(b, rail, rv):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("SDA", "SCL")
    b.shunt("R1", "Device:R", fmt_r(rv), R_FP["0603"], "SDA", rail)
    b.shunt("R2", "Device:R", fmt_r(rv), R_FP["0603"], "SCL", rail)
    b.header("J1", "SDA", "left", "SDA")
    b.header("J2", "SCL", "right", "SCL")


@family
def f_i2c_pullup():
    """I2C 两条线的上拉。"""
    for rail in ("+3V3", "+5V", "+1V8", "+2V5"):
        for rv in (1000, 2200, 4700, 10000):
            key = "i2c_%s_%s" % (tok(rail), fmt_r(rv))
            yield spec(
                key, "I2C 上拉（%s / %s）" % (rname(rail), fmt_r(rv)),
                "上拉与总线", "i2c_pullup",
                "SDA/SCL 各用 %s 上拉到 %s，标准模式 100k。"
                % (fmt_r(rv), rname(rail)),
                [["电源", rname(rail)], ["上拉", fmt_r(rv)],
                 ["总线", "I2C"]],
                lambda b, rail=rail, rv=rv: _i2c(b, rail, rv),
                pitch=12.7)


def _i2c_shift(b, lo, hi, rv, dx=0.0):
    """单向电平搬移：低压侧 drives 栅极，高压侧只挂在漏极上。"""
    b.rail(hi, side="up")
    b.rail(lo, side="up")
    b.rail("GND", side="down")
    b.cols("L", "H")
    # rot=0：栅极从左侧进（落在低压行节点上），漏/源上下分布
    b.dev("Q1", "Transistor_FET:2N7002", "2N7002", SOT23,
          {"1": "L", "2": "GND", "3": "H"}, col="H", rot=0, x_off=-7.62 + dx)
    b.shunt("R1", "Device:R", fmt_r(rv), R_FP["0603"], "H", hi)
    b.shunt("R2", "Device:R", fmt_r(rv), R_FP["0603"], "L", lo)
    b.header("J1", "L", "left", "SDA-%s" % rname(lo))
    b.header("J2", "H", "right", "SDA-%s" % rname(hi))


SHIFT_PAIRS = [("+1V8", "+3V3"), ("+2V5", "+3V3"), ("+3V3", "+5V"),
               ("+1V8", "+5V"), ("+2V5", "+5V")]


@family
def f_i2c_level_shift():
    """单管 I2C 电平转换：两边各两条上拉。"""
    for lo, hi in SHIFT_PAIRS:
        for rv in (4700, 10000):
            key = "i2cshift_%s_%s_%s" % (tok(lo), tok(hi), fmt_r(rv))
            yield solved(
                key, "I2C 电平转换 %s↔%s" % (rname(lo), rname(hi)),
                "电平转换", "i2c_shift",
                "2N7002 单向抬电平，%s 侧栅极驱动、%s 侧漏极上拉，各用 %s。"
                % (rname(lo), rname(hi), fmt_r(rv)),
                [["低压", rname(lo)], ["高压", rname(hi)],
                 ["上拉", fmt_r(rv)]],
                lambda b, lo=lo, hi=hi, rv=rv, **p:
                _i2c_shift(b, lo, hi, rv, dx=p.get("dx", 0.0)),
                combos(dx=[0.0, 1.27, -1.27, 2.54],
                       blk=[{"pitch": 12.7, "lane": 17.78,
                             "lane_step": 6.35},
                            {"pitch": 15.24, "lane": 20.32,
                             "lane_step": 6.35}]))


def _series_r(b, sig, rv):
    b.rail("GND")
    b.cols("IN", "OUT")
    b.series("R1", "Device:R", fmt_r(rv), R_FP["0603"], "IN", "OUT")
    b.header("J1", "IN", "left", "驱动侧")
    b.header("J2", "OUT", "right", sig)


SIG_LINES = ["MOSI", "MISO", "SCK", "CS", "SDA", "SCL", "TX", "RX",
             "PWM", "ADC", "DAC", "CLK", "RST", "INT"]


@family
def f_spi_series():
    """单线串阻：压振铃、降 EMI，SPI/UART/时钟线通用。"""
    for sig in SIG_LINES:
        for rv in (22, 33, 47, 100, 220):
            key = "sr_%s_%s" % (sig.lower(), fmt_r(rv))
            yield spec(
                key, "%s 线串阻 %s" % (sig, fmt_r(rv)),
                "上拉与总线", "series_r",
                "%s 线源端串 %s，吸收边沿反射、压住振铃。" % (sig, fmt_r(rv)),
                [["信号", sig], ["串阻", fmt_r(rv)]],
                lambda b, sig=sig, rv=rv: _series_r(b, sig, rv),
                pitch=12.7)


def _one_wire(b, rail, rv, rot=0, x_off=10.16, y_off=0):
    """DS1822 的 1=GND、2=DQ、3=VDD，别按直觉把电源接到 1 脚。"""
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("BUS")
    b.shunt("R1", "Device:R", fmt_r(rv), R_FP["0603"], "BUS", rail)
    b.shunt("C1", "Device:C", "100n", C_FP["0402"], "BUS", "GND")
    b.dev("U1", "Sensor_Temperature:DS1822", "DS1822", TO92,
          {"1": "GND", "2": "BUS", "3": rail}, col="BUS", rot=rot,
          x_off=x_off, y_off=y_off)
    b.header("J1", "BUS", "left", "1-WIRE")


@family
def f_one_wire():
    """单总线：上拉 + 器件 + 去耦。"""
    for rail in ("+3V3", "+5V", "+12V"):
        for rv in (2200, 4700, 10000):
            key = "onewire_%s_%s" % (tok(rail), fmt_r(rv))
            yield solved(
                key, "单总线上拉 %s" % fmt_r(rv),
                "上拉与总线", "one_wire",
                "%s 经 %s 上拉单总线，挂 DS1822 与 100n 去耦。"
                % (rname(rail), fmt_r(rv)),
                [["电源", rname(rail)], ["上拉", fmt_r(rv)]],
                lambda b, rail=rail, rv=rv, **p: _one_wire(b, rail, rv, **p),
                combos(rot=[0, 180], x_off=[10.16, 12.7, 7.62, 15.24],
                       blk=[{"pitch": 15.24, "lane": 20.32},
                            {"pitch": 15.24, "lane": 25.4}]))


def _uart_esd(b):
    b.rail("GND")
    b.cols("TX", "RX")
    b.shunt("D1", "Device:D_TVS", "TVS 5V", D_FP["SOD123"], "TX", "GND")
    b.shunt("D2", "Device:D_TVS", "TVS 5V", D_FP["SOD123"], "RX", "GND")
    b.header("J1", "TX", "left", "TX")
    b.header("J2", "RX", "right", "RX")


@family
def f_uart_esd():
    """串口两线对地 TVS 钳位。"""
    key = "uart_esd"
    yield spec(
        key, "UART 口 TVS 保护",
        "电源保护", "uart_esd",
        "TX/RX 各挂一只双向 TVS 到地，ESD 先泄放再进芯片。",
        [["线路", "TX/RX"], ["器件", "SOD-123 TVS"]],
        _uart_esd, pitch=12.7)


# ================================================================== 滤波去耦
def _decouple(b, rail, cv, pkg):
    b.rail(rail, side="up")
    b.rail("GND")
    b.dev("C1", "Device:C", fmt_c(cv), C_FP[pkg],
          {"1": rail, "2": "GND"}, col=0)


@family
def f_decouple():
    """单只去耦电容。"""
    for rail in ("+1V8", "+2V5", "+3V3", "+5V", "+9V", "+12V", "+24V", "VDD"):
        for cv in (100e-12, 1e-9, 10e-9, 100e-9, 1e-6, 10e-6):
            for pkg in ("0402", "0603", "0805"):
                key = "dec_%s_%s_%s" % (tok(rail), tok(fmt_c(cv)), pkg)
                yield spec(
                    key, "%s 去耦 %s/%s" % (rname(rail), fmt_c(cv), pkg),
                    "滤波去耦", "decouple",
                    "%s 就近放 %s（%s）到地，滤掉中高频纹波。"
                    % (rname(rail), fmt_c(cv), pkg),
                    [["电源", rname(rail)], ["容量", fmt_c(cv)],
                     ["封装", pkg]],
                    lambda b, rail=rail, cv=cv, pkg=pkg:
                    _decouple(b, rail, cv, pkg))


def _decouple_pair(b, rail, big, pkg):
    b.rail(rail, side="up")
    b.rail("GND")
    b.dev("C1", "Device:C", "100n", C_FP["0603"], {"1": rail, "2": "GND"},
          col=0)
    b.dev("C2", "Device:C_Polarized", fmt_c(big), CP_FP["ELEC5"],
          {"1": rail, "2": "GND"}, col=0, x_off=7.62)


@family
def f_decouple_pair():
    """大小电容并联：小瓷片管高频，大电解管低频储能。"""
    for rail in ("+3V3", "+5V", "+9V", "+12V", "+24V", "VBUS"):
        for big in (10e-6, 22e-6, 100e-6, 220e-6, 470e-6):
            key = "dec2_%s_%s" % (tok(rail), tok(fmt_c(big)))
            yield spec(
                key, "%s 去耦组合 100n+%s" % (rname(rail), fmt_c(big)),
                "滤波去耦", "decouple_pair",
                "%s 上 100n 瓷片并联 %s 电解，高低频一起滤。"
                % (rname(rail), fmt_c(big)),
                [["电源", rname(rail)], ["小电容", "100n"],
                 ["大电容", fmt_c(big)]],
                lambda b, rail=rail, big=big: _decouple_pair(b, rail, big,
                                                             "0603"),
                pitch=12.7)


def _pi(b, rail, beads, cv):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("IN", "OUT")
    b.shunt("C1", "Device:C", fmt_c(cv), C_FP["0603"], "IN", "GND")
    b.series("FB1", "Device:FerriteBead", beads, FB_FP["1206"], "IN", "OUT")
    b.shunt("C2", "Device:C", fmt_c(cv), C_FP["0603"], "OUT", "GND")
    b.header("J1", "IN", "left", "IN")
    b.header("J2", "OUT", "right", "OUT")


@family
def f_pi_filter():
    """磁珠 π 型滤波：DC 电源进板一级。"""
    for rail in ("+1V8", "+2V5", "+3V3", "+5V", "+12V"):
        for beads, cur in (("600R@100MHz", "600mA"), ("120R@100MHz", "2A"),
                           ("60R@100MHz", "3A")):
            for cv in (10e-6, 22e-6, 100e-6):
                key = "pi_%s_%s_%s" % (tok(rail), tok(cur), tok(fmt_c(cv)))
                yield spec(
                    key, "%s 磁珠 π 滤波（%s）" % (rname(rail), cur),
                    "滤波去耦", "pi_filter",
                    "%s → %s → %s，两侧 %s 磁珠 π 型滤波。"
                    % (rname(rail), fmt_c(cv), beads, fmt_c(cv)),
                    [["电源", rname(rail)], ["磁珠", beads],
                     ["电流", cur], ["电容", fmt_c(cv)]],
                    lambda b, rail=rail, beads=beads, cv=cv:
                    _pi(b, rail, beads, cv),
                    pitch=15.24)


def _rc_lp(b, rv, cv):
    b.rail("GND")
    b.cols("IN", "OUT")
    b.series("R1", "Device:R", fmt_r(rv), R_FP["0603"], "IN", "OUT")
    b.shunt("C1", "Device:C", fmt_c(cv), C_FP["0603"], "OUT", "GND")
    b.header("J1", "IN", "left", "IN")
    b.header("J2", "OUT", "right", "OUT")


@family
def f_rc_lowpass():
    """RC 低通：按 R×C 报截止频率。"""
    for rv in (100, 1000, 4700, 10000, 47000, 100000):
        for cv in (1e-9, 10e-9, 100e-9, 1e-6, 10e-6):
            fc = 1 / (2 * math.pi * rv * cv)
            key = "rcLP_%s_%s" % (fmt_r(rv), tok(fmt_c(cv)))
            yield spec(
                key, "RC 低通 %.0fHz" % fc,
                "滤波去耦", "rc_lowpass",
                "%s + %s，截止约 %.1gHz，ADC 前端常用。"
                % (fmt_r(rv), fmt_c(cv), fc),
                [["电阻", fmt_r(rv)], ["电容", fmt_c(cv)],
                 ["截止", "%.0fHz" % fc]],
                lambda b, rv=rv, cv=cv: _rc_lp(b, rv, cv),
                pitch=12.7)


def _rc_hp(b, rv, cv):
    b.rail("GND")
    b.cols("IN", "OUT")
    b.series("C1", "Device:C", fmt_c(cv), C_FP["0603"], "IN", "OUT")
    b.shunt("R1", "Device:R", fmt_r(rv), R_FP["0603"], "OUT", "GND")
    b.header("J1", "IN", "left", "IN")
    b.header("J2", "OUT", "right", "OUT")


@family
def f_rc_highpass():
    """RC 高通 / 交流耦合：隔直流只让变化量过。"""
    for rv in (1000, 10000, 47000, 100000):
        for cv in (100e-9, 1e-6, 4.7e-6, 10e-6):
            fc = 1 / (2 * math.pi * rv * cv)
            key = "rcHP_%s_%s" % (fmt_r(rv), tok(fmt_c(cv)))
            yield spec(
                key, "RC 高通 %.0fHz" % fc,
                "滤波去耦", "rc_highpass",
                "%s 隔直 + %s 到地，截止约 %.1gHz。"
                % (fmt_c(cv), fmt_r(rv), fc),
                [["电容", fmt_c(cv)], ["电阻", fmt_r(rv)],
                 ["截止", "%.0fHz" % fc]],
                lambda b, rv=rv, cv=cv: _rc_hp(b, rv, cv),
                pitch=12.7)


def _snubber(b, rv, cv):
    b.rail("GND")
    b.cols("SW")
    b.series("R1", "Device:R", fmt_r(rv), R_FP["0805"], "SW", "N2")
    b.shunt("C1", "Device:C", fmt_c(cv), C_FP["0805"], "N2", "GND")
    b.header("J1", "SW", "left", "开关节点")


@family
def f_snubber():
    """RC 吸收：吃掉开关节点的振铃尖峰。"""
    for rv in (10, 22, 47, 100, 220):
        for cv in (470e-12, 1e-9, 2.2e-9, 10e-9):
            key = "snub_%s_%s" % (fmt_r(rv), tok(fmt_c(cv)))
            yield spec(
                key, "RC 吸收 %s+%s" % (fmt_r(rv), fmt_c(cv)),
                "滤波去耦", "snubber",
                "开关节点串 %s 并 %s 到地，阻尼振铃。"
                % (fmt_r(rv), fmt_c(cv)),
                [["电阻", fmt_r(rv)], ["电容", fmt_c(cv)]],
                lambda b, rv=rv, cv=cv: _snubber(b, rv, cv),
                pitch=12.7)


def _lc_lp(b, rail, lv, cv):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("IN", "OUT")
    b.series("L1", "Device:L", lv, L_FP["1210"], "IN", "OUT")
    b.shunt("C1", "Device:C_Polarized", fmt_c(cv), CP_FP["ELEC5"],
            "OUT", "GND")
    b.header("J1", "IN", "left", "IN")
    b.header("J2", "OUT", "right", "OUT")


@family
def f_lc_filter():
    """LC 低通：大电流电源入口。"""
    for rail in ("+5V", "+9V", "+12V", "+24V"):
        for lv in ("10uH", "47uH", "100uH", "220uH"):
            for cv in (22e-6, 100e-6, 220e-6):
                key = "lc_%s_%s_%s" % (tok(rail), tok(lv), tok(fmt_c(cv)))
                yield spec(
                    key, "%s LC 滤波 %s" % (rname(rail), lv),
                    "滤波去耦", "lc_filter",
                    "%s 串 %s 电感、%s 电解落地，转角约 %.0fkHz。"
                    % (rname(rail), lv, fmt_c(cv),
                       1 / (2 * math.pi * math.sqrt(
                           float(lv[:-2]) * 1e-6 * cv))),
                    [["电源", rname(rail)], ["电感", lv],
                     ["电容", fmt_c(cv)]],
                    lambda b, rail=rail, lv=lv, cv=cv: _lc_lp(b, rail, lv, cv),
                    pitch=15.24)


def _capnet(b, rail, cv):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("L1", "L2", "L3", "L4")
    for i, net in enumerate(("L1", "L2", "L3", "L4")):
        b.shunt("C%d" % (i + 1), "Device:C", fmt_c(cv), C_FP["0603"],
                net, "GND")
        b.header("J%d" % (i + 1), net, "right", net)


@family
def f_cap_array():
    """四线去耦电容组：每条线就近落地一只。"""
    for rail in ("+3V3", "+5V"):
        for cv in (1e-9, 10e-9, 100e-9):
            key = "capbank_%s_%s" % (tok(rail), tok(fmt_c(cv)))
            yield spec(
                key, "四线去耦 %s" % fmt_c(cv),
                "滤波去耦", "cap_bank",
                "四条信号线各用 %s 落地，%s 只作参考轨。"
                % (fmt_c(cv), rname(rail)),
                [["每节", fmt_c(cv)], ["线数", "4"]],
                lambda b, rail=rail, cv=cv: _capnet(b, rail, cv),
                pitch=20.32, lane=20.32)


# ================================================================== 复位与时序
def _reset_rc(b, rail, rv, cv):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("RST")
    b.shunt("R1", "Device:R", fmt_r(rv), R_FP["0603"], "RST", rail)
    b.shunt("C1", "Device:C", fmt_c(cv), C_FP["0603"], "RST", "GND")
    b.shunt("SW1", "Switch:SW_Push", "RESET", SW_FP, "RST", "GND")
    b.header("J1", "RST", "left", "RESET")


@family
def f_reset_rc():
    """RC + 按键复位：上电自动拉低一段再释放。"""
    for rail in ("+1V8", "+2V5", "+3V3", "+5V", "+9V", "+12V"):
        for rv in (10000, 47000, 100000):
            for cv in (1e-6, 10e-6):
                t = rv * cv
                key = "rst_%s_%s_%s" % (tok(rail), fmt_r(rv), tok(fmt_c(cv)))
                yield spec(
                    key, "%s RC 复位（%.0fms）" % (rname(rail), t * 1000),
                    "复位与时序", "reset_rc",
                    "%s/%s 上电复位约 %.0fms，并联按键强制复位。"
                    % (fmt_r(rv), fmt_c(cv), t * 1000),
                    [["电源", rname(rail)], ["电阻", fmt_r(rv)],
                     ["电容", fmt_c(cv)], ["时长", "%.0fms" % (t * 1000)]],
                    lambda b, rail=rail, rv=rv, cv=cv:
                    _reset_rc(b, rail, rv, cv),
                    pitch=12.7)


def _button(b, rail, rv):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("KEY")
    b.shunt("R1", "Device:R", fmt_r(rv), R_FP["0603"], "KEY", rail)
    b.shunt("SW1", "Switch:SW_Push", "KEY", SW_FP, "KEY", "GND")
    b.header("J1", "KEY", "left", "KEY")


@family
def f_button_pullup():
    """按键 + 上拉：按下接地、松开为高。"""
    for rail in ANY_RAILS:
        for rv in (1000, 4700, 10000, 47000):
            key = "btn_%s_%s" % (tok(rail), fmt_r(rv))
            yield spec(
                key, "%s 按键输入（上拉 %s）" % (rname(rail), fmt_r(rv)),
                "复位与时序", "button_pullup",
                "%s 上拉 %s，按键按下时拉低，松开为高。"
                % (rname(rail), fmt_r(rv)),
                [["电源", rname(rail)], ["上拉", fmt_r(rv)]],
                lambda b, rail=rail, rv=rv: _button(b, rail, rv))


def _button_dn(b, rail, rv):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("KEY")
    b.shunt("R1", "Device:R", fmt_r(rv), R_FP["0603"], "KEY", "GND")
    b.shunt("SW1", "Switch:SW_Push", "KEY", SW_FP, "KEY", rail)
    b.header("J1", "KEY", "left", "KEY")


@family
def f_button_pull_down():
    """按键 + 下拉：按下给高，松开为低。"""
    for rail in ("+3V3", "+5V", "+12V", "+24V"):
        for rv in (1000, 4700, 10000, 47000):
            key = "btnd_%s_%s" % (tok(rail), fmt_r(rv))
            yield spec(
                key, "%s 按键输入（下拉 %s）" % (rname(rail), fmt_r(rv)),
                "复位与时序", "button_pull_down",
                "%s 下拉 %s，按键按下时输出高电平。"
                % (rname(rail), fmt_r(rv)),
                [["电源", rname(rail)], ["下拉", fmt_r(rv)]],
                lambda b, rail=rail, rv=rv: _button_dn(b, rail, rv))


def _debounce(b, rail, rv, cv):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("SW", "OUT")
    b.shunt("R1", "Device:R", fmt_r(rv), R_FP["0603"], "SW", rail)
    b.shunt("SW1", "Switch:SW_Push", "KEY", SW_FP, "SW", "GND")
    b.series("R2", "Device:R", "10k", R_FP["0603"], "SW", "OUT")
    b.shunt("C1", "Device:C", fmt_c(cv), C_FP["0603"], "OUT", "GND")
    b.header("J1", "OUT", "right", "KEY")


@family
def f_debounce():
    """按键消抖：RC 吸收抖动沿。"""
    for rail in ("+3V3", "+5V", "+12V"):
        for cv in (100e-9, 1e-6, 10e-6):
            key = "deb_%s_%s" % (tok(rail), tok(fmt_c(cv)))
            yield spec(
                key, "%s 按键消抖（%.0fms）" % (rname(rail), 10e3 * cv * 1000),
                "复位与时序", "debounce",
                "上拉 %s + 10k/%s 一阶低通，抖动沿被压平。"
                % (rname(rail), fmt_c(cv)),
                [["电源", rname(rail)], ["滤波", "10k+%s" % fmt_c(cv)]],
                lambda b, rail=rail, cv=cv: _debounce(b, rail, 10000, cv),
                pitch=15.24)


def _555_ast(b, rv1, rv2, cv, rail="+5V", dx=0.0):
    """NE555 无稳态：R1/R2/C 定时；左侧同列引脚用 hang 逐个挂出去。"""
    b.rail(rail, side="up")
    b.rail("GND")
    b.dev("U1", "Timer:NE555P", "NE555", DIP8,
          {"1": "GND", "2": "THR", "3": "OUT", "4": rail, "5": "CTR",
           "6": "THR", "7": "DIS", "8": rail}, col=None, rot=0, x_off=dx)
    b.hang("R1", "Device:R", fmt_r(rv1), R_FP["0805"], "U1", "7", rail,
           below=False, dx=-7.62, gap=5.08)
    b.hang("R2", "Device:R", fmt_r(rv2), R_FP["0805"], "U1", "7", "THR",
           below=True, dx=-7.62, gap=3.81)
    b.hang("C1", "Device:C", fmt_c(cv), C_FP["0805"], "R2", "2", "GND",
           below=True, gap=2.54)
    b.hang("C2", "Device:C", "10n", C_FP["0402"], "U1", "5", "GND",
           below=True, dx=-8.89)
    b.header("J1", "OUT", "right", "方波输出")


@family
def f_555_astable():
    """555 无稳态：R/C 网格决定振荡频率。"""
    for rv1 in (1000, 10000, 100000):
        for rv2 in (10000, 100000, 1000000):
            for cv in (1e-9, 10e-9, 100e-9, 1e-6):
                f = 1.44 / ((rv1 + 2 * rv2) * cv)
                key = "555a_%s_%s_%s" % (fmt_r(rv1), fmt_r(rv2),
                                          tok(fmt_c(cv)))
                yield solved(
                    key, "555 振荡 %.3gHz" % f,
                    "复位与时序", "555_astable",
                    "NE555 无稳态，R1=%s R2=%s C=%s → %.3gHz，占空比 %.0f%%。"
                    % (fmt_r(rv1), fmt_r(rv2), fmt_c(cv), f,
                       100.0 * (rv1 + rv2) / (rv1 + 2 * rv2)),
                    [["R1", fmt_r(rv1)], ["R2", fmt_r(rv2)],
                     ["C", fmt_c(cv)], ["频率", "%.3gHz" % f]],
                    lambda b, rv1=rv1, rv2=rv2, cv=cv, **p:
                    _555_ast(b, rv1, rv2, cv, dx=p.get("dx", 0.0)),
                    combos(dx=[0.0, 2.54, -2.54, 5.08],
                           blk=[{"pitch": 15.24, "lane": 25.4,
                                 "lane_step": 7.62},
                                {"pitch": 15.24, "lane": 27.94,
                                 "lane_step": 7.62},
                                {"pitch": 15.24, "lane": 22.86,
                                 "lane_step": 7.62}]))


_ALIAS = ("VBUS", "+BATT", "VCC", "VDD", "VAA")
RAILS_POS = [k for k in sorted(VOLT, key=lambda k: VOLT[k])
             if VOLT[k] > 0 and k not in _ALIAS]
RAILS_NEG = [k for k in sorted(VOLT, key=lambda k: VOLT[k])
             if VOLT[k] < 0 and k not in _ALIAS]

# ================================================================== 电源变换
LDO_IN = ("VI", "VIN", "IN", "V_{IN}")
LDO_OUT = ("VO", "VOUT", "OUT", "V_{OUT}")
LDO_GND = ("GND", "V_{SS}", "VSS")
LDO_EN = ("EN", "CE", "ON/~{OFF}", "~{ON}/OFF", "~{SHDN}", "SHDN")
LDO_BP = ("BP",)

# (器件, 输出轨, 压差, 最大输入)：压差筛掉压不够的输入，最大输入筛掉会烧的输入
LDO_FIX = [
    ("Regulator_Linear:AMS1117-1.5", "+1V5", 1.1, 18.0),
    ("Regulator_Linear:AMS1117-1.8", "+1V8", 1.1, 18.0),
    ("Regulator_Linear:AMS1117-2.5", "+2V5", 1.1, 18.0),
    ("Regulator_Linear:AMS1117-3.3", "+3V3", 1.1, 18.0),
    ("Regulator_Linear:AMS1117-5.0", "+5V", 1.1, 18.0),
    ("Regulator_Linear:LP2985-1.8", "+1V8", 0.3, 16.0),
    ("Regulator_Linear:LP2985-2.5", "+2V5", 0.3, 16.0),
    ("Regulator_Linear:LP2985-3.0", "+3V0", 0.3, 16.0),
    ("Regulator_Linear:LP2985-3.3", "+3V3", 0.3, 16.0),
    ("Regulator_Linear:LP2985-5.0", "+5V", 0.3, 16.0),
    ("Regulator_Linear:ME6211C12M5", "+1V2", 0.25, 12.0),
    ("Regulator_Linear:ME6211C15M5", "+1V5", 0.25, 12.0),
    ("Regulator_Linear:ME6211C18M5", "+1V8", 0.25, 12.0),
    ("Regulator_Linear:ME6211C25M5", "+2V5", 0.25, 12.0),
    ("Regulator_Linear:ME6211C30M5", "+3V0", 0.25, 12.0),
    ("Regulator_Linear:ME6211C33M5", "+3V3", 0.25, 12.0),
    ("Regulator_Linear:ME6211C50M5", "+5V", 0.25, 12.0),
    ("Regulator_Linear:SPX3819M5-L-1-2", "+1V2", 0.3, 6.0),
    ("Regulator_Linear:SPX3819M5-L-1-5", "+1V5", 0.3, 6.0),
    ("Regulator_Linear:SPX3819M5-L-1-8", "+1V8", 0.3, 6.0),
    ("Regulator_Linear:SPX3819M5-L-2-5", "+2V5", 0.3, 6.0),
    ("Regulator_Linear:SPX3819M5-L-3-0", "+3V0", 0.3, 6.0),
    ("Regulator_Linear:SPX3819M5-L-3-3", "+3V3", 0.3, 6.0),
    ("Regulator_Linear:SPX3819M5-L-5-0", "+5V", 0.3, 6.0),
    ("Regulator_Linear:LP5907MFX-1.2", "+1V2", 0.14, 5.5),
    ("Regulator_Linear:LP5907MFX-1.8", "+1V8", 0.14, 5.5),
    ("Regulator_Linear:LP5907MFX-2.5", "+2V5", 0.14, 5.5),
    ("Regulator_Linear:LP5907MFX-3.3", "+3V3", 0.14, 5.5),
    ("Regulator_Linear:TPS7A0512PDBV", "+1V2", 0.2, 3.9),
    ("Regulator_Linear:TPS7A0518PDBV", "+1V8", 0.2, 3.9),
    ("Regulator_Linear:TPS7A0525PDBV", "+2V5", 0.2, 3.9),
]
XC6206_CODES = (("122", "+1V2"), ("182", "+1V8"), ("252", "+2V5"),
                ("302", "+3V0"), ("332", "+3V3"), ("502", "+5V"))
LDO_INPUTS = ("+3V3", "+3V8", "+5V", "VBUS", "+6V", "+9V", "+12V", "+15V",
              "+BATT", "+24V")
LDO_CAPS = (("10u", "10u", "0805"), ("22u", "10u", "0805"),
            ("1u", "1u", "0603"))
ADJ_R1 = (240, 1000)
LDO_ADJ = [("Regulator_Linear:LM317_SOT-223", "LM317", 1.25, 40.0),
           ("Regulator_Linear:LM317L_SO8", "LM317L", 1.25, 40.0),
           ("Regulator_Linear:LM1117DT-ADJ", "LM1117-ADJ", 1.194, 15.0),
           ("Regulator_Linear:AMS1117", "AMS1117-ADJ", 1.25, 15.0)]


def rail_choices(vout, vhead, vin_max, inputs, limit=2):
    """能用的输入轨：压差够、且不超器件最大输入。同电压只留一个，取最低的 limit 个。"""
    out = []
    used = set()
    for r in sorted(inputs, key=lambda t: VOLT[t]):
        if VOLT[r] in used or VOLT[r] > vin_max or VOLT[r] - vout < vhead:
            continue
        used.add(VOLT[r])
        out.append(r)
        if len(out) >= limit:
            break
    return out


def pin_span(lib_id, a_name, b_name, rot=0):
    """两个引脚名之间的水平跨距 —— 决定器件占几列、x_off 取多少。"""
    names = pin_names(lib_id)
    g = E.lib_pins(lib_id, rot)
    return round(abs(g[names[a_name][0]][0] - g[names[b_name][0]][0]), 2)


def _ldo_fix(b, lib, val, in_rail, out_rail, cin, cout, pkg, dx=0.0):
    """固定输出 LDO：输出轨走内道、输入轨走外道，滤波电容直接挂在引脚下面。"""
    b.rail(out_rail, side="up")
    b.rail(in_rail, side="up")
    b.rail("GND")
    nin, nout = pick(lib, LDO_IN), pick(lib, LDO_OUT)
    pairs = [(nin, in_rail), (nout, out_rail),
             (pick(lib, LDO_GND), "GND")]
    en = pick(lib, LDO_EN)
    if en:
        pairs.append((en, in_rail))
    bp = pick(lib, LDO_BP)
    if bp:
        pairs.append((bp, "BP"))
    span = pin_span(lib, nin, nout)
    place(b, "U1", lib, val, *pairs, x_off=span / 2.0 + dx)
    nums = pin_names(lib)
    b.hang("C1", "Device:C", cin, C_FP[pkg], "U1", nums[nin][0], "GND",
           gap=3.81, dx=-2.54)
    b.hang("C2", "Device:C", cout, C_FP[pkg], "U1", nums[nout][0], "GND",
           gap=3.81, dx=2.54)
    if bp:
        b.hang("C3", "Device:C", "10n", C_FP["0402"], "U1", nums[bp][0],
               "GND", gap=5.08, dx=7.62)


@family
def f_ldo_fixed():
    """固定输出 LDO：型号 × 输入轨 × 输入输出电容。"""
    parts = [(lib, lib.split(":")[1], out, do, vin)
             for lib, out, do, vin in LDO_FIX]
    parts += [("Regulator_Linear:XC6206PxxxMR", "XC6206P%sMR" % c, out,
               0.25, 10.0) for c, out in XC6206_CODES]
    for lib, val, out_rail, dropout, vin_max in parts:
        vout = VOLT[out_rail]
        for in_rail in rail_choices(vout, dropout + 0.4, vin_max, LDO_INPUTS):
            for cin, cout, pkg in LDO_CAPS:
                key = "ldo_%s_%s_%s" % (tok(val), tok(in_rail), tok(out_rail))
                if cin != "10u":
                    key += "_" + tok(cin)
                yield solved(
                    key, "%s→%s LDO（%s）" % (rname(in_rail), rname(out_rail),
                                            val),
                    "电源变换", "ldo_fix",
                    "%s 输入经 %s 稳压到 %s，压差 %.1fV，输入输出各 %s/%s。"
                    % (rname(in_rail), val, rname(out_rail),
                       VOLT[in_rail] - vout, cin, cout),
                    [["器件", val], ["输入", rname(in_rail)],
                     ["输出", rname(out_rail)], ["滤波", "%s/%s" % (cin, cout)],
                     ["封装", pkg]],
                    lambda b, lib=lib, val=val, in_rail=in_rail,
                    out_rail=out_rail, cin=cin, cout=cout, pkg=pkg, **p:
                    _ldo_fix(b, lib, val, in_rail, out_rail, cin, cout,
                             pkg, **p),
                    combos(dx=[0.0, 1.27, -1.27, 2.54, -2.54],
                           blk=[{"pitch": 15.24, "lane": 17.78,
                                 "lane_step": 6.35},
                                {"pitch": 17.78, "lane": 20.32,
                                 "lane_step": 7.62},
                                {"pitch": 20.32, "lane": 22.86,
                                 "lane_step": 7.62}]))


def _ldo_adj(b, lib, val, in_rail, out_rail, r1, r2, dx=0.0):
    """可调 LDO：R1 跨在 OUT-ADJ 之间定基准电流，R2 从 ADJ 落地分压。"""
    b.rail(out_rail, side="up")
    b.rail(in_rail, side="up")
    b.rail("GND")
    nin, nout = pick(lib, LDO_IN), pick(lib, LDO_OUT)
    span = pin_span(lib, nin, nout)
    place(b, "U1", lib, val,
          (nin, in_rail), (nout, out_rail), ("ADJ", "ADJ"),
          x_off=span / 2.0 + dx)
    nums = pin_names(lib)
    adj = nums["ADJ"][0]
    b.hang("C1", "Device:C", "10u", C_FP["0805"], "U1", nums[nin][0], "GND",
           gap=3.81, dx=-2.54)
    b.hang("C2", "Device:C", "10u", C_FP["0805"], "U1", nums[nout][0], "GND",
           gap=3.81, dx=2.54)
    b.lead("R1", "Device:R", fmt_r(r1), R_FP["0805"], "U1", adj, out_rail,
           right=True, gap=7.62)
    b.hang("R2", "Device:R", fmt_r(r2), R_FP["0805"], "U1", adj, "GND",
           gap=7.62)


@family
def f_ldo_adj():
    """可调 LDO：按目标输出反算下偏电阻，输出贴到最近的电源轨。"""
    for lib, base, vref, vin_max in LDO_ADJ:
        for out_rail in RAILS_POS:
            vout = VOLT[out_rail]
            if vout < vref + 0.2 or vout > 25.0:
                continue
            for r1 in ADJ_R1:
                r2 = nearest_e24(r1 * (vout / vref - 1.0))
                real = vref * (1.0 + r2 / float(r1))
                for in_rail in rail_choices(vout, 2.0, vin_max, LDO_INPUTS,
                                            limit=1):
                    key = "ldoa_%s_%s_%s_%s" % (tok(base), tok(in_rail),
                                                tok(out_rail), tok(fmt_r(r1)))
                    yield solved(
                        key, "%s 可调输出 %s" % (base, rname(out_rail)),
                        "电源变换", "ldo_adj",
                        "%s 接 %s 输入，R1=%s R2=%s → 输出 %.2fV（标称 %s）。"
                        % (base, rname(in_rail), fmt_r(r1), fmt_r(r2),
                           real, rname(out_rail)),
                        [["器件", base], ["输入", rname(in_rail)],
                         ["输出", rname(out_rail)], ["R1", fmt_r(r1)],
                         ["R2", fmt_r(r2)]],
                        lambda b, lib=lib, base=base, in_rail=in_rail,
                        out_rail=out_rail, r1=r1, r2=r2, **p:
                        _ldo_adj(b, lib, base, in_rail, out_rail, r1, r2, **p),
                        combos(dx=[0.0, 1.27, -1.27, 2.54],
                               blk=[{"pitch": 15.24, "lane": 20.32,
                                     "lane_step": 7.62},
                                    {"pitch": 17.78, "lane": 22.86,
                                     "lane_step": 7.62}]))


# (器件, 型号, 反馈基准电压, 最大输入)
BUCK_ADJ = [("Regulator_Switching:LM2576T-ADJ", "LM2576T-ADJ", 1.23, 40.0),
            ("Regulator_Switching:LM2576S-ADJ", "LM2576S-ADJ", 1.23, 40.0),
            ("Regulator_Switching:LM2596S-ADJ", "LM2596S-ADJ", 1.23, 40.0),
            ("Regulator_Switching:LM2596T-ADJ", "LM2596T-ADJ", 1.23, 60.0)]
BUCK_INPUTS = ("+9V", "+12V", "+15V", "+BATT", "+24V", "+28V", "+36V", "+48V")
BUCK_L = (33e-6, 68e-6)


def _buck_adj(b, lib, val, in_rail, out_rail, r1, r2, lval, cin, cout, dx=0.0):
    """降压模块：开关节点 → 电感 → 输出，续流二极管挂回地，FB 由分压取。"""
    b.rail(out_rail, side="up")
    b.rail(in_rail, side="up")
    b.rail("GND")
    b.cols(in_rail, "SW", out_rail, "FB")
    place(b, "U1", lib, val, ("VIN", in_rail), ("OUT", "SW"), ("GND", "GND"),
          ("FB", "FB"), ("~{ON}/OFF", in_rail), x_off=12.7 + dx,
          fp=fp_of(lib, TO220))
    b.hang("C1", "Device:C", cin, C_FP["0805"], "U1", "1", "GND",
           gap=3.81, dx=-2.54)
    b.shunt("D1", "Diode:SS34", "SS34", "Diode_SMD:D_SMA", "SW", "GND", col=1)
    b.series("L1", "Device:L", fmt_l(lval), L_FP["1210"], "SW", out_rail)
    b.shunt("C2", "Device:C", cout, C_FP["0805"], out_rail, "GND", col=2)
    b.series("R1", "Device:R", fmt_r(r1), R_FP["0805"], out_rail, "FB")
    b.shunt("R2", "Device:R", fmt_r(r2), R_FP["0805"], "FB", "GND", col=3)


@family
def f_buck_adj():
    """LM2576/LM2596 降压：输出电压由 FB 分压决定，电感按输出电压档取。"""
    for lib, val, vref, vin_max in BUCK_ADJ:
        for out_rail in ("+1V8", "+2V5", "+3V3", "+5V", "+9V", "+12V", "+15V"):
            vout = VOLT[out_rail]
            r1 = 1000
            r2 = nearest_e24(r1 * (vout / vref - 1.0))
            real = vref * (1.0 + r2 / float(r1))
            lv = BUCK_L[0] if vout <= 5 else BUCK_L[1]
            for in_rail in rail_choices(vout, 3.0, vin_max, BUCK_INPUTS,
                                        limit=2):
                key = "buck_%s_%s_%s" % (tok(val), tok(in_rail),
                                         tok(out_rail))
                yield solved(
                    key, "%s 降压 %s→%s" % (val, rname(in_rail),
                                           rname(out_rail)),
                    "电源变换", "buck_adj",
                    "%s 降压：%s 输入 → %.2fV 输出（标称 %s），"
                    "FB 分压 %s/%s，续流 SS34，电感 %s。"
                    % (val, rname(in_rail), real, rname(out_rail),
                       fmt_r(r1), fmt_r(r2), fmt_l(lv)),
                    [["器件", val], ["输入", rname(in_rail)],
                     ["输出", rname(out_rail)], ["分压", "%s/%s"
                                                 % (fmt_r(r1), fmt_r(r2))],
                     ["电感", fmt_l(lv)]],
                    lambda b, lib=lib, val=val, in_rail=in_rail,
                    out_rail=out_rail, r1=r1, r2=r2, lv=lv, **p:
                    _buck_adj(b, lib, val, in_rail, out_rail, r1, r2, lv,
                              "100u", "100u", **p),
                    combos(dx=[0.0, 2.54, -2.54],
                           blk=[{"pitch": 25.4, "lane": 17.78,
                                 "lane_step": 6.35},
                                {"pitch": 25.4, "lane": 20.32,
                                 "lane_step": 7.62},
                                {"pitch": 20.32, "lane": 22.86,
                                 "lane_step": 7.62}]))


# ================================================================== 开关驱动
BASE_PINS = ("B", "G")
EMIT_PINS = ("E", "S")
DRN_PINS = ("C", "D")

# 负载件：ln/hn 是低边、高边挂法里靠开关这一侧的脚号（电流方向相反，
# 极性件要掉头），r=True 表示外侧还要串一只限流电阻。
SW_LOADS = {
    "led": dict(lib="Device:LED", ref="D1", fp=LED_FP["0603"],
                ln=("1", "2"), hn=("2", "1"), r=True, cn="LED 指示灯"),
    "r": dict(lib="Device:R", ref="RL1", fp=R_FP["1206"],
              ln=("1", "2"), hn=("1", "2"), r=False, cn="阻性负载"),
    "motor": dict(lib="Motor:Motor_DC", ref="M1", fp="",
                  ln=("2", "1"), hn=("1", "2"), r=False, cn="直流电机"),
    "buzzer": dict(lib="Device:Buzzer", ref="BZ1", fp=BZ_FP,
                   ln=("2", "1"), hn=("1", "2"), r=False, cn="有源蜂鸣器"),
    "lamp": dict(lib="Device:Lamp", ref="L1", fp=LED_FP["THT"],
                 ln=("1", "2"), hn=("1", "2"), r=False, cn="灯泡"),
}


def _load_chain(b, anchor, cpin, rail, load, lval, rv, rpkg, hi):
    """把负载竖挂在开关引脚上：低边朝上挂、回正轨；高边朝下挂、回 GND。

    高边开关的源(射)极已经占了正轨，负载再回正轨就等于同一根轨接在
    器件两端，竖线必然压到开关自己的引脚，所以另一端必须往下回地。
    """
    s = SW_LOADS[load]
    near, far = (s["hn"] if hi else s["ln"])
    fp = s["fp"] or fp_of(s["lib"])
    ret = "GND" if hi else rail
    b.hang(s["ref"], s["lib"], lval, fp, anchor, cpin,
           "MID" if s["r"] else ret,
           pin_near=near, pin_far=far, below=hi, gap=2.54)
    if s["r"]:
        b.hang("R2", "Device:R", fmt_r(rv), R_FP[rpkg], s["ref"], far, ret,
               below=hi, gap=7.62)


def _switch(b, lib, val, fp, rail, rb, load, lval, rv, rpkg, pd, hi, dx=0.0):
    b.rail(rail, side="up")
    b.rail("GND")
    bn, en, cn = pick(lib, BASE_PINS), pick(lib, EMIT_PINS), pick(lib, DRN_PINS)
    nums = pin_names(lib)
    if hi:
        # 高边：栅/基极在行线上，源(射)极朝上接轨、漏(集)极朝下挂负载
        b.cols("DRV", "IN")
        b.series("R1", "Device:R", fmt_r(rb), R_FP["0603"], "DRV", "IN")
        place(b, "Q1", lib, val, (bn, "DRV"), (en, rail), (cn, "LOAD"),
              col="DRV", rot=180, x_off=-5.08 + dx, fp=fp)
        b.shunt("R3", "Device:R", fmt_r(pd), R_FP["0603"], "DRV", rail)
        _load_chain(b, "Q1", nums[cn][0], rail, load, lval, rv, rpkg, True)
        b.header("J1", "IN", "right", "控制")
    else:
        b.cols("IN", "DRV")
        b.series("R1", "Device:R", fmt_r(rb), R_FP["0603"], "IN", "DRV")
        place(b, "Q1", lib, val, (bn, "DRV"), (en, "GND"), (cn, "LOAD"),
              col="DRV", rot=0, x_off=5.08 + dx, fp=fp)
        if pd:
            b.shunt("R3", "Device:R", fmt_r(pd), R_FP["0603"], "DRV", "GND")
        _load_chain(b, "Q1", nums[cn][0], rail, load, lval, rv, rpkg, False)
        b.header("J1", "IN", "left", "控制")


SW_BLK = [{"pitch": 7.62, "lane": 17.78},
          {"pitch": 7.62, "lane": 20.32},
          {"pitch": 10.16, "lane": 20.32, "lane_step": 6.35},
          {"pitch": 7.62, "lane": 25.40}]

SW_LO = [
    # (器件, 标称, 封装兜底, 可用轨, 驱动电阻, 下拉值(0=不加), 类型)
    ("Transistor_BJT:MMBT3904", "MMBT3904", SOT23,
     ("+3V3", "+5V", "+12V", "+24V"), (1000, 10000), (0, 10000), "NPN"),
    ("Transistor_BJT:2N3904", "2N3904", TO92,
     ("+5V", "+12V", "+24V"), (1000, 10000), (0,), "NPN"),
    ("Transistor_BJT:SS8050", "SS8050", SOT23,
     ("+5V", "+12V", "+24V"), (470, 1000, 10000), (0,), "NPN"),
    ("Transistor_BJT:TIP120", "TIP120", TO220,
     ("+12V", "+24V", "+48V"), (1000,), (10000,), "NPN 达林顿"),
    ("Transistor_FET:2N7002", "2N7002", SOT23,
     ("+3V3", "+5V", "+12V", "+24V"), (100, 1000), (100000,), "N-MOS"),
    ("Transistor_FET:BSS138", "BSS138", SOT23,
     ("+3V3", "+5V", "+12V"), (100, 1000), (100000,), "N-MOS"),
    ("Transistor_FET:IRF540N", "IRF540N", TO220,
     ("+12V", "+24V", "+48V"), (10, 47), (100000,), "N-MOS 功率"),
]

SW_HI = [
    ("Transistor_BJT:MMBT3906", "MMBT3906", SOT23,
     ("+3V3", "+5V", "+12V", "+24V"), (1000, 10000), (10000,), "PNP"),
    ("Transistor_BJT:SS8550", "SS8550", SOT23,
     ("+5V", "+12V", "+24V"), (470, 1000, 10000), (10000,), "PNP"),
    ("Transistor_FET:AO3401A", "AO3401A", SOT23,
     ("+3V3", "+5V", "+12V", "+24V"), (100, 1000), (100000,), "P-MOS"),
    ("Transistor_FET:IRF9540N", "IRF9540N", TO220,
     ("+12V", "+24V", "+48V"), (10, 47), (100000,), "P-MOS 功率"),
]


def _loads_for(rail):
    """这条轨上能挂的负载：[(种类, 显示值, 限流电阻, 限流封装)]。"""
    out = []
    rpkg = "0805" if VOLT[rail] >= 9 else "0603"
    for color in ("红", "绿", "蓝"):
        dv = VOLT[rail] - LED_V[color]
        if dv >= 0.6:
            out.append(("led", "LED_" + LED_EN[color],
                        nearest_e24(dv / 0.01), rpkg))
    out.append(("r", fmt_r(1000), 0, "1206"))
    out.append(("motor", "直流电机", 0, "0805"))
    out.append(("buzzer", "有源蜂鸣器", 0, "0805"))
    out.append(("lamp", "灯泡", 0, "0805"))
    return out


def _switch_presets(table, hi):
    """把开关表摊成预设：低边 swl_ / 高边 swh_。"""
    out = []
    for lib, val, fp, rails, rbs, pds, kind in table:
        drive = "栅极" if "MOS" in kind else "基极"
        for rail in rails:
            for rb in rbs:
                for pd in pds:
                    for load, lval, rv, rpkg in _loads_for(rail):
                        bias = ("%s%s %s，默认关断"
                                % (drive, "下拉" if not hi else "上拉",
                                   fmt_r(pd)) if pd
                                else "%s不加偏置电阻" % drive)
                        tail = ("，外侧串 %s 限流约 10mA" % fmt_r(rv)
                                if load == "led" else "")
                        loop = rname(rail) if not hi else "地"
                        key = "sw%s_%s_%s_%s_%s%s%s" % (
                            "l" if not hi else "h", tok(val), tok(rail),
                            load, tok(fmt_r(rb)),
                            ("_" + lval.split("_")[1]) if load == "led" else "",
                            "_pd" if pd else "")
                        params = [["器件", val], ["类型", kind],
                                  ["电源", rname(rail)],
                                  ["负载", SW_LOADS[load]["cn"]],
                                  ["驱动电阻", fmt_r(rb)],
                                  ["偏置", fmt_r(pd) if pd else "无"]]
                        if load == "led":
                            params.append(["限流", fmt_r(rv)])
                        out.append(solved(
                            key, "%s开关 %s（%s / %s）"
                            % ("低边" if not hi else "高边", val,
                               rname(rail), SW_LOADS[load]["cn"]),
                            "开关驱动",
                            "switch_low" if not hi else "switch_high",
                            "%s 经 %s 到 %s：%s 串在 %s 与开关之间%s。"
                            "控制端串 %s，%s。"
                            % (rname(rail), val, SW_LOADS[load]["cn"],
                               SW_LOADS[load]["cn"], loop, tail,
                               fmt_r(rb), bias),
                            params,
                            lambda b, lib=lib, val=val, fp=fp, rail=rail,
                            rb=rb, load=load, lval=lval, rv=rv, rpkg=rpkg,
                            pd=pd, hi=hi, dx=0.0:
                            _switch(b, lib, val, fp, rail, rb, load, lval,
                                    rv, rpkg, pd, hi, dx),
                            combos(dx=[0.0, 1.27, -1.27], blk=SW_BLK)))
    return out


@family
def f_switch_low():
    """低边开关：控制脚经驱动电阻打 NPN/N-MOS，负载夹在正电源与集电极之间。"""
    return _switch_presets(SW_LO, False)


@family
def f_switch_high():
    """高边开关：控制脚拉低 PNP/P-MOS，负载夹在电源与发射极之间。"""
    return _switch_presets(SW_HI, True)


# ================================================================== 时钟谐振
XTALS = [("32.768kHz", 12e-12), ("1MHz", 18e-12), ("4MHz", 18e-12),
         ("6MHz", 20e-12), ("8MHz", 18e-12), ("10MHz", 20e-12),
         ("12MHz", 20e-12), ("16MHz", 18e-12), ("20MHz", 15e-12),
         ("24MHz", 15e-12), ("25MHz", 15e-12), ("27MHz", 15e-12)]
XTAL_STRAY = (3e-12, 5e-12)


def _xtal(b, f, cv1, cv2):
    b.rail("GND")
    b.cols("X1", "X2")
    b.series("Y1", "Device:Crystal", f, fp_of("Device:Crystal", XTAL_FP),
             "X1", "X2")
    b.shunt("C1", "Device:C", cv1, C_FP["0603"], "X1", "GND")
    b.shunt("C2", "Device:C", cv2, C_FP["0603"], "X2", "GND")
    b.header("J1", "X1", "left", "XTAL1")
    b.header("J2", "X2", "right", "XTAL2")


@family
def f_crystal():
    """无源晶振 + 两只负载电容：C = 2×(CL − 杂散)，杂散按 3~5pF 估。"""
    for f, cl in XTALS:
        for cs in XTAL_STRAY:
            cv = fmt_c(nearest_e24(2 * (cl - cs)))
            key = "xtal_%s_%s" % (tok(f), tok(cv))
            yield spec(
                key, "晶振 %s（CL %s）" % (f, fmt_c(cl)),
                "时钟谐振", "crystal",
                "%s 晶振两端各对地接 %s，按 CL=%s、杂散 %s 算出；"
                "外壳接地，走线尽量短。"
                % (f, cv, fmt_c(cl), fmt_c(cs)),
                [["频率", f], ["CL", fmt_c(cl)], ["负载电容", cv]],
                lambda b, f=f, cv=cv: _xtal(b, f, cv, cv),
                pitch=15.24, lane=12.70)


def _resonator(b, f):
    b.rail("GND")
    b.cols("X1", "X2")
    place(b, "Y1", "Device:Resonator", f, ("1", "X1"), ("2", "GND"),
          ("3", "X2"), col="X1", rot=0, x_off=3.81,
          fp=fp_of("Device:Resonator", XTAL_FP))
    b.header("J1", "X1", "left", "XTAL1")
    b.header("J2", "X2", "right", "XTAL2")


@family
def f_resonator():
    """陶瓷谐振器（内置负载电容）：三脚，中间那脚接地。"""
    for f in ("2MHz", "4MHz", "8MHz", "10MHz", "11.0592MHz", "12MHz",
              "16MHz", "20MHz"):
        key = "res_%s" % tok(f)
        yield spec(
            key, "陶瓷谐振器 %s" % f,
            "时钟谐振", "resonator",
            "%s 三脚陶瓷谐振器，内置负载电容，直接接 MCU 的 XTAL1/XTAL2，"
            "中间脚接地。比晶振省两件、精度差一些。" % f,
            [["频率", f], ["负载", "内置"]],
            lambda b, f=f: _resonator(b, f),
            pitch=7.62, lane=12.70)


# ================================================================== 基准电压
VREF_TL431 = 2.495
TL431_LIBS = [("Reference_Voltage:TL431DBZ", "TL431BZ", SOT23),
              ("Reference_Voltage:TL431D", "TL431D", SO8)]


def _tl431(b, lib, val, fp, rail, rt, ra, rb):
    """阴极串电阻接轨；ra/rb 为 0 时 REF 直连阴极（输出固定 2.495V）。

    行线上同净的件只能靠列距分开：值文本统一落在器件下方 3.17mm，
    横向错 2.54 就会互相压字，所以阴极电阻与退耦电容各占一列。
    可调版把器件转 180°（K 脚占阴极列、A 脚在右、REF 朝下），分压链挂在
    阴极列正下方 —— REF 朝上时怎么走线都会压到 A 脚或阴极行线。
    链上两级之间留 7.62：上级的值文本占了下引脚下方 3.17mm，
    间距再小就会被下级的标号压住。
    """
    b.rail(rail, side="up")
    b.rail("GND")
    nums = pin_names(lib)
    if ra:
        b.cols("CAP", "RT", "K")
        place(b, "U1", lib, val, ("K", "K"), ("A", "GND"), ("REF", "MID"),
              col="K", rot=180, x_off=2.54, fp=fp)
        b.shunt("R1", "Device:R", fmt_r(rt), R_FP["0805"], "K", rail, col=1)
        b.shunt("C1", "Device:C", "100n", C_FP["0603"], "K", "GND", col=0)
        b.hang("R2", "Device:R", fmt_r(ra), R_FP["0805"],
               "U1", nums["K"][0], "MID", gap=7.62, dx=-10.16)
        b.hang("R3", "Device:R", fmt_r(rb), R_FP["0805"],
               "R2", "2", "GND", gap=7.62)
        b.header("J1", "K", "left", "VREF")
        return
    b.cols("K", "RT", "CAP")
    place(b, "U1", lib, val, ("K", "K"), ("A", "GND"), ("REF", "K"),
          col="K", rot=0, x_off=-2.54, fp=fp)
    b.shunt("R1", "Device:R", fmt_r(rt), R_FP["0805"], "K", rail, col=1)
    b.shunt("C1", "Device:C", "100n", C_FP["0603"], "K", "GND", col=2)
    b.header("J1", "K", "right", "VREF")


@family
def f_tl431():
    """TL431 可调基准：Vout = 2.495×(1+R上/R下)，阴极电阻按 5mA 估。"""
    for lib, val, fp in TL431_LIBS:
        for rail in ("+3V3", "+5V", "+9V", "+12V", "+15V", "+24V"):
            yield solved(
                "tl431_%s_fix_%s" % (tok(val), tok(rail)),
                "TL431 基准 2.5V（%s 供电）" % rname(rail),
                "基准电压", "tl431_fix",
                "%s 经 %s 接到 TL431 阴极，REF 与阴极短接 → 稳定在 2.495V，"
                "阴极电阻按约 5mA 工作电流选。" % (rname(rail), val),
                [["器件", val], ["供电", rname(rail)],
                 ["输出", "2.495V"], ["阴极电阻", "5mA"]],
                lambda b, lib=lib, val=val, fp=fp, rail=rail:
                _tl431(b, lib, val, fp, rail,
                       nearest_e24((VOLT[rail] - VREF_TL431) / 0.005), 0, 0),
                combos(blk=[{"pitch": 25.40, "lane": 17.78},
                            {"pitch": 20.32, "lane": 17.78},
                            {"pitch": 25.40, "lane": 15.24}]))
            for vout in (3.0, 3.3, 4.096, 5.0, 6.0, 8.0, 9.0, 10.0, 12.0):
                if VOLT[rail] - vout < 1.5 or vout <= VREF_TL431:
                    continue
                rb = 10000
                ra = nearest_e24(rb * (vout / VREF_TL431 - 1.0))
                rt = nearest_e24((VOLT[rail] - vout) / 0.005)
                yield solved(
                    "tl431_%s_%s_%s_%s" % (tok(val), tok(rail), fmt_r(vout),
                                           fmt_r(ra)),
                    "TL431 基准 %s（%s 供电）" % (fmt_u(vout), rname(rail)),
                    "基准电压", "tl431_adj",
                    "%s 经 %s 供电，TL431 配 %s/%s 分压把输出抬到 %s；"
                    "分压电阻取 10k 量级，比 cathode 电流大一个数量级以免拉偏。"
                    % (rname(rail), val, fmt_r(ra), fmt_r(rb), fmt_u(vout)),
                    [["器件", val], ["供电", rname(rail)],
                     ["输出", fmt_u(vout)], ["上分压", fmt_r(ra)],
                     ["下分压", fmt_r(rb)]],
                    lambda b, lib=lib, val=val, fp=fp, rail=rail, rt=rt,
                    ra=ra, rb=rb: _tl431(b, lib, val, fp, rail, rt, ra, rb),
                    combos(blk=[{"pitch": 25.40, "lane": 30.48},
                                {"pitch": 25.40, "lane": 25.40},
                                {"pitch": 20.32, "lane": 30.48}]))


# ================================================================== 电源保护
FUSE_TYPES = [("Device:Polyfuse", "自恢复保险丝", "Fuse:Fuse_1206_3216Metric"),
              ("Device:Fuse", "熔断保险丝",
               "Fuse:Fuseholder_Cylinder-5x20mm_Schurter_0031_8201_Horizontal_Open")]
FUSE_I = [(0.05, "50mA"), (0.1, "100mA"), (0.2, "200mA"), (0.5, "500mA"),
          (1.0, "1A"), (2.0, "2A"), (3.0, "3A"), (5.0, "5A")]
TVS_RAILS = [("+5V", "SMBJ5.0A"), ("+9V", "SMBJ9.0A"),
             ("+12V", "SMBJ12A"), ("+15V", "SMBJ15A"),
             ("+24V", "SMBJ24A"), ("+48V", "SMBJ48A")]


def _fuse_in(b, flib, fp, iv, rail, tvs):
    """输入口：保险丝串在火线上，TVS 钳位到地，后面挂一只储能电容。"""
    b.rail("GND")
    b.cols("IN", "OUT")
    b.series("F1", flib, iv, fp_of(flib, fp), "IN", "OUT")
    b.shunt("D1", "Device:D_TVS", tvs, D_FP["SMB"], "IN", "GND")
    b.shunt("C1", "Device:C", "22u", C_FP["1206"], "OUT", "GND")
    b.header("J1", "IN", "left", "电源输入")
    b.header("J2", "OUT", "right", "后级")


@family
def f_fuse_tvs():
    """电源入口保护：保险丝 + TVS 钳位 + 储能电容。"""
    for flib, fname, fp in FUSE_TYPES:
        for iv, ilabel in FUSE_I:
            for rail, tvs in TVS_RAILS:
                key = "fz_%s_%s_%s" % ("poly" if "Polyfuse" in flib else "fuse",
                                       tok(ilabel), tok(rail))
                yield spec(
                    key, "%s %s + TVS（%s）" % (fname, ilabel, rname(rail)),
                    "电源保护", "fuse_tvs",
                    "%s 输入经 %s %s 串到后级，前端 %s（%s）对地钳位，"
                    "后级 22u 储能；浪涌时 TVS 先击穿把保险丝打跳。"
                    % (rname(rail), fname, ilabel, tvs, rail),
                    [["输入", rname(rail)], ["保险丝", fname],
                     ["保持电流", ilabel], ["TVS", tvs]],
                    lambda b, flib=flib, fp=fp, ilabel=ilabel, rail=rail, tvs=tvs:
                    _fuse_in(b, flib, fp, ilabel, rail, tvs),
                    pitch=10.16, lane=15.24)


def _rev_mos(b, lib, val, fp, rail):
    """P-MOS 防反接：漏极接输入，源/栅极接输出，接反时 Vgs=0 自然关断。"""
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("OUT")
    place(b, "Q1", lib, val, ("D", rail), ("S", "OUT"), ("G", "OUT"),
          col="OUT", rot=0, x_off=5.08, fp=fp)
    b.shunt("C1", "Device:C", "10u", C_FP["1206"], "OUT", "GND")
    b.header("J1", "OUT", "right", "负载")


@family
def f_reverse_polarity():
    """防反接：P-MOS 理想二极管（压降小）与肖特基（简单）两种。"""
    for lib, val, fp in (("Transistor_FET:AO3401A", "AO3401A", SOT23),
                         ("Transistor_FET:IRF9540N", "IRF9540N", TO220)):
        for rail in ("VBUS", "+5V", "+9V", "+12V", "+24V", "+48V", "+BATT"):
            key = "rev_%s_%s" % (tok(val), tok(rail))
            yield solved(
                key, "%s 防反接（%s）" % (val, rname(rail)),
                "电源保护", "reverse_mos",
                "%s 作理想二极管：%s 接漏极，源极与栅极短接后接输出；"
                "极性正确时 Vgs<0 导通（压降只有几十 mV），接反时 Vgs=0 关断。"
                % (val, rname(rail)),
                [["器件", val], ["输入", rname(rail)],
                 ["方式", "P-MOS 理想二极管"]],
                lambda b, lib=lib, val=val, fp=fp, rail=rail:
                _rev_mos(b, lib, val, fp, rail),
                combos(
                       blk=[{"pitch": 10.16, "lane": 15.24},
                            {"pitch": 12.70, "lane": 15.24},
                            {"pitch": 10.16, "lane": 17.78}]))
    for rail in ("VBUS", "+5V", "+9V", "+12V", "+24V", "+BATT"):
        key = "rev_d_%s" % tok(rail)
        yield spec(
            key, "肖特基防反接（%s）" % rname(rail),
            "电源保护", "reverse_diode",
            "%s 串一只 %s 肖特基：正接压降约 0.4V，接反直接截止。"
            "结构简单、成本低，大电流时损耗比理想二极管大。"
            % (rname(rail), "SS34"),
            [["输入", rname(rail)], ["方式", "肖特基串联"],
             ["压降", "约 0.4V"]],
            lambda b, rail=rail: _rev_diode(b, rail),
            pitch=10.16, lane=15.24)


def _rev_diode(b, rail):
    b.rail(rail, side="up")
    b.rail("GND")
    b.cols("OUT")
    b.shunt("D1", "Diode:SS34", "SS34", D_FP["SMA"], "OUT", rail,
            pin_a="2", pin_b="1")
    b.shunt("C1", "Device:C", "10u", C_FP["1206"], "OUT", "GND")
    b.header("J1", "OUT", "right", "负载")


# 这一行必须在文件末尾：@family 是逐条注册的，提前摊就漏掉后面的家族。
# test/catalog_check.py 靠替换这一行来接管摊开的过程，别改写法。
CIRCUITS, CIRCUIT_LABELS, _TPL = collect()
