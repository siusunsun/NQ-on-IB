# -*- coding: utf-8 -*-
"""Portfolio combination + allocation search across ALL 7 NQ sleeves (1-MNQ units).
Full-sample optimum (in-sample, overfit-prone) vs WALK-FORWARD (optimize H1, test H2 = honest).
Integer weights 0..3, total <= 8 MNQ. Read-only."""
import sys, itertools; sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd, glob
BT='/root/v9/bt/'; NQ_DIR='/root/v9/data_1m/NQ/'; MNQ=2.0
def etd(s): return pd.to_datetime(s,utc=True,errors='coerce').dt.tz_convert('America/New_York').dt.date
def vwap(fn):
    d=pd.read_csv(BT+fn); d['dt']=pd.to_datetime(d['entry_time_et']).dt.date
    return d.groupby('dt')['pnl_usd'].sum()
VL=vwap('trades_VWAP_LONG_R3.csv'); VS=vwap('trades_VWAP_SHORT_R1.csv')
tl=pd.read_csv(BT+'_tlbib_hybrid_trades.csv'); tl['dt']=pd.to_datetime(tl['entry_date']).dt.date
TL=tl.groupby('dt')['pnl_usd'].sum()
r3=pd.read_csv(BT+'_r3v_fires.csv'); r3['dt']=pd.to_datetime(r3['entry_date_et']).dt.date
R3=r3.groupby('dt')['pnl_usd_1mnq'].sum()
cc=pd.read_csv(BT+'_cci_trades.csv'); cc=cc[cc['slot']=='AM'].copy(); cc['dt']=pd.to_datetime(cc['date']).dt.date
CCI=(cc['pts']*MNQ).groupby(cc['dt']).sum()
rc=pd.read_csv('/root/tlbrc_paper/ref/_tlbrc_trades.csv'); rc['dt']=etd(rc['entry_t'])
RCLM=(rc['r']*rc['risk']*MNQ).groupby(rc['dt']).sum()
# snapback @1 MNQ
def load1m():
    p=[pd.read_csv(f,usecols=['time','close']) for f in sorted(glob.glob(NQ_DIR+'*.csv'))]
    d=pd.concat(p,ignore_index=True); d['time']=pd.to_datetime(d['time'],utc=True,errors='coerce')
    return d.dropna(subset=['time']).drop_duplicates('time').set_index('time').sort_index().tz_convert('America/New_York')['close']
c1=load1m(); day=c1.groupby(c1.index.normalize()).last(); day.index=day.index.tz_localize(None); day=day.dropna()
z=(day-day.rolling(20).mean())/day.rolling(20).std(ddof=0); dz=pd.DataFrame({'c':day,'z':z}).dropna()
ret=dz['c'].pct_change().fillna(0).values; expo=np.r_[0.0,(dz['z'].values<-1.5).astype(float)[:-1]]
dp=np.abs(np.diff(np.r_[0.0,expo])); SNAP=pd.Series((expo*ret-(0.00015/2)*dp)*dz['c'].values*MNQ/dz['c'].values, index=pd.to_datetime(dz.index).date)
# NOTE snap series above is in $ per 1 "unit"; approximate 1 MNQ by pts: use price-diff form
dpts=np.r_[0.0,np.diff(dz['c'].values)]; SNAP=pd.Series((expo*dpts-(0.00015/2)*dz['c'].values*dp)*MNQ, index=pd.to_datetime(dz.index).date)

S={'VWAP_R3':VL,'VWAP_short':VS,'TLB_hyb':TL,'R3_re':R3,'CCI':CCI,'Snap':SNAP,'TLB_recl':RCLM}
TAG={'VWAP_R3':'core·robust','VWAP_short':'core·thin','TLB_hyb':'core·weak-recent','R3_re':'ROBUST(DSR.98)',
     'CCI':'OVERFIT(DSR.79)','Snap':'OVERFIT(DSR.66)','TLB_recl':'fragile(t1.94,tail)'}
start=TL.index.min(); end=max(x.index.max() for x in S.values())
I=pd.Index(sorted(set(pd.bdate_range(start,end).date)|set().union(*[set(x.index) for x in S.values()])))
I=pd.Index([d for d in I if start<=d<=end]); names=list(S)
Mtx=np.column_stack([S[k].reindex(I).fillna(0.0).values for k in names])  # days x 7
ANN=252.0
def stats(x):
    cu=np.cumsum(x); dd=(cu-np.maximum.accumulate(cu)).min(); sd=x.std(ddof=1)
    return (cu[-1], x.mean()/sd*np.sqrt(ANN) if sd>0 else 0.0, dd, cu[-1]/abs(dd) if dd<0 else 0.0)
