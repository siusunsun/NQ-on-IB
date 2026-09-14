"""_cci_portfolio.py - portfolio fit of CCI sleeve beside V9 book (VWAP_LONG_R3+VWAP_SHORT_R1+TLB_LONG).
Read-only. Uses points P&L (Sharpe/corr/retDD are scale-invariant)."""
import pandas as pd, numpy as np
BT="/root/v9/bt/"

def daily_pts_from_v9(fn, datecol='entry_time_et'):
    d=pd.read_csv(BT+fn)
    d['dt']=pd.to_datetime(d[datecol]).dt.date
    return d.groupby('dt')['pnl_points'].sum()

vl=daily_pts_from_v9('trades_VWAP_LONG_R3.csv')
vs=daily_pts_from_v9('trades_VWAP_SHORT_R1.csv')
tl=daily_pts_from_v9('trades_TLB_LONG.csv')

cci=pd.read_csv(BT+'_cci_trades.csv')
cci['dt']=pd.to_datetime(cci['date']).dt.date
cci['wpts']=cci['pts']*np.where(cci['slot']=='AM',1.0,0.33)
cci_two=cci.groupby('dt')['wpts'].sum()          # two-slot weighted
cci_am=cci[cci['slot']=='AM'].groupby('dt')['pts'].sum()  # single-slot

# common trading-day index = union of all trade dates within overlap window
start=max(vl.index.min(),vs.index.min(),tl.index.min())  # V9 warmup start
allidx=sorted(set(vl.index)|set(vs.index)|set(tl.index)|set(cci_two.index))
allidx=[d for d in allidx if d>=start]
idx=pd.Index(allidx,name='dt')
def align(s): return s.reindex(idx).fillna(0.0)
VL,VS,TL=align(vl),align(vs),align(tl)
C2,CA=align(cci_two),align(cci_am)
V9=VL+VS+TL

def sharpe(x):
    x=np.asarray(x,float); sd=x.std(ddof=1); return (x.mean()/sd*np.sqrt(252)) if sd>0 else float('nan')
def maxdd(x):
    c=np.cumsum(x); peak=np.maximum.accumulate(c); return (c-peak).min()
def retdd(x):
    dd=maxdd(x); return (np.sum(x)/abs(dd)) if dd<0 else float('nan')
def summ(x,label):
    x=np.asarray(x,float)
    print(f"  {label:32s} netpts={x.sum():8.0f}  Sharpe={sharpe(x):5.2f}  maxDD={maxdd(x):8.0f}  ret/DD={retdd(x):5.2f}")

print(f"Overlap window: {idx.min()} -> {idx.max()}  ({len(idx)} trading days)")
print(f"CCI two-slot trades in window: {(C2!=0).sum()} days;  V9 active days: {(V9!=0).sum()}")

print("\n--- STANDALONE (daily, points) ---")
summ(VL,"VWAP_LONG_R3"); summ(VS,"VWAP_SHORT_R1"); summ(TL,"TLB_LONG")
summ(V9,"V9 BOOK (sum of 3)")
summ(CA,"CCI single-slot (AM)")
summ(C2,"CCI two-slot (AM1.0+PM0.33)")

print("\n--- DAILY-RETURN CORRELATION (CCI two-slot vs) ---")
for nm,s in [('VWAP_LONG_R3',VL),('VWAP_SHORT_R1',VS),('TLB_LONG',TL),('V9 BOOK',V9),('CCI single-slot',CA)]:
    r=np.corrcoef(C2,s)[0,1]
    print(f"  {nm:20s} corr={r:+.3f}")
print("  (single-slot CCI vs V9 BOOK corr=%.3f)"%np.corrcoef(CA,V9)[0,1])

# incremental blend at candidate weights (scale CCI two-slot to a vol multiple of V9)
sd_v9=V9.std(ddof=1); sd_c=C2.std(ddof=1)
k_eq=sd_v9/sd_c
print(f"\n--- ADD CCI(two-slot) TO V9 BOOK at candidate weights ---")
print(f"  (equal-vol multiplier k_eq={k_eq:.2f} contracts-equiv; V9 book run at 1x each sleeve)")
base_sh=sharpe(V9); base_rd=retdd(V9)
print(f"  BASELINE V9 book:                Sharpe={base_sh:.2f}  ret/DD={base_rd:.2f}  netpts={V9.sum():.0f}  maxDD={maxdd(V9):.0f}")
for label,w in [('equal-vol (1.00x k)',1.0),('half   (0.50x k)',0.5),('third  (0.33x k)',0.33),('raw 1 contract (no scale)',None)]:
    add = C2*(k_eq*w) if w is not None else C2
    blend=V9+add
    dsh=sharpe(blend)-base_sh; drd=retdd(blend)-base_rd
    print(f"  +CCI {label:26s} Sharpe={sharpe(blend):.2f} (d{dsh:+.2f})  ret/DD={retdd(blend):.2f} (d{drd:+.2f})  netpts={blend.sum():.0f}  maxDD={maxdd(blend):.0f}")

# recent-regime: 2026 and 2026-06+ blocks
def block(lo,hi=None):
    m=(idx>=pd.to_datetime(lo).date())
    if hi: m&=(idx<pd.to_datetime(hi).date())
    return m
print("\n--- RECENT REGIME (Sharpe / netpts) ---")
for lbl,lo,hi in [('2025 full','2025-01-01','2026-01-01'),('2026 YTD','2026-01-01',None),('2026-06+','2026-06-01',None)]:
    m=block(lo,hi)
    print(f"  {lbl:10s}: V9 net={V9[m].sum():7.0f} Sh={sharpe(V9[m]):5.2f} | CCI2 net={C2[m].sum():7.0f} Sh={sharpe(C2[m]):5.2f} | corr={np.corrcoef(C2[m],V9[m])[0,1]:+.3f}" if m.sum()>2 else f"  {lbl}: n/a")
