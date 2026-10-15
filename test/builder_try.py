"""builder 试跑：几条典型拓扑 → 几何自检 →（可选）真 KiCad ERC。

用法:  D:/KiCad/bin/python.exe test/builder_try.py          # 全部自检
       D:/KiCad/bin/python.exe test/builder_try.py erc      # 再过真 ERC
       D:/KiCad/bin/python.exe test/builder_try.py search npn_low   # 搜器件朝向
"""
import os
import shutil
import sys

sys.path.insert(0, "D:/AutoDraw/app/engine")
import engine as E          # noqa: E402
import builder as B         # noqa: E402

RFP = "Resistor_SMD:R_0603_1608Metric"
CFP = "Capacitor_SMD:C_0603_1608Metric"
DFP = "LED_SMD:LED_0603_1608Metric"
TO92 = "Package_TO_SOT_THT:TO-92_Inline"


def rc_lowpass():
    b = B.Block("b_rc_lowpass", "RC 低通 1k/100n")
    b.rail("GND")
    b.cols("VIN", "VOUT")
    b.header("J1", "VIN", "left")
    b.series("R1", "Device:R", "1k", RFP, "VIN", "VOUT")
    b.shunt("C1", "Device:C", "100n", CFP, "VOUT", "GND")
    b.header("J2", "VOUT", "right")
    return b.finish(cat="滤波去耦")


def divider():
    b = B.Block("b_divider", "电阻分压 10k/10k")
    b.rail("+5V", side="up")
    b.rail("GND", side="down")
    b.cols("MID")
    b.shunt("R1", "Device:R", "10k", RFP, "MID", "+5V")
    b.shunt("R2", "Device:R", "10k", RFP, "MID", "GND")
    b.header("J1", "MID", "right")
    return b.finish(cat="分压测量")


def led_rail():
    b = B.Block("b_led", "5V 指示灯")
    b.rail("+5V", side="up")
    b.rail("GND", side="down")
    b.cols("A")
    b.shunt("R1", "Device:R", "1k", RFP, "A", "+5V")
    b.shunt("D1", "Device:LED", "LED", DFP, "A", "GND")
    return b.finish(cat="指示")


def npn_low(rot=180, x_off=0.0, y_off=0.0):
    b = B.Block("b_npn", "NPN 低边开关")
    b.rail("+5V", side="up")
    b.rail("GND", side="down")
    b.cols("IN", "QB", "COL")
    b.header("J1", "IN", "left")
    b.series("R1", "Device:R", "10k", RFP, "IN", "QB")
    b.dev("Q1", "Transistor_BJT:BC547", "BC547", TO92,
          {"1": "GND", "2": "QB", "3": "COL"},
          col="QB", rot=rot, x_off=x_off, y_off=y_off)
    b.shunt("R2", "Device:R", "1k", RFP, "COL", "+5V")
    b.header("J2", "COL", "right")
    return b.finish(cat="开关驱动")


ALL = {"rc_lowpass": rc_lowpass, "divider": divider,
       "led_rail": led_rail, "npn_low": npn_low}


def show(name, c):
    nets = E.circuit_nets(c)
    print("ok   %-14s %d 件 %d 线 %d 网络 | pcb %d 件 %d 线 板 %sx%s"
          % (name,
             len([s for s in c["symbols"] if not s["ref"].startswith("#")]),
             len(c["wires"]), len(nets),
             len(c["pcb"]["footprints"]), len(c["pcb"]["tracks"]),
             c["pcb"]["board"][0], c["pcb"]["board"][1]))
    for n in nets:
        print("       %-12s %s" % (n["name"], " ".join(n["pins"])))


def search(name, fn):
    offs = [0.0, 2.54, -2.54, 5.08, -5.08]
    good = []
    for rot in (0, 90, 180, 270):
        for xo in offs:
            for yo in offs:
                try:
                    fn(rot=rot, x_off=xo, y_off=yo)
                except B.BuildError as e:
                    continue
                except Exception as e:
                    print("  rot=%-4d x=%-6s y=%-6s 异常 %r" % (rot, xo, yo, e))
                    continue
                good.append((rot, xo, yo))
    print("%s 可用摆位 %d 个: %s" % (name, len(good), good[:12]))
    return good


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "search":
        for nm in args[1:] or list(ALL):
            search(nm, ALL[nm])
        sys.exit(0)
    do_erc = "erc" in args
    outdir = r"D:\AutoDraw\test\_builder_out"
    if do_erc:
        shutil.rmtree(outdir, ignore_errors=True)
        os.makedirs(outdir, exist_ok=True)
    bad = 0
    for nm, fn in ALL.items():
        try:
            c = fn()
        except Exception as e:
            bad += 1
            print("FAIL %-14s %r" % (nm, e))
            continue
        show(nm, c)
        if do_erc:
            _p, erc_ok, info = E.generate_schematic(c, outdir, lambda m: None)
            print("      ERC %s %s" % ("通过" if erc_ok else "失败", info))
            if not erc_ok:
                bad += 1
                rpt = os.path.join(outdir, c["name"] + "_erc.rpt")
                if os.path.exists(rpt):
                    for line in open(rpt, encoding="utf-8").read().splitlines():
                        if line.strip():
                            print("      | " + line[:160])
    print("SUMMARY", "all green" if not bad else "%d 条有问题" % bad)
    sys.exit(1 if bad else 0)
