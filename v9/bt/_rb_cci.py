# -*- coding: utf-8 -*-
"""_rb_cci.py  CCI single-slot AM sleeve - FULL HISTORY robustness audit. READ-ONLY.
Adds transaction cost (the original _hist_cci.py modelled ZERO cost), a vectorised
parameter sweep, random-entry permutation null, DSR, walk-forward, and a trade dump."""
import sys, glob, warnings; warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8', errors='replace'); sys.path.insert(0,'/root/v9/bt')
import numpy as np, pandas as pd, pytz
from _rb_lib import sharpe, dsr, book_stats
ET = pytz.timezone("America/New_York")
NQ_DIR = "/root/tlbrc_paper/ref/data_full/NQ"
PT = 2.0          # 1 MNQ
COST_PTS = 0.75   # per side, 1x
RNG = np.random.default_rng(23)

def load_all():
    dfs=[]
    for f in sorted(glob.glob(NQ_DIR+"/*.csv")):
        d=pd.read_csv(f); d.columns=[c.strip().lower() for c in d.columns]
        tc=next(c for c in ('time','timestamp','date','datetime') if c in d.columns)
        d[tc]=pd.to_datetime(d[tc],utc=True,errors='coerce')
        d=d.dropna(subset=[tc]).set_index(tc)
        if 'volume' not in d.columns: d['volume']=0.0
        dfs.append(d[['open','high','low','close','volume']].astype(float))
    df=pd.concat(dfs).sort_index()
    return df[~df.index.duplicated(keep='last')].sort_index()

