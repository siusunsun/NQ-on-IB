# -*- coding: utf-8 -*-
"""_rb_lib.py  READ-ONLY robustness helpers (no scipy on this box)."""
import numpy as np, pandas as pd, math

EULER = 0.5772156649015329

def ncdf(x):
    return 0.5*(1.0+math.erf(x/math.sqrt(2.0)))

def nppf(p):
    """Acklam inverse normal CDF."""
    if p<=0: return -np.inf
    if p>=1: return np.inf
    a=[-3.969683028665376e+01,2.209460984245205e+02,-2.759285104469687e+02,1.383577518672690e+02,-3.066479806614716e+01,2.506628277459239e+00]
    b=[-5.447609879822406e+01,1.615858368580409e+02,-1.556989798598866e+02,6.680131188771972e+01,-1.328068155288572e+01]
    c=[-7.784894002430293e-03,-3.223964580411365e-01,-2.400758277161838e+00,-2.549732539343734e+00,4.374664141464968e+00,2.938163982698783e+00]
    d=[7.784695709041462e-03,3.224671290700398e-01,2.445134137142996e+00,3.754408661907416e+00]
    pl=0.02425
    if p<pl:
        q=math.sqrt(-2*math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p>1-pl:
        q=math.sqrt(-2*math.log(1-p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q=p-0.5; r=q*q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q/(((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)

def skew_kurt(x):
    x=np.asarray(x,float); n=len(x); m=x.mean(); s=x.std(ddof=0)
    if s==0 or n<4: return 0.0,3.0
    g3=((x-m)**3).mean()/s**3
    g4=((x-m)**4).mean()/s**4
    return g3,g4

def sharpe(x, ann=252):
    x=np.asarray(x,float); s=x.std(ddof=1)
    return x.mean()/s*np.sqrt(ann) if s>0 else 0.0

def dsr(returns, sr_trials_std, n_trials, sr_bench_ann=0.0, ann=252):
    """Deflated Sharpe. `returns` = per-observation returns (trade-level or daily).
    Returns (SR_per_obs, SR0_per_obs, DSR_prob)."""
    x=np.asarray(returns,float); N=len(x)
    s=x.std(ddof=1)
    if s==0 or N<8: return 0.0,0.0,0.0
    sr=x.mean()/s
    g3,g4=skew_kurt(x)
    # expected max SR under the null of n_trials trials
    e1=nppf(1.0-1.0/n_trials); e2=nppf(1.0-1.0/(n_trials*math.e))
    sr0=sr_trials_std*((1-EULER)*e1+EULER*e2)
    sr0=max(sr0, sr_bench_ann/np.sqrt(ann))
    den=math.sqrt(max(1e-12, 1.0-g3*sr+((g4-1.0)/4.0)*sr*sr))
    z=(sr-sr0)*math.sqrt(N-1)/den
    return sr,sr0,ncdf(z)

def dd_stats(pnl):
    x=np.asarray(pnl,float); cu=np.cumsum(x); pk=np.maximum.accumulate(cu)
    dd=cu-pk
    return float(cu[-1]), float(dd.min())

def book_stats(daily, ann=252):
    x=np.asarray(daily,float); cu=np.cumsum(x); pk=np.maximum.accumulate(cu); dd=cu-pk
    s=x.std(ddof=1)
    neg=x[x<0]; ds=neg.std(ddof=1) if len(neg)>1 else 0.0
    nz=x[x!=0]; w=nz[nz>0]; l=nz[nz<0]
    return dict(net=float(cu[-1]), sharpe=float(x.mean()/s*np.sqrt(ann)) if s>0 else 0.0,
                sortino=float(x.mean()/ds*np.sqrt(ann)) if ds>0 else 0.0,
                maxdd=float(dd.min()), retdd=float(cu[-1]/abs(dd.min())) if dd.min()<0 else 0.0,
                ann=float(x.mean()*ann),
                pf=float(w.sum()/abs(l.sum())) if l.sum()!=0 else 0.0,
                ndays=int(len(x)), ntd=int(len(nz)))

def wf_folds(dates, vals, k=5):
    """k contiguous calendar folds; return list of (label, n, mean)."""
    d=pd.DatetimeIndex(dates); order=np.argsort(d.values)
    d=d[order]; v=np.asarray(vals,float)[order]
    edges=np.linspace(0,len(v),k+1).astype(int)
    out=[]
    for i in range(k):
        a,b=edges[i],edges[i+1]
        if b-a<2: continue
        out.append((f"{d[a].date()}..{d[b-1].date()}", b-a, float(v[a:b].mean()), float(v[a:b].sum())))
    return out
