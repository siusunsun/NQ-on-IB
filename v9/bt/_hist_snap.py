import glob,sys,warnings; warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
import numpy as np, pandas as pd
RNG=np.random.default_rng(7); COST_FRAC=0.00015
NQ_DIR='/root/tlbrc_paper/ref/data_full/NQ/'

def load_1m():
    parts=[]
    for f in sorted(glob.glob(NQ_DIR+'*.csv')):
        try: parts.append(pd.read_csv(f,usecols=['time','close']))
        except: pass
    d=pd.concat(parts,ignore_index=True)
    t=pd.to_datetime(d['time'],utc=True,errors='coerce')
    if t.isna().mean()>0.5: t=pd.to_datetime(d['time'],utc=True,unit='s',errors='coerce')
    d['time']=t
    d=d.dropna(subset=['time']).drop_duplicates('time').set_index('time').sort_index().tz_convert('America/New_York')
    return d['close']

def daily_at(c1m,hh,mm):
    cutoff=c1m.index.normalize()+pd.Timedelta(hours=hh,minutes=mm)
    ok=c1m[c1m.index<=cutoff]
    day=ok.groupby(ok.index.normalize()).last(); day.index=day.index.tz_localize(None)
    return day.dropna()

def sh(r,ann=252):
    r=np.asarray(r,float); return r.mean()/r.std()*np.sqrt(ann) if r.std()>0 else 0.0

def snapback(close,lb,thr):
    z=(close-close.rolling(lb).mean())/close.rolling(lb).std(ddof=0)
    df=pd.DataFrame({'c':close,'z':z}).dropna()
    ret=df['c'].pct_change().fillna(0).values
    pos=(df['z'].values<-thr).astype(float)
    expo=np.r_[0.0,pos[:-1]]
    dpos=np.abs(np.diff(np.r_[0.0,expo]))
    r=expo*ret-(COST_FRAC/2)*dpos
    return pd.Series(r,index=df.index),pd.Series(expo,index=df.index),pd.Series(ret,index=df.index)

def perm(r,expo,ret,n=2000):
    cost=r.values-expo.values*ret.values; real=sh(r.values); e=expo.values
    null=np.array([sh(e*RNG.permutation(ret.values)+cost) for _ in range(n)])
    return real,(null<real).mean()*100

c1m=load_1m()
nq=daily_at(c1m,23,59)
print('NQ midnight daily:',len(nq),nq.index[0].date(),'->',nq.index[-1].date(),' yrs=%.2f'%(len(nq)/252))
print('='*92)
print('PART 1 ROBUSTNESS GRID (Sharpe @1.5bp): full / H1 / H2')
mid=nq.index[len(nq)//2]
print('H1',nq.index[0].date(),'..',mid.date(),' H2',mid.date(),'..',nq.index[-1].date())
print(f"{'lb':<6}"+''.join(f'{'thr '+str(t):>22}' for t in [1.0,1.5,2.0]))
print(f"{'':6}"+f"{'full   H1    H2':>22}"*3)
for lb in [10,15,20,25,30]:
    row=f'n={lb:<4}'
    for thr in [1.0,1.5,2.0]:
        r,e,rr=snapback(nq,lb,thr)
        row+=f'{sh(r.values):>9.2f}{sh(r[r.index<mid].values):>6.2f}{sh(r[r.index>=mid].values):>7.2f}'
    print(row)
r,e,rr=snapback(nq,20,1.5)
real,pct=perm(r,e,rr)
ntr=int(((e.values!=0)&(np.r_[0,e.values[:-1]]==0)).sum())
print(f'\ndeployed (20,1.5): Sharpe {real:.2f}, perm {pct:.0f}th pct, entries {ntr} = {ntr/(len(nq)/252):.1f}/yr, TiM {e.mean()*100:.0f}%')
print(f'   H1 {sh(r[r.index<mid].values):+.2f} / H2 {sh(r[r.index>=mid].values):+.2f}   net cum-ret {r.sum()*100:+.1f}%')
print('\n--- per-year Sharpe / netret%% / entries (deployed 20,1.5) ---')
for y in sorted(set(nq.index.year)):
    ry=r[r.index.year==y]; ey=e[e.index.year==y]
    nty=int(((ey.values!=0)&(np.r_[0,ey.values[:-1]]==0)).sum())
    print(f'  {y}: Sharpe {sh(ry.values):+.2f}  netret {ry.sum()*100:+5.1f}%  entries {nty}  TiM {ey.mean()*100:.0f}%')
print('\n--- 2026 H2 focus (2026-06+) ---')
r26=r[r.index>=pd.Timestamp('2026-06-01')]; e26=e[e.index>=pd.Timestamp('2026-06-01')]
nt26=int(((e26.values!=0)&(np.r_[0,e26.values[:-1]]==0)).sum())
print(f'  2026-06-01..end: days {len(r26)}  Sharpe {sh(r26.values):+.2f}  netret {r26.sum()*100:+.1f}%  entries {nt26}')
print('\n--- ANCHOR ROBUSTNESS (deployed 20,1.5) ---')
for hh,mm,lbl in [(23,59,'23:59 midnight'),(16,0,'16:00 cash'),(18,30,'18:30 evening')]:
    dd=daily_at(c1m,hh,mm); rr2,ee2,_=snapback(dd,20,1.5); real2,pct2=perm(rr2,ee2,_,n=800)
    nt2=int(((ee2.values!=0)&(np.r_[0,ee2.values[:-1]]==0)).sum())
    print(f'  {lbl:<16} Sharpe {real2:+.2f}  perm {pct2:.0f}th  entries {nt2}')
