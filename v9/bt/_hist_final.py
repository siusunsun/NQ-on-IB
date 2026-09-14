# -*- coding: utf-8 -*-
"""Full-history (2021-2026) stats for THREE books, from the _hist_ per-sleeve trade files.
  CURRENT = VWAP_R3 x2 + SHORT x2 + TLB x1
  NO-TLB  = VWAP_R3 x2 + SHORT x2 + R3_RE x1
  FOUR    = VWAP_R3 x2 + SHORT x2 + TLB x1 + R3_RE x1   <- the long-sample winner
Emits headline / yearly / monthly / drawdowns / weekly equity as JSON."""
import sys, json, glob; sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd
BT='/root/v9/bt/'
def load(tag):
    d=pd.read_csv(BT+f'_hist_tr_{tag}.csv')
    dc=[c for c in d.columns if 'entry' in c.lower() and ('date' in c.lower() or 'time' in c.lower() or 'utc' in c.lower())]
    col=dc[0]
    t=pd.to_datetime(d[col],utc=True,errors='coerce')
    if t.isna().all(): t=pd.to_datetime(d[col],errors='coerce').dt.tz_localize('UTC')
    d['dt']=t.dt.tz_convert('America/New_York').dt.date
    pc=[c for c in d.columns if c.lower() in ('pnl_usd','pnl_usd_1mnq','net_usd','usd')]
    return d.groupby('dt')[pc[0]].sum()
VL=load('VWAP_LONG_R3'); VS=load('VWAP_SHORT_R1'); TL=load('TLB_HYB40'); RE=load('R3_RE')
start=min(x.index.min() for x in [VL,VS,TL,RE]); end=max(x.index.max() for x in [VL,VS,TL,RE])
I=pd.Index(sorted(set(pd.bdate_range(start,end).date)|set(VL.index)|set(VS.index)|set(TL.index)|set(RE.index)))
I=pd.Index([d for d in I if start<=d<=end])
al=lambda s: s.reindex(I).fillna(0.0)
VLa,VSa,TLa,REa=al(VL),al(VS),al(TL),al(RE)
BOOKS={'current':VLa*2+VSa*2+TLa, 'notlb':VLa*2+VSa*2+REa, 'four':VLa*2+VSa*2+TLa+REa}
ANN=252.0
def stats(x):
    x=np.asarray(x,float); cu=np.cumsum(x); pk=np.maximum.accumulate(cu); dd=cu-pk
    sd=x.std(ddof=1); nz=x[x!=0]; w=nz[nz>0]; l=nz[nz<0]; neg=x[x<0]
    ds=neg.std(ddof=1) if len(neg)>1 else 0
    flat=0;mx=0;peak=cu[0]
    for v in cu:
        if v>=peak: peak=v; flat=0
        else: flat+=1; mx=max(mx,flat)
    return dict(net=float(cu[-1]),ann=float(x.mean()*ANN),sharpe=float(x.mean()/sd*np.sqrt(ANN)) if sd>0 else 0.0,
      sortino=float(x.mean()/ds*np.sqrt(ANN)) if ds>0 else 0.0,maxdd=float(dd.min()),
      retdd=float(cu[-1]/abs(dd.min())) if dd.min()<0 else 0.0,
      pf=float(w.sum()/abs(l.sum())) if l.sum()!=0 else 0.0,
      win=float(100*(nz>0).mean()) if len(nz) else 0.0,ntd=int(len(nz)),
      best=float(x.max()),worst=float(x.min()),flat=int(mx))
out={'window':[str(I.min()),str(I.max()),len(I)]}
out['headline']={k:stats(v) for k,v in BOOKS.items()}
out['sleeves']={'VWAP_R3_x2':float((VLa*2).sum()),'VWAP_short_x2':float((VSa*2).sum()),
                'TLB_x1':float(TLa.sum()),'R3re_x1':float(REa.sum())}
yr=pd.Series(pd.to_datetime(I).year,index=I)
out['yearly']={}
for y in sorted(set(yr)):
    m=(yr==y).values
    out['yearly'][int(y)]={k:stats(v[m]) for k,v in BOOKS.items()}
    out['yearly'][int(y)]['sleeves']={'VWAP_R3':float((VLa*2)[m].sum()),'VWAP_short':float((VSa*2)[m].sum()),
                                      'TLB':float(TLa[m].sum()),'R3re':float(REa[m].sum())}
    out['yearly'][int(y)]['ndays']=int(m.sum())
mo=pd.Series(pd.to_datetime(I).to_period('M').astype(str),index=I)
ms={k:v.groupby(mo).sum() for k,v in BOOKS.items()}
out['monthly']=[{'m':k,'cur':round(float(ms['current'][k])),'four':round(float(ms['four'][k]))} for k in ms['current'].index]
def msum(s):
    a=s.values
    return dict(mean=float(a.mean()),median=float(np.median(a)),pos=float(100*(a>0).mean()),
        worst=float(a.min()),best=float(a.max()),std=float(a.std(ddof=1)),n=int(len(a)),
        topshare=float(100*a.max()/a.sum()),top3=float(100*np.sort(a)[-3:].sum()/a.sum()))
out['monthly_summary']={k:msum(v) for k,v in ms.items()}
def dds(x):
    cu=np.cumsum(np.asarray(x,float)); pk=np.maximum.accumulate(cu); d=cu-pk; dts=list(I); eps=[];i=0
    while i<len(d):
        if d[i]<0:
            j=i
            while j<len(d) and d[j]<0: j+=1
            seg=d[i:j]; tr=i+int(np.argmin(seg))
            eps.append({'depth':round(float(seg.min())),'start':str(dts[i]),'trough':str(dts[tr]),
                        'recover':str(dts[min(j,len(dts)-1)]),'dur':int(j-i)}); i=j
        else: i+=1
    return sorted(eps,key=lambda e:e['depth'])[:6]
out['dd_four']=dds(BOOKS['four']); out['dd_cur']=dds(BOOKS['current'])
eq={k:pd.Series(np.cumsum(np.asarray(v,float)),index=pd.to_datetime(I)) for k,v in BOOKS.items()}
w=eq['current'].resample('W').last().dropna()
out['equity']={'dates':[str(d.date()) for d in w.index],
  'cur':[round(float(x)) for x in w.values],
  'four':[round(float(x)) for x in eq['four'].resample('W').last().reindex(w.index).values],
  'notlb':[round(float(x)) for x in eq['notlb'].resample('W').last().reindex(w.index).values]}
print("JSON_START"); print(json.dumps(out)); print("JSON_END")
