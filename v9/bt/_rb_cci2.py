# -*- coding: utf-8 -*-
"""_rb_cci2.py  Follow-up: (a) the LOOSE CCI cell that dominates the deployed one on the
27-cell grid, given the same scrutiny; (b) SNAPBACK beta/alpha decomposition. READ-ONLY."""
import sys, warnings; warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8', errors='replace'); sys.path.insert(0,'/root/v9/bt')
import numpy as np, pandas as pd
exec(open('/root/v9/bt/_rb_cci.py').read().split("BASE=dict(")[0])   # reuse loaders/run()
from _rb_lib import sharpe, dsr, book_stats
BASE=dict(cci_n=20,lvl=100.0,sep=0.0015,kstop=1.0,atrf=1.0,w0=570,w1=720,ema_sp=50)
LOOSE=dict(cci_n=20,lvl=80.0,sep=0.0005,kstop=0.5,atrf=1.0,w0=570,w1=720,ema_sp=50)

def rep(name,P):
    T=run(**P); T['dt']=pd.to_datetime(T['date'])
    R=T['R'].values; u=T['pts'].values*2.0
    t=R.mean()/(R.std(ddof=1)/np.sqrt(len(R)))
    tu=u.mean()/(u.std(ddof=1)/np.sqrt(len(u)))
    print('\n'+'='*104); print('%s   %s'%(name,P)); print('='*104)
    print('  n=%d (%.1f/yr)  net $%.0f @1MNQ  expPts %+.2f  expR %+.3f  t(R)=%.2f t($)=%.2f  PF %.2f  win %.0f%%'%(
        len(T),len(T)/5.65,u.sum(),T['pts'].mean(),R.mean(),t,tu,
        u[u>0].sum()/-u[u<0].sum(),100*(u>0).mean()))
    for y in sorted(T.year.unique()):
        g=T[T.year==y]; gu=g['pts'].values*2
        print('    %d: n=%3d net $%+7.0f  expPts %+7.2f  expR %+.3f  win %.0f%%'%(
            y,len(g),gu.sum(),g['pts'].mean(),g['R'].mean(),100*(gu>0).mean()))
    TT=T.sort_values('i').reset_index(drop=True); ed=np.linspace(0,len(TT),6).astype(int)
    print('  5 contiguous folds:')
    for i in range(5):
        a,b=ed[i],ed[i+1]; g=TT.iloc[a:b]
        print('    fold%d %s..%s n=%3d expPts %+7.2f net $%+7.0f expR %+.3f'%(
            i+1,g['date'].iloc[0],g['date'].iloc[-1],len(g),g['pts'].mean(),g['pts'].sum()*2,g['R'].mean()))
    sr=np.sort(u)
    print('  concentration: top-3 %.0f%% of net, top-5 %.0f%% ; net ex-best5 $%.0f'%(
        100*sr[-3:].sum()/sr.sum(),100*sr[-5:].sum()/sr.sum(),sr[:-5].sum()))
    for m in (1,2,3):
        t2=run(**dict(P,cost=0.75*m)); print('  cost %dx: net $%.0f  expPts %+.2f'%(m,t2['pts'].sum()*2,t2['pts'].mean()))
    return T

TL=rep('LOOSE CELL (lvl80, sep0.05pct, k0.5)',LOOSE)
TL.to_csv('/root/v9/bt/_rb_cci_loose_trades.csv',index=False)

# DSR for the loose cell using the 27-cell core grid as the trial family
print('\n  DSR for LOOSE cell:')
srs=[]
for lvl in (80.,100.,120.):
    for k in (0.5,1.0,1.5):
        for sp in (0.0005,0.0015,0.0025):
            t=run(**dict(BASE,lvl=lvl,kstop=k,sep=sp))
            if len(t)>10: srs.append(t['R'].mean()/t['R'].std(ddof=1))
srs=np.array(srs); R=TL['R'].values
for NT in (27,42,100,500):
    sr,sr0,p=dsr(R,float(srs.std(ddof=1)),NT)
    print('    N_trials=%-4d  SR/trade %.4f  SR0 %.4f  DSR %.3f  %s'%(NT,sr,sr0,p,'PASS' if p>=0.95 else 'FAIL'))

# permutation for loose cell
print('\n  random-entry null for LOOSE cell (400 draws):')
real=TL['pts'].sum()*2; realR=TL['R'].mean(); ntr=len(TL)
nlong=(TL['dir']=='long').mean()
poolmask=(etmin>=570)&(etmin<720)&np.isfinite(atrv)&np.isfinite(l20)&np.isfinite(h20)
pool=np.arange(n)[poolmask]
RNG=np.random.default_rng(77)
nulls=[];nullR=[]
for d in range(400):
    pick=RNG.choice(pool,size=min(ntr,len(pool)),replace=False)
    tot=0.;rs=[]
    for i in pick:
        if i+1>=n or etdate[i+1]!=etdate[i]: continue
        lg=RNG.random()<nlong; entry=op[i+1]
        stop=(l20[i]-0.5*atrv[i]) if lg else (h20[i]+0.5*atrv[i])
        risk=abs(entry-stop)
        if risk<=0: continue
        j=i+1;xp=None
        while j<n and etdate[j]==etdate[i+1]:
            if etmin[j]>=960: xp=op[j];break
            if lg and lo[j]<=stop: xp=stop;break
            if (not lg) and hi[j]>=stop: xp=stop;break
            j+=1
        if xp is None: xp=px[min(j,n-1)]
        p_=(1 if lg else -1)*(xp-entry)-1.5
        tot+=p_*2; rs.append(p_/risk)
    nulls.append(tot); nullR.append(np.mean(rs))
nulls=np.array(nulls); nullR=np.array(nullR)
print('    real $%.0f vs null mean $%.0f std $%.0f -> %.1fth pctile'%(real,nulls.mean(),nulls.std(),(nulls<real).mean()*100))
print('    real expR %+.4f vs null %+.4f -> %.1fth pctile'%(realR,nullR.mean(),(nullR<realR).mean()*100))
print('\n  wrote _rb_cci_loose_trades.csv')
