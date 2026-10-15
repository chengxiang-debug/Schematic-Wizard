# -*- coding: utf-8 -*-
"""通用布局 / 布线器：给一份网表，产出几何自洽的原理图和 PCB。

写这条路的理由：1000+ 预设不可能手写坐标。这里把电路压成
「一行信号 + 上下两条电源轨」，坐标由格点算出来，走线由路由器生成，
最后用引擎自己的 circuit_nets 反查图纸几何、和声明的网表逐脚比对，
不一致直接抛异常 —— 宁可生成失败，也不产出"看着像对"的电路。

拓扑铁律（和手写预设一致）：
  * 每根导线两端只允许落在引脚上，或落在同一网络的另一根线上（T 型/拐角）。
  * 不同网络的线不得共点、不得共线重叠 —— 由 circuit_nets 反查兜底。
  * 每个引脚都必须接线；每条电源轨配一个 PWR_FLAG，被真实 power_out 驱动的轨不配。
"""

import io
import math
import os
import re

import engine as E

GRID = 1.27
X0 = 88.90            # 第 0 号节点的 x
ROW_Y = 50.80         # 信号行的 y
PITCH = 10.16         # 相邻节点的横向间距
SLOT = 2.54           # 同一节点上第 2、3 个挂点的横向退让
LANE = 15.24          # 电源轨离信号行的纵向距离
LANE_STEP = 5.08      # 同侧多条轨的间距
PORT_GAP = 10.16      # 行端接线柱离行端的距离
CHAR_W = 1.05         # 1.27mm 字高下每个字的宽度（SVG 实量 0.97~1.11）
CHAR_H = 1.27         # 同一字号下文字带的宽度（横排是字高，竖排是字宽）
TEXT_TOL = 0.15       # 两段文字之间允许贴到多近
BODY_TOL = 0.70       # 文字压到本体包围盒的容忍量：本体按矩形算，角上是空白

_FP_LIB_RE = re.compile(r'\(pad "([^"]+)"')


class BuildError(Exception):
    """几何/网表自检不通过。"""


def snap(v):
    return round(round(v / GRID) * GRID, 2)


def lib_key(lib_id):
    nick, name = lib_id.split(":", 1)
    return (nick, name)


# ---------------------------------------------------------------- 文字包围盒
def _text_size(txt, rot):
    """一段 Reference/Value 的包围盒尺寸：横排是一行，竖排是一条窄带。"""
    w = CHAR_W * len(txt)
    return (CHAR_H, w) if rot in (90, 270) else (w, CHAR_H)


def _text_box(x, y, txt, rot):
    """KiCad 画在 (x, y) 的文字实际占到的范围。

    实测：横排（0°）从锚点往右长、180° 往左长，竖排 90° 往上、270° 往下，
    锚点始终在文字带的一端、另一个方向居中。
    """
    bw, bh = _text_size(txt, rot % 360)
    if rot % 360 == 180:
        return (x - bw, x, y - bh / 2.0, y + bh / 2.0)
    if rot % 360 == 90:
        return (x - bw / 2.0, x + bw / 2.0, y - bh, y)
    if rot % 360 == 270:
        return (x - bw / 2.0, x + bw / 2.0, y, y + bh)
    return (x, x + bw, y - bh / 2.0, y + bh / 2.0)


def _text_anchor(bx0, by0, txt, rot):
    """把「文字带左上角」换成 KiCad 要的锚点：不同朝向锚点落在带的不同角。"""
    bw, bh = _text_size(txt, rot % 360)
    if rot % 360 == 180:
        return (round(bx0 + bw, 2), round(by0 + bh / 2.0, 2))
    if rot % 360 == 90:
        return (round(bx0 + bw / 2.0, 2), round(by0 + bh, 2))
    if rot % 360 == 270:
        return (round(bx0 + bw / 2.0, 2), round(by0, 2))
    return (round(bx0, 2), round(by0 + bh / 2.0, 2))


def _clash(box, other, tol):
    ox = min(box[1], other[1]) - max(box[0], other[0])
    oy = min(box[3], other[3]) - max(box[2], other[2])
    return (ox - tol) * (oy - tol) if ox > tol and oy > tol else 0.0


