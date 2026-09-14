"""_ra_dsr2.py - DSR sensitivity: full grid vs 'plausible' sub-grid trial variance,
plus book-level stats and a 2-year vs 5.6-year comparison. READ-ONLY."""
import sys, importlib.util
import numpy as np, pandas as pd
_s = importlib.util.spec_from_file_location("_ra_common", "/root/v9/bt/_ra_common.py")
C = importlib.util.module_from_spec(_s); sys.modules["_ra_common"] = C; _s.loader.exec_module(C)

S = ["VWAP_LONG_R3", "VWAP_SHORT_R1", "TLB_HYB40", "R3_RE"]
def load(s):
    d = pd.read_csv("/root/v9/bt/_hist_tr_%s.csv" % s)
    et = pd.to_datetime(d["entry_utc"], utc=True).dt.tz_convert("America/New_York")
    d["date"] = et.dt.date; d["year"] = et.dt.year
    return d
T = {s: load(s) for s in S}
fg = pd.read_csv("/root/v9/bt/_ra_fullgrid.csv")
r3 = pd.read_csv("/root/v9/bt/_ra_r3re_sweep.csv")

def _sk(x):
    x = np.asarray(x, float); m = x.mean(); sd = x.std(ddof=0); return float(((x-m)**3).mean()/sd**3)
def _ku(x):
    x = np.asarray(x, float); m = x.mean(); sd = x.std(ddof=0); return float(((x-m)**4).mean()/sd**4)

print("DSR sensitivity  (bar = 0.95).  'plausible' grid = configs with 0.4x <= n <= 2.5x deployed n")
print("%-14s %8s %8s | %-34s | %-34s" % ("sleeve", "SR/tr", "n", "FULL GRID", "PLAUSIBLE SUB-GRID"))
for s in S:
    x = T[s].pnl_usd.values - C.COST_RT
    n = len(x); sr = x.mean()/x.std(ddof=1); sk = _sk(x); ku = _ku(x)
    if s == "R3_RE":
        g = r3.copy(); dep_n = 173
    else:
        g = fg[fg.sleeve == s].copy(); dep_n = int(g[g.deployed == 1].n.iloc[0])
    cells = []
    for lab, sub in (("full", g), ("plaus", g[(g.n >= 0.4*dep_n) & (g.n <= 2.5*dep_n)])):
        N = len(sub); v = float(np.var(sub.sr.values, ddof=1)) if N > 1 else 0.0
        sr0, D = C.dsr(sr, max(N, 2), v, n, sk, ku)
        cells.append("N=%-3d varSR=%.5f SR0=%.3f DSR=%.3f" % (N, v, sr0, D))
    print("%-14s %8.4f %8d | %s | %s" % (s, sr, n, cells[0], cells[1]))

print("\n2-YEAR WINDOW (2024-08-27 -> 2026-08-26) vs FULL 5.6yr, net 1x cost, 1 MNQ")
print("%-14s | %-38s | %-38s" % ("sleeve", "last 2 yrs", "full history"))
cut = pd.Timestamp("2024-08-27").date()
for s in S:
    d = T[s]
    for lab, sub in (("2y", d[d.date >= cut]), ("full", d)):
        b = C.stat_block(sub.pnl_usd.values)
        if lab == "2y":
            a = "n=%-4d $%-8s PF %.2f SR/tr %.4f" % (b["n"], format(b["net"], ",.0f"), b["pf"], b["sr"])
        else:
            bb = "n=%-4d $%-8s PF %.2f SR/tr %.4f" % (b["n"], format(b["net"], ",.0f"), b["pf"], b["sr"])
    print("%-14s | %-38s | %-38s" % (s, a, bb))

print("\nLAST-12-MONTHS check (2025-08-27 -> 2026-08-26), net 1x:")
cut2 = pd.Timestamp("2025-08-27").date()
for s in S:
    b = C.stat_block(T[s][T[s].date >= cut2].pnl_usd.values)
    print("   %-14s n=%-4d net=$%-8s PF=%.2f avg=$%.1f" % (s, b["n"], format(b["net"], ",.0f"), b["pf"], b["avg"]))

print("\nROLLING-6-MONTH net (1 MNQ, 1x cost) per sleeve:")
for s in S:
    d = T[s].copy(); d["m"] = pd.to_datetime(d["date"]).dt.to_period("Q")
    g = d.groupby("m").apply(lambda x: round(x.pnl_usd.sum() - C.COST_RT*len(x)))
    print("   %-14s %s" % (s, " ".join("%s:%d" % (k, v) for k, v in g.items())))
print("DONE")
