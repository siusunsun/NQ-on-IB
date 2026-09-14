"""_cci2_cmp.py - single-slot vs two-slot head-to-head on OUR data. Read-only, points+$.
Daily-aggregated series, Sharpe=mean/std*sqrt(252), maxDD on cumsum, retDD=net/|maxDD|."""
import pandas as pd, numpy as np
PT=20.0
T=pd.read_csv('/root/v9/bt/_cci2_trades.csv')
T['dt']=pd.to_datetime(T['date']).dt.date
AM =T[T.slot=='AM']
PMh=T[T.slot=='PM_his']
PMo=T[T.slot=='PM_old']

def sharpe(x):
    x=np.asarray(x,float); sd=x.std(ddof=1); return (x.mean()/sd*np.sqrt(252)) if sd>0 else float('nan')
def maxdd(x):
    c=np.cumsum(x); peak=np.maximum.accumulate(c); return (c-peak).min() if len(x) else 0.0
def retdd(x):
    dd=maxdd(x); return (np.sum(x)/abs(dd)) if dd<0 else float('nan')

def daily(sub,w=1.0):
    s=sub.copy(); s['wpts']=s['pts']*w
    return s.groupby('dt')['wpts'].sum()

def series_on(index, *parts):
    # parts: list of (sub, weight)
    out=pd.Series(0.0,index=index)
    for sub,w in parts:
        d=daily(sub,w).reindex(index).fillna(0.0)
        out=out+d
    return out

def row(label,series):
    x=series.values
    ntr_days=(x!=0).sum()
    print(f"  {label:34s} days={len(x):4d} act={ntr_days:4d} netpts={x.sum():8.0f} net$={x.sum()*PT:11,.0f} "
          f"Sharpe={sharpe(x):5.2f} maxDD$={maxdd(x)*PT:10,.0f} ret/DD={retdd(x):5.2f}")

def peryear(label,series):
    df=pd.DataFrame({'p':series.values},index=pd.to_datetime(series.index))
    df['yr']=df.index.year
    print(f"  --- per-year {label} ---")
    for yr,g in df.groupby('yr'):
        x=g['p'].values
        print(f"      {yr}: net$={x.sum()*PT:10,.0f} Sharpe={sharpe(x):5.2f} maxDD$={maxdd(x)*PT:9,.0f} ret/DD={retdd(x):5.2f} actdays={(x!=0).sum():3d}")

print("="*100)
print("TRADE COUNTS: AM=%d  PM_his(12-14)=%d  PM_old(13-15)=%d" %(len(AM),len(PMh),len(PMo)))
print("date range:", T.date.min(), "->", T.date.max())

# ---- common index = union of AM + PM_his days (his-window comparison) ----
idxh=pd.Index(sorted(set(AM.dt)|set(PMh.dt)),name='dt')
print("\n"+"="*100)
print("HIS WINDOW (PM 12:00-14:00 k=1.5) -- FULL SAMPLE, common index = union(AM,PM_his) days=%d"%len(idxh))
S_AM   = series_on(idxh,(AM,1.0))
S_PMh  = series_on(idxh,(PMh,1.0))
S_2eq  = series_on(idxh,(AM,1.0),(PMh,1.0))
S_2his = series_on(idxh,(AM,1.0),(PMh,0.33))
row("SINGLE-SLOT (AM k=1.0)",        S_AM)
row("PM_his STANDALONE (k=1.5)",     S_PMh)
row("TWO-SLOT equal (AM1.0+PM1.0)",  S_2eq)
row("TWO-SLOT his   (AM1.0+PM0.33)", S_2his)
print("  corr(AM daily, PM_his daily) = %+.3f"%np.corrcoef(S_AM.values,S_PMh.values)[0,1])

# incremental: PM on top of AM
d_sh_eq  = sharpe(S_2eq.values)-sharpe(S_AM.values)
d_sh_his = sharpe(S_2his.values)-sharpe(S_AM.values)
d_rd_eq  = retdd(S_2eq.values)-retdd(S_AM.values)
d_rd_his = retdd(S_2his.values)-retdd(S_AM.values)
print("  INCREMENTAL (add PM_his to AM):")
print("     equal wt : dSharpe=%+.2f  dret/DD=%+.2f"%(d_sh_eq,d_rd_eq))
print("     his  wt  : dSharpe=%+.2f  dret/DD=%+.2f"%(d_sh_his,d_rd_his))

peryear("SINGLE-SLOT AM", S_AM)
peryear("TWO-SLOT his (AM1.0+PM0.33)", S_2his)

# ---- OLD WINDOW reconciliation (PM 13:00-15:00) ----
idxo=pd.Index(sorted(set(AM.dt)|set(PMo.dt)),name='dt')
print("\n"+"="*100)
print("OLD WINDOW (PM 13:00-15:00 k=1.5) -- reconciliation w/ memory 'Sharpe 1.72 vs 1.47', days=%d"%len(idxo))
O_AM  = series_on(idxo,(AM,1.0))
O_PMo = series_on(idxo,(PMo,1.0))
O_2eq = series_on(idxo,(AM,1.0),(PMo,1.0))
O_2his= series_on(idxo,(AM,1.0),(PMo,0.33))
row("SINGLE-SLOT (AM k=1.0)",        O_AM)
row("PM_old STANDALONE (k=1.5)",     O_PMo)
row("TWO-SLOT equal (AM1.0+PM1.0)",  O_2eq)
row("TWO-SLOT old   (AM1.0+PM0.33)", O_2his)
print("  corr(AM daily, PM_old daily) = %+.3f"%np.corrcoef(O_AM.values,O_PMo.values)[0,1])
# AM sharpe on AM-only active days (alt index)
amonly=daily(AM,1.0)
print("  [alt idx] AM Sharpe on AM-active-days-only = %.2f  (n=%d)"%(sharpe(amonly.values),len(amonly)))

# ---- RECENT REGIME (his window) ----
print("\n"+"="*100)
print("RECENT REGIME (his window PM 12-14):")
def block(series,lo,hi=None):
    ix=pd.to_datetime(series.index)
    m=(ix>=pd.to_datetime(lo))
    if hi: m&=(ix<pd.to_datetime(hi))
    return series[m]
for lbl,lo,hi in [('2025','2025-01-01','2026-01-01'),('2026 YTD','2026-01-01',None),('2026-06+','2026-06-01',None)]:
    am=block(S_AM,lo,hi); pm=block(S_PMh,lo,hi); t2=block(S_2his,lo,hi)
    if len(am)<3: print("  %s: n/a"%lbl); continue
    print(f"  {lbl:9s}: AM net$={am.values.sum()*PT:9,.0f} Sh={sharpe(am.values):5.2f} | "
          f"PM$={pm.values.sum()*PT*0.33:8,.0f} Sh={sharpe(pm.values):5.2f} PMactdays={(pm.values!=0).sum():2d} | "
          f"2slot net$={t2.values.sum()*PT:9,.0f} Sh={sharpe(t2.values):5.2f} dSh={sharpe(t2.values)-sharpe(am.values):+.2f}")