def _place_label(txt, rot, minx, maxx, hi, lo, is_val, taken, bodies):
    """按候选位置依次试，挑第一个不压字也不压图形的；全压就挑最轻的一处。

    候选 = 本体左列 / 右列 × 本体上方 / 贴身上方 / 贴身下方 / 本体下方。
    标号先试上方、值先试下方，退路是顺着本体纵向贴身排（竖排件不占走廊）。
    """
    bw, bh = _text_size(txt, rot)
    ys_up = (hi - 1.27 - bh, hi - 1.27, lo + 1.27 - bh, lo + 1.27)
    ys_dn = (lo + 1.27, lo + 1.27 - bh, hi - 1.27, hi - 1.27 - bh)
    best, least = None, None
    for x0 in (minx - 2.54, maxx + 2.54):
        for y0 in (ys_dn if is_val else ys_up):
            box = (x0, x0 + bw, y0, y0 + bh)
            cost = sum(_clash(box, t, TEXT_TOL) for t in taken)
            cost += sum(_clash(box, b, BODY_TOL) for b in bodies)
            if cost <= 0.0:
                return _text_anchor(x0, y0, txt, rot)
            if least is None or cost < least:
                best, least = (x0, y0), cost
    return _text_anchor(best[0], best[1], txt, rot) if best else (minx, hi)


def layout_labels(symbols):
    """给一批符号实例的 Reference / Value 各挑一个不压字、不压图形的落点。

    KiCad 让实例文字跟着符号一起转：横放件的文字是一行，竖放件的是
    一条 1.27 宽的窄带，只能顺着本体纵向铺。串在一条链上的两级之间只有
    7.62 的走廊，硬按「标号在上、值在下」摆就会互相咬住，所以按候选试位置。
    先排完所有标号再排值：短标号先占住常规位置，长值文本自己让开。
    """
    geo = []
    for s in symbols:
        rot = s.get("rot", 0)
        g = E.symbol_geometry(s["lib_id"], s["x"], s["y"], rot)
        pts = [p for item in g["body"] for p in item["pts"]] or \
              [[s["x"], s["y"]]]
        geo.append((s, g, pts, rot))
    bodies = [(round(min(p[0] for p in pts), 2),
               round(max(p[0] for p in pts), 2),
               round(min(p[1] for p in pts), 2),
               round(max(p[1] for p in pts), 2)) for _s, _g, pts, _r in geo]
    frames, taken = [], []
    for s, g, pts, rot in geo:
        xs = [p[0] for p in pts] + [p["x"] for p in g["pins"]]
        ys = [p[1] for p in pts]
        minx, maxx = round(min(xs), 2), round(max(xs), 2)
        miny, maxy = round(min(ys), 2), round(max(ys), 2)
        if s["ref"].startswith("#"):
            # 电源符号：本体朝上（+V）标签就在上方，本体朝下（GND）在下方
            s["val_xy"] = ((minx, miny - 1.9) if maxy <= s["y"] + 1e-6
                           else (minx, maxy + 1.27))
            s["ref_xy"] = s["val_xy"]
            continue
        # 引脚号画在引脚端点外侧、水平居中于引脚，所以文字让开一列；
        # 上下边界取到引脚末端（AMS1117 的 GND 就在本体下方）。
        frames.append((s, minx, maxx, rot,
                       min([miny] + [p["y"] for p in g["pins"]]),
                       max([maxy] + [p["y"] for p in g["pins"]])))
    for kind in ("ref", "val"):
        for s, minx, maxx, rot, hi, lo in frames:
            txt = s[kind]
            if not txt or s.get(kind + "_hide"):
                continue
            xy = _place_label(txt, rot, minx, maxx, hi, lo,
                              kind == "val", taken, bodies)
            s[kind + "_xy"] = xy
            taken.append(_text_box(xy[0], xy[1], txt, rot))


# ---------------------------------------------------------------- 库查询
_PAD_CACHE = {}


def footprint_path(fp):
    if not fp or ":" not in fp:
        return None
    lib, name = fp.split(":", 1)
    return os.path.join(E.KICAD_FPS, lib + ".pretty", name + ".kicad_mod")


def footprint_exists(fp):
    """封装是否真的在 KiCad 库里 —— 缺了就是个挂不上网络的哑件。"""
    p = footprint_path(fp)
    return bool(p) and os.path.exists(p)


