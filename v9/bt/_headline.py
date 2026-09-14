# -*- coding: utf-8 -*-
"""Recompute NQ headline stats using the SAME components the dashboard pipeline uses,
so the card and the published monthly series agree.
Earlier figures used _r3v_fires.csv (uncapped R3); the pipeline uses max_wait=48."""
import sys, math, importlib.util
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd
BT='/root/v9/bt/'; RT=5.40; CAP=22500.0

def load(tag, qty):
    d = pd.read_csv(BT+f'_hist_tr_{tag}.csv')
    c = [x for x in d.columns if 'entry' in x.lower() and ('date' in x.lower() or 'utc' in x.lower() or 'time' in x.lower())][0]
    t = pd.to_datetime(d[c], utc=True, errors='coerce')
    if t.isna().all(): t = pd.to_datetime(d[c], errors='coerce').dt.tz_localize('UTC')
    d['dt'] = t.dt.tz_convert('America/New_York').dt.date
    p = [x for x in d.columns if x.lower() in ('pnl_usd','pnl_usd_1mnq','net_usd','usd')][0]
    d['net'] = d[p]*qty - RT*qty
    return d.groupby('dt')['net'].sum()

VL = load('VWAP_LONG_R3', 2); VS = load('VWAP_SHORT_R1', 1); TL = load('TLB_HYB40', 1)

# R3 at max_wait=48 — the pipeline's setting
_s = importlib.util.spec_from_file_location("_ra_common", BT+"_ra_common.py")
C = importlib.util.module_from_spec(_s); sys.modules["_ra_common"] = C; _s.loader.exec_module(C)
head = open(BT+"_ra_r3re_sweep.py", encoding="utf-8").read().split("rows = []")[0]
ns = {"__name__": "_p"}; exec(compile(head, "_h", "exec"), ns)
fires = ns["run"](wait=2, stop_src="si", magnet="adaptive", max_wait=48)
fr = pd.DataFrame(fires, columns=["yr","usd","dt"])
R3 = (fr["usd"] - RT).groupby(fr["dt"]).sum()
print(f"R3 (max_wait=48): {len(fr)} fires")

start = min(x.index.min() for x in [VL,VS,TL,R3]); end = max(x.index.max() for x in [VL,VS,TL,R3])
I = pd.Index(sorted(set(pd.bdate_range(start,end).date)|set(VL.index)|set(VS.index)|set(TL.index)|set(R3.index)))
I = pd.Index([d for d in I if start <= d <= end])
al = lambda s: s.reindex(I).fillna(0.0)
B = al(VL)+al(VS)+al(TL)+al(R3)
x = np.asarray(B,float); cu = np.cumsum(x); dd = cu-np.maximum.accumulate(cu)
yrs = (pd.Timestamp(end)-pd.Timestamp(start)).days/365.25
sd = x.std(ddof=1); neg = x[x<0]; nz = x[x!=0]; w = nz[nz>0]; l = nz[nz<0]
net = cu[-1]; mdd = abs(dd.min())
print(f"\nwindow {I.min()} .. {I.max()}  ({yrs:.2f} yrs)  capital ${CAP:,.0f}")
print(f"  net            ${net:,.0f}")
print(f"  cumulative     {100*net/CAP:.1f}%")
print(f"  CAGR           {100*(((CAP+net)/CAP)**(1/yrs)-1):.1f}%")
print(f"  Sharpe         {x.mean()/sd*math.sqrt(252):.2f}")
print(f"  Sortino        {x.mean()/neg.std(ddof=1)*math.sqrt(252):.2f}")
print(f"  Profit factor  {w.sum()/abs(l.sum()):.2f}")
print(f"  maxDD          ${-mdd:,.0f}  ({100*mdd/CAP:.1f}% of capital)")
print(f"  Return/DD      {net/mdd:.2f}")
print(f"\nper-sleeve net: VWAP_R3x2 ${al(VL).sum():,.0f} | short ${al(VS).sum():,.0f} | "
      f"TLB ${al(TL).sum():,.0f} | R3 ${al(R3).sum():,.0f}")
mr = al(VL).sum()+al(VS).sum()+al(R3).sum()
print(f"mean-reversion share of gross P&L: {100*mr/(mr+al(TL).sum()):.0f}%  (TLB breakout is the rest)")
