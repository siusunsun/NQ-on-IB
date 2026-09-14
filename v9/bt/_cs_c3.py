import sys; sys.path.insert(0,'/root/v9/bt')
import numpy as np, pandas as pd
from _cs_lib import *
from _cs_lib import _sim
from _cs_regime import build
pd.set_option('display.width',240)
rng=np.random.default_rng(20260827)
F=build()
def attach(T): return T.join(F,on='dord')
DEP=attach(scan(100.0,0.0015,1.0))
LOO=attach(scan(80.0,0.0005,1.0))
# COMMON SAMPLE: only days where the expanding-median gates are defined
DEFN=DEP['er20_med'].notna()&DEP['adx14_med'].notna()
DEPc=DEP[DEFN]
print("common-sample (gate-defined) days: deployed n=%d of %d; first year=%s"%(len(DEPc),len(DEP),DEPc['yr'].min()))
def line(T,label):
    if len(T)<3: return dict(label=label,n=len(T))
    r=T['R'].values
    return dict(label=label,n=len(T),expR=round(r.mean(),3),
       t=round(r.mean()/r.std(ddof=1)*np.sqrt(len(r)),2),net=round(T['netpts'].sum()*PT_USD),
       net2x=round((T['pts']-2*COST_PTS).sum()*PT_USD),net3x=round((T['pts']-3*COST_PTS).sum()*PT_USD),
       win=round((T['netpts']>0).mean()*100,1),
       top5=round(100*np.sort(T['netpts'].values*PT_USD)[-5:].sum()/max(1e-9,T['netpts'].sum()*PT_USD),1))
print("\n=== APPLES-TO-APPLES: base restricted to gate-defined days ===")
rows=[line(DEPc,'deployed (common sample)')]
for g in ['er20_hi','adx14_hi','er10_hi','er5_hi','brk20_hi']:
    rows.append(line(DEPc[DEPc[g]==True],'  gate ON: '+g))
    rows.append(line(DEPc[DEPc[g]==False],'  gate OFF(chop): '+g))
print(pd.DataFrame(rows).to_string(index=False))
print("\n=== ERA SPLIT on common sample ===")
rows=[]
for nm,T in [('deployed',DEPc),('dep+er20_hi',DEPc[DEPc.er20_hi==True]),('dep+adx14_hi',DEPc[DEPc.adx14_hi==True])]:
    e1=T[T['yr']<=2023]; e2=T[T['yr']>=2024]
    rows.append(dict(spec=nm,n_2022_23=len(e1),expR_e1=round(e1['R'].mean(),3),net_e1=round(e1['netpts'].sum()*PT_USD),
        n_2024_26=len(e2),expR_e2=round(e2['R'].mean(),3),net_e2=round(e2['netpts'].sum()*PT_USD)))
print(pd.DataFrame(rows).to_string(index=False))
AMrows_by_day={}
for r in AM: AMrows_by_day.setdefault(etdate[r],[]).append(r)
for k in AMrows_by_day: AMrows_by_day[k]=np.array(AMrows_by_day[k])
def perm_null(T,ndraw=300):
    days=T['dord'].values; dirs=T['dir'].values
    obs=T['netpts'].sum()*PT_USD; obsR=T['R'].mean(); nets=[];exps=[]
    for _ in range(ndraw):
        si=np.array([rng.choice(AMrows_by_day[d]) for d in days]); fi=si+1
        ent=op[fi]; st=np.where(dirs==1, low20[si]-1.0*atr[si], high20[si]+1.0*atr[si])
        xp,rs,xi=_sim(fi,dirs,st)
        pts=dirs*(xp-ent); risk=np.abs(ent-st); net=pts-COST_PTS
        nets.append(net.sum()*PT_USD); exps.append((net/risk).mean())
    nets=np.array(nets); exps=np.array(exps)
    return dict(obs_net=round(obs),null_net_med=round(np.median(nets)),pctile_net=round(100*(nets<obs).mean(),1),
        obs_expR=round(obsR,3),null_expR_med=round(np.median(exps),3),pctile_expR=round(100*(exps<obsR).mean(),1))
print("\n=== PERMUTATION NULL (300 draws; same days, same direction, random AM entry minute) ===")
for nm,T in [('deployed ALL',DEP),('deployed common',DEPc),('dep+er20_hi',DEPc[DEPc.er20_hi==True]),
             ('dep+adx14_hi',DEPc[DEPc.adx14_hi==True]),('loose-proxy',LOO)]:
    print(f"{nm:18s}",perm_null(T))
print("\n=== DSR with honest trial counts (per-trade R Sharpe) ===")
for nm,T in [('deployed',DEP),('dep+er20_hi',DEPc[DEPc.er20_hi==True]),('dep+adx14_hi',DEPc[DEPc.adx14_hi==True]),('loose-proxy',LOO)]:
    for nt in (1,42,100,160):
        print(f"  {nm:14s} trials={nt:4d} DSR={dsr(T['R'].values,max(nt,2)):.3f}")
