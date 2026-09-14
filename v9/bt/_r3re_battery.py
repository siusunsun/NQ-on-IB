# -*- coding: utf-8 -*-
"""Decision battery for the R3 re-entry overlay on OUR data.

A = pure spec (max_wait=None)  -- identical to kristov v9_reentry.py AND our paper bot
B = our live variant (max_wait=48)

Judged BOTH standalone and as a MARGINAL addition to the deployed 3-sleeve book.
Cost $5.40/MNQ round-turn. Decision on PORTFOLIO basis.
"""
import sys, math, importlib.util
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np, pandas as pd

BT = "/root/v9/bt/"
RT = 5.40
CAP = 22500.0
rng = np.random.default_rng(20260907)


def sleeve(tag, qty):
    d = pd.read_csv(BT + "_hist_tr_%s.csv" % tag)
    c = [x for x in d.columns
         if "entry" in x.lower() and any(k in x.lower() for k in ("date", "utc", "time"))][0]
    t = pd.to_datetime(d[c], utc=True, errors="coerce")
    if t.isna().all():
        t = pd.to_datetime(d[c], errors="coerce").dt.tz_localize("UTC")
    d["dt"] = t.dt.tz_convert("America/New_York").dt.date
    p = [x for x in d.columns if x.lower() in ("pnl_usd", "pnl_usd_1mnq", "net_usd", "usd")][0]
    d["net"] = d[p] * qty - RT * qty
    return d.groupby("dt")["net"].sum()


VL = sleeve("VWAP_LONG_R3", 2)
VS = sleeve("VWAP_SHORT_R1", 1)
TL = sleeve("TLB_HYB40", 1)

_s = importlib.util.spec_from_file_location("_ra_common", BT + "_ra_common.py")
C = importlib.util.module_from_spec(_s)
sys.modules["_ra_common"] = C
_s.loader.exec_module(C)
head = open(BT + "_ra_r3re_sweep.py", encoding="utf-8").read().split("rows = []")[0]
ns = {"__name__": "_p"}
exec(compile(head, "_h", "exec"), ns)

print("R3 re-entry fires by max_wait (1 MNQ):")
FIRES = {}
for mw in (None, 12, 24, 48):
    f = ns["run"](wait=2, stop_src="si", magnet="adaptive", max_wait=mw)
    FIRES[mw] = pd.DataFrame(f, columns=["yr", "gross", "dt"])
    g = sum(x[1] for x in f)
    print(f"  max_wait={str(mw):>4}: {len(f):3d} fires  gross ${g:+,.0f}  net ${g-RT*len(f):+,.0f}", flush=True)


def daily(fr, k=1.0):
    return (fr["gross"] - k * RT).groupby(fr["dt"]).sum()


allidx = set(VL.index) | set(VS.index) | set(TL.index)
for mw in FIRES:
    allidx |= set(FIRES[mw]["dt"])
start, end = min(allidx), max(allidx)
I = pd.Index(sorted(set(pd.bdate_range(start, end).date) | allidx))
al = lambda s: s.reindex(I).fillna(0.0)
BOOK = al(VL) + al(VS) + al(TL)
YRS = (pd.Timestamp(I[-1]) - pd.Timestamp(I[0])).days / 365.25


def stats(x):
    x = np.asarray(x, float)
    cu = np.cumsum(x)
    dd = cu - np.maximum.accumulate(cu)
    nz = x[x != 0]
    w = nz[nz > 0]
    l = nz[nz < 0]
    neg = x[x < 0]
    net = cu[-1]
    mdd = abs(dd.min())
    return dict(net=net, cagr=((CAP + net) / CAP) ** (1 / YRS) - 1,
                sharpe=x.mean() / x.std(ddof=1) * math.sqrt(252),
                sortino=(x.mean() / neg.std(ddof=1) * math.sqrt(252)) if len(neg) > 1 else 0.0,
                pf=(w.sum() / abs(l.sum())) if len(l) else 99.0,
                mdd=mdd, retdd=(net / mdd) if mdd else 0.0)


print("\n" + "=" * 92)
print("1) MARGINAL VALUE TO THE DEPLOYED BOOK  (portfolio basis, cost 1x, capital $22,500)")
b = stats(BOOK)
print(f'  {"variant":<28}{"net":>10}{"CAGR":>8}{"Sharpe":>8}{"maxDD":>10}{"ret/DD":>8}{"PF":>7}')
print(f'  {"BOOK (3 sleeves)":<28}${b["net"]:>9,.0f}{100*b["cagr"]:>7.1f}%{b["sharpe"]:>8.2f}${b["mdd"]:>9,.0f}{b["retdd"]:>8.2f}{b["pf"]:>7.2f}')
for mw, lbl in ((None, "A pure spec (his+paper)"), (48, "B ours max_wait=48"), (24, "  max_wait=24")):
    s = stats(BOOK + al(daily(FIRES[mw])))
    print(f'  {"+R3_RE "+lbl:<28}${s["net"]:>9,.0f}{100*s["cagr"]:>7.1f}%{s["sharpe"]:>8.2f}${s["mdd"]:>9,.0f}{s["retdd"]:>8.2f}{s["pf"]:>7.2f}   dNet ${s["net"]-b["net"]:+,.0f}  dDD ${s["mdd"]-b["mdd"]:+,.0f}')

