"""_cs_lib.py - shared scan/sim/stats engine for the CCI single-slot AM study."""
import numpy as np, pandas as pd
import math
class _N:
    @staticmethod
    def cdf(x): return 0.5*(1.0+math.erf(float(x)/math.sqrt(2.0)))
    @staticmethod
    def ppf(p):
        p=float(p)
        if p<=0 or p>=1: raise ValueError(p)
        a=[-3.969683028665376e+01,2.209460984245205e+02,-2.759285104469687e+02,1.383577518672690e+02,-3.066479806614716e+01,2.506628277459239e+00]
        b=[-5.447609879822406e+01,1.615858368580409e+02,-1.556989798598866e+02,6.680131188771972e+01,-1.328068155288572e+01]
        c=[-7.784894002430293e-03,-3.223964580411365e-01,-2.400758277161838e+00,-2.549732539343734e+00,4.374664141464968e+00,2.938163982698783e+00]
        d=[7.784695709041462e-03,3.224671290700398e-01,2.445134137142996e+00,3.754408661907416e+00]
        pl=0.02425
        if p<pl:
            q=math.sqrt(-2*math.log(p))
            x=(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        elif p>1-pl:
            q=math.sqrt(-2*math.log(1-p))
            x=-(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        else:
            q=p-0.5; r=q*q
            x=(((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q/(((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
        e=0.5*math.erfc(-x/math.sqrt(2))-p
        u=e*math.sqrt(2*math.pi)*math.exp(x*x/2)
        return x-u/(1+x*u/2)
NORM=_N

Z = np.load("/root/v9/bt/_cs_feat.npz")
ts=Z['ts']; op=Z['op'].astype('float64'); hi=Z['hi'].astype('float64')
lo=Z['lo'].astype('float64'); cl=Z['cl'].astype('float64')
cci=Z['cci'].astype('float64'); vwap=Z['vwap'].astype('float64')
ema=Z['ema50'].astype('float64'); atr=Z['atr5'].astype('float64')
e30=Z['e30'].astype('float64'); low20=Z['low20'].astype('float64')
high20=Z['high20'].astype('float64'); nrm=Z['norm'].astype('float64')
etmin=Z['etmin'].astype('int32'); etdate=Z['etdate'].astype('int64')
N=len(ts)
del Z

AM0,AM1=9*60+30,12*60
EOD=16*60
PT_USD=2.0        # 1 MNQ
COST_PTS=2.04/PT_USD   # 1.02 pts round turn

# ---- day boundaries ----
udays,dstart=np.unique(etdate,return_index=True)
dend=np.append(dstart[1:],N)
day_of=np.searchsorted(udays,etdate)
day_start=dstart[day_of]; day_end=dend[day_of]
# first row at/after 16:00 ET per day
eod_row=np.full(len(udays),-1,dtype=np.int64)
m=etmin>=EOD
for k in range(len(udays)):
    s,e=dstart[k],dend[k]
    w=np.nonzero(m[s:e])[0]
    eod_row[k]= s+w[0] if len(w) else e-1
EOD_OF=eod_row[day_of]

AM=np.nonzero((etmin>=AM0)&(etmin<AM1))[0]
AM=AM[AM>0]
AM=AM[etdate[AM]==etdate[AM-1]]          # need prev bar same day for cross
ok=(~np.isnan(ema[AM]))&(~np.isnan(vwap[AM]))&(~np.isnan(atr[AM]))&(~np.isnan(e30[AM]))
ok&=(~np.isnan(nrm[AM]))&(~np.isnan(cci[AM]))&(~np.isnan(cci[AM-1]))
ok&=(~np.isnan(low20[AM]))&(~np.isnan(high20[AM]))
ok&=(AM+1<N)
AM=AM[ok]
AM=AM[etdate[AM+1]==etdate[AM]]
AMday=etdate[AM]

def scan(level=100.0, sep=0.0015, kvol=1.0, need_slope=True, need_ema=True):
    """Return DataFrame of trades (one per day, first signal) for a parameter cell."""
    i=AM; p=i-1
    price=cl[i]
    volok = atr[i] <= kvol*nrm[i]
    Lc = (cci[p]<-level)&(cci[i]>=-level)
    Sc = (cci[p]> level)&(cci[i]<= level)
    Lf = volok&Lc
    Sf = volok&Sc
    if need_ema:
        Lf&= (cl[i]>ema[i]); Sf&=(cl[i]<ema[i])
    Lf &= (ema[i]-vwap[i])>=sep*price
    Sf &= (vwap[i]-ema[i])>=sep*price
    if need_slope:
        Lf&=(ema[i]>e30[i]); Sf&=(ema[i]<e30[i])
    sig = Lf|Sf
    if not sig.any(): return pd.DataFrame()
    si=i[sig]; sdir=np.where(Lf[sig],1,-1)
    # first per day
    d=etdate[si]
    firstmask=np.ones(len(si),bool); firstmask[1:]= d[1:]!=d[:-1]
    si=si[firstmask]; sdir=sdir[firstmask]
    fi=si+1
    entry=op[fi]
    stop=np.where(sdir==1, low20[si]-1.0*atr[si], high20[si]+1.0*atr[si])
    return _build(si,fi,sdir,entry,stop)

def _sim(fi,sdir,stop):
    n=len(fi); xp=np.empty(n); reason=np.empty(n,dtype=object); xi=np.empty(n,dtype=np.int64)
    for k in range(n):
        f=fi[k]; e=EOD_OF[f]
        if e<=f:
            xp[k]=cl[f]; reason[k]='EOD'; xi[k]=f; continue
        if sdir[k]==1: br=lo[f:e]<=stop[k]
        else: br=hi[f:e]>=stop[k]
        w=br.argmax()
        if br[w]:
            xp[k]=stop[k]; reason[k]='STOP'; xi[k]=f+w
        else:
            xp[k]=op[e]; reason[k]='EOD'; xi[k]=e
    return xp,reason,xi

def _build(si,fi,sdir,entry,stop):
    xp,reason,xi=_sim(fi,sdir,stop)
    pts=sdir*(xp-entry)
    risk=np.abs(entry-stop)
    dt=pd.to_datetime(ts[fi],unit='s',utc=True).tz_convert('America/New_York')
    return pd.DataFrame(dict(date=dt.date.astype(str),dord=etdate[fi],yr=dt.year,
        dir=sdir,entry=entry,stop=stop,exitp=xp,reason=reason,pts=pts,risk=risk,
        etmin=etmin[si],netpts=pts-COST_PTS,R=(pts-COST_PTS)/risk))

# ---------- stats ----------
def dsr(r, n_trials, var_sr=None):
    r=np.asarray(r,dtype='float64'); T=len(r)
    if T<5: return np.nan
    sr=r.mean()/r.std(ddof=1)
    sk=pd.Series(r).skew(); ku=pd.Series(r).kurt()+3.0
    g=0.5772156649
    if var_sr is None or var_sr<=0: var_sr=(1-sk*sr+(ku-1)/4*sr**2)/(T-1)
    e=NORM.ppf(1-1.0/n_trials); e2=NORM.ppf(1-1.0/(n_trials*np.e))
    sr0=np.sqrt(var_sr)*((1-g)*e+g*e2)
    den=np.sqrt(1-sk*sr+(ku-1)/4*sr**2)
    return NORM.cdf((sr-sr0)*np.sqrt(T-1)/den)

def scorecard(T,label,n_trials=50,var_sr=None,perm=None):
    if len(T)==0: return {'label':label,'n':0}
    r=T['R'].values; net=T['netpts'].values*PT_USD
    sr=r.mean()/r.std(ddof=1)
    tstat=r.mean()/r.std(ddof=1)*np.sqrt(len(r))
    g=T['pts'].values*PT_USD
    pf_p=(g[g>0].sum()); pf_n=-(g[g<0].sum())
    net2=(T['pts'].values-2*COST_PTS)*PT_USD
    net3=(T['pts'].values-3*COST_PTS)*PT_USD
    top5=np.sort(net)[-5:].sum()
    d=dict(label=label,n=len(T),expR=r.mean(),t=tstat,SR=sr,
        net1x=net.sum(),net2x=net2.sum(),net3x=net3.sum(),
        PF=(pf_p/pf_n if pf_n>0 else np.nan),
        win=(net>0).mean()*100,
        top5pct=(100*top5/net.sum() if net.sum()!=0 else np.nan),
        DSR=dsr(r,n_trials,var_sr), perm_pctile=perm)
    return d

def yearly(T):
    out={}
    for y,gp in T.groupby('yr'):
        out[int(y)]=(len(gp),round(gp['netpts'].sum()*PT_USD),round(gp['R'].mean(),3))
    return out
