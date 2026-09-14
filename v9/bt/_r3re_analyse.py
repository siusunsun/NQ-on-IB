# -*- coding: utf-8 -*-
"""Phase 2: decision battery for the R3 re-entry overlay, from the dumped fire lists.
Light (<100MB) - reads CSVs only, no archive.

A = pure spec (max_wait=None)  == kristov v9_reentry.py == our paper bot
B = our live variant (max_wait=48)

Decision on PORTFOLIO basis (marginal value to the deployed 3-sleeve book), cost $5.40/MNQ RT.
"""
import sys, math, resource
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np, pandas as pd

BT = "/root/v9/bt/"
RT = 5.40
CAP = 22500.0
rng = np.random.default_rng(20260907)


def sleeve(tag, qty):
    d = pd.read_csv(BT + f"_hist_tr_{tag}.csv")
    c = [x for x in d.columns
         if "entry" in x.lower() and any(k in x.lower() for k in ("date", "utc", "time"))][0]
    t = pd.to_datetime(d[c], utc=True, errors="coerce")
    if t.isna().all():
        t = pd.to_datetime(d[c], errors="coerce").dt.tz_localize("UTC")
    d["dt"] = t.dt.tz_convert("America/New_York").dt.date
    p = [x for x in d.columns if x.lower() in ("pnl_usd", "pnl_usd_1mnq", "net_usd", "usd")][0]
    d["net"] = d[p] * qty - RT * qty
    return d.groupby("dt")["net"].sum()


VL, VS, TL = sleeve("VWAP_LONG_R3", 2), sleeve("VWAP_SHORT_R1", 1), sleeve("TLB_HYB40", 1)

KEYS = ["none", "12", "24", "48"]
FIRES = {}
for k in KEYS:
    f = pd.read_csv(BT + f"_r3re_fires_mw_{k}.csv")
    f["dt"] = pd.to_datetime(f["dt"]).dt.date
    FIRES[k] = f
    print(f"  max_wait={k:>4}: {len(f):3d} fires  gross ${f.gross.sum():+,.0f}  net ${f.gross.sum()-RT*len(f):+,.0f}")

daily = lambda fr, k=1.0: (fr["gross"] - k * RT).groupby(fr["dt"]).sum()

allidx = set(VL.index) | set(VS.index) | set(TL.index)
for k in KEYS:
    allidx |= set(FIRES[k]["dt"])
I = pd.Index(sorted(set(pd.bdate_range(min(allidx), max(allidx)).date) | allidx))
al = lambda s: s.reindex(I).fillna(0.0)
BOOK = al(VL) + al(VS) + al(TL)
YRS = (pd.Timestamp(I[-1]) - pd.Timestamp(I[0])).days / 365.25


def stats(x):
    x = np.asarray(x, float)
    cu = np.cumsum(x)
    dd = cu - np.maximum.accumulate(cu)
    nz = x[x != 0]; w = nz[nz > 0]; l = nz[nz < 0]; neg = x[x < 0]
    net, mdd = cu[-1], abs(dd.min())
    return dict(net=net, cagr=((CAP + net) / CAP) ** (1 / YRS) - 1,
                sharpe=x.mean() / x.std(ddof=1) * math.sqrt(252),
                pf=(w.sum() / abs(l.sum())) if len(l) else 99.0,
                mdd=mdd, retdd=(net / mdd) if mdd else 0.0)


LBL = {"none": "A pure spec (his+paper)", "48": "B ours max_wait=48",
       "24": "  max_wait=24", "12": "  max_wait=12"}

print("\n" + "=" * 96)
print(f"1) MARGINAL VALUE TO THE DEPLOYED BOOK  (portfolio basis, 1x cost, capital ${CAP:,.0f}, {YRS:.2f} yrs)")
b = stats(BOOK)
print(f'  {"variant":<28}{"net":>10}{"CAGR":>8}{"Sharpe":>8}{"maxDD":>10}{"ret/DD":>8}{"PF":>7}')
print(f'  {"BOOK (3 sleeves)":<28}${b["net"]:>9,.0f}{100*b["cagr"]:>7.1f}%{b["sharpe"]:>8.2f}${b["mdd"]:>9,.0f}{b["retdd"]:>8.2f}{b["pf"]:>7.2f}')
for k in ["none", "48", "24", "12"]:
    s = stats(BOOK + al(daily(FIRES[k])))
    print(f'  {"+R3RE " + LBL[k]:<28}${s["net"]:>9,.0f}{100*s["cagr"]:>7.1f}%{s["sharpe"]:>8.2f}'
          f'${s["mdd"]:>9,.0f}{s["retdd"]:>8.2f}{s["pf"]:>7.2f}   dNet ${s["net"]-b["net"]:+,.0f}  dDD ${s["mdd"]-b["mdd"]:+,.0f}')