print("\n" + "=" * 92)
print("2) WALK-FORWARD: can max_wait be chosen honestly? (24m train -> 6m test, grid incl. None)")
mo = pd.Series(pd.to_datetime(I).to_period("M"), index=I)
months = pd.period_range(mo.min(), mo.max(), freq="M")
S = {mw: al(daily(FIRES[mw])) for mw in FIRES}
oos = {mw: [] for mw in FIRES}
oos["WF"] = []
picks = []
i = 24
while i + 6 <= len(months):
    tr = months[i - 24:i]
    te = months[i:i + 6]
    mtr = mo.isin(tr).values
    mte = mo.isin(te).values
    best, bs = None, -1e18
    for mw in FIRES:
        v = S[mw].values[mtr].sum()
        if v > bs:
            bs, best = v, mw
    picks.append((str(te[0]), str(best), round(S[best].values[mte].sum())))
    for mw in FIRES:
        oos[mw].append(S[mw].values[mte])
    oos["WF"].append(S[best].values[mte])
    i += 6
print("  fold picks: " + ", ".join(f"{f}:{p}(${v:+,.0f})" for f, p, v in picks))
for k in (None, 12, 24, 48, "WF"):
    v = np.concatenate(oos[k])
    print(f"    OOS max_wait={str(k):>4}: ${v.sum():+9,.0f}")
wf = np.concatenate(oos["WF"]).sum()
pure = np.concatenate(oos[None]).sum()
print(f"  -> honest WF selection ${wf:+,.0f} vs pure-spec ${pure:+,.0f}  ==> " + ("max_wait IS selectable" if wf > pure else "NO, pure spec wins OOS"))

print("\n" + "=" * 92)
print("3) ERA SPLIT")
half = I[len(I) // 2]
for mw, lbl in ((None, "A pure"), (48, "B mw48")):
    s = al(daily(FIRES[mw]))
    e1 = s[s.index <= half]
    e2 = s[s.index > half]
    print(f"  {lbl:<8} era1 (..{half}): ${e1.sum():+8,.0f}    era2 ({half}..): ${e2.sum():+8,.0f}")

print("\n" + "=" * 92)
print("4) COST SENSITIVITY (marginal dNet to the book)")
for mw, lbl in ((None, "A pure"), (48, "B mw48")):
    fr = FIRES[mw]
    row = []
    for k in (1, 2, 3):
        d = (fr["gross"] - k * RT).groupby(fr["dt"]).sum()
        row.append(stats(BOOK + al(d))["net"] - b["net"])
    print(f"  {lbl:<8} 1x ${row[0]:+8,.0f}   2x ${row[1]:+8,.0f}   3x ${row[2]:+8,.0f}")

print("\n" + "=" * 92)
print("5) CONCENTRATION (standalone net, dropping the best N fires)")
for mw, lbl in ((None, "A pure"), (48, "B mw48")):
    fr = FIRES[mw].copy()
    fr["net"] = fr["gross"] - RT
    for n in (0, 3, 5, 10):
        keep = fr.drop(fr["net"].nlargest(n).index) if n else fr
        print(f"  {lbl:<8} drop-top-{n:<3} ${keep['net'].sum():+8,.0f}" + ("   <-- full" if n == 0 else ""))

print("\n" + "=" * 92)
print("6) BOOTSTRAP CI on standalone net (10k resamples of the fire list)")
for mw, lbl in ((None, "A pure"), (48, "B mw48")):
    v = (FIRES[mw]["gross"] - RT).values
    bs = np.array([rng.choice(v, size=len(v), replace=True).sum() for _ in range(10000)])
    print(f"  {lbl:<8} net ${v.sum():+,.0f}   95% CI [${np.percentile(bs,2.5):+,.0f} , ${np.percentile(bs,97.5):+,.0f}]   P(net<=0) {(bs<=0).mean():.3f}")

print("\n" + "=" * 92)
print("7) PER-YEAR BREADTH (standalone net)")
for mw, lbl in ((None, "A pure"), (48, "B mw48")):
    fr = FIRES[mw].copy()
    fr["net"] = fr["gross"] - RT
    g = fr.groupby("yr")["net"].agg(["count", "sum"])
    print(f"  {lbl:<8} " + "  ".join(f"{y}:{r['sum']:+.0f}(n{int(r['count'])})" for y, r in g.iterrows()))
    print(f"           positive years {(g['sum']>0).sum()}/{len(g)}")
