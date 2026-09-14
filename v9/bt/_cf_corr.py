"""_cf_corr.py - cross-strategy correlation stability (per-year). READ-ONLY.
Series: daily summed R (0-fill on union business calendar). Snapback uses daily return."""
import glob, numpy as np, pandas as pd
NQ_DIR='/root/v9/data_1m/NQ/'

# --- CCI daily R (AM single-slot) ---
cci=pd.read_csv('/root/v9/bt/_cci_trades.csv')
cci=cci[cci['slot']=='AM'].copy()
cci['date']=pd.to_datetime(cci['date']).dt.normalize()
cci['R']=cci['pts']/cci['risk']
cci_d=cci.groupby('date')['R'].sum()

# --- R3 re-entry daily R ---
r3=pd.read_csv('/root/v9/bt/_cf_r3_daily.csv')
r3['date']=pd.to_datetime(r3['date']).dt.normalize()
r3_d=r3.groupby('date')['R'].sum()

# --- V9 portfolio daily (trades_all) sum R by entry ET date ---
v9=pd.read_csv('/root/v9/bt/trades_all.csv')
v9['date']=pd.to_datetime(v9['entry_time_et']).dt.normalize()
v9['R']=pd.to_numeric(v9['R'],errors='coerce').fillna(0)
v9_d=v9.groupby('date')['R'].sum()
# also V9 R3-sleeve alone
v9r3=v9[v9['sleeve']=='VWAP_LONG_R3'].groupby('date')['R'].sum()

# --- Snapback daily return (deployed 20,1.5) ---
def load_1m():
    parts=[]
    for f in sorted(glob.glob(NQ_DIR+'*.csv')):
        try: parts.append(pd.read_csv(f,usecols=['time','close']))
        except: pass
    d=pd.concat(parts,ignore_index=True); t=pd.to_datetime(d['time'],utc=True,errors='coerce')
    if t.isna().mean()>0.5: t=pd.to_datetime(d['time'],utc=True,unit='s',errors='coerce')
    d['time']=t
    return d.dropna(subset=['time']).drop_duplicates('time').set_index('time').sort_index().tz_convert('America/New_York')['close']
c1m=load_1m()
cutoff=c1m.index.normalize()+pd.Timedelta(hours=23,minutes=59)
ok=c1m[c1m.index<=cutoff]; day=ok.groupby(ok.index.normalize()).last(); day.index=day.index.tz_localize(None)
nq=day.dropna()
z=(nq-nq.rolling(20).mean())/nq.rolling(20).std(ddof=0)
d2=pd.DataFrame({'c':nq,'z':z}).dropna(); ret=d2['c'].pct_change().fillna(0).values
expo=np.r_[0.0,(d2['z'].values<-1.5).astype(float)[:-1]]
snap_d=pd.Series(expo*ret,index=d2.index)

# --- union calendar ---
alld=sorted(set(cci_d.index)|set(r3_d.index)|set(v9_d.index)|set(snap_d.index))
cal=pd.bdate_range(min(alld),max(alld))
def align(s): return s.reindex(cal).fillna(0.0)
CCI=align(cci_d); R3=align(r3_d); V9=align(v9_d); SNAP=align(snap_d); V9R3=align(v9r3)
M=pd.DataFrame({'CCI':CCI,'SNAP':SNAP,'R3re':R3,'V9':V9})
print("Daily series (0-fill business cal). Active-day counts:")
for c in M.columns: print(f"  {c}: {(M[c]!=0).sum()} active days")
print("\n--- 6. PAIRWISE CORRELATION (full sample) ---")

def pear(a,b):
    a=np.asarray(a,float);b=np.asarray(b,float);a=a-a.mean();b=b-b.mean()
    d=np.sqrt((a*a).sum()*(b*b).sum());return (a*b).sum()/d if d>0 else 0.0
cols=list(M.columns)
print('     '+'  '.join(f'{c:>6}' for c in cols))
for i in cols:
    print(f'{i:>5} '+'  '.join(f'{pear(M[i],M[j]):>6.3f}' for j in cols))
print("\nR3re vs V9-R3sleeve corr (full):", round(pear(R3,V9R3),3),
      "  (overlay is child of R3 sleeve - expect some link)")
print("\n--- PER-YEAR pairwise corr ---")
pairs=[('CCI','SNAP'),('CCI','R3re'),('CCI','V9'),('SNAP','R3re'),('SNAP','V9'),('R3re','V9')]
years=sorted(set(M.index.year))
hdr="year  "+"  ".join(f"{a}-{b}" for a,b in pairs)
print(hdr)
for y in years:
    sub=M[M.index.year==y]
    if len(sub)<20: continue
    vals=[]
    for a,b in pairs:
        if sub[a].std()>0 and sub[b].std()>0: vals.append(f"{pear(sub[a].values,sub[b].values):+.2f}")
        else: vals.append("  na")
    print(f"{y}  "+"    ".join(f"{v:>6}" for v in vals))
# max abs pairwise per year (stress spike check)
print("\nMax |corr| among the 4 strategies, per year:")
for y in years:
    sub=M[M.index.year==y]
    if len(sub)<20: continue
    import itertools
    mx=max(abs(pear(sub[a],sub[b])) for a,b in itertools.combinations(cols,2)); print(f"  {y}: max|corr|={mx:.2f}")