def footprint_pads(fp):
    """封装的焊盘号集合；直接读 .kicad_mod 文本，不起 KiCad。"""
    if not fp:
        return frozenset()
    if fp in _PAD_CACHE:
        return _PAD_CACHE[fp]
    p = footprint_path(fp)
    out = frozenset()
    if p:
        try:
            out = frozenset(_FP_LIB_RE.findall(io.open(p, encoding="utf-8").read()))
        except Exception:
            out = frozenset()
    _PAD_CACHE[fp] = out
    return out


def orient(lib_id, a, b, want, a_first=True):
    """找旋转角，使引脚 a/b 呈期望的横排或竖排。

    want="h": a 在左 b 在右；want="v": a 在上 b 在下（a_first=False 则反过来）。
    这样二极管/LED 的极性、电解电容的方向都由同一个函数挑出正确朝向。
    """
    for rot in (0, 90, 180, 270):
        g = E.lib_pins(lib_id, rot)
        if a not in g or b not in g:
            continue
        pa, pb = g[a], g[b]
        if want == "h" and abs(pa[1] - pb[1]) < 1e-6:
            if (pa[0] < pb[0]) == a_first:
                return rot
        if want == "v" and abs(pa[0] - pb[0]) < 1e-6:
            if (pa[1] < pb[1]) == a_first:
                return rot
    return None


def _mode_y(pts, prefer):
    """出现最多的 y 当干线高度；打平时选离信号行近的。"""
    hist = {}
    for _, y in pts:
        hist[y] = hist.get(y, 0) + 1
    return min(hist, key=lambda y: (-hist[y], abs(y - prefer)))


def _hits(segs, blockers):
    """走线（含线头）碰到别的网络的引脚就算短路。"""
    for (x1, y1, x2, y2) in segs:
        for p in blockers:
            if E._pt_seg(p, (x1, y1), (x2, y2)) == 0:
                return True
    return False


def _chain(coords, fixed, horiz):
    """把一串坐标连成首尾相接的线段：中间每个接点都断开，不留"线压脚"。"""
    xs = sorted(set(coords))
    out = []
    for a, b in zip(xs, xs[1:]):
        out.append((a, fixed, b, fixed) if horiz else (fixed, a, fixed, b))
    return out


def _net_segs(pts, y0):
    xs = sorted({p[0] for p in pts})
    ys = sorted({p[1] for p in pts})
    if len(ys) == 1:
        return _chain(xs, ys[0], True)
    if len(xs) == 1:
        return _chain(ys, xs[0], False)
    segs = _chain(xs, y0, True)
    for (x, y) in pts:
        if y == y0:
            continue
        stops = sorted({y, y0} | {q[1] for q in pts if q[0] == x})
        segs.extend((x, a, x, b) for a, b in zip(stops, stops[1:]))
    return segs


def route_net(pts, lane=None, prefer=ROW_Y, blockers=()):
    """一个网络的走线：一条水平干线 + 若干垂直短线，避开别的网络的引脚。"""
    pts = sorted(set((snap(x), snap(y)) for x, y in pts))
    if len(pts) < 2:
        return []
    blockers = [b for b in blockers if b not in pts]
    if lane is not None:
        segs = _net_segs(pts, snap(lane))
        if _hits(segs, blockers):
            raise BuildError("电源轨干线 y=%.2f 上压到了别的引脚" % lane)
        return segs
    tries = [_mode_y(pts, prefer)]
    tries.extend(snap(prefer + s * k * GRID)
                 for k in range(1, 14) for s in (1, -1))
    tries.extend(sorted({p[1] for p in pts}))
    tried = set()
    for y0 in tries:
        if y0 in tried:
            continue
        tried.add(y0)
        segs = _net_segs(pts, y0)
        if not _hits(segs, blockers):
            return segs
    raise BuildError("网络 %s 找不到不压别的引脚的走线通道" % pts)


