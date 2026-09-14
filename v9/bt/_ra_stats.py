"""_ra_stats.py - cost sensitivity, per-year era stability, per-year correlation,
DSR, and deployed-config fold stability from the existing _hist_tr_*.csv trade files.
READ-ONLY.
"""
import sys, importlib.util, json
import numpy as np, pandas as pd
_s = importlib.util.spec_from_file_location("_ra_common", "/root/v9/bt/_ra_common.py")
C = importlib.util.module_from_spec(_s); sys.modules["_ra_common"] = C; _s.loader.exec_module(C)

SLEEVES = ["VWAP_LONG_R3", "VWAP_SHORT_R1", "TLB_HYB40", "R3_RE"]
SIZE = {"VWAP_LONG_R3": 2, "VWAP_SHORT_R1": 2, "TLB_HYB40": 1, "R3_RE": 1}

def load(s):
    d = pd.read_csv("/root/v9/bt/_hist_tr_%s.csv" % s, parse_dates=["entry_utc", "exit_utc"])
    et = pd.to_datetime(d["entry_utc"], utc=True).dt.tz_convert("America/New_York")
    d["date"] = et.dt.date
    d["year"] = et.dt.year
    return d.sort_values("entry_utc").reset_index(drop=True)

T = {s: load(s) for s in SLEEVES}

print("=" * 100)
print("A. BASELINE (1 MNQ, gross) vs reference")
for s in SLEEVES:
    d = T[s]
    print("  %-14s n=%-4d gross=$%-9s PF=%.3f   span %s -> %s"
          % (s, len(d), format(d.pnl_usd.sum(), ",.0f"), C.pf(d.pnl_usd.values),
             d.date.min(), d.date.max()))

print("\n" + "=" * 100)
print("B. COST SENSITIVITY  (cost per round-turn per MNQ contract; 1x=$%.2f)" % C.COST_RT)
print("   %-14s %-8s | %-28s | %-28s | %-28s" % ("sleeve", "n", "1x", "2x", "3x"))
cost_rows = []
for s in SLEEVES:
    d = T[s]; pnl = d.pnl_usd.values
    cells = []
    for m in (1, 2, 3):
        b = C.stat_block(pnl, cost=C.COST_RT * m, mult=1)
        cells.append("net=$%-8s PF=%-5.2f avg=$%-6.1f" % (format(b["net"], ",.0f"), b["pf"], b["avg"]))
        cost_rows.append(dict(sleeve=s, mult=m, net=b["net"], pf=b["pf"], avg=b["avg"], sr=b["sr"]))
    print("   %-14s %-8d | %s | %s | %s" % (s, len(d), cells[0], cells[1], cells[2]))

print("\n   at LIVE size (R3x2 SHORTx2 TLBx1 R3REx1), cost scales with contracts:")
for m in (1, 2, 3):
    tot = 0
    for s in SLEEVES:
        b = C.stat_block(T[s].pnl_usd.values, cost=C.COST_RT * m, mult=SIZE[s])
        tot += b["net"]
    print("     %dx cost -> book net = $%s" % (m, format(tot, ",.0f")))

print("\n" + "=" * 100)
print("C. PER-CALENDAR-YEAR EXPECTANCY  (1 MNQ, NET at 1x cost)")
yrs = sorted({y for s in SLEEVES for y in T[s].year.unique()})
hdr = "   %-14s" % "sleeve" + "".join("%14s" % y for y in yrs)
print(hdr)
for s in SLEEVES:
    d = T[s]; line = "   %-14s" % s
    for y in yrs:
        p = d[d.year == y].pnl_usd.values
        if len(p) == 0:
            line += "%14s" % "-"
        else:
            b = C.stat_block(p)
            line += "%14s" % ("%d/$%s" % (b["n"], format(b["net"], ",.0f")))
    print(line)
print("\n   per-year avg $/trade (net 1x):")
print(hdr)
for s in SLEEVES:
    d = T[s]; line = "   %-14s" % s
    for y in yrs:
        p = d[d.year == y].pnl_usd.values
        line += "%14s" % ("-" if len(p) == 0 else "%.1f" % C.stat_block(p)["avg"])
    print(line)

print("\n" + "=" * 100)
print("D. DEPLOYED-CONFIG FOLD STABILITY (5 equal-count folds, chronological; net 1x)")
for s in SLEEVES:
    d = T[s]; pnl = d.pnl_usd.values; n = len(pnl)
    ks = np.array_split(np.arange(n), 5)
    line = "   %-14s" % s
    for f in ks:
        b = C.stat_block(pnl[f])
        line += "  [n=%-3d $%-7s PF %.2f]" % (b["n"], format(b["net"], ",.0f"), b["pf"])
    print(line)
    print("      dates: " + " | ".join("%s..%s" % (d.date.iloc[f[0]], d.date.iloc[f[-1]]) for f in ks))

