import pickle, numpy as np, pandas as pd
rows=pickle.load(open("/root/v9/bt/_f19_rows.pkl","rb"))
RECENT=pd.Timestamp("2026-06-01",tz="UTC")
POINT=2.0; QTY={"VWAP_LONG_R3":2,"VWAP_SHORT_R1":2,"TLB_LONG":1,"ORB":1}

def usd(r):  # qty-adjusted $ (matches prior ATR test convention)
    return r["pnl_points"]*POINT*QTY[r["sleeve"]]

# ---- filter variants (direction-aware) ----
def f_base(d,s): return True
def f_A1(d,s):   return s["alignBull"] if d=="long" else s["alignBear"]
def f_B1(d,s):   return (s["bullPct"]>s["bearPct"]) if d=="long" else (s["bearPct"]>s["bullPct"])
def f_B2(d,s):   return (s["bias"]!="STRONG BEAR") if d=="long" else (s["bias"]!="STRONG BULL")
def f_B3(d,s):   return (s["bias"]=="STRONG BULL") if d=="long" else (s["bias"]=="STRONG BEAR")
VAR=[("0.BASE",f_base),("A1.align",f_A1),("B1.biasPos",f_B1),("B2.notStrOpp",f_B2),("B3.strAlign",f_B3)]

def stats(rs):
    if not rs: return None
    R=np.array([r["R"] for r in rs]); u=np.array([usd(r) for r in rs])
    gp=u[u>0].sum(); gl=-u[u<0].sum(); pf=gp/gl if gl>0 else float('inf')
    rr=sorted(rs,key=lambda r:r["entry_utc"]); cum=np.cumsum([usd(r) for r in rr])
    peak=np.maximum.accumulate(cum); mdd=(cum-peak).min() if len(cum) else 0
    return dict(n=len(rs),sumR=R.sum(),pf=pf,usd=u.sum(),win=100*(R>0).mean(),mdd=mdd)

def line(name,st,nbase):
    if st is None: return f"  {name:14} none"
    pf="inf" if st['pf']==float('inf') else f"{st['pf']:.2f}"
    pct=f"{100*st['n']/nbase:4.0f}%" if nbase else "  - "
    return f"  {name:14} {st['n']:4d} {pct} sumR={st['sumR']:7.2f} PF={pf:>5} net$={st['usd']:9,.0f} win%={st['win']:5.1f} DD={st['mdd']:8,.0f}"

def report(window_name,filt_time):
    print("\n"+"#"*118); print(f"### {window_name}"); print("#"*118)
    for sl in ("VWAP_LONG_R3","VWAP_SHORT_R1","TLB_LONG","ORB"):
        base=[r for r in rows if r["sleeve"]==sl and filt_time(r)]
        nb=len(base)
        print(f"\n-- {sl}  (baseline n={nb}, qty={QTY[sl]}) --")
        for vn,vf in VAR:
            rs=[r for r in base if vf(r["direction"],r["sig19"])]
            print(line(vn,stats(rs),nb))

report("FULL PERIOD (2023-12 -> 2026-08)",lambda r:True)
report("RECENT (2026-06-01 -> present)",lambda r:r["entry_utc"]>=RECENT)

# Signal A vs Signal B isolation on trend sleeves + ORB salvage focus
print("\n"+"="*70)
print("NOTE: A1=Signal A (bare EMA9/21 cross-state). B1/B2/B3=Signal B (7-factor).")
print("A2 (ALIGN-BLOCK-OPPOSED) == A1 here (binary alignBull/alignBear); reported once.")