print("\n" + "=" * 96)
print("2) WALK-FORWARD: is max_wait honestly selectable? (24m train -> 6m test, grid includes None)")
mo = pd.Series(pd.to_datetime(I).to_period("M"), index=I)
months = pd.period_range(mo.min(), mo.max(), freq="M")
S = {k: al(daily(FIRES[k])) for k in KEYS}
oos = {k: [] for k in KEYS}; oos["WF"] = []; picks = []
i = 24
while i + 6 <= len(months):
    tr, te = months[i - 24:i], months[i:i + 6]
    mtr, mte = mo.isin(tr).values, mo.isin(te).values
    best = max(KEYS, key=lambda k: S[k].values[mtr].sum())
    picks.append((str(te[0]), best, S[best].values[mte].sum()))
    for k in KEYS:
        oos[k].append(S[k].values[mte])
    oos["WF"].append(S[best].values[mte])
    i += 6
print("  fold picks: " + ", ".join(f"{f}->mw{p}(${v:+,.0f})" for f, p, v in picks))
for k in KEYS + ["WF"]:
    print(f"    OOS max_wait={k:>4}: ${np.concatenate(oos[k]).sum():+9,.0f}")
wf, pure = np.concatenate(oos["WF"]).sum(), np.concatenate(oos["none"]).sum()
print(f"  -> honest WF ${wf:+,.0f} vs pure spec ${pure:+,.0f}  ==> "
      + ("max_wait IS selectable OOS" if wf > pure else "NO - pure spec wins OOS, the cap is in-sample only"))

print("\n" + "=" * 96)
print("3) ERA SPLIT (standalone net)")
half = I[len(I) // 2]
for k in ["none", "48"]:
    s = al(daily(FIRES[k]))
    print(f"  {LBL[k]:<26} era1 (..{half}): ${s[s.index<=half].sum():+8,.0f}    era2 ({half}..): ${s[s.index>half].sum():+8,.0f}")

print("\n" + "=" * 96)
print("4) COST SENSITIVITY (marginal dNet to the book)")
for k in ["none", "48"]:
    fr = FIRES[k]
    row = [stats(BOOK + al((fr["gross"] - c * RT).groupby(fr["dt"]).sum()))["net"] - b["net"] for c in (1, 2, 3)]
    print(f"  {LBL[k]:<26} 1x ${row[0]:+8,.0f}   2x ${row[1]:+8,.0f}   3x ${row[2]:+8,.0f}")

print("\n" + "=" * 96)
print("5) CONCENTRATION (standalone net, dropping best N fires)")
for k in ["none", "48"]:
    fr = FIRES[k].copy(); fr["net"] = fr["gross"] - RT
    line = "  ".join(f"drop{n}: ${(fr.drop(fr['net'].nlargest(n).index) if n else fr)['net'].sum():+,.0f}"
                     for n in (0, 3, 5, 10))
    print(f"  {LBL[k]:<26} {line}")

print("\n" + "=" * 96)
print("6) BOOTSTRAP CI on standalone net (10k resamples)")
for k in ["none", "48"]:
    v = (FIRES[k]["gross"] - RT).values
    bs = np.array([rng.choice(v, size=len(v), replace=True).sum() for _ in range(10000)])
    print(f"  {LBL[k]:<26} net ${v.sum():+,.0f}  95% CI [${np.percentile(bs,2.5):+,.0f}, ${np.percentile(bs,97.5):+,.0f}]  P(net<=0) {(bs<=0).mean():.3f}")

print("\n" + "=" * 96)
print("7) PER-YEAR BREADTH (standalone net)")
for k in ["none", "48"]:
    fr = FIRES[k].copy(); fr["net"] = fr["gross"] - RT
    g = fr.groupby("yr")["net"].agg(["count", "sum"])
    print(f"  {LBL[k]:<26} " + "  ".join(f"{y}:{r['sum']:+.0f}(n{int(r['count'])})" for y, r in g.iterrows()))
    print(f"  {'':<26} positive years {(g['sum']>0).sum()}/{len(g)}")

print("\n" + "=" * 96)
print("8) DAY-OVERLAP with the parent sleeve (is this new risk or doubling down?)")
vldays = set(VL.index)
for k in ["none", "48"]:
    d = set(FIRES[k]["dt"])
    print(f"  {LBL[k]:<26} fires on {len(d)} days; {len(d & vldays)} ({100*len(d&vldays)/len(d):.0f}%) are days VWAP_LONG_R3 also traded")

print(f"\npeakRSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024}MB")
