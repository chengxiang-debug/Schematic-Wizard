# -*- coding: utf-8 -*-
"""Headless 自测: 跑一个预设, 检查嘉立创导出产物。"""
import io
import json
import os
import subprocess
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else r"D:/AutoDraw_Test/jlc"
KEYS = (sys.argv[2].split(",") if len(sys.argv) > 2 else ["led_3v3"])

req = {"action": "generate", "keys": KEYS, "outdir": OUT,
       "sch": True, "pcb": True, "jlc": True}
p = subprocess.run(
    [r"D:\KiCad\bin\python.exe", r"D:\AutoDraw\app\engine\engine.py"],
    input=json.dumps(req), capture_output=True, text=True,
    encoding="utf-8", errors="replace", timeout=1200)

events = []
for line in (p.stdout or "").splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        events.append(json.loads(line))
    except ValueError:
        print("[RAW]", line[:200])

for ev in events:
    if ev.get("ev") in ("log", "result", "error", "done"):
        if ev.get("ev") == "log":
            print("[log]", ev.get("text", "")[:220])
        elif ev.get("ev") == "result":
            print("[result]", ev.get("key"), "erc", ev.get("erc_ok"),
                  "pcb", ev.get("pcb_ok"))
            for t, f in sorted((ev.get("files") or {}).items()):
                print("   ", t, f)
        else:
            print("[", ev.get("ev"), "]", json.dumps(ev, ensure_ascii=False)[:400])

print("returncode", p.returncode)
print("stderr", (p.stderr or "")[-1500:])
print("---- %s listing ----" % OUT)
if os.path.isdir(OUT):
    for n in sorted(os.listdir(OUT)):
        print("  %-40s %8d" % (n, os.path.getsize(os.path.join(OUT, n))))
else:
    print("  (目录不存在)")
