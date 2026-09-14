"""PART A: #18 directional STATE as a filter. Read-only scratch.
States S1 centerline / S2 reclaim-latch / S3 momentum, on smoothed and raw RSI.
Filters F1 KEEP-ALIGNED, F2 DROP-OPPOSED (direction-aware, mirrored for shorts)."""
import pickle, numpy as np, pandas as pd
rows=pickle.load(open("/root/v9/bt/_f18f_rows.pkl","rb"))
RECENT=pd.Timestamp("2026-06-01",tz="UTC")
POINT=2.0; QTY={"VWAP_LONG_R3":2,"VWAP_SHORT_R1":2,"TLB_LONG":1,"ORB":1}
def usd(r): return r["pnl_points"]*POINT*QTY[r["sleeve"]]

# ---- state -> {'bull','bear','neut'} given a trade's snapshot ----
def st_S1(s,raw):
    v=s["rsiRaw"] if raw else s["rsiSmooth"]; return "bull" if v>50 else "bear"
def st_S2(s,raw):
    v=s["s2_raw"] if raw else s["s2_smooth"]; return "bull" if v>0 else ("bear" if v<0 else "neut")
def st_S3(s,raw):
    line=s["rsiRaw"] if raw else s["rsiSmooth"]; sig=s["signalRaw"] if raw else s["signal"]
    return "bull" if line>sig else "bear"
STATES=[("S1",st_S1),("S2",st_S2),("S3",st_S3)]

def aligned(direction): return "bull" if direction=="long" else "bear"
def opposed(direction): return "bear" if direction=="long" else "bull"

def stats(rs):
    if not rs: return None
    R=np.array([r["R"] for r in rs]); u=np.array([usd(r) for r in rs])
    gp=u[u>0].sum(); gl=-u[u<0].sum(); pf=gp/gl if gl>0 else float('inf')
    rr=sorted(rs,key=lambda r:r["entry_utc"]); cum=np.cumsum([usd(r) for r in rr])
    peak=np.maximum.accumulate(cum); mdd=(cum-peak).min() if len(cum) else 0
    return dict(n=len(rs),sumR=R.sum(),pf=pf,usd=u.sum(),win=100*(R>0).mean(),mdd=mdd)

def line(name,st,nbase):
    if st is None: return f"    {name:16} none"
    pf="inf " if st['pf']==float('inf') else f"{st['pf']:.2f}"
    pct=f"{100*st['n']/nbase:3.0f}%" if nbase else " - "
    return (f"    {name:16} {st['n']:4d} {pct} PF={pf:>5} net$={st['usd']:9,.0f} "
            f"sumR={st['sumR']:7.2f} win%={st['win']:5.1f} DD={st['mdd']:8,.0f}")

def report(title,tfilt,raw):
    tag="RAW-RSI" if raw else "SMOOTHED"
    print("\n"+"#"*104); print(f"### {title}   [{tag} engine]"); print("#"*104)
    for sl in ("VWAP_LONG_R3","VWAP_SHORT_R1","TLB_LONG","ORB"):
        base=[r for r in rows if r["sleeve"]==sl and tfilt(r)]
        nb=len(base)
        bs=stats(base)
        print(f"\n  {sl}  baseline n={nb} qty={QTY[sl]}"); print(line("0.BASE",bs,nb))
        for snm,sfn in STATES:
            d0=base[0]["direction"] if base else "long"
            # F1 KEEP-ALIGNED
            f1=[r for r in base if sfn(r["sig18"],raw)==aligned(r["direction"])]
            # F2 DROP-OPPOSED
            f2=[r for r in base if sfn(r["sig18"],raw)!=opposed(r["direction"])]
            same = len(f1)==len(f2)
            print(line(f"{snm}.F1keepAlign",stats(f1),nb))
            if not same: print(line(f"{snm}.F2dropOpp",stats(f2),nb))
    print("  (F2 omitted where identical to F1 — binary state, no neutral)")

for raw in (False,True):
    report("FULL PERIOD (2023-12 -> 2026-08)",lambda r:True,raw)
report("RECENT (2026-06-01 -> present)",lambda r:r["entry_utc"]>=RECENT,False)
report("RECENT (2026-06-01 -> present)",lambda r:r["entry_utc"]>=RECENT,True)