# per-sleeve
print("=== PER-SLEEVE @1 MNQ ==="); print(f"{'sleeve':<11}{'net$':>8}{'Sharpe':>7}{'maxDD':>8}  tag")
for i,k in enumerate(names):
    n,s,d,_=stats(Mtx[:,i]); print(f"{k:<11}{n:>8,.0f}{s:>7.2f}{d:>8,.0f}  {TAG[k]}")
print("\n=== CORRELATION ==="); print(pd.DataFrame(Mtx,columns=names).corr().round(2).to_string())
# split
h=len(I)//2; H1=Mtx[:h]; H2=Mtx[h:]
print(f"\nsplit: H1 {I[0]}..{I[h-1]} ({h}d)  H2 {I[h]}..{I[-1]} ({len(I)-h}d)")
# search integer weights 0..3, total<=8, not all zero
levels=range(0,4); best=[]
combos=[w for w in itertools.product(levels,repeat=7) if 0<sum(w)<=8]
def port(M,w): return M@np.array(w,float)
res=[]
for w in combos:
    xf=port(Mtx,w); nf,sf,df,rf=stats(xf)
    res.append((w,nf,sf,df,rf))
W=list(combos); res=np.array([(r[1],r[2],r[3],r[4]) for r in res])
def show(title, order_idx, k=6):
    print(f"\n=== {title} ===")
    print(f"{'weights (R3/Sh/TLB/Re/CCI/Snp/Rcl)':<36}{'net$':>8}{'Sharpe':>7}{'maxDD':>8}{'ret/DD':>7}")
    for j in order_idx[:k]:
        w=W[j]; print(f"{str(w):<36}{res[j,0]:>8,.0f}{res[j,1]:>7.2f}{res[j,2]:>8,.0f}{res[j,3]:>7.2f}")
# full-sample optima
show("FULL-SAMPLE max Sharpe (IN-SAMPLE, overfit-prone)", np.argsort(-res[:,1]))
show("FULL-SAMPLE max ret/DD (IN-SAMPLE)", np.argsort(-res[:,3]))
# walk-forward: optimize on H1 (max Sharpe), report H2
h1s=np.array([stats(port(H1,w))[1] for w in W]); h1r=np.array([stats(port(H1,w))[3] for w in W])
def h2(w): return stats(port(H2,w))
print("\n=== WALK-FORWARD: pick weights by H1, measure H2 (OUT-OF-SAMPLE, honest) ===")
for lab,crit in [('H1-max-Sharpe',h1s),('H1-max-ret/DD',h1r)]:
    j=int(np.argmax(crit)); w=W[j]; n2,s2,d2,r2=h2(w); n1,s1,d1,r1=stats(port(H1,w))
    print(f"  {lab}: w={w}  ->  H1 Sh {s1:.2f} r/DD {r1:.2f} | H2(OOS) Sh {s2:.2f} r/DD {r2:.2f} net ${n2:,.0f}")
# reference portfolios OOS
def full(w): return stats(port(Mtx,w))
refs={
 'CURRENT 2/2/1/1 (V9core+R3)':(2,2,1,1,0,0,0),
 'ROBUST core+R3 (drop nothing)':(2,2,1,1,0,0,0),
 'NO-TLB (VWAP_R3/short/R3)':(2,2,0,1,0,0,0),
 'VWAP_R3+R3 only':(2,0,0,1,0,0,0),
 'ALL-SEVEN 1x each':(1,1,1,1,1,1,1),
}
print("\n=== REFERENCE PORTFOLIOS (full / H2-OOS) ===")
print(f"{'portfolio':<30}{'full net':>9}{'fSh':>6}{'fr/DD':>7}   {'H2 net':>8}{'H2Sh':>6}{'H2r/DD':>7}")
for lab,w in refs.items():
    nf,sf,df,rf=full(w); n2,s2,d2,r2=h2(w)
    print(f"{lab:<30}{nf:>9,.0f}{sf:>6.2f}{rf:>7.2f}   {n2:>8,.0f}{s2:>6.2f}{r2:>7.2f}")
# best ROBUST-ONLY (restrict to core+R3: indices 0,1,2,3), by full ret/DD and by H2
robmask=[all(w[i]==0 for i in [4,5,6]) for w in W]
ridx=[i for i,m in enumerate(robmask) if m]
show("ROBUST-ONLY (core+R3, no CCI/Snap/Recl) — max ret/DD full", sorted(ridx,key=lambda j:-res[j,3]))
# also robust-only best OOS
rj=max(ridx,key=lambda j:stats(port(H1,W[j]))[1]); w=W[rj]; n2,s2,d2,r2=h2(w)
print(f"\n  ROBUST-ONLY H1-max-Sharpe -> w={w}  H2(OOS) Sh {s2:.2f} r/DD {r2:.2f} net ${n2:,.0f}")
