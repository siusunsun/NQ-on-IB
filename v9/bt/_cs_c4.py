import sys; sys.path.insert(0,'/root/v9/bt')
import numpy as np, pandas as pd
from _cs_lib import *
from _cs_lib import _sim
from _cs_regime import build
rng=np.random.default_rng(7)
F=build()
def attach(T): return T.join(F,on='dord')
DEP=attach(scan(100.0,0.0015,1.0)); DEPc=DEP[DEP['er20_med'].notna()]
G=DEPc[DEPc.er20_hi==True]
AMd={}
for r in AM: AMd.setdefault(etdate[r],[]).append(r)
for k in AMd: AMd[k]=np.array(AMd[k])
def null(T,mode,nd=300):
    days=T['dord'].values; dr=T['dir'].values
    obsR=T['R'].mean(); obs=T['netpts'].sum()*PT_USD; e=[];nn=[]
    for _ in range(nd):
        si=np.array([rng.choice(AMd[d]) for d in days]); fi=si+1
        d2 = dr if mode=='keepdir' else rng.choice([-1,1],len(si))
        ent=op[fi]; st=np.where(d2==1,low20[si]-atr[si],high20[si]+atr[si])
        xp,_r,_x=_sim(fi,d2,st); pts=d2*(xp-ent); net=pts-COST_PTS
        e.append((net/np.abs(ent-st)).mean()); nn.append(net.sum()*PT_USD)
    e=np.array(e);nn=np.array(nn)
    return f"obsR={obsR:+.3f} nullR_med={np.median(e):+.3f} pctile={100*(e<obsR).mean():.1f} | obsNet=${obs:,.0f} nullNet_med=${np.median(nn):,.0f} pctile={100*(nn<obs).mean():.1f}"
for nm,T in [('deployed',DEPc),('dep+er20_hi',G)]:
    print(f"{nm:13s} keepdir  :",null(T,'keepdir'))
    print(f"{nm:13s} randomdir:",null(T,'randdir'))
# does the DAY selection matter? compare vs random days
print("\n--- er20_hi gate trade-count / concentration ---")
print("gate ON n=%d over %d yrs = %.1f/yr; top5=%.0f%% of net; worst-fold expR"%(len(G),4.6,len(G)/4.6,
  100*np.sort(G['netpts'].values*PT_USD)[-5:].sum()/(G['netpts'].sum()*PT_USD)))
print("per-year:",{int(y):(len(g),round(g['R'].mean(),2)) for y,g in G.groupby('yr')})
# strict-sealed check: does er20 gate hold if threshold uses only 2021-2023 data (frozen)?
thr=F.loc[F.index<pd.Timestamp('2024-01-01').toordinal(),'er20'].median()
print("\nfrozen er20 threshold from 2021-2023 only:",round(thr,4))
S=DEP[DEP['er20']>=thr]; S2=S[S['yr']>=2024]
print("frozen-threshold gate, 2024-2026 OOS: n=%d expR=%.3f net=$%.0f  (vs ungated 2024-26 n=%d expR=%.3f net=$%.0f)"%(
  len(S2),S2['R'].mean(),S2['netpts'].sum()*PT_USD,
  len(DEP[DEP.yr>=2024]),DEP[DEP.yr>=2024]['R'].mean(),DEP[DEP.yr>=2024]['netpts'].sum()*PT_USD))
