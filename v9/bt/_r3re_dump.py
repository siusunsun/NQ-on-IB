# -*- coding: utf-8 -*-
"""Phase 1: dump R3 re-entry fires for ONE max_wait value, then exit so the
~500MB archive is released. Memory-safe on the live box (see vps-memory-limit-oom)."""
import sys, importlib.util, resource
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pandas as pd
BT = "/root/v9/bt/"
arg = sys.argv[1]
mw = None if arg == "none" else int(arg)
_s = importlib.util.spec_from_file_location("_ra_common", BT + "_ra_common.py")
C = importlib.util.module_from_spec(_s); sys.modules["_ra_common"] = C; _s.loader.exec_module(C)
head = open(BT + "_ra_r3re_sweep.py", encoding="utf-8").read().split("rows = []")[0]
ns = {"__name__": "_p"}; exec(compile(head, "_h", "exec"), ns)
f = ns["run"](wait=2, stop_src="si", magnet="adaptive", max_wait=mw)
d = pd.DataFrame(f, columns=["yr", "gross", "dt"])
d.to_csv(BT + "_r3re_fires_mw_%s.csv" % arg, index=False)
print("max_wait=%-4s fires=%3d gross=$%.0f  peakRSS=%dMB"
      % (arg, len(d), d.gross.sum(), resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024))
