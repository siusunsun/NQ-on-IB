"""_hist_verify.py - (1) prove the fast sim_vwap == original sim_vwap on the live 2-yr data;
(2) report a 4-sleeve book variant. READ-ONLY."""
import sys, importlib.util, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd, pytz
ET = pytz.timezone("America/New_York")
sys.path.insert(0, "/root/v9/bt"); sys.path.insert(0, "/root/v9")

def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p); m = importlib.util.module_from_spec(s)
    sys.modules[n] = m; s.loader.exec_module(m); return m

print("=== EQUIVALENCE: original bt_nq.sim_vwap vs _hist fast sim_vwap (LIVE dir, 2-yr) ===", flush=True)
A = _load("bt_nq", "/root/v9/bt/bt_nq.py")                 # original loop
B = _load("bt_nq2", "/root/v9/bt/_hist_bt_nq.py")          # fast version
B.NQ_DIR = Path("/root/v9/data_1m/NQ")                     # point BOTH at live data
df = A.load_all()
daily, reg = A.build_daily_and_regime(df)
bars5 = A.build_5min(df)
print("  live bars5=%d" % len(bars5), flush=True)
ta, _ = A.run_vwap_tlb(df, bars5, reg)
tb, _ = B.run_vwap_tlb(df, bars5, reg)
ok = True
for s in ("VWAP_LONG_R3", "VWAP_SHORT_R1"):
    a = pd.DataFrame(ta[s])[["entry_utc", "exit_utc", "entry_px", "exit_px", "exit_reason", "pnl_points"]]
    b = pd.DataFrame(tb[s])[["entry_utc", "exit_utc", "entry_px", "exit_px", "exit_reason", "pnl_points"]]
    same = a.equals(b)
    ok &= same
    print("  %-14s orig n=%d  fast n=%d  IDENTICAL=%s  net orig=%.2fpt fast=%.2fpt"
          % (s, len(a), len(b), same, a.pnl_points.sum(), b.pnl_points.sum()), flush=True)
print("  OVERALL EQUIVALENCE: %s" % ("PASS" if ok else "*** FAIL ***"), flush=True)

# ---------- 4-sleeve book ----------
print("\n=== 4-SLEEVE BOOK (R3x2 + SHORTx2 + TLB_HYB40x1 + R3_REx1), full history ===", flush=True)
Bd = "/root/v9/bt/"
D = {}
for s in ["VWAP_LONG_R3", "VWAP_SHORT_R1", "TLB_HYB40", "R3_RE"]:
    d = pd.read_csv(Bd + "_hist_tr_%s.csv" % s, parse_dates=["entry_utc"])
    d["date"] = pd.to_datetime(d["entry_utc"], utc=True).dt.tz_convert(ET).dt.date
    D[s] = d

def ds(s, q):
    g = D[s].groupby("date")["pnl_usd"].sum() * q
    g.index = pd.to_datetime(list(g.index)); return g

spec = [("VWAP_LONG_R3", 2), ("VWAP_SHORT_R1", 2), ("TLB_HYB40", 1), ("R3_RE", 1)]
parts = [ds(s, q) for s, q in spec]
idx = sorted(set().union(*[set(p.index) for p in parts]))
ser = pd.Series(0.0, index=pd.DatetimeIndex(idx))
for p in parts:
    ser = ser.add(p.reindex(ser.index).fillna(0.0), fill_value=0.0)
r = ser.reindex(pd.bdate_range(ser.index.min(), ser.index.max())).fillna(0.0)
cum = r.cumsum(); dd = cum - cum.cummax(); mdd = dd.min()
yrs = (r.index[-1] - r.index[0]).days / 365.25
neg = r[r < 0]
gp = r[r > 0].sum(); gl = -r[r < 0].sum()
print("  net=${:,.0f}  ann=${:,.0f}/yr  Sharpe={:.2f}  Sortino={:.2f}  PF={:.3f}  maxDD=${:,.0f}  ret/DD={:.2f}".format(
    r.sum(), r.sum() / yrs, r.mean() / r.std() * np.sqrt(252), r.mean() / neg.std() * np.sqrt(252),
    gp / gl, mdd, r.sum() / abs(mdd)))
m = r.resample("ME").sum()
print("  monthly mean=${:,.0f} median=${:,.0f} %pos={:.1f}%  best={:.1f}% of profit  top3={:.1f}%".format(
    m.mean(), m.median(), 100 * (m > 0).mean(), 100 * m.max() / m.sum(), 100 * m.nlargest(3).sum() / m.sum()))
print("  per-year net$: " + "  ".join("{}: ${:,.0f}".format(y, r[r.index.year == y].sum()) for y in range(2021, 2027)))
for lbl, sub in [("2yr 2024-08+", r[r.index >= "2024-08-01"]), ("early <2024-08", r[r.index < "2024-08-01"])]:
    y2 = (sub.index[-1] - sub.index[0]).days / 365.25
    print("  {:<16} net=${:,.0f}  ann=${:,.0f}  Sharpe={:.2f}".format(lbl, sub.sum(), sub.sum() / y2,
                                                                     sub.mean() / sub.std() * np.sqrt(252)))
