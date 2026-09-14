# -*- coding: utf-8 -*-
"""Sizing/allocation analysis for the deployable NQ book (V9 core + R3), 1-MNQ unit series.
Per-sleeve risk, correlations, current vs vol-parity vs Sharpe-optimal weights. Read-only."""
import sys; sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd
BT='/root/v9/bt/'
def etdate(s): return pd.to_datetime(s,utc=True,errors='coerce').dt.tz_convert('America/New_York').dt.date
def vwap(fn):
    d=pd.read_csv(BT+fn); d['dt']=pd.to_datetime(d['entry_time_et']).dt.date
    return d.assign(u=d['pnl_usd']).groupby('dt')['u'].sum()   # 1 MNQ unit
VL=vwap('trades_VWAP_LONG_R3.csv'); VS=vwap('trades_VWAP_SHORT_R1.csv')
tl=pd.read_csv(BT+'_tlbib_hybrid_trades.csv'); tl['dt']=pd.to_datetime(tl['entry_date']).dt.date
TL=tl.assign(u=tl['pnl_usd']).groupby('dt')['u'].sum()
r3=pd.read_csv(BT+'_r3v_fires.csv'); r3['dt']=pd.to_datetime(r3['entry_date_et']).dt.date
R3=r3.assign(u=r3['pnl_usd_1mnq']).groupby('dt')['u'].sum()
S={'VWAP_R3':VL,'VWAP_short':VS,'TLB_hyb':TL,'R3_re':R3}
start=TL.index.min(); end=max(x.index.max() for x in S.values())
cal=pd.bdate_range(start,end).date
I=pd.Index(sorted(set(cal)|set().union(*[set(x.index) for x in S.values()])))
I=pd.Index([d for d in I if start<=d<=end])
A={k:v.reindex(I).fillna(0.0) for k,v in S.items()}
ANN=252.0
def st(x):
    x=np.asarray(x,float); cu=np.cumsum(x); dd=(cu-np.maximum.accumulate(cu)).min()
    sd=x.std(ddof=1); return dict(net=cu[-1], dstd=sd, annvol=sd*np.sqrt(ANN),
        sharpe=x.mean()/sd*np.sqrt(ANN) if sd>0 else 0, maxdd=dd, ntd=int((x!=0).sum()))
print("=== PER-SLEEVE @ 1 MNQ (unit) ===")
print(f"{'sleeve':<11}{'net$':>8}{'annVol$':>9}{'Sharpe':>7}{'maxDD$':>8}{'trd-days':>9}")
U={}
for k,x in A.items():
    s=st(x); U[k]=s
    print(f"{k:<11}{s['net']:>8,.0f}{s['annvol']:>9,.0f}{s['sharpe']:>7.2f}{s['maxdd']:>8,.0f}{s['ntd']:>9}")
# correlation (active-overlap and full)
M=pd.DataFrame(A)
print("\n=== DAILY CORRELATION (full series, 0-filled) ===")
print(M.corr().round(3).to_string())
# current sizing 2/2/1/1
w_cur={'VWAP_R3':2,'VWAP_short':2,'TLB_hyb':1,'R3_re':1}
def port(w):
    p=sum(A[k]*w[k] for k in A); return st(p), p
def riskcontrib(w):
    # marginal risk contribution to portfolio variance
    cols=list(A); cov=M.cov().values
    wv=np.array([w[k] for k in cols])
    pv=wv@cov@wv; mc=(cov@wv)*wv/pv if pv>0 else wv*0
    return {cols[i]: mc[i] for i in range(len(cols))}
sc,_=port(w_cur)
print(f"\n=== CURRENT SIZING 2/2/1/1 ===")
print(f"  book: net ${sc['net']:,.0f}  annVol ${sc['annvol']:,.0f}  Sharpe {sc['sharpe']:.2f}  maxDD ${sc['maxdd']:,.0f}")
rc=riskcontrib(w_cur)
print("  risk contribution (% of book variance):")
for k in A: print(f"    {k:<11}{100*rc[k]:>6.0f}%   (size {w_cur[k]} MNQ)")
# vol-parity: w ~ 1/annvol, scaled so smallest sleeve ~1 unit
inv={k:1.0/U[k]['annvol'] for k in A}
base=min(inv.values())
w_vp={k:inv[k]/base for k in A}
# Sharpe-optimal (uncorrelated approx): w ~ Sharpe/vol ~ mean/var
mu={k:A[k].mean() for k in A}; var={k:U[k]['dstd']**2 for k in A}
so={k: mu[k]/var[k] for k in A}; bso=min(v for v in so.values() if v>0)
w_so={k: max(0, so[k]/bso) for k in A}
print("\n=== SUGGESTED RELATIVE WEIGHTS (unit = smallest sleeve) ===")
print(f"{'sleeve':<11}{'current':>8}{'vol-parity':>12}{'Sharpe-opt':>12}")
for k in A:
    print(f"{k:<11}{w_cur[k]:>8}{w_vp[k]:>12.2f}{w_so[k]:>12.2f}")
# round vol-parity and Sharpe to MNQ integers, show resulting book
def rnd(w): return {k:max(1,round(v)) for k,v in w.items()}
for nm,w in [('vol-parity(int)',rnd(w_vp)),('Sharpe-opt(int)',rnd(w_so))]:
    s,_=port(w)
    print(f"\n  {nm} sizes {dict(w)}: net ${s['net']:,.0f} annVol ${s['annvol']:,.0f} Sharpe {s['sharpe']:.2f} maxDD ${s['maxdd']:,.0f}")
# account-risk framing at current sizing
print("\n=== ACCOUNT-RISK FRAMING (current 2/2/1/1) ===")
mdd=abs(sc['maxdd']); dvol=sc['dstd']
for acct in [50000,100000,150000,200000]:
    print(f"  ${acct:>7,}: realized maxDD {100*mdd/acct:.1f}%  ·  plan 1.5x maxDD {100*1.5*mdd/acct:.1f}%  ·  daily 1σ {100*dvol/acct:.2f}%")
print(f"\n  book daily 1σ = ${dvol:,.0f}; realized maxDD ${mdd:,.0f}; suggested plan-for ${1.5*mdd:,.0f}")
