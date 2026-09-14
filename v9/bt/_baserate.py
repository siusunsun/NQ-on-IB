"""Where does the live experience sit in the backtest's OWN distribution?
Live: 2026-06-24..08-26, ~44 trading days, -$1,906 gross (-$1,456 adjusted for the bug)."""
import numpy as np, pandas as pd, math
BT='/root/v9/bt/'; RT=5.40   # realistic cost
def load(tag,qty):
    d=pd.read_csv(BT+f'_hist_tr_{tag}.csv')
    c=[x for x in d.columns if 'entry' in x.lower() and ('date' in x.lower() or 'utc' in x.lower() or 'time' in x.lower())][0]
    t=pd.to_datetime(d[c],utc=True,errors='coerce')
    if t.isna().all(): t=pd.to_datetime(d[c],errors='coerce').dt.tz_localize('UTC')
    d['dt']=t.dt.tz_convert('America/New_York').dt.date
    p=[x for x in d.columns if x.lower() in ('pnl_usd','pnl_usd_1mnq','net_usd','usd')][0]
    d['net']=d[p]*qty-RT*qty
    return d.groupby('dt')['net'].sum()
VL=load('VWAP_LONG_R3',2); VS=load('VWAP_SHORT_R1',1); TL=load('TLB_HYB40',1)
r=pd.read_csv(BT+'_r3v_fires.csv'); r['dt']=pd.to_datetime(r['entry_date_et']).dt.date
R3=(r['pnl_usd_1mnq']-RT).groupby(r['dt']).sum()
st=min(x.index.min() for x in [VL,VS,TL,R3]); en=max(x.index.max() for x in [VL,VS,TL,R3])
I=pd.Index(sorted(set(pd.bdate_range(st,en).date)|set(VL.index)|set(VS.index)|set(TL.index)|set(R3.index)))
I=pd.Index([d for d in I if st<=d<=en]); al=lambda s:s.reindex(I).fillna(0.0)
# LIVE CONFIG as it actually ran Jun-Aug: VWAP_LONG x2 + TLB x1 (short never fired, R3 not live)
B=al(VL)+al(TL)
x=np.asarray(B,float); W=44
roll=np.array([x[i:i+W].sum() for i in range(len(x)-W+1)])
LIVE=-1906.50; ADJ=-1456.50
print(f"backtest, SAME sleeves that were live (VWAP-Long x2 + TLB x1), realistic cost")
print(f"rolling {W}-day P&L over {len(roll)} windows, 5 years\n")
print(f"  mean   ${roll.mean():+8.0f}")
print(f"  median ${np.median(roll):+8.0f}")
print(f"  worst  ${roll.min():+8.0f}")
print(f"  best   ${roll.max():+8.0f}")
print(f"  % of windows NEGATIVE: {100*(roll<0).mean():.0f}%")
for lbl,v in (('live gross',LIVE),('live adj (ex-bug)',ADJ)):
    pct=100*(roll<=v).mean()
    print(f"\n  {lbl} ${v:+,.0f}  ->  {pct:.0f}th percentile of the backtest's own 2-month windows")
    print(f"     i.e. the backtest had a stretch this bad or worse {pct:.0f}% of the time")
print(f"\n  worst backtest 2-month stretch: ${roll.min():,.0f}  (live is {'INSIDE' if LIVE>roll.min() else 'OUTSIDE'} that envelope)")
# how long do losing stretches last
neg=0; runs=[]
for v in x:
    pass
cu=np.cumsum(x); pk=np.maximum.accumulate(cu); dd=cu-pk
print(f"  backtest max drawdown (these 2 sleeves): ${dd.min():,.0f}")
print(f"  live drawdown to date: ${LIVE:,.0f}  ({100*abs(LIVE/dd.min()):.0f}% of the historical worst)")
