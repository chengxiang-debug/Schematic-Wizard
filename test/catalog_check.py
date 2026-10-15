# -*- coding: utf-8 -*-
"""catalog 体检：逐家族摊预设，把每条的 BuildError 全量列出来。

用法:
  D:/KiCad/bin/python.exe test/catalog_check.py            # 只看建图自检
  D:/KiCad/bin/python.exe test/catalog_check.py -v          # 附带每条违规明细
  D:/KiCad/bin/python.exe test/catalog_check.py led rgb     # 只查 key 含这些片段的

建图自检 = builder 自己的 _verify（声明网表 == 图纸几何反查）。
真 KiCad ERC 请用 test/preset_scale.py，那个按 ERC 等价类去重后才跑。
"""
import os
import re
import sys
import traceback

sys.path.insert(0, "D:/AutoDraw/app/engine")
import builder as B          # noqa: E402


def load_catalog():
    """载入 catalog，但把模块级的 collect() 换成安全版：家族出错不中断全局。"""
    src = io_read(os.path.join("D:/AutoDraw/app/engine", "catalog.py"))
    if "CIRCUITS, CIRCUIT_LABELS, _TPL = collect()" not in src:
        raise RuntimeError("catalog 结构变了，请同步 catalog_check.load_catalog")
    src = src.replace("CIRCUITS, CIRCUIT_LABELS, _TPL = collect()",
                      "CIRCUITS, CIRCUIT_LABELS, _TPL = {}, {}, {}")
    import types
    mod = types.ModuleType("catalog")
    mod.__file__ = "D:/AutoDraw/app/engine/catalog.py"
    sys.modules["catalog"] = mod
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod


def io_read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv
    C = load_catalog()
    import catalog  # 已是 sys.modules 里那份安全版
    stats, fam_err, ent_err, dup = [], [], [], {}
    total = 0
    for fn in catalog.FAMILIES:
        name = fn.__name__
        n = 0
        ents = []
        try:
            for item in fn():
                key, label, build = item
                ents.append((key, label, build))
                if key in dup:
                    fam_err.append((name, key, "key 与 %s 重复" % dup[key]))
                    continue
                dup[key] = name
                try:
                    c = build()
                except B.BuildError as e:
                    ent_err.append((name, key, str(e)))
                    continue
                except Exception as e:
                    ent_err.append((name, key, "%r" % e))
                    if verbose:
                        traceback.print_exc()
                    continue
                n += 1
        except Exception as e:
            fam_err.append((name, "-", "家族生成器异常: %r" % e))
            if verbose:
                traceback.print_exc()
        stats.append((name, n, len(ents)))
        total += n
        del ents

    w = max(len(s[0]) for s in stats)
    print("%-*s  %6s %6s" % (w, "family", "ok", "gen"))
    for name, ok, gen in stats:
        print("%-*s  %6d %6d%s" % (w, name, ok, gen,
                                   "" if ok == gen else "  <<<"))
    print("-" * (w + 16))
    print("%-*s  %6d" % (w, "TOTAL ok", total))
    print("家族异常 %d 条，建图异常 %d 条" % (len(fam_err), len(ent_err)))

    for name, key, msg in fam_err:
        print("FAM  %-24s %-16s %s" % (name, key, msg))
    shown = 0
    bymsg = {}
    for name, key, msg in ent_err:
        bymsg.setdefault(msg.split("\n")[0][:110], []).append((name, key))
    for msg, items in sorted(bymsg.items(), key=lambda kv: -len(kv[1])):
        print("ERR  %4d ×  %s" % (len(items), msg))
        print("        e.g. %s / %s" % (items[0][0], items[0][1]))
        shown += len(items)
    if verbose:
        for name, key, msg in ent_err:
            print("ENT  %-24s %-22s %s" % (name, key, msg.split("\n")[0]))
    if args:
        pats = [re.compile(a) for a in args]
        print("\n----- 匹配 %s 的家族 -----" % args)
        for name, key, msg in ent_err:
            if any(p.search(name) or p.search(key) for p in pats):
                print("ENT  %-24s %-22s %s" % (name, key, msg.split("\n")[0]))
    sys.exit(1 if (fam_err or ent_err) else 0)


if __name__ == "__main__":
    main()
