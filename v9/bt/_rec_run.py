"""_rec_run.py - READ-ONLY live-vs-model reconciliation for the NQ book."""
import sys, json, os
sys.path.insert(0, "/root/v9/bt")
import numpy as np, pandas as pd
import dash_lib as L

ET = L.ET
print("memAvail=%.0fMB" % L.mem_available_mb(), flush=True)

df, cutoff, nf = L.load_window()
print("window", df.index[0], "..", df.index[-1], len(df), "files", nf, "rss=%.0f" % L.rss_mb(), flush=True)

cached = L.load_daily_closes()
fresh  = L.daily_from_window(df)
daily  = cached.copy()
for k, v in fresh.items():
    daily.loc[k] = float(v)
daily = daily.sort_index()
daily = daily[~daily.index.duplicated(keep="last")]
win_dates = set(d.date() for d in fresh.index)
regime = L.regimes_for(daily, win_dates)
print("regime days", len(regime), "rss=%.0f" % L.rss_mb(), flush=True)

bt = L.harness()["bt"]
bars5 = bt.build_5min(df)
print("bars5", len(bars5), "rss=%.0f" % L.rss_mb(), flush=True)

vt, _sigs = bt.run_vwap_tlb(df, bars5, regime)
r3 = L._apply_cap(vt["VWAP_LONG_R3"]);  [t.update(sleeve="VWAP_LONG_R3") for t in r3]
vs = L._apply_cap(vt["VWAP_SHORT_R1"]); [t.update(sleeve="VWAP_SHORT_R1") for t in vs]
tlb_hyb = L.run_tlb_hyb40(bars5, regime, hybrid_stop_pts=40.0)
tlb_piv = L.run_tlb_hyb40(bars5, regime, hybrid_stop_pts=None)
for t in tlb_piv: t["sleeve"] = "TLB_PIVOT"
r3re = L.run_r3_reentry(df, bars5, r3)
print("counts r3=%d vs=%d tlbhyb=%d tlbpiv=%d r3re=%d rss=%.0f"
      % (len(r3), len(vs), len(tlb_hyb), len(tlb_piv), len(r3re), L.rss_mb()), flush=True)

allm = r3 + vs + tlb_hyb + tlb_piv + r3re
rows = []
for t in allm:
    rows.append(dict(sleeve=t["sleeve"], entry_utc=str(t["entry_utc"]), exit_utc=str(t["exit_utc"]),
                     entry_px=t["entry_px"], exit_px=t["exit_px"], exit_reason=t["exit_reason"],
                     pnl_points=t["pnl_points"], R=t.get("R"),
                     direction=t.get("direction","long")))
pd.DataFrame(rows).sort_values("entry_utc").to_csv("/root/v9/bt/_rec_model.csv", index=False)
print("wrote _rec_model.csv", len(rows), flush=True)
