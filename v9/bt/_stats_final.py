# -*- coding: utf-8 -*-
"""FINAL report stats. Three books over the common window:
  A CURRENT LIVE   = VWAP_R3 x2 + VWAP_short x2 + TLB_hyb x1            (what runs today)
  B RECOMMENDED    = VWAP_R3 x2 + VWAP_short x2 + R3_re x1  (TLB dropped, R3 added)
  C CURRENT+R3     = A + R3_re x1                            (for reference)
Emits headline / yearly / monthly / drawdowns / weekly equity as JSON."""
import sys, json; sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd
BT='/root/v9/bt/'
def vwap(fn):
    d=pd.read_csv(BT+fn); d['dt']=pd.to_datetime(d['entry_time_et']).dt.date
    return d.groupby('dt')['pnl_usd'].sum()
VL=vwap('trades_VWAP_LONG_R3.csv'); VS=vwap('trades_VWAP_SHORT_R1.csv')
tl=pd.read_csv(BT+'_tlbib_hybrid_trades.csv'); tl['dt']=pd.to_datetime(tl['entry_date']).dt.date
TL=tl.groupby('dt')['pnl_usd'].sum()
r3=pd.read_csv(BT+'_r3v_fires.csv'); r3['dt']=pd.to_datetime(r3['entry_date_et']).dt.date
R3=r3.groupby('dt')['pnl_usd_1mnq'].sum()
start=TL.index.min(); end=max(TL.index.max(),VL.index.max(),R3.index.max())
I=pd.Index(sorted(set(pd.bdate_range(start,end).date)|set(VL.index)|set(VS.index)|set(TL.index)|set(R3.index)))
I=pd.Index([d for d in I if start<=d<=end])
al=lambda s: s.reindex(I).fillna(0.0)
VLa,VSa,TLa,R3a=al(VL),al(VS),al(TL),al(R3)
A = VLa*2 + VSa*2 + TLa*1            # current live
B = VLa*2 + VSa*2 + R3a*1            # recommended
C = VLa*2 + VSa*2 + TLa*1 + R3a*1    # current + R3
ANN=252.0
def stats(x):
    x=np.asarray(x,float); cu=np.cumsum(x); pk=np.maximum.accumulate(cu); dd=cu-pk
    sd=x.std(ddof=1); nz=x[x!=0]; w=nz[nz>0]; l=nz[nz<0]
    neg=x[x<0]; dstd=neg.std(ddof=1) if len(neg)>1 else 0
    flat=0; mx=0; peak=cu[0]
    for v in cu:
        if v>=peak: peak=v; flat=0
        else: flat+=1; mx=max(mx,flat)
    return dict(net=float(cu[-1]), ann=float(x.mean()*ANN), sharpe=float(x.mean()/sd*np.sqrt(ANN)) if sd>0 else 0.0,
        sortino=float(x.mean()/dstd*np.sqrt(ANN)) if dstd>0 else 0.0,
        maxdd=float(dd.min()), retdd=float(cu[-1]/abs(dd.min())) if dd.min()<0 else 0.0,
        pf=float(w.sum()/abs(l.sum())) if l.sum()!=0 else 0.0,
        win=float(100*(nz>0).mean()) if len(nz) else 0.0, ntd=int(len(nz)),
        best=float(x.max()), worst=float(x.min()), flat=int(mx),
        avgw=float(w.mean()) if len(w) else 0.0, avgl=float(l.mean()) if len(l) else 0.0)
out={'window':[str(I.min()),str(I.max()),len(I)]}
out['books']={'current':stats(A),'recommended':stats(B),'current_plus_r3':stats(C)}
out['sleeves']={'VWAP_R3_x2':float((VLa*2).sum()),'VWAP_short_x2':float((VSa*2).sum()),
                'TLB_x1':float(TLa.sum()),'R3_x1':float(R3a.sum())}
yr=pd.Series(pd.to_datetime(I).year,index=I)
out['yearly']={}
for y in sorted(set(yr)):
    m=(yr==y).values
    out['yearly'][int(y)]={'cur':stats(A[m]),'rec':stats(B[m]),'ndays':int(m.sum())}
mo=pd.Series(pd.to_datetime(I).to_period('M').astype(str),index=I)
am=A.groupby(mo).sum(); bm=B.groupby(mo).sum()
out['monthly']=[{'m':k,'cur':round(float(am[k])),'rec':round(float(bm[k]))} for k in am.index]
out['monthly_summary']={
 'cur':{'pos':float(100*(am>0).mean()),'worst':float(am.min()),'best':float(am.max()),'avg':float(am.mean()),'std':float(am.std())},
 'rec':{'pos':float(100*(bm>0).mean()),'worst':float(bm.min()),'best':float(bm.max()),'avg':float(bm.mean()),'std':float(bm.std())}}
def dds(x):
    cu=np.cumsum(np.asarray(x,float)); pk=np.maximum.accumulate(cu); d=cu-pk; dts=list(I); eps=[]; i=0
    while i<len(d):
        if d[i]<0:
            j=i
            while j<len(d) and d[j]<0: j+=1
            seg=d[i:j]; tr=i+int(np.argmin(seg))
            eps.append({'depth':round(float(seg.min())),'start':str(dts[i]),'trough':str(dts[tr]),
                        'recover':str(dts[min(j,len(dts)-1)]),'dur':int(j-i)}); i=j
        else: i+=1
    return sorted(eps,key=lambda e:e['depth'])[:6]
out['dd_rec']=dds(B); out['dd_cur']=dds(A)
eqa=pd.Series(np.cumsum(np.asarray(A,float)),index=pd.to_datetime(I))
eqb=pd.Series(np.cumsum(np.asarray(B,float)),index=pd.to_datetime(I))
wa=eqa.resample('W').last().dropna(); wb=eqb.resample('W').last().dropna()
out['equity']={'dates':[str(d.date()) for d in wa.index],
               'cur':[round(float(v)) for v in wa.values],
               'rec':[round(float(v)) for v in wb.reindex(wa.index).values]}
# H1/H2 walk-forward for both books
h=len(I)//2
out['wf']={'split':[str(I[0]),str(I[h-1]),str(I[h]),str(I[-1])],
           'cur':{'h1':stats(A[:h]),'h2':stats(A[h:])},
           'rec':{'h1':stats(B[:h]),'h2':stats(B[h:])}}
print("JSON_START"); print(json.dumps(out)); print("JSON_END")
