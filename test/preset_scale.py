# -*- coding: utf-8 -*-
"""1000+ 预设的规模体检：先自检几何，再按 ERC 等价类跑真 KiCad ERC/DRC。

用法（在 D:/AutoDraw 下跑）:
  D:/KiCad/bin/python.exe test/preset_scale.py              # 全量：结构 + ERC + DRC
  D:/KiCad/bin/python.exe test/preset_scale.py --erc        # 只跑 ERC
  D:/KiCad/bin/python.exe test/preset_scale.py --sample 6   # 每个家族抽 6 条跑 PCB
  D:/KiCad/bin/python.exe test/preset_scale.py -v           # 打印每条违规明细

为什么按等价类：换电阻值不改 ERC 结论，1300+ 条真跑没有意义；
builder.erc_class() 把「符号摆放 + 引脚类型 + 走线几何」压成指纹，
同指纹只跑一次，跑不过就把整类列出来。
"""
import io
import json
import os
import re
import sys
import time

sys.path.insert(0, "D:/AutoDraw/app/engine")
import engine as E          # noqa: E402
import builder as B         # noqa: E402

OUT = "D:/AutoDraw_Test/scale"
MIN_PRESETS = 1000


def log(m):
    sys.stdout.write(str(m) + "\n")
    sys.stdout.flush()


def structure(reg, cat_keys):
    """结构体检：数量、key 唯一、分类合法、参数/说明齐全（齐全只要求 catalog 条目）。"""
    problems, cats = [], set(E.CAT_ORDER)
    seen = set()
    for key in reg:
        if key in seen:
            problems.append("key 重复: %s" % key)
        seen.add(key)
    for key, fn in sorted(reg.items()):
        try:
            c = fn()
        except B.BuildError as e:
            problems.append("%s 建图失败: %s" % (key, str(e).split("\n")[0]))
            continue
        except Exception as e:
            problems.append("%s 建图异常: %r" % (key, e))
            continue
        if (c.get("cat") or E.CIRCUIT_CATS.get(key, "其他")) not in cats:
            problems.append("%s 分类非法: %r" % (key, c.get("cat")))
        if key not in cat_keys:
            continue
        if not (c.get("desc") or "").strip():
            problems.append("%s 没有说明" % key)
        if not c.get("params"):
            problems.append("%s 没有参数表" % key)
        if not [s for s in c["symbols"] if not s["ref"].startswith("#")]:
            problems.append("%s 没有实体元件" % key)
    return problems


def erc_parse(rpt):
    """ERC 报告 → [(severity, kind, detail)]。"""
    out = []
    if not os.path.exists(rpt):
        return out
    txt = io.open(rpt, encoding="utf-8").read()
    for m in re.finditer(r"\[(\w+)\]: (.+?)\n(?:\s+; (\w+)\n)?\s+@\(([^)]*)\): (.+)",
                         txt):
        _kind, msg, sev, _pos, detail = m.groups()
        out.append((sev or "warn", msg.strip(), detail.strip()))
    return out


def drc_parse(rpt):
    out = []
    if not os.path.exists(rpt):
        return out
    txt = io.open(rpt, encoding="utf-8").read()
    for m in re.finditer(r"\[(\w+)\]\s+(.*?)(?=\n\[|\n$|\n\n)", txt, re.S):
        kind, msg = m.group(1), " ".join(m.group(2).split())
        out.append((kind, msg[:160]))
    return out


