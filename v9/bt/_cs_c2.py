import sys; sys.path.insert(0,'/root/v9/bt')
import numpy as np, pandas as pd
from _cs_lib import *
from _cs_regime import build
pd.set_option('display.width',240); pd.set_option('display.max_columns',50)
rng=np.random.default_rng(20260827)
F=build()

def attach(T):
    return T.join(F,on='dord')

DEP=attach(scan(100.0,0.0015,1.0))
LOO=attach(scan(80.0,0.0005,1.0))   # closest proxy to prior audit's "loose cell"

# ---------- permutation null: same days, same direction, random AM entry minute ----------
AMrows_by_day={}
for r in AM:
    AMrows_by_day.setdefault(etdate[r],[]).append(r)
for k in AMrows_by_day: AMrows_by_day[k]=np.array(AMrows_by_day[k])

def perm_null(T,ndraw=300):
    days=T['dord'].values; dirs=T['dir'].values
    obs=T['netpts'].sum()*PT_USD; obsR=T['R'].mean()
    nets=[];exps=[]
    for _ in range(ndraw):
        si=np.array([rng.choice(AMrows_by_day[d]) for d in days])
        fi=si+1
        ent=op[fi]
        st=np.where(dirs==1, low20[si]-1.0*atr[si], high20[si]+1.0*atr[si])
        xp,rs,xi=_sim(fi,dirs,st)
        pts=dirs*(xp-ent); risk=np.abs(ent-st)
        net=(pts-COST_PTS)
        nets.append(net.sum()*PT_USD); exps.append((net/risk).mean())
    nets=np.array(nets); exps=np.array(exps)
    return dict(obs_net=round(obs), null_med=round(np.median(nets)),
        pct_net=round(100*(nets<obs).mean(),1), obs_expR=round(obsR,3),
        null_expR_med=round(np.median(exps),3), pct_expR=round(100*(exps<obsR).mean(),1))

# ---------- gates ----------
GATES={'er20_hi':'er20_hi','adx14_hi':'adx14_hi','er10_hi':'er10_hi',
       'er5_hi':'er5_hi','brk20_hi':'brk20_hi'}
def gated(T,g): 
    s=T[T[g]==True]
    return s

def line(T,label):
    if len(T)<5: return dict(label=label,n=len(T))
    r=T['R'].values
    return dict(label=label,n=len(T),expR=round(r.mean(),3),
       t=round(r.mean()/r.std(ddof=1)*np.sqrt(len(r)),2),
       net=round(T['netpts'].sum()*PT_USD),
       net2x=round((T['pts']-2*COST_PTS).sum()*PT_USD),
       net3x=round((T['pts']-3*COST_PTS).sum()*PT_USD),
       win=round((T['netpts']>0).mean()*100,1),
       top5=round(100*np.sort(T['netpts'].values*PT_USD)[-5:].sum()/max(1e-9,T['netpts'].sum()*PT_USD),1))

print("="*100); print("SECTION 2b: CAUSAL GATE, FULL / ERA SPLIT / FOLDS  (base = DEPLOYED cell)")
rows=[line(DEP,'deployed ALL')]
for g in GATES: rows.append(line(gated(DEP,g),'deployed + '+g))
print(pd.DataFrame(rows).to_string(index=False))

print("\n--- ERA SPLIT (2021-2023 vs 2024-2026) ---")
rows=[]
for nm,T in [('deployed',DEP)]+[('dep+'+g,gated(DEP,g)) for g in GATES]:
    e1=T[T['yr']<=2023]; e2=T[T['yr']>=2024]
    rows.append(dict(spec=nm,n1=len(e1),expR1=round(e1['R'].mean(),3) if len(e1) else np.nan,
        net1=round(e1['netpts'].sum()*PT_USD),n2=len(e2),
        expR2=round(e2['R'].mean(),3) if len(e2) else np.nan,net2=round(e2['netpts'].sum()*PT_USD)))
print(pd.DataFrame(rows).to_string(index=False))

print("\n--- 5-FOLD (chronological, equal calendar-day folds; FIXED spec, no fitting) ---")
alld=np.unique(AMday); cuts=np.array_split(alld,5)
for nm,T in [('deployed',DEP),('dep+er20_hi',gated(DEP,'er20_hi')),('dep+adx14_hi',gated(DEP,'adx14_hi'))]:
    out=[]
    for i,c in enumerate(cuts):
        s=T[T['dord'].isin(c)]
        out.append(f"f{i+1}: n={len(s):3d} expR={s['R'].mean():+.3f} net=${s['netpts'].sum()*PT_USD:>7,.0f}" if len(s) else f"f{i+1}: n=0")
    print(f"{nm:16s} "+" | ".join(out))

print("\n--- PER-YEAR expR ---")
for nm,T in [('deployed',DEP),('dep+er20_hi',gated(DEP,'er20_hi')),('dep+adx14_hi',gated(DEP,'adx14_hi')),('loose-proxy',LOO)]:
    print(f"{nm:16s}", {int(y):(len(g),round(g['R'].mean(),2),round(g['netpts'].sum()*PT_USD)) for y,g in T.groupby('yr')})

print("\n"+"="*100); print("PERMUTATION NULLS (300 draws, day+direction matched, random AM entry minute)")
for nm,T in [('deployed',DEP),('dep+er20_hi',gated(DEP,'er20_hi')),('dep+adx14_hi',gated(DEP,'adx14_hi')),('loose-proxy',LOO)]:
    print(f"{nm:16s}",perm_null(T))

print("\n"+"="*100); print("LOOSE-PROXY scorecard + gate")
rows=[line(LOO,'loose-proxy ALL')]
for g in GATES: rows.append(line(gated(LOO,g),'loose + '+g))
print(pd.DataFrame(rows).to_string(index=False))
e1=LOO[LOO['yr']<=2023]; e2=LOO[LOO['yr']>=2024]
print(f"loose era1 n={len(e1)} expR={e1['R'].mean():.3f} net=${e1['netpts'].sum()*PT_USD:,.0f} | era2 n={len(e2)} expR={e2['R'].mean():.3f} net=${e2['netpts'].sum()*PT_USD:,.0f}")

print("\n"+"="*100); print("ANCHORED WALK-FORWARD OF THE SEARCH PROCEDURE (pick best cell by IS expR, trade next fold)")
grid=[(l,s,k,sl) for l in (80,100) for s in (0.0005,0.0015) for k in (0.5,0.75,1.0,1.25,1.5,2.0,99.0) for sl in (True,False)]
cache={g:attach(scan(g[0],g[1],g[2],need_slope=g[3])) for g in grid}
print("grid cells evaluated:",len(grid))
oos=[]
for i in range(1,5):
    isdays=np.concatenate(cuts[:i]); oosdays=cuts[i]
    best=None
    for g in grid:
        T=cache[g]; s=T[T['dord'].isin(isdays)]
        if len(s)<20: continue
        v=s['R'].mean()
        if best is None or v>best[1]: best=(g,v)
    T=cache[best[0]]; o=T[T['dord'].isin(oosdays)]
    print(f"fold{i+1}: IS-best={best[0]} (IS expR {best[1]:+.3f}) -> OOS n={len(o)} expR={o['R'].mean():+.3f} net=${o['netpts'].sum()*PT_USD:,.0f}")
    oos.append(o)
O=pd.concat(oos)
print("AGGREGATE OOS of search procedure:",line(O,'search-WF-OOS'))
