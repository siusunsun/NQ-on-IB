"""_ra_gate.py - does the V9 regime cell gate add value on 5.6 years?
Compares GATED (deployed) vs UNGATED (sleeve trades every day) per sleeve and at book level.
READ-ONLY.
"""
import sys, importlib.util
import numpy as np, pandas as pd
_s = importlib.util.spec_from_file_location("_ra_common", "/root/v9/bt/_ra_common.py")
C = importlib.util.module_from_spec(_s); sys.modules["_ra_common"] = C; _s.loader.exec_module(C)

def gated(s):
    d = pd.read_csv("/root/v9/bt/_hist_tr_%s.csv" % s)
    et = pd.to_datetime(d["entry_utc"], utc=True).dt.tz_convert("America/New_York")
    return pd.DataFrame(dict(date=et.dt.date, year=et.dt.year, pnl=d.pnl_usd.values))

def ung(f):
    d = pd.read_csv("/root/v9/bt/_ra_ungated_%s.csv" % f)
    dt = pd.to_datetime(d["et_date"] if "et_date" in d else d["date"])
    return pd.DataFrame(dict(date=dt.dt.date, year=dt.dt.year, pnl=d.pnl_usd.values))

PAIRS = [("VWAP_LONG_R3", "LONG"), ("VWAP_SHORT_R1", "SHORT"), ("TLB_HYB40", "TLB")]
SIZE = {"VWAP_LONG_R3": 2, "VWAP_SHORT_R1": 2, "TLB_HYB40": 1}

print("=" * 104)
print("REGIME-GATE VALUE (net at 1x cost $%.2f/RT, 1 MNQ)" % C.COST_RT)
print("%-15s %-9s %6s %10s %7s %8s %9s | %6s %10s %7s %8s" %
      ("sleeve", "variant", "n", "net", "PF", "$/trade", "maxDD", "n21-23", "net21-23", "PF", "net24-26"))
book = {}
for s, f in PAIRS:
    for tag, d in (("GATED", gated(s)), ("UNGATED", ung(f))):
        d = d.sort_values("date")
        b = C.stat_block(d.pnl.values)
        cum = np.cumsum(d.pnl.values - C.COST_RT)
        e1 = C.stat_block(d[d.year <= 2023].pnl.values)
        e2 = C.stat_block(d[d.year >= 2024].pnl.values)
        print("%-15s %-9s %6d %10s %7.3f %8.1f %9s | %6d %10s %7.3f %8s" %
              (s, tag, b["n"], format(b["net"], ",.0f"), b["pf"], b["avg"],
               format(C.maxdd(cum), ",.0f"), e1["n"], format(e1["net"], ",.0f"), e1["pf"],
               format(e2["net"], ",.0f")))
        book.setdefault(tag, []).append((s, d))
    print("-" * 104)

print("\nBOOK LEVEL (live sizes R3x2 SHORTx2 TLBx1; R3_RE excluded - it inherits the LONG gate):")
for tag in ("GATED", "UNGATED"):
    ds = book[tag]
    alld = sorted(set().union(*[set(d.date) for _s, d in ds]))
    tot = pd.Series(0.0, index=alld)
    for s, d in ds:
        g = d.groupby("date").apply(lambda x: (x.pnl.sum() - C.COST_RT * len(x)) * SIZE[s])
        tot = tot.add(g.reindex(alld).fillna(0.0), fill_value=0.0)
    cum = tot.cumsum()
    dd = C.maxdd(cum.values)
    ntr = sum(len(d) for _s, d in ds)
    print("  %-8s trades=%-5d net=$%-10s maxDD=$%-9s ret/DD=%5.2f  days=%d" %
          (tag, ntr, format(tot.sum(), ",.0f"), format(dd, ",.0f"),
           tot.sum() / abs(dd) if dd else float("nan"), len(alld)))
    yrs = pd.Series(tot.values, index=pd.to_datetime(alld))
    print("     per year: " + "  ".join("%d=$%s" % (y, format(v, ",.0f"))
                                        for y, v in yrs.groupby(yrs.index.year).sum().items()))
print("DONE")
