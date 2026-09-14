# -*- coding: utf-8 -*-
"""Drawdown profile + scaling table for the FOUR-SLEEVE book (full history 2021-2026).
Monthly DD stats, rolling-12m worst, and what each scale multiple implies."""
import sys; sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd
BT='/root/v9/bt/'
def load(tag):
    d=pd.read_csv(BT+f'_hist_tr_{tag}.csv')
    col=[c for c in d.columns if 'entry' in c.lower() and ('date' in c.lower() or 'time' in c.lower() or 'utc' in c.lower())][0]
    t=pd.to_datetime(d[col],utc=True,errors='coerce')
    if t.isna().all(): t=pd.to_datetime(d[col],errors='coerce').dt.tz_localize('UTC')
    d['dt']=t.dt.tz_convert('America/New_York').dt.date
    pc=[c for c in d.columns if c.lower() in ('pnl_usd','pnl_usd_1mnq','net_usd','usd')][0]
    return d.groupby('dt')[pc].sum()
VL,VS,TL,RE=load('VWAP_LONG_R3'),load('VWAP_SHORT_R1'),load('TLB_HYB40'),load('R3_RE')
start=min(x.index.min() for x in [VL,VS,TL,RE]); end=max(x.index.max() for x in [VL,VS,TL,RE])
I=pd.Index(sorted(set(pd.bdate_range(start,end).date)|set(VL.index)|set(VS.index)|set(TL.index)|set(RE.index)))
I=pd.Index([d for d in I if start<=d<=end])
al=lambda s: s.reindex(I).fillna(0.0)
B=al(VL)*2+al(VS)*2+al(TL)*1+al(RE)*1   # FOUR-SLEEVE at 1x unit
x=np.asarray(B,float); cu=np.cumsum(x)
mo=pd.Series(pd.to_datetime(I).to_period('M').astype(str),index=I)

# --- within-month drawdown (peak-to-trough inside each calendar month) ---
mdd=[]
for m,grp in B.groupby(mo):
    c=np.cumsum(np.asarray(grp,float)); pk=np.maximum.accumulate(c)
    mdd.append((m,float((c-pk).min()),float(c[-1])))
md=pd.DataFrame(mdd,columns=['m','dd','net'])
print("=== WITHIN-MONTH DRAWDOWN (four-sleeve, 1x unit) — %d months ==="%len(md))
print("  average  month DD  $%.0f"%md.dd.mean())
print("  median   month DD  $%.0f"%md.dd.median())
print("  worst    month DD  $%.0f  (%s)"%(md.dd.min(), md.loc[md.dd.idxmin(),'m']))
print("  75th pct month DD  $%.0f   (1 in 4 months worse than this)"%md.dd.quantile(0.25))
print("  90th pct month DD  $%.0f   (1 in 10 months worse)"%md.dd.quantile(0.10))
print("  months with DD deeper than $500 : %d of %d (%.0f%%)"%((md.dd<-500).sum(),len(md),100*(md.dd<-500).mean()))
print("  months with DD deeper than $1000: %d of %d (%.0f%%)"%((md.dd<-1000).sum(),len(md),100*(md.dd<-1000).mean()))

# --- full-curve drawdown episodes ---
pk=np.maximum.accumulate(cu); dd=cu-pk
print("\n=== PEAK-TO-TROUGH (whole curve) ===")
print("  max drawdown        $%.0f"%dd.min())
print("  average daily DD    $%.0f  (mean of the underwater series)"%dd.mean())
print("  time underwater     %.0f%% of days"%(100*(dd<0).mean()))
# rolling 12m worst DD
s=pd.Series(cu,index=pd.to_datetime(I))
r12=[]
for i in range(len(s)):
    w=s.iloc[max(0,i-252):i+1]
    if len(w)>20:
        p=w.cummax(); r12.append(float((w-p).min()))
r12=np.array(r12)
print("  rolling 12-mo worst DD: median $%.0f, 90th-pct $%.0f, max $%.0f"%(np.median(r12),np.percentile(r12,10),r12.min()))

# --- scaling table ---
ann=x.mean()*252; mm=md.net.mean()
print("\n=== SCALING (four-sleeve = VWAP_R3 2 : short 2 : TLB 1 : R3 1 per unit) ===")
print(f"{'scale':<7}{'contracts (MNQ)':<22}{'ann $':>9}{'avg mo $':>10}{'avg mo DD':>11}{'maxDD $':>10}")
for k in [1,2,3,5,8,10]:
    lots=f"{2*k}/{2*k}/{1*k}/{1*k}"
    tot=6*k
    print(f"{k}x{'':<5}{lots+f'  ({tot} lots)':<22}{ann*k:>9,.0f}{mm*k:>10,.0f}{md.dd.mean()*k:>11,.0f}{dd.min()*k:>10,.0f}")
print("\n  (1 NQ = 10 MNQ, so 10x = 2/2/1/1 in NQ contracts)")
print("\n=== ACCOUNT FRAMING: max drawdown as % of account ===")
print(f"{'scale':<7}{'maxDD $':>10}   " + "".join(f"{'$'+str(a//1000)+'k':>9}" for a in [100000,150000,200000,300000,500000]))
for k in [1,2,3,5,8,10]:
    row=f"{k}x{'':<5}{dd.min()*k:>10,.0f}   "
    for a in [100000,150000,200000,300000,500000]:
        row+=f"{100*abs(dd.min()*k)/a:>8.1f}%"
    print(row)
print("\n  plan-for drawdown = 1.5x realized (standard haircut):")
for k in [3,5,8,10]:
    print(f"    {k}x -> realized ${abs(dd.min()*k):,.0f}, plan for ${abs(dd.min()*k)*1.5:,.0f}")