def main():
    args = sys.argv[1:]
    only_erc = "--erc" in args
    only_pcb = "--pcb" in args
    verbose = "-v" in args
    sample = 0
    if "--sample" in args:
        sample = int(args[args.index("--sample") + 1])
    pats = [a for a in args if not a.startswith("-") and a != str(sample)]

    t0 = time.time()
    reg = E.presets()
    circuits = {}
    for key, fn in reg.items():
        try:
            circuits[key] = fn()
        except Exception:
            pass                      # structure() 会记账
    log("预设 %d 条，建图 %d 条，%.1fs" % (len(reg), len(circuits),
                                        time.time() - t0))

    problems = structure(reg, set(E._catalog().CIRCUITS))
    bad_struct = [p for p in problems]
    log("结构体检 %d 条问题" % len(bad_struct))
    for p in bad_struct[:40]:
        log("  STRUCT " + p)

    # 按 ERC 等价类归组，每类取一条代表去真跑
    classes = {}
    for key, c in sorted(circuits.items()):
        if pats and not any(p in key or p in c.get("tpl", "") for p in pats):
            continue
        classes.setdefault(E.erc_key(c), []).append(key)
    log("ERC 等价类 %d 个" % len(classes))

    erc_bad, drc_bad, slow = [], [], 0.0
    for n, (cls, keys) in enumerate(sorted(classes.items(),
                                           key=lambda kv: kv[1][0]), 1):
        rep = keys[0]
        circ = circuits[rep]
        outdir = os.path.join(OUT, rep)
        if not os.path.isdir(outdir):
            os.makedirs(outdir)
        t1 = time.time()
        rec = {"key": rep, "tpl": circ.get("tpl") or rep, "n": len(keys)}
        if not only_pcb:
            try:
                _sch, ok, info = E.generate_schematic(circ, outdir, lambda m: None)
            except Exception as e:
                ok, info = False, {"errors": -1, "warns": 0, "msgs": 0}
                rec["exc"] = repr(e)
            rec["erc_ok"], rec["info"] = ok, info
            if not ok:
                rec["msgs"] = erc_parse(os.path.join(outdir, rep + "_erc.rpt"))
                erc_bad.append(rec)
        if not only_erc:
            keys_pcb = keys if not sample else keys[:sample]
            for k in keys_pcb:
                c2 = circuits[k]
                od2 = os.path.join(OUT, k)
                if not os.path.isdir(od2):
                    os.makedirs(od2)
                try:
                    p = E.generate_pcb(c2, od2, lambda m: None)
                except Exception as e:
                    drc_bad.append({"key": k, "exc": repr(e), "msgs": []})
                    continue
                if p is None:
                    drc_bad.append({"key": k, "exc": "PCB 生成失败", "msgs": []})
                    continue
                v = drc_parse(os.path.join(od2, k + "_drc.rpt"))
                if v:
                    drc_bad.append({"key": k, "exc": "", "msgs": v})
        slow = max(slow, time.time() - t1)
        done = "ERC✗" if rec.get("erc_ok") is False else (
            "DRC✗" if any(r["key"].startswith(rep) for r in drc_bad) else "ok")
        log("[%2d/%2d] %-16s 同类%4d 条 %s  %.0fs"
            % (n, len(classes), rec["tpl"], len(keys), done, time.time() - t1))

    log("\n===== 汇总 =====")
    log("预设 %d 条（要求 ≥%d），等价类 %d 个，单类最慢 %.0fs"
        % (len(reg), MIN_PRESETS, len(classes), slow))
    if len(reg) < MIN_PRESETS:
        log("  !! 数量不足 %d" % MIN_PRESETS)
    log("ERC 未通过 %d 类，DRC 未通过 %d 条" % (len(erc_bad), len(drc_bad)))
    for r in erc_bad:
        log("## %s (%s, 同类 %d 条) errors=%s warns=%s"
            % (r["key"], r["tpl"], r["n"], r["info"].get("errors"),
               r["info"].get("warns")))
        if r.get("exc"):
            log("   " + r["exc"])
        for sev, msg, detail in (r["msgs"] if verbose else r["msgs"][:4]):
            log("   %-6s %s — %s" % (sev, msg, detail))
    for r in drc_bad:
        log("## DRC %s %s" % (r["key"], r["exc"]))
        for kind, msg in (r["msgs"] if verbose else r["msgs"][:4]):
            log("   %-22s %s" % (kind, msg))
    with io.open(os.path.join(OUT, "scale.json"), "w", encoding="utf-8") as f:
        json.dump({"presets": len(reg), "classes": len(classes),
                   "struct": bad_struct,
                   "erc_bad": [{k: v for k, v in r.items() if k != "info"}
                               for r in erc_bad],
                   "drc_bad": drc_bad}, f, ensure_ascii=False, indent=1)
    sys.exit(1 if (bad_struct or erc_bad or drc_bad) else 0)


if __name__ == "__main__":
    main()
