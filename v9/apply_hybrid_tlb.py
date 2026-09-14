# -*- coding: utf-8 -*-
"""Apply the TLB HYBRID STOP (kristov18 upgrade, validated on our harness 2026-08-26) to live V9.
All-or-nothing: validate every anchor first, back up, then write. hybrid_stop_pts=40 / lookback=20."""
import shutil, sys, datetime

STAMP = "20260826_hybtlb"
EX = "/root/v9/v9_execution.py"
LT = "/root/v9/v9_live_trade.py"
CF = "/root/v9/v9_config_live_user.py"

# ---- anchors (must exist exactly once) ----
A1_old = (
"    def __init__(self, k: int = 3, r_multiple: float = 1.0):\n"
"        self.k = k\n"
"        self.r_multiple = r_multiple\n"
)
A1_new = (
"    def __init__(self, k: int = 3, r_multiple: float = 1.0,\n"
"                 hybrid_stop_pts: float = 0.0, hybrid_lookback: int = 20):\n"
"        self.k = k\n"
"        self.r_multiple = r_multiple\n"
"        self.hybrid_stop_pts = hybrid_stop_pts\n"
"        self.hybrid_lookback = hybrid_lookback\n"
)

A2_old = (
"                entry = self.c[i_now]\n"
"                stop = self.last_pl[-1][1]\n"
"                risk = entry - stop\n"
)
A2_new = (
"                entry = self.c[i_now]\n"
"                stop = self.last_pl[-1][1]\n"
"                # HYBRID STOP (2026-08-26, ported from kristov18 trendline_break, his live 08-13):\n"
"                # a sub-threshold pivot risk sits on a just-broken pivot and gets whipsawed; widen it\n"
"                # to the 20-bar structural low (incl signal bar). Only widens tight stops; wide untouched.\n"
"                if self.hybrid_stop_pts > 0 and (entry - stop) < self.hybrid_stop_pts:\n"
"                    _lb = self.hybrid_lookback\n"
"                    stop = min(self.l[max(0, i_now - _lb + 1): i_now + 1])\n"
"                risk = entry - stop\n"
)

B1_old = '        self.tlb_state = TLBLive(k=C.TLB["pivot_k"], r_multiple=C.TLB["r_multiple"])\n'
B1_new = (
'        self.tlb_state = TLBLive(k=C.TLB["pivot_k"], r_multiple=C.TLB["r_multiple"],\n'
'                                 hybrid_stop_pts=C.TLB.get("hybrid_stop_pts", 0.0),\n'
'                                 hybrid_lookback=C.TLB.get("hybrid_lookback", 20))\n'
)

C1_old = (
"TLB = dict(\n"
"    bar_min=5, pivot_k=3, r_multiple=1.0, use_24h=True, slope_filter=False,\n"
"    entry_windows_et=[(600, 720), (895, 920)],\n"
")\n"
)
C1_new = (
"TLB = dict(\n"
"    # 2026-08-26: HYBRID STOP (kristov18 upgrade, validated on our harness +29% full / all yrs >=0).\n"
"    # If pivot-stop risk < 40pt, widen to the 20-bar low (incl signal bar). Only widens tight stops.\n"
"    hybrid_stop_pts=40.0, hybrid_lookback=20,\n"
"    bar_min=5, pivot_k=3, r_multiple=1.0, use_24h=True, slope_filter=False,\n"
"    entry_windows_et=[(600, 720), (895, 920)],\n"
")\n"
)

jobs = [(EX, [(A1_old, A1_new), (A2_old, A2_new)]),
        (LT, [(B1_old, B1_new)]),
        (CF, [(C1_old, C1_new)])]

# ---- phase 1: validate ALL anchors before writing anything ----
loaded = {}
for path, reps in jobs:
    with open(path, encoding="utf-8") as f:
        s = f.read()
    for old, _new in reps:
        cnt = s.count(old)
        if cnt != 1:
            sys.exit(f"ABORT: anchor in {path} found {cnt}x (need exactly 1):\n---\n{old}\n---")
    loaded[path] = s
print("phase 1 OK: all anchors present exactly once")

# ---- phase 2: back up + write ----
for path, reps in jobs:
    shutil.copy(path, f"{path}.bak_{STAMP}")
    s = loaded[path]
    for old, new in reps:
        s = s.replace(old, new)
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)
    print(f"patched {path}  (backup .bak_{STAMP})")

print("DONE", datetime.datetime.utcnow().isoformat())
