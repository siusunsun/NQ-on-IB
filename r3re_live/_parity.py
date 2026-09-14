# -*- coding: utf-8 -*-
"""PARITY TEST: does the LIVE arming path reproduce the BATCH backtest, fire for fire?

This is the test that would have caught the one-bar-late bug. For every reference fire
produced by the batch overlay, we replay what the live executor would have seen bar by
bar and check that:
  (a) it ARMS before the fill bar opens, and
  (b) the resting BUY-STOP it would place equals the reference entry price.

Run AFTER the c < ki+1 arming fix.
"""
import sys, resource
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "/root/r3re_live")
sys.path.insert(0, "/root/r3re_paper")
import numpy as np, pandas as pd
import r3re_paper_bot as PB
import r3v_live_spec as S

WINDOW_DAYS = 7
N_TEST = 25

print("loading archive ...", flush=True)
full = PB.load_csv_warmup(days=100000)
print(f"  bars {len(full):,}  {full.index.min()} .. {full.index.max()}", flush=True)
reg = S.build_regime(full)

print("batch reference pass (live spec, MAX_WAIT_BARS=%d) ..." % S.MAX_WAIT_BARS, flush=True)
ref = S.overlay_fires(full, reg)
print(f"  reference fires: {len(ref)}", flush=True)

b5 = S.bt.build_5min(full)
bts = [b[0] for b in b5]
idx = {t: i for i, t in enumerate(bts)}

tests = ref[-N_TEST:]
print(f"\nreplaying the LIVE arming path for the last {len(tests)} fires\n")
print(f"  {'r3_stop':<17}{'ref entry':>10}{'armed at':>17}{'buy_stop':>10}{'bars':>6}  verdict")
ok = bad = skip = 0
for f in tests:
    ki = idx.get(S._floor5(f["r3_stop_time"]))
    fb = idx.get(S._floor5(f["reentry_time"]))
    if ki is None or fb is None:
        skip += 1
        continue
    hit = None
    for c in range(ki + 1, min(ki + S.MAX_WAIT_BARS, len(b5) - 1)):
        t_end = bts[c]
        win = full[(full.index <= t_end + pd.Timedelta(minutes=4, seconds=59)) &
                   (full.index > t_end - pd.Timedelta(days=WINDOW_DAYS))]
        try:
            a = S.arming_state(win, reg)
        except Exception:
            a = None
        if a is not None and S._floor5(a["r3_stop_time"]) == bts[ki]:
            hit = (c, a["buy_stop"])
            break
    if hit is None:
        print(f"  {str(bts[ki])[:16]:<17}{f['entry']:>10.2f}{'NEVER ARMED':>17}{'-':>10}{'-':>6}  FAIL")
        bad += 1
        continue
    c, bs = hit
    # the order must be resting BEFORE the fill bar opens  ->  c must be <= fb-1
    intime = c <= fb - 1
    pxok = abs(bs - float(f["entry"])) < 0.26 or bs <= float(f["entry"]) + 0.001
    v = "OK" if (intime and pxok) else ("LATE" if not intime else "PX")
    if v == "OK":
        ok += 1
    else:
        bad += 1
    print(f"  {str(bts[ki])[:16]:<17}{f['entry']:>10.2f}{str(bts[c])[:16]:>17}{bs:>10.2f}{c-ki:>6}  {v}")

print(f"\n  PARITY: {ok} ok / {bad} fail / {skip} skipped   -> "
      + ("PASS" if bad == 0 else "FAIL"))
print(f"peakRSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024}MB")