def wilder_atr(h,l,c,n=14):
    pc=c.shift(1)
    tr=pd.concat([(h-l),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()

def cci(h,l,c,n):
    tp=(h+l+c)/3.0
    sma=tp.rolling(n).mean()
    md=tp.rolling(n).apply(lambda x: np.abs(x-x.mean()).mean(), raw=True)
    return (tp-sma)/(0.015*md)

print("[load] NQ 1-min ...", flush=True)
df=load_all()
et=df.index.tz_convert(ET)
sess=np.where(et.hour>=18, et.normalize(), (et-pd.Timedelta(days=1)).normalize())
df['vsess']=pd.to_datetime(sess)
tp=(df['high']+df['low']+df['close'])/3.0
cum_pv=(tp*df['volume']).groupby(df['vsess']).cumsum()
cum_v=df['volume'].groupby(df['vsess']).cumsum().replace(0,np.nan)
df['vwap']=(cum_pv/cum_v).ffill()
print("[calc] cci variants ...", flush=True)
CCIV={n: cci(df['high'],df['low'],df['close'],n).values for n in (14,20,30)}

o5=df['open'].resample('5min',label='left',closed='left').first()
h5=df['high'].resample('5min',label='left',closed='left').max()
l5=df['low'].resample('5min',label='left',closed='left').min()
c5=df['close'].resample('5min',label='left',closed='left').last()
b5=pd.DataFrame({'o':o5,'h':h5,'l':l5,'c':c5}).dropna()
EMASP={}
for sp in (30,50,80):
    EMASP[sp]=b5['c'].ewm(span=sp,adjust=False,min_periods=sp).mean()
atr5=wilder_atr(b5['h'],b5['l'],b5['c'],14)
low20_5m=b5['l'].rolling(20).min(); high20_5m=b5['h'].rolling(20).max()
b5et=b5.index.tz_convert(ET); slot=b5et.hour*60+b5et.minute
tmp=pd.DataFrame({'atr':atr5.values,'slot':slot},index=b5.index)
norm=pd.Series(index=b5.index,dtype=float)
for s,g in tmp.groupby('slot'):
    norm.loc[g.index]=g['atr'].shift(1).rolling(20,min_periods=10).median().values
avail=b5.index + pd.Timedelta(minutes=5)
cols={'atr5':atr5.values,'low20':low20_5m.values,'high20':high20_5m.values,'norm':norm.values}
for sp in EMASP: cols['ema%d'%sp]=EMASP[sp].values; cols['ema%d_30ago'%sp]=EMASP[sp].shift(6).values
F=pd.DataFrame(cols,index=avail); F=F[~F.index.duplicated(keep='last')]
F5=F.reindex(df.index,method='ffill')
idx_et=df.index.tz_convert(ET)
etmin=(idx_et.hour*60+idx_et.minute).values
etdate=np.array(idx_et.date)
px=df['close'].values; op=df['open'].values; hi=df['high'].values; lo=df['low'].values
vw=df['vwap'].values; atrv=F5['atr5'].values; nrm=F5['norm'].values
l20=F5['low20'].values; h20=F5['high20'].values
EMA={sp:F5['ema%d'%sp].values for sp in EMASP}
E30={sp:F5['ema%d_30ago'%sp].values for sp in EMASP}
n=len(df); ts=df.index
yr=idx_et.year.values
print("[calc] done. bars=%d  %s .. %s"%(n,etdate[0],etdate[-1]), flush=True)

# ---- day row index (RTH) ----
rth=(etmin>=570)&(etmin<960)
rows_by_day={}
pos=np.arange(n)[rth]
dk=etdate[rth]
chg=np.r_[True, dk[1:]!=dk[:-1]]
starts=np.flatnonzero(chg); ends=np.r_[starts[1:], len(dk)]
DAYS=[(dk[s], pos[s:e]) for s,e in zip(starts,ends)]
print("[calc] RTH days: %d"%len(DAYS), flush=True)

def run(cci_n=20, lvl=100.0, sep=0.0015, kstop=1.0, atrf=1.0, w0=570, w1=720,
        ema_sp=50, cost=COST_PTS, need_slope=True, need_vwap=True):
    C=CCIV[cci_n]; ema=EMA[ema_sp]; e30=E30[ema_sp]
    tr=[]
    for dkey, rws in DAYS:
        got=None
        for ii in range(1,len(rws)):
            i=rws[ii]
            m=etmin[i]
            if m<w0 or m>=w1: continue
            pc=C[i-1]; cc=C[i]
            if pc!=pc or cc!=cc: continue
            if ema[i]!=ema[i] or vw[i]!=vw[i] or atrv[i]!=atrv[i] or e30[i]!=e30[i] or nrm[i]!=nrm[i]: continue
            price=px[i]
            if atrf is not None and not (atrv[i] <= atrf*nrm[i]): continue
            lc = pc< -lvl and cc>=-lvl
            sc = pc>  lvl and cc<= lvl
            if lc and px[i]>ema[i] and ((ema[i]-vw[i])>=sep*price or not need_vwap) and (ema[i]>e30[i] or not need_slope):
                if i+1>=n or etdate[i+1]!=etdate[i]: continue
                got=('long',i+1,op[i+1],l20[i]-kstop*atrv[i]); break
            if sc and px[i]<ema[i] and ((vw[i]-ema[i])>=sep*price or not need_vwap) and (ema[i]<e30[i] or not need_slope):
                if i+1>=n or etdate[i+1]!=etdate[i]: continue
                got=('short',i+1,op[i+1],h20[i]+kstop*atrv[i]); break
        if not got: continue
        d,fi,entry,stop=got
        j=fi; xp=None; why=None
        while j<n and etdate[j]==etdate[fi]:
            if etmin[j]>=960: xp,why=op[j],'EOD'; break
            if d=='long' and lo[j]<=stop: xp,why=stop,'STOP'; break
            if d=='short' and hi[j]>=stop: xp,why=stop,'STOP'; break
            j+=1
        if xp is None: jj=min(j,n-1); xp,why=px[jj],'EOD'
        sgn=1 if d=='long' else -1
        pts=sgn*(xp-entry)-2*cost
        risk=abs(entry-stop)
        tr.append((str(dkey), d, float(entry), float(stop), float(xp), why, float(pts),
                   float(risk), float(pts/risk) if risk>0 else 0.0, int(fi), int(ts[fi].year)))
    return pd.DataFrame(tr, columns=['date','dir','entry','stop','exit','why','pts','risk','R','i','year'])

def summ(T):
    if len(T)==0: return dict(n=0,net=0,exp=0,pf=0,win=0,sh=0)
    p=T['pts'].values*PT
    w=p[p>0].sum(); l=-p[p<0].sum()
    return dict(n=len(T), net=p.sum(), exp=T['pts'].mean(), pf=(w/l if l>0 else 9.99),
                win=100*(p>0).mean(), sh=sharpe(T['R'].values, ann=len(T)/5.65))

BASE=dict(cci_n=20,lvl=100.0,sep=0.0015,kstop=1.0,atrf=1.0,w0=570,w1=720,ema_sp=50)
print("\n[run] deployed cell ...", flush=True)
T=run(**BASE)
E1=T[T.year<2024]; E2=T[T.year>=2024]
s=summ(T)
print("="*104)
print("DEPLOYED CELL (AM only, cci20, +/-100 cross, sep 0.15pct, k=1.0, ATR<=1.0x, 09:30-12:00), COST 0.75pt/side")
print("="*104)
print("  n=%d  net $%.0f @1MNQ  exp %+.2f pts/tr  PF %.2f  win %.1f%%"%(s['n'],s['net'],s['exp'],s['pf'],s['win']))
print("  [no-cost reference: net $%.0f]"%((T['pts'].values+1.5).sum()*PT))
print("  era1 2021-23: n=%d net $%.0f exp %+.2f  |  era2 2024-26: n=%d net $%.0f exp %+.2f"%(
    len(E1),E1['pts'].sum()*PT,E1['pts'].mean() if len(E1) else 0,
    len(E2),E2['pts'].sum()*PT,E2['pts'].mean() if len(E2) else 0))
T.to_csv('/root/v9/bt/_rb_cci_trades.csv',index=False)

print("\n"+"="*104)
print("1. PARAMETER SENSITIVITY  -- net$@1MNQ / n / expPts, full | era1(21-23) | era2(24-26)")
print("="*104)
CELLS=[]
def show(label, **kw):
    p=dict(BASE); p.update(kw)
    t=run(**p); a=summ(t); e1=summ(t[t.year<2024]); e2=summ(t[t.year>=2024])
    a['srt']=float(t['R'].mean()/t['R'].std(ddof=1)) if len(t)>10 else np.nan
    CELLS.append((label,a,e1,e2,tuple(sorted(kw.items()))))
    print("  %-34s  full $%8.0f n%4d e%+6.2f | era1 $%8.0f n%3d e%+6.2f | era2 $%8.0f n%3d e%+6.2f"%(
        label,a['net'],a['n'],a['exp'],e1['net'],e1['n'],e1['exp'],e2['net'],e2['n'],e2['exp']))
print("  -- core 3D grid: cross level x stop-k x vwap/ema separation --")
for lvl in (80.,100.,120.):
    for k in (0.5,1.0,1.5):
        for sp in (0.0005,0.0015,0.0025):
            show("lvl%d k%.1f sep%.2f%%"%(lvl,k,sp*100), lvl=lvl,kstop=k,sep=sp)
print("  -- one-at-a-time --")
for cn in (14,20,30): show("cci_n=%d"%cn, cci_n=cn)
for a in (0.8,1.0,1.25,None): show("atr_filter=%s"%a, atrf=a)
for w in (660,720,780): show("window_end=%d:%02d"%(w//60,w%60), w1=w)
for sp in (30,50,80): show("ema_span=%d"%sp, ema_sp=sp)
show("no vwap-sep filter", need_vwap=False)
show("no ema-slope filter", need_slope=False)

core=[c for c in CELLS if c[0].startswith('lvl')]
nets=np.array([c[1]['net'] for c in core]); e1n=np.array([c[2]['net'] for c in core]); e2n=np.array([c[3]['net'] for c in core])
print("\n  27-cell core grid: full net$ mean $%.0f median $%.0f  positive %d/27"%(nets.mean(),np.median(nets),(nets>0).sum()))
print("     era1 positive %d/27 (mean $%.0f) ; era2 positive %d/27 (mean $%.0f)"%(
    (e1n>0).sum(),e1n.mean(),(e2n>0).sum(),e2n.mean()))
both=sum(1 for a,b in zip(e1n,e2n) if a>0 and b>0)
print("     positive in BOTH eras: %d/27"%both)
dep=[c for c in core if c[0]=='lvl100 k1.0 sep0.15%'][0]
print("     deployed cell rank (full net) %d/27 ; era1 %d/27 ; era2 %d/27"%(
    1+int((nets>dep[1]['net']).sum()), 1+int((e1n>dep[2]['net']).sum()), 1+int((e2n>dep[3]['net']).sum())))

print("\n"+"="*104); print("2. PERMUTATION NULL - random-entry (same day-count, same window, same direction mix, same stop rule)")
print("="*104)
real=T['pts'].sum()*PT; realR=T['R'].mean()
nlong=(T['dir']=='long').mean(); ntr=len(T)
# candidate pool: every RTH bar in the AM window that has valid indicators
poolmask=(etmin>=570)&(etmin<720)&np.isfinite(atrv)&np.isfinite(l20)&np.isfinite(h20)
pool=np.arange(n)[poolmask]
nulls=[]; nullR=[]
for d in range(400):
    pick=RNG.choice(pool, size=ntr, replace=False)
    tot=0.0; rs=[]
    for i in pick:
        if i+1>=n or etdate[i+1]!=etdate[i]: continue
        long_= RNG.random()<nlong
        entry=op[i+1]
        stop=(l20[i]-BASE['kstop']*atrv[i]) if long_ else (h20[i]+BASE['kstop']*atrv[i])
        risk=abs(entry-stop)
        if risk<=0: continue
        j=i+1; xp=None
        while j<n and etdate[j]==etdate[i+1]:
            if etmin[j]>=960: xp=op[j]; break
            if long_ and lo[j]<=stop: xp=stop; break
            if (not long_) and hi[j]>=stop: xp=stop; break
            j+=1
        if xp is None: xp=px[min(j,n-1)]
        sgn=1 if long_ else -1
        p=sgn*(xp-entry)-1.5
        tot+=p*PT; rs.append(p/risk)
    nulls.append(tot); nullR.append(np.mean(rs))
nulls=np.array(nulls); nullR=np.array(nullR)
print("  400 random-entry draws: real net $%.0f  vs null mean $%.0f std $%.0f -> %.1fth pctile (p=%.3f)"%(
    real,nulls.mean(),nulls.std(),(nulls<real).mean()*100,1-(nulls<real).mean()))
print("  by mean-R: real %+.3f R/tr vs null mean %+.3f -> %.1fth pctile"%(realR,nullR.mean(),(nullR<realR).mean()*100))

print("\n"+"="*104); print("3. COST SENSITIVITY"); print("="*104)
for m in (1,2,3):
    t=run(**dict(BASE, cost=COST_PTS*m)); a=summ(t)
    e1=summ(t[t.year<2024]); e2=summ(t[t.year>=2024])
    print("  %dx (%.2f pt/side, %.1f pt r/t): net $%.0f  exp %+.2f pts  PF %.2f | era1 $%.0f | era2 $%.0f"%(
        m,COST_PTS*m,2*COST_PTS*m,a['net'],a['exp'],a['pf'],e1['net'],e2['net']))

print("\n"+"="*104); print("4. DEFLATED SHARPE (trade-level R)"); print("="*104)
Rv=T['R'].values
NT=len(CELLS)
trial_sr=np.array([c[1]['srt'] for c in CELLS if c[1].get('srt')==c[1].get('srt')])
srstd=float(trial_sr.std(ddof=1))
sr,sr0,p=dsr(Rv, srstd, NT)
print("  n trades = %d over 5.65 yrs (%.1f/yr)"%(len(Rv), len(Rv)/5.65))
print("  N_trials = %d (this audit's grid alone; the ORIGINAL search that produced the cell is larger)"%NT)
print("  trial SR std (per trade) = %.4f"%srstd)
print("  SR/trade %.4f ; SR0 %.4f ; t-stat %.2f"%(sr,sr0,Rv.mean()/(Rv.std(ddof=1)/np.sqrt(len(Rv)))))
print("  DSR = %.3f -> %s at 0.95"%(p,'PASS' if p>=0.95 else 'FAIL'))
for NT2 in (10,50,100,500):
    _,s0,pb=dsr(Rv,srstd,NT2); print("    sensitivity N_trials=%-4d DSR=%.3f"%(NT2,pb))
print("  [also on $ P&L per trade] mean $%.0f std $%.0f  t=%.2f"%(
    (T['pts']*PT).mean(),(T['pts']*PT).std(ddof=1),(T['pts']*PT).mean()/((T['pts']*PT).std(ddof=1)/np.sqrt(len(T)))))

print("\n"+"="*104); print("5. ERA STABILITY"); print("="*104)
print("  per-calendar-year (deployed, WITH cost):")
for y in sorted(T.year.unique()):
    g=T[T.year==y]
    print("    %d: n=%2d  net $%+8.0f  exp %+7.2f pts  win %.0f%%  medR %+.2f"%(
        y,len(g),g['pts'].sum()*PT,g['pts'].mean(),100*(g['pts']>0).mean(),g['R'].median()))
TT=T.sort_values('i').reset_index(drop=True)
ed=np.linspace(0,len(TT),6).astype(int)
print("  walk-forward, 5 equal-trade-count contiguous folds (params FIXED):")
for i in range(5):
    a,b=ed[i],ed[i+1]; g=TT.iloc[a:b]
    print("    fold%d %s..%s n=%d  exp %+7.2f pts  net $%+.0f  win %.0f%%"%(
        i+1,g['date'].iloc[0],g['date'].iloc[-1],len(g),g['pts'].mean(),g['pts'].sum()*PT,100*(g['pts']>0).mean()))
print("  concentration: top-1 trade = %.0f%% of net ; top-3 = %.0f%% ; top-5 = %.0f%%"%(
    100*TT['pts'].max()/TT['pts'].sum(), 100*np.sort(TT['pts'])[-3:].sum()/TT['pts'].sum(),
    100*np.sort(TT['pts'])[-5:].sum()/TT['pts'].sum()))
print("  net excluding best 3 trades: $%.0f  ; excluding best 5: $%.0f"%(
    (np.sort(TT['pts'])[:-3].sum())*PT,(np.sort(TT['pts'])[:-5].sum())*PT))
print("\n  wrote _rb_cci_trades.csv")
