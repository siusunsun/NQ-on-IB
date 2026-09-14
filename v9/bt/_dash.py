import numpy as np, pandas as pd, math
RT=2.04; BT='/root/v9/bt/'
def load(tag,qty):
    d=pd.read_csv(BT+f'_hist_tr_{tag}.csv')
    col=[c for c in d.columns if 'entry' in c.lower() and ('date' in c.lower() or 'utc' in c.lower() or 'time' in c.lower())][0]
    t=pd.to_datetime(d[col],utc=True,errors='coerce')
    if t.isna().all(): t=pd.to_datetime(d[col],errors='coerce').dt.tz_localize('UTC')
    d['dt']=t.dt.tz_convert('America/New_York').dt.date
    pc=[c for c in d.columns if c.lower() in ('pnl_usd','pnl_usd_1mnq','net_usd','usd')][0]
    d['net']=d[pc]*qty-RT*qty
    return d.groupby('dt')['net'].sum()
VL=load('VWAP_LONG_R3',2); VS=load('VWAP_SHORT_R1',1); TL=load('TLB_HYB40',1)
r=pd.read_csv(BT+'_r3_48_daily.csv'); r['dt']=pd.to_datetime(r['date']).dt.date
R3=r.set_index('dt')['usd']
start=min(x.index.min() for x in [VL,VS,TL,R3]); end=max(x.index.max() for x in [VL,VS,TL,R3])
I=pd.Index(sorted(set(pd.bdate_range(start,end).date)|set(VL.index)|set(VS.index)|set(TL.index)|set(R3.index)))
I=pd.Index([d for d in I if start<=d<=end])
al=lambda s:s.reindex(I).fillna(0.0)
B=al(VL)+al(VS)+al(TL)+al(R3)
x=np.asarray(B,float); cu=np.cumsum(x); dd=cu-np.maximum.accumulate(cu)
yrs=(pd.Timestamp(end)-pd.Timestamp(start)).days/365.25
sd=x.std(ddof=1); sh=x.mean()/sd*math.sqrt(252); annvol=sd*math.sqrt(252)
net=cu[-1]; ann=net/yrs; mdd=abs(dd.min())
neg=x[x<0]; sortino=x.mean()/neg.std(ddof=1)*math.sqrt(252)
print(f'window {I.min()} .. {I.max()}   {len(I)} days   {yrs:.2f} yrs')
print(f'net ${net:,.0f}   ann ${ann:,.0f}   Sharpe {sh:.2f}   Sortino {sortino:.2f}   annVol ${annvol:,.0f}   maxDD ${mdd:,.0f}')
print()
print(f"{'capital':>10}{'return%':>9}{'maxDD%':>8}{'volTgt':>8}")
for cap in (20000,22000,22500,25000,28000):
    print(f'${cap:>9,}{100*ann/cap:>8.1f}%{100*mdd/cap:>7.1f}%{100*annvol/cap:>7.1f}%')
# capital that puts maxDD exactly at 12% / 11%
for tgt in (0.12,0.11,0.10):
    c=mdd/tgt
    print(f'  DD={int(tgt*100)}% -> capital ${c:,.0f}  return {100*ann/c:.1f}%  vol {100*annvol/c:.1f}%')