def erc_class(c):
    """ERC 等价类指纹：只由几何和引脚类型决定，与取值/型号/标号无关。

    换电阻值、换同引脚同类型的型号，ERC 判定必然一样；1000+ 条预设
    靠它把真 ERC 的次数压到"每个拓扑一次"。
    """
    syms = []
    for s in c["symbols"]:
        g = E.lib_pins(s["lib_id"], s["rot"])
        syms.append((round(s["x"], 2), round(s["y"], 2), s["rot"],
                     tuple(sorted((dx, dy, t) for dx, dy, t in g.values()))))
    xs = [t[0] for t in syms] + [w[0] for w in c["wires"]]
    ys = [t[1] for t in syms] + [w[1] for w in c["wires"]]
    ox, oy = min(xs), min(ys)
    shape = sorted((round(x - ox, 2), round(y - oy, 2), rot, pins)
                   for x, y, rot, pins in syms)
    wires = sorted((round(min(a, cc) - ox, 2), round(min(b, d) - oy, 2),
                    round(max(a, cc) - ox, 2), round(max(b, d) - oy, 2))
                   for a, b, cc, d in c["wires"])
    return repr((shape, wires))


# ---------------------------------------------------------------- 布局主体
class Block(object):
    """一行信号 + 上下电源轨。

    cols() 声明行上节点从左到右的顺序；series 横放在两节点之间，
    shunt 竖挂在节点与另一头之间，dev 放多脚器件，header 在行端加接线柱，
    rail 声明电源轨（自动补电源符号和 PWR_FLAG）。
    """

    def __init__(self, name, title, x0=X0, y0=ROW_Y, pitch=PITCH,
                 lane=LANE, lane_step=LANE_STEP):
        self.name = name
        self.title = title
        self.x0, self.y0, self.pitch = x0, y0, pitch
        self.lane, self.lane_step = lane, lane_step
        self.order = []            # 行上节点（网络名）的左右顺序
        self.symbols = []
        self.net_pts = {}          # 网络名 -> [(x, y)]
        self.pin_net = {}          # "R1.1" -> 网络名
        self.nc_pins = set()       # 声明为悬空的引脚（NC）
        self.rails = []            # (网络名, 符号, up/down)
        self.power_out = set()     # 已有真实电源输出的网络
        self.slot = {}             # 节点 -> 已占用的挂点数
        self.wires = []
        self._npwr = 0
        self._nflg = 0

    # ---- 节点
    def cols(self, *nets):
        for n in nets:
            if n not in self.order:
                self.order.append(n)
        return self

    def col_x(self, net=None, idx=None):
        i = self.order.index(net) if idx is None else idx
        return snap(self.x0 + i * self.pitch)

    def lane_y(self, net):
        """每条轨一个独占的 y，同侧多条轨依次外移，避免共线并网。"""
        side = self.rail_side(net)
        same = [r[0] for r in self.rails if r[2] == side]
        k = same.index(net)
        d = self.lane + k * self.lane_step
        return snap(self.y0 + (d if side == "down" else -d))

    def rail_side(self, net):
        for n, _s, side in self.rails:
            if n == net:
                return side
        return None

    def is_rail(self, net):
        return self.rail_side(net) is not None

    # ---- 放件
    def _add(self, ref, lib_id, val, fp, x, y, rot, pinmap, hide_ref=False):
        g = E.lib_pins(lib_id, rot)
        for num in pinmap:
            if num not in g:
                raise BuildError("%s(%s) 没有引脚 %s" % (ref, lib_id, num))
        for num, net in pinmap.items():
            dx, dy, ptype = g[num]
            self.net_pts.setdefault(net, []).append((snap(x + dx), snap(y + dy)))
            self.pin_net["%s.%s" % (ref, num)] = net
            if ptype == "power_out" and not ref.startswith("#"):
                self.power_out.add(net)
        s = dict(lib_id=lib_id, x=snap(x), y=snap(y), rot=rot, ref=ref,
                 val=val, fp=fp,
                 pins=sorted(pinmap, key=lambda t: (len(t), t)),
                 ref_xy=(0, 0), val_xy=(0, 0),
                 ref_hide=hide_ref, val_hide=False)
        self.symbols.append(s)
        return s

    def series(self, ref, lib_id, val, fp, net_a, net_b,
               pin_a="1", pin_b="2", row_off=0):
        """横放在两个相邻节点之间的两脚元件。"""
        self.cols(net_a)
        self.cols(net_b)
        ia, ib = self.order.index(net_a), self.order.index(net_b)
        if abs(ia - ib) != 1:
            raise BuildError("%s 跨了非相邻节点 %s/%s" % (ref, net_a, net_b))
        rot = orient(lib_id, pin_a, pin_b, "h")
        if rot is None:
            raise BuildError("%s 找不到横放的旋转角" % ref)
        g = E.lib_pins(lib_id, rot)
        cx = snap((self.col_x(net_a) + self.col_x(net_b)) / 2.0
                  - (g[pin_a][0] + g[pin_b][0]) / 2.0)
        return self._add(ref, lib_id, val, fp, cx,
                         snap(self.y0 + row_off * GRID), rot,
                         {pin_a: net_a, pin_b: net_b})

    def shunt(self, ref, lib_id, val, fp, net_a, net_b,
              pin_a="1", pin_b="2", col=None):
        """竖挂的两脚元件：一脚落在信号行的节点上，另一脚朝电源轨。"""
        if self.is_rail(net_a) and not self.is_rail(net_b):
            net_a, net_b, pin_a, pin_b = net_b, net_a, pin_b, pin_a
        self.cols(net_a)
        up = self.rail_side(net_b) == "up"
        rot = orient(lib_id, pin_a, pin_b, "v", a_first=not up)
        if rot is None:
            raise BuildError("%s 找不到竖放的旋转角" % ref)
        g = E.lib_pins(lib_id, rot)
        k = self.slot.get(net_a, 0)
        self.slot[net_a] = k + 1
        x = (self.col_x(net_a) + k * SLOT) if col is None else self.col_x(idx=col)
        cy = snap(self.y0 - g[pin_a][1])
        return self._add(ref, lib_id, val, fp, x, cy, rot,
                         {pin_a: net_a, pin_b: net_b})

    def dev(self, ref, lib_id, val, fp, pinmap, col=None, rot=0,
            x_off=0.0, y_off=0.0):
        """多脚器件：按格点摆放，引脚到网络的映射由调用方给全。"""
        idx = self.order.index(col) if isinstance(col, str) else col
        x = self.x0 if idx is None else self.col_x(idx=idx)
        for net in pinmap.values():
            self.cols(net)
        return self._add(ref, lib_id, val, fp, snap(x + x_off),
                         snap(self.y0 + y_off), rot, pinmap)

    def put(self, ref, lib_id, val, fp, pinmap, x, y, rot=0):
        """绝对坐标放件：多脚 IC 周边的小件按引脚位置摆，不动行节点顺序。"""
        return self._add(ref, lib_id, val, fp, snap(x), snap(y), rot, pinmap)

    def nc(self, ref, *nums):
        """声明空脚：NC 本来就不该接线，硬塞进某个网络反而是错的电路。"""
        for num in nums:
            key = "%s.%s" % (ref, num)
            if key not in self.pin_net and not any(
                    s["ref"] == ref and num in E.lib_pins(s["lib_id"], s["rot"])
                    for s in self.symbols):
                raise BuildError("%s 没有引脚 %s" % (ref, num))
            self.nc_pins.add(key)
        return self

    def pin_xy(self, ref, num):
        """已放元件某个引脚的图纸坐标。"""
        for s in self.symbols:
            if s["ref"] == ref:
                g = E.lib_pins(s["lib_id"], s["rot"])
                if num not in g:
                    raise BuildError("%s 没有引脚 %s" % (ref, num))
                dx, dy, _t = g[num]
                return (snap(s["x"] + dx), snap(s["y"] + dy))
        raise BuildError("还没放 %s" % ref)

    def hang(self, ref, lib_id, val, fp, anchor, pin, net_far,
             pin_near="1", pin_far="2", below=True, gap=7.62, dx=0.0):
        """把两脚件竖直挂在某引脚的下方（或上方），中间一段竖线相连。

        dx 把件整体挪开某一列，免得竖线正下方压着同一器件的另一只引脚。
        返回 (近端坐标, 远端坐标)，调用方可以接着往远端挂第二只件。
        """
        want_v = orient(lib_id, pin_near, pin_far, "v", a_first=below)
        if want_v is None:
            raise BuildError("%s 找不到竖放的旋转角" % ref)
        g = E.lib_pins(lib_id, want_v)
        ax, ay = self.pin_xy(anchor, pin)
        ax = snap(ax + dx)
        ny = ay + (gap if below else -gap)
        s = self._add(ref, lib_id, val, fp, snap(ax - g[pin_near][0]),
                      snap(ny - g[pin_near][1]), want_v,
                      {pin_near: self.pin_net["%s.%s" % (anchor, pin)],
                       pin_far: net_far})
        return (ax, ny), self.pin_xy(ref, pin_far)

    def lead(self, ref, lib_id, val, fp, anchor, pin, net_far,
             pin_near="1", pin_far="2", right=True, gap=7.62, dy=0.0):
        """把两脚件水平挂在某引脚的右侧（或左侧），中间一段横线相连。"""
        rot = orient(lib_id, pin_near, pin_far, "h", a_first=right)
        if rot is None:
            raise BuildError("%s 找不到横放的旋转角" % ref)
        g = E.lib_pins(lib_id, rot)
        ax, ay = self.pin_xy(anchor, pin)
        ay = snap(ay + dy)
        nx = ax + (gap if right else -gap)
        self._add(ref, lib_id, val, fp, snap(nx - g[pin_near][0]),
                  snap(ay - g[pin_near][1]), rot,
                  {pin_near: self.pin_net["%s.%s" % (anchor, pin)],
                   pin_far: net_far})
        return (nx, ay), self.pin_xy(ref, pin_far)

    def rail(self, net, sym=None, side="down"):
        sym = sym or ("power:GND" if net == "GND" else "power:" + net)
        if E.lib_pins(sym) is None or "1" not in E.lib_pins(sym):
            raise BuildError("电源符号 %s 不可用" % sym)
        self.rails.append((net, sym, side))
        return self

    def header(self, ref, net, side="left", label=None):
        """行端接线柱：外部信号得有落点，否则线头是死端。"""
        lib = "Connector:Conn_01x01_Pin"
        fp = "Connector_PinHeader_2.54mm:PinHeader_1x01_P2.54mm_Vertical"
        self.cols(net)
        x = self.col_x(net)
        x += -(5.08 + PORT_GAP) if side == "left" else (5.08 + PORT_GAP)
        return self._add(ref, lib, label or net,
                         fp if footprint_exists(fp) else "",
                         x, self.y0, 0 if side == "left" else 180, {"1": net})

    # ---- 布线
    def _place_rail_symbols(self):
        for net, sym, side in self.rails:
            pts = sorted(set(self.net_pts.get(net, [])))
            if not pts:
                continue        # 只当标签用的轨：留空道，不放电源符号
            lane = self.lane_y(net)
            self._npwr += 1
            self._add("#PWR%02d" % self._npwr, sym, net, "",
                      min(p[0] for p in pts) - 7.62, lane, 0, {"1": net},
                      hide_ref=True)
            if net in self.power_out:
                continue
            self._nflg += 1
            self._add("#FLG%02d" % self._nflg, "power:PWR_FLAG", "PWR_FLAG", "",
                      max(p[0] for p in pts) + 7.62, lane,
                      0 if side == "down" else 180, {"1": net}, hide_ref=True)

    def _wire_all(self):
        self._place_rail_symbols()
        allpts = {net: set((snap(x), snap(y)) for x, y in pts)
                  for net, pts in self.net_pts.items()}
        seen = set()
        for net in sorted(self.net_pts):
            blockers = [p for other, ps in allpts.items()
                        if other != net for p in ps]
            for seg in route_net(self.net_pts[net],
                                 lane=self.lane_y(net) if self.is_rail(net)
                                 else None, prefer=self.y0, blockers=blockers):
                if seg not in seen:
                    seen.add(seg)
                    self.wires.append(seg)

    # ---- 标签锚点
    def _labels(self):
        layout_labels(self.symbols)

    # ---- PCB：网格放件 + 按网络串成链
    def _pcb(self):
        real = [s for s in self.symbols
                if not s["ref"].startswith("#") and footprint_exists(s["fp"])]
        n = max(len(real), 1)
        cols = min(6, n)
        rows = int(math.ceil(n / float(cols)))
        px, py, step = 10.0, 9.0, 10.0
        fps, pos, fpof = [], {}, {}
        for i, s in enumerate(real):
            c, r = i % cols, i // cols
            x, y = px + c * step, py + r * step
            lib, name = s["fp"].split(":", 1)
            fps.append((s["ref"], lib, name, x, y, 0))
            pos[s["ref"]] = (x, y)
            fpof[s["ref"]] = s["fp"]
        tracks, ends = [], []
        for net in sorted(self.net_pts):
            chain = []
            for key in sorted(self.pin_net,
                              key=lambda k: (str(k.split(".")[0]),
                                             len(k.split(".")[1]),
                                             k.split(".")[1])):
                if self.pin_net[key] != net:
                    continue
                ref, num = key.split(".")
                if ref not in pos or num not in footprint_pads(fpof[ref]):
                    continue
                if (ref, num) not in chain:
                    chain.append((ref, num))
            rest, link = chain[:], []
            if rest:
                link.append(rest.pop(0))
                while rest:
                    x0, y0 = pos[link[-1][0]]
                    k = min(range(len(rest)),
                            key=lambda i: (pos[rest[i][0]][0] - x0) ** 2
                                        + (pos[rest[i][0]][1] - y0) ** 2)
                    link.append(rest.pop(k))
            if len(link) == 1:
                # 对端只有电源符号（没封装），这块铜也得挂上网络
                ends.append((link[0][0], link[0][1], net))
            for a, b in zip(link, link[1:]):
                tracks.append((a[0], a[1], b[0], b[1], net))
        return dict(footprints=fps, tracks=tracks, net_ends=ends,
                    board=(round(max(40.0, px + cols * step + 4.0), 1),
                           round(max(25.0, py + rows * step + 4.0), 1)))

    # ---- 自检
    def _verify(self):
        pins = {}
        for s in self.symbols:
            for num, (dx, dy, _t) in E.lib_pins(s["lib_id"], s["rot"]).items():
                pins["%s.%s" % (s["ref"], num)] = (snap(s["x"] + dx),
                                                   snap(s["y"] + dy))
        for key, pt in pins.items():
            if key in self.nc_pins:
                continue
            net = self.pin_net.get(key)
            if net is None:
                raise BuildError("%s 没分配网络" % key)
            if pt not in self.net_pts[net]:
                raise BuildError("%s 的坐标没记进网络 %s" % (key, net))
        for net, pts in self.net_pts.items():
            if len(pts) < 2:
                raise BuildError("网络 %s 只有一个接点 (%s)" % (net, pts))
        ends = {}
        for (x1, y1, x2, y2) in self.wires:
            for a in ((x1, y1), (x2, y2)):
                ends[a] = ends.get(a, 0) + 1
        pinset = set(pins.values())
        for pt, cnt in ends.items():
            if pt in pinset or cnt > 1:
                continue
            on_mid = any(x1 < pt[0] < x2 and y1 == y2 == pt[1]
                         or y1 < pt[1] < y2 and x1 == x2 == pt[0]
                         for (x1, y1, x2, y2) in self.wires)
            if not on_mid:
                raise BuildError("死端 (%.2f, %.2f) 不接任何引脚" % pt)
        groups = E.circuit_nets({"symbols": self.symbols, "wires": self.wires})
        seen = set()
        for g in groups:
            names = set(self.pin_net.get(p) for p in g["pins"])
            if len(names) > 1:
                raise BuildError("网络被并在一起: %s"
                                 % sorted("%s=%s" % (p, self.pin_net.get(p))
                                          for p in g["pins"]))
            if set(g["pins"]) <= self.nc_pins:
                continue
            if len(g["pins"]) < 2:
                raise BuildError("孤立引脚 %s（没有线接）" % g["pins"])
            seen.update(g["pins"])
        missing = set(pins) - seen - self.nc_pins
        if missing:
            raise BuildError("引脚悬空: %s" % sorted(missing))

    # ---- 收尾
    def finish(self, cat=None, desc=None, params=(), tpl=None,
               erc_verified=True, extra=None):
        self._wire_all()
        self._labels()
        self._verify()
        out = dict(name=self.name, title=self.title,
                   symbols=self.symbols, wires=self.wires,
                   libs=sorted(set(lib_key(s["lib_id"]) for s in self.symbols)),
                   pcb=self._pcb(), cat=cat, desc=desc,
                   params=list(params), tpl=tpl or self.name,
                   erc_verified=erc_verified)
        if extra:
            out.update(extra)
        return out