print("\n" + "=" * 100)
print("E. DEFLATED SHARPE (per-trade SR; N_trials from the audit grid)")
# trial counts = size of the parameter search space plausibly explored per sleeve
NTRIALS = {"VWAP_LONG_R3": 81, "VWAP_SHORT_R1": 81, "TLB_HYB40": 36, "R3_RE": 14}
sweep_var = {}
try:
    fg = pd.read_csv("/root/v9/bt/_ra_fullgrid.csv")
    for s in fg.sleeve.unique():
        v = fg[fg.sleeve == s].sr.values
        sweep_var[s] = float(np.var(v, ddof=1))
        NTRIALS[s] = int((fg.sleeve == s).sum())
except Exception as e:
    print("   (full grid not ready: %s)" % e)
try:
    sw3 = pd.read_csv("/root/v9/bt/_ra_r3re_sweep.csv")
    sweep_var["R3_RE"] = float(np.var(sw3.sr.values, ddof=1))
    NTRIALS["R3_RE"] = len(sw3)
except Exception:
    pass

def _skew(x):
    x = np.asarray(x, float); m = x.mean(); s = x.std(ddof=0)
    return float(((x - m) ** 3).mean() / s ** 3)
def _kurt(x):
    x = np.asarray(x, float); m = x.mean(); s = x.std(ddof=0)
    return float(((x - m) ** 4).mean() / s ** 4)
for s in SLEEVES:
    x = T[s].pnl_usd.values - C.COST_RT
    n = len(x); sr = x.mean() / x.std(ddof=1)
    sk = _skew(x); ku = _kurt(x)
    v = sweep_var.get(s)
    if v is None:
        print("   %-14s SR/trade=%.4f n=%d skew=%.2f kurt=%.2f  [no trial variance yet]" % (s, sr, n, sk, ku))
        continue
    sr0, D = C.dsr(sr, NTRIALS[s], v, n, sk, ku)
    print("   %-14s SR/trade=%.4f  ann~%.2f  n=%d skew=%.2f kurt=%.2f  Ntrials=%d varSR=%.5f  SR0=%.4f  DSR=%.4f %s"
          % (s, sr, sr * np.sqrt(n / 5.66), n, sk, ku, NTRIALS[s], v, sr0, D, "PASS" if D >= 0.95 else "FAIL"))

print("\n" + "=" * 100)
print("F. PER-YEAR PAIRWISE CORRELATION of DAILY net P&L (1 MNQ)")
daily = {}
for s in SLEEVES:
    daily[s] = T[s].groupby("date").pnl_usd.sum()
allidx = sorted(set().union(*[set(v.index) for v in daily.values()]))
D = pd.DataFrame({s: daily[s].reindex(allidx).fillna(0.0) for s in SLEEVES}, index=pd.to_datetime(allidx))
for y in yrs:
    sub = D[D.index.year == y]
    if len(sub) < 20:
        print("   %d: too few days (%d)" % (y, len(sub))); continue
    c = sub.corr()
    prs = []
    for i in range(len(SLEEVES)):
        for j in range(i + 1, len(SLEEVES)):
            a, b = SLEEVES[i], SLEEVES[j]
            prs.append("%s/%s=%+.2f" % (a[:6], b[:6], c.loc[a, b]))
    print("   %d (d=%-4d): %s" % (y, len(sub), "  ".join(prs)))
print("\n   FULL-SAMPLE:")
c = D.corr()
for i in range(len(SLEEVES)):
    for j in range(i + 1, len(SLEEVES)):
        print("     %-14s / %-14s  %+.3f" % (SLEEVES[i], SLEEVES[j], c.iloc[i, j]))

print("\n   BOOK equity (live sizes, net 1x): ")
bk = sum(D[s] * SIZE[s] for s in SLEEVES)
# subtract cost per trade
cost_by_day = sum(T[s].groupby("date").size().reindex(allidx).fillna(0) * C.COST_RT * SIZE[s] for s in SLEEVES)
cost_by_day.index = pd.to_datetime(allidx)
bkn = bk - cost_by_day
cum = bkn.cumsum()
print("     net=$%s  maxDD=$%s  ret/DD=%.2f  days=%d" %
      (format(bkn.sum(), ",.0f"), format(C.maxdd(cum.values), ",.0f"),
       bkn.sum() / abs(C.maxdd(cum.values)) if C.maxdd(cum.values) else float("nan"), len(bkn)))
for y in yrs:
    sub = bkn[bkn.index.year == y]
    if len(sub):
        print("     %d: $%s" % (y, format(sub.sum(), ",.0f")))
print("DONE")
