"""_cf_cci.py - anti-overfit stress battery for CCI single-slot (AM, k=1.0). READ-ONLY."""
import glob, numpy as np, pandas as pd, pytz
from collections import defaultdict
import math
class _N:
    @staticmethod
    def cdf(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
    @staticmethod
    def ppf(p):
        a=[-3.969683028665376e+01,2.209460984245205e+02,-2.759285104469687e+02,1.383577518672690e+02,-3.066479806614716e+01,2.506628277459239e+00]
        b=[-5.447609879822406e+01,1.615858368580409e+02,-1.556989798598866e+02,6.680131188771972e+01,-1.328068155288572e+01]
        c=[-7.784894002430293e-03,-3.223964580411365e-01,-2.400758277161838e+00,-2.549732539343734e+00,4.374664141464968e+00,2.938163982698783e+00]
        d=[7.784695709041462e-03,3.224671290700398e-01,2.445134137142996e+00,3.754408661907416e+00]
        pl=0.02425
        if p<pl:
            q=math.sqrt(-2*math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        if p<=1-pl:
            q=p-0.5; r=q*q; return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q/(((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
        q=math.sqrt(-2*math.log(1-p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
norm=_N()
def skew(x):
    x=np.asarray(x,float); m=x.mean(); s=x.std()
    return ((x-m)**3).mean()/s**3 if s>0 else 0.0
def kurtosis(x,fisher=True):
    x=np.asarray(x,float); m=x.mean(); s=x.std()
    k=((x-m)**4).mean()/s**4 if s>0 else 0.0
    return k-3 if fisher else k
ET=pytz.timezone("America/New_York"); NQ_DIR="/root/v9/data_1m/NQ"; PT_USD=20.0
RNG=np.random.default_rng(11)

def load_all():
    dfs=[]
    for f in sorted(glob.glob(NQ_DIR+"/*.csv")):
        d=pd.read_csv(f); d.columns=[c.strip().lower() for c in d.columns]
        tc=next(c for c in ('time','timestamp','date','datetime') if c in d.columns)
        d[tc]=pd.to_datetime(d[tc],utc=True,errors='coerce')
        d=d.dropna(subset=[tc]).set_index(tc)
        if 'volume' not in d.columns: d['volume']=0.0
        dfs.append(d[['open','high','low','close','volume']].astype(float))
    df=pd.concat(dfs).sort_index(); df=df[~df.index.duplicated(keep='last')].sort_index()
    return df
def wilder_atr(h,l,c,n=14):
    pc=c.shift(1); tr=pd.concat([(h-l),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
def cci(h,l,c,n):
    tp=(h+l+c)/3.0; sma=tp.rolling(n).mean()
    md=tp.rolling(n).apply(lambda x: np.abs(x-x.mean()).mean(), raw=True)
    return (tp-sma)/(0.015*md)

DF=load_all()
_CACHE={}
def build(cci_len):
    if cci_len in _CACHE: return _CACHE[cci_len]
    df=DF.copy()
    et=df.index.tz_convert(ET)
    sess=np.where(et.hour>=18, et.normalize(), (et-pd.Timedelta(days=1)).normalize())
    df['vsess']=pd.to_datetime(sess)
    tp=(df['high']+df['low']+df['close'])/3.0
    cum_pv=(tp*df['volume']).groupby(df['vsess']).cumsum()
    cum_v=df['volume'].groupby(df['vsess']).cumsum().replace(0,np.nan)
    df['vwap']=(cum_pv/cum_v).ffill()
    df['cci']=cci(df['high'],df['low'],df['close'],cci_len)
    o5=df['open'].resample('5min',label='left',closed='left').first()
    h5=df['high'].resample('5min',label='left',closed='left').max()
    l5=df['low'].resample('5min',label='left',closed='left').min()
    c5=df['close'].resample('5min',label='left',closed='left').last()
    b5=pd.DataFrame({'o':o5,'h':h5,'l':l5,'c':c5}).dropna()
    ema50=b5['c'].ewm(span=50,adjust=False,min_periods=50).mean()
    atr5=wilder_atr(b5['h'],b5['l'],b5['c'],14); ema50_30ago=ema50.shift(6)
    low20=b5['l'].rolling(20).min(); high20=b5['h'].rolling(20).max()
    b5et=b5.index.tz_convert(ET); slot=b5et.hour*60+b5et.minute
    tmp=pd.DataFrame({'atr':atr5.values,'slot':slot},index=b5.index)
    norm_s=pd.Series(index=b5.index,dtype=float)
    for s,g in tmp.groupby('slot'):
        m=g['atr'].shift(1).rolling(20,min_periods=10).median(); norm_s.loc[g.index]=m.values
    avail=b5.index+pd.Timedelta(minutes=5)
    F=pd.DataFrame({'ema50':ema50.values,'atr5':atr5.values,'ema50_30ago':ema50_30ago.values,
        'low20':low20.values,'high20':high20.values,'norm':norm_s.values},index=avail)
    F=F[~F.index.duplicated(keep='last')]; F5=F.reindex(df.index,method='ffill')
    for cc in F.columns: df[cc]=F5[cc].values
    ie=df.index.tz_convert(ET); df['etmin']=ie.hour*60+ie.minute
    out=(df, np.array(ie.date)); _CACHE[cci_len]=out
    return out

def runcci(cci_len=20, lvl=100, sep=0.0015, volmult=1.0, cost_rt=0.0, slot='AM'):
    df,etdate=build(cci_len)
    px=df['close'].values; op=df['open'].values; hi=df['high'].values; lo=df['low'].values
    cciv=df['cci'].values; ema=df['ema50'].values; vw=df['vwap'].values; atrv=df['atr5'].values
    e30=df['ema50_30ago'].values; l20=df['low20'].values; h20=df['high20'].values
    nrm=df['norm'].values; etmin=df['etmin'].values; n=len(df); ts=df.index
    w0,w1,kstop=(9*60+30,12*60,1.0) if slot=='AM' else (13*60,15*60,1.5)
    def scan(rows):
        for ii in range(1,len(rows)):
            i=rows[ii]; m=etmin[i]
            if m<w0 or m>=w1: continue
            pc=cciv[i-1]; cc=cciv[i]
            if np.isnan(pc) or np.isnan(cc) or np.isnan(ema[i]) or np.isnan(vw[i]) or np.isnan(atrv[i]) or np.isnan(e30[i]) or np.isnan(nrm[i]): continue
            price=px[i]
            if not (atrv[i]<=volmult*nrm[i]): continue
            lc=pc<-lvl and cc>=-lvl; sc=pc>lvl and cc<=lvl
            if lc and px[i]>ema[i] and (ema[i]-vw[i])>=sep*price and ema[i]>e30[i]:
                if i+1>=n or etdate[i+1]!=etdate[i]: continue
                return ('long',i+1,op[i+1],l20[i]-kstop*atrv[i])
            if sc and px[i]<ema[i] and (vw[i]-ema[i])>=sep*price and ema[i]<e30[i]:
                if i+1>=n or etdate[i+1]!=etdate[i]: continue
                return ('short',i+1,op[i+1],h20[i]+kstop*atrv[i])
        return None
    def sim(direction,fi,stop):
        j=fi
        while j<n and etdate[j]==etdate[fi]:
            if etmin[j]>=16*60: return op[j]
            if direction=='long' and lo[j]<=stop: return stop
            if direction=='short' and hi[j]>=stop: return stop
            j+=1
        return px[min(j,n-1)]
    rth=(etmin>=9*60+30)&(etmin<16*60); pos=np.arange(n)[rth]; dk=etdate[rth]
    daymap=defaultdict(list)
    for p,d in zip(pos,dk): daymap[d].append(p)
    trades=[]
    for d in sorted(daymap.keys()):
        r=scan(daymap[d])
        if not r: continue
        direction,fi,entry,stop=r; xp=sim(direction,fi,stop)
        sgn=1 if direction=='long' else -1
        pts=sgn*(xp-entry)-(cost_rt*entry if cost_rt>0 else 0.0)
        trades.append((str(d)[:4],pts,direction,fi,entry,stop))
    return trades

def netusd(trades): return sum(t[1] for t in trades)*PT_USD
def yearly(trades):
    y=defaultdict(list)
    for t in trades: y[t[0]].append(t[1])
    return {k:(len(v),round(sum(v)*PT_USD)) for k,v in sorted(y.items())}

print("="*80); print("CCI SINGLE-SLOT (AM, k=1.0) STRESS BATTERY"); print("="*80)
base=runcci()
print(f"\nBASELINE (len20,lvl100,sep0.15pct,vol1.0): n={len(base)} net=${netusd(base):,.0f}")
print("  yearly:",yearly(base))

print("\n--- 1. PARAMETER SENSITIVITY (net$ / n) ---")
print("CCI length:")
for L in [14,20,26]:
    t=runcci(cci_len=L); print(f"  len={L}: n={len(t):3d} net=${netusd(t):>9,.0f}  {yearly(t)}")
print("Extreme level:")
for lv in [80,100,120]:
    t=runcci(lvl=lv); print(f"  lvl={lv}: n={len(t):3d} net=${netusd(t):>9,.0f}  {yearly(t)}")
print("VWAP-sep threshold:")
for s in [0.0010,0.0015,0.0020]:
    t=runcci(sep=s); print(f"  sep={s*100:.2f}pct: n={len(t):3d} net=${netusd(t):>9,.0f}  {yearly(t)}")
print("ATR vol-gate mult:")
for vm in [0.8,1.0,1.2]:
    t=runcci(volmult=vm); print(f"  vol={vm}: n={len(t):3d} net=${netusd(t):>9,.0f}  {yearly(t)}")

print("\n--- 3. COST SENSITIVITY (round-turn = price*frac) ---")
for mult,lbl in [(0.0,'0x(none)'),(0.00015,'1x=1.5bp~3pt'),(0.0003,'2x'),(0.00045,'3x')]:
    t=runcci(cost_rt=mult); print(f"  {lbl:14s}: n={len(t):3d} net=${netusd(t):>9,.0f}  avg_pts={np.mean([x[1] for x in t]):+.2f}")

print("\n--- 4. SUB-PERIOD (baseline, no cost) ---")
y=yearly(base); tot=netusd(base)
for yr,(nn,vv) in y.items(): print(f"  {yr}: n={nn:3d} net=${vv:>9,.0f} ({vv/tot*100:+.0f}pct of total)")
pts=[t[1] for t in base]; half=len(pts)//2
print(f"  IS(1st half n={half}) net=${sum(pts[:half])*PT_USD:,.0f}  OOS(2nd half n={len(pts)-half}) net=${sum(pts[half:])*PT_USD:,.0f}")
ex26=sum(v for k,(n0,v) in y.items() if k!='2026')
print(f"  ex-2026 (2024+2025 only): net=${ex26:,.0f}")

print("\n--- 2. PERMUTATION NULL (random-entry placebo, same n & window) ---")
df,etdate=build(20)
lo=df['low'].values; hi=df['high'].values; op=df['open'].values; px=df['close'].values
atrv=df['atr5'].values; l20=df['low20'].values; h20=df['high20'].values; etmin=df['etmin'].values
n=len(df)
cand=[]
for i in range(n-1):
    if 9*60+30<=etmin[i]<12*60 and etdate[i+1]==etdate[i] and not np.isnan(atrv[i]) and not np.isnan(l20[i]) and not np.isnan(h20[i]):
        cand.append(i)
cand=np.array(cand)
dirs=[t[2] for t in base]; realnet=sum(t[1] for t in base)
def simrand(i,direction):
    fi=i+1; entry=op[fi]
    stop=l20[i]-1.0*atrv[i] if direction=='long' else h20[i]+1.0*atrv[i]
    j=fi; xp=px[min(fi,n-1)]
    while j<n and etdate[j]==etdate[fi]:
        if etmin[j]>=16*60: xp=op[j]; break
        if direction=='long' and lo[j]<=stop: xp=stop; break
        if direction=='short' and hi[j]>=stop: xp=stop; break
        j+=1
    else: xp=px[min(j,n-1)]
    sgn=1 if direction=='long' else -1
    return sgn*(xp-entry)
nd=len(base)
null=[]
for _ in range(200):
    pick=RNG.choice(cand,size=nd,replace=False); pdirs=RNG.permutation(dirs)
    null.append(sum(simrand(i,d) for i,d in zip(pick,pdirs))*PT_USD)
null=np.array(null); pct=(null<realnet*PT_USD).mean()*100
print(f"  real net=${realnet*PT_USD:,.0f}  vs 200 random: mean=${null.mean():,.0f} sd=${null.std():,.0f}  -> {pct:.0f}th pctile")

print("\n--- 5. DEFLATED SHARPE (per-trade series, N trials from grid) ---")
grid=[]
for L in [14,20,26]:
    for lv in [80,100,120]:
        for s in [0.0010,0.0015,0.0020]:
            for vm in [0.8,1.0,1.2]:
                tt=runcci(cci_len=L,lvl=lv,sep=s,volmult=vm)
                p=np.array([x[1] for x in tt])
                sr=p.mean()/p.std() if len(p)>2 and p.std()>0 else 0.0
                grid.append(sr)
grid=np.array(grid); Ntr=len(grid)
p=np.array([t[1] for t in base]); T=len(p)
sr=p.mean()/p.std()
sk=skew(p); ku=kurtosis(p,fisher=False); varSR=grid.var()
gamma=0.5772156649
emax=np.sqrt(varSR)*((1-gamma)*norm.ppf(1-1.0/Ntr)+gamma*norm.ppf(1-1.0/(Ntr*np.e)))
denom=np.sqrt(1-sk*sr+(ku-1)/4.0*sr**2)
dsr=norm.cdf((sr-emax)*np.sqrt(T-1)/denom)
print(f"  per-trade SR={sr:.3f} (T={T}), skew={sk:.2f} kurt={ku:.2f}")
print(f"  N_trials={Ntr}, sd(SR across trials)={np.sqrt(varSR):.3f}, E[max SR|null]={emax:.3f}")
print(f"  ann_SR(252/~17peryr approx)={sr*np.sqrt(T/ (835/252)):.2f}")
print(f"  DSR={dsr:.3f}  -> {'PASS' if dsr>0.95 else 'FAIL'} (>0.95)")
