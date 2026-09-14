"""_cf_snap.py - snapback cost + DSR extension. READ-ONLY."""
import glob, numpy as np, pandas as pd, math
RNG=np.random.default_rng(7); NQ_DIR='/root/v9/data_1m/NQ/'
class _N:
    @staticmethod
    def cdf(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
    @staticmethod
    def ppf(p):
        a=[-3.969683028665376e+01,2.209460984245205e+02,-2.759285104469687e+02,1.383577518672690e+02,-3.066479806614716e+01,2.506628277459239e+00]
        b=[-5.447609879822406e+01,1.615858368580409e+02,-1.556989798598866e+02,6.680131188771972e+01,-1.328068155288572e+01]
        c=[-7.784894002430293e-03,-3.223964580411365e-01,-2.400758277161838e+00,-2.549732539343734e+00,4.374664141464968e+00,2.938163982698783e+00]
        d=[7.784695709041462e-03,3.224671290700398e-01,2.445134137142996e+00,3.754408661907416e+00]; pl=0.02425
        if p<pl:
            q=math.sqrt(-2*math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        if p<=1-pl:
            q=p-0.5; r=q*q; return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q/(((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
        q=math.sqrt(-2*math.log(1-p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
norm=_N()
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
def snapback(close,lb,thr,cost_frac):
    z=(close-close.rolling(lb).mean())/close.rolling(lb).std(ddof=0)
    df=pd.DataFrame({'c':close,'z':z}).dropna()
    ret=df['c'].pct_change().fillna(0).values
    pos=(df['z'].values<-thr).astype(float); expo=np.r_[0.0,pos[:-1]]
    dpos=np.abs(np.diff(np.r_[0.0,expo]))
    r=expo*ret-(cost_frac/2)*dpos
    return pd.Series(r,index=df.index),pd.Series(expo,index=df.index),pd.Series(ret,index=df.index)

c1m=load_1m(); nq=daily_at(c1m,23,59)
print("SNAPBACK cost + DSR extension. days=%d"%len(nq))
print("\n--- 3. COST SENSITIVITY (deployed 20,1.5) ---")
for mult,lbl in [(0.0,'0x'),(1.0,'1x=1.5bp'),(2.0,'2x=3bp'),(3.0,'3x=4.5bp')]:
    r,e,rr=snapback(nq,20,1.5,0.00015*mult)
    print(f"  {lbl:10s}: Sharpe {sh(r.values):+.2f}  netret {r.sum()*100:+.1f}pct")
print("  (also lb10,thr1.0 the best-grid cell):")
for mult,lbl in [(0.0,'0x'),(1.0,'1x'),(2.0,'2x'),(3.0,'3x')]:
    r,e,rr=snapback(nq,10,1.0,0.00015*mult)
    print(f"  {lbl:10s}: Sharpe {sh(r.values):+.2f}  netret {r.sum()*100:+.1f}pct")

print("\n--- 5. DEFLATED SHARPE (deployed 20,1.5) ---")
r,e,rr=snapback(nq,20,1.5,0.00015)
rv=r.values; T=len(rv)
sr_d=rv.mean()/rv.std()  # daily SR
# trials grid
srs=[]
for lb in [10,15,20,25,30]:
    for thr in [1.0,1.5,2.0]:
        rg,_,_=snapback(nq,lb,thr,0.00015); v=rg.values
        srs.append(v.mean()/v.std() if v.std()>0 else 0.0)
srs=np.array(srs); Ntr=len(srs)
sk=((rv-rv.mean())**3).mean()/rv.std()**3
ku=((rv-rv.mean())**4).mean()/rv.std()**4
gamma=0.5772156649; varSR=srs.var()
emax=np.sqrt(varSR)*((1-gamma)*norm.ppf(1-1.0/Ntr)+gamma*norm.ppf(1-1.0/(Ntr*np.e)))
denom=np.sqrt(1-sk*sr_d+(ku-1)/4.0*sr_d**2)
dsr=norm.cdf((sr_d-emax)*np.sqrt(T-1)/denom)
print(f"  daily SR={sr_d:.4f} (ann {sr_d*np.sqrt(252):.2f}), T={T}, skew={sk:.2f} kurt={ku:.2f}")
print(f"  N_trials={Ntr}, sd(SR trials)={np.sqrt(varSR):.4f}, E[maxSR|null]={emax:.4f}")
print(f"  DSR={dsr:.3f} -> {'PASS' if dsr>0.95 else 'FAIL'}")
# also DSR for best cell 10,1.0
r2,_,_=snapback(nq,10,1.0,0.00015); v2=r2.values; sr2=v2.mean()/v2.std()
sk2=((v2-v2.mean())**3).mean()/v2.std()**3; ku2=((v2-v2.mean())**4).mean()/v2.std()**4
den2=np.sqrt(1-sk2*sr2+(ku2-1)/4.0*sr2**2)
dsr2=norm.cdf((sr2-emax)*np.sqrt(len(v2)-1)/den2)
print(f"  [best cell 10,1.0] daily SR={sr2:.4f} DSR={dsr2:.3f} -> {'PASS' if dsr2>0.95 else 'FAIL'}")
