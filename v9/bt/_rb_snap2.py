# -*- coding: utf-8 -*-
"""_rb_snap2.py  SNAPBACK: is it alpha or just long-NQ beta? + mirror/short control. READ-ONLY."""
import glob,sys,warnings; warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8',errors='replace'); sys.path.insert(0,'/root/v9/bt')
import numpy as np, pandas as pd
from _rb_lib import sharpe
NQ_DIR='/root/tlbrc_paper/ref/data_full/NQ/'
def load_1m():
    parts=[]
    for f in sorted(glob.glob(NQ_DIR+'*.csv')):
        try: parts.append(pd.read_csv(f,usecols=['time','close']))
        except: pass
    d=pd.concat(parts,ignore_index=True)
    t=pd.to_datetime(d['time'],utc=True,errors='coerce')
    d['time']=t; d=d.dropna(subset=['time']).drop_duplicates('time').set_index('time').sort_index().tz_convert('America/New_York')
    return d['close']
c1m=load_1m()
cut=c1m.index.normalize()+pd.Timedelta(hours=23,minutes=59)
ok=c1m[c1m.index<=cut]; nq=ok.groupby(ok.index.normalize()).last().astype(float); nq.index=nq.index.tz_localize(None); nq=nq.dropna()
z=(nq-nq.rolling(20).mean())/nq.rolling(20).std(ddof=0)
df=pd.DataFrame({'c':nq,'z':z}).dropna()
ret=df['c'].astype(float).pct_change().fillna(0).values.astype(float)
def strat(sig):
    expo=np.r_[0.0,sig[:-1]]; dp=np.abs(np.diff(np.r_[0.0,expo]))
    return expo*ret-(0.00015/2)*dp, expo
long_r,long_e=strat((df['z'].values<-1.5).astype(float))
short_r,short_e=strat(-(df['z'].values>1.5).astype(float))
symm_r,_=strat((df['z'].values<-1.5).astype(float)-(df['z'].values>1.5).astype(float))
idx=df.index
print('='*100); print('SNAPBACK: ALPHA vs BETA  (n=%d days %s..%s)'%(len(idx),idx[0].date(),idx[-1].date())); print('='*100)
print('  buy & hold NQ            : Sharpe %.2f  cum %+.0f%%  maxDD %.0f%%'%(
    sharpe(ret), (np.exp(np.log1p(ret).sum())-1)*100,
    100*((np.cumprod(1+ret)/np.maximum.accumulate(np.cumprod(1+ret)))-1).min()))
print('  SNAPBACK long dip-buy    : Sharpe %.2f  cum %+.0f%%  time-in-market %.0f%%'%(
    sharpe(long_r), long_r.sum()*100, long_e.mean()*100))
print('  MIRROR short z>+1.5      : Sharpe %.2f  cum %+.0f%%  time-in-market %.0f%%   <- control'%(
    sharpe(short_r), short_r.sum()*100, np.abs(short_e).mean()*100))
print('  symmetric long+short     : Sharpe %.2f  cum %+.0f%%'%(sharpe(symm_r), symm_r.sum()*100))
# beta decomposition
X=np.c_[np.ones(len(ret)),ret]
beta_hat,res,_,_=np.linalg.lstsq(X,long_r,rcond=None)
resid=long_r-X@beta_hat
se=np.sqrt(np.sum(resid**2)/(len(ret)-2)*np.linalg.inv(X.T@X)[0,0])
print('\n  OLS  strat_ret = a + b*NQ_ret :  b = %.3f (= average exposure %.3f)  a = %+.2fbp/day  t(a) = %.2f'%(
    beta_hat[1],long_e.mean(),beta_hat[0]*1e4,beta_hat[0]/se))
print('  annualised alpha %+.1f%%/yr ; annualised beta-contribution %+.1f%%/yr'%(
    beta_hat[0]*252*100, beta_hat[1]*ret.mean()*252*100))
print('  residual (market-neutralised) Sharpe %.2f'%sharpe(resid))
# same-exposure random-day benchmark (already done) + "always long 12% of days at random" is the null.
print('\n  timing test - compare with holding the SAME number of days but chosen by:')
k=int(long_e.sum())
srt=np.argsort(-df['z'].values)  # highest z (most extended) - the opposite signal
alt=np.zeros(len(ret)); alt[srt[:k]]=1.0
alt_r,_=strat(alt)
print('    most-EXTENDED days (z highest, k=%d): Sharpe %.2f  cum %+.0f%%'%(k,sharpe(alt_r),alt_r.sum()*100))
print('    (the deployed dip signal: Sharpe %.2f  cum %+.0f%%)'%(sharpe(long_r),long_r.sum()*100))
print('\n  per-year alpha t-stat (market-neutralised):')
for y in sorted(set(idx.year)):
    m=idx.year==y
    if m.sum()<50: continue
    Xy=np.c_[np.ones(m.sum()),ret[m]]; bh,_,_,_=np.linalg.lstsq(Xy,long_r[m],rcond=None)
    rs=long_r[m]-Xy@bh
    sey=np.sqrt(np.sum(rs**2)/(m.sum()-2)*np.linalg.inv(Xy.T@Xy)[0,0])
    print('    %d: NQ %+6.1f%%  strat %+6.1f%%  beta %.2f  alpha %+.2fbp/d  t(a) %+.2f'%(
        y, (np.exp(np.log1p(ret[m]).sum())-1)*100, long_r[m].sum()*100, bh[1], bh[0]*1e4, bh[0]/sey))
