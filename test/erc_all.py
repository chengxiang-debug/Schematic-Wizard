"""跑全 18 条预设的原理图 ERC，逐条打印违规明细。"""
import io
import os
import re
import subprocess
import sys

sys.path.insert(0, "D:/AutoDraw/app/engine")
import engine as E

OUT = "D:/AutoDraw_Test/erc"
if not os.path.isdir(OUT):
    os.makedirs(OUT)
keys = sys.argv[1:] or list(E.CIRCUITS)
silent = lambda m: None

bad = {}
for k in keys:
    circ = E.CIRCUITS[k]()
    outdir = os.path.join(OUT, k)
    if not os.path.isdir(outdir):
        os.makedirs(outdir)
    try:
        sch, ok, info = E.generate_schematic(circ, outdir, silent)
    except Exception as ex:
        print("%-14s 异常: %s" % (k, ex))
        bad[k] = ["异常 " + str(ex)]
        continue
    rpt = os.path.join(outdir, k + "_erc.rpt")
    lines = []
    if os.path.exists(rpt):
        txt = io.open(rpt, encoding="utf-8").read()
        for m in re.finditer(r"\[(\w+)\]: (.+?)\n(?:\s+; (\w+)\n)?\s+@\(([^)]*)\): (.+)",
                             txt):
            kind, msg, sev, pos, detail = m.groups()
            lines.append("%-8s %s @%s — %s" % (sev or "warn", kind, pos, detail))
    if not ok:
        bad[k] = lines
    print("%-14s errors=%d warns=%d %s" % (
        k, info["errors"], info["warns"], "" if ok else "<<<"))

print("\n----- 明细 -----")
for k, lines in bad.items():
    print("## %s" % k)
    for l in lines:
        print("   " + l)
print("\n%d/%d 条有 ERC 违规" % (len(bad), len(keys)))
