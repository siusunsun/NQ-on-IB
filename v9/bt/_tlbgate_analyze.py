"""#TLB momentum-gate analysis. Read-only scratch. Reuses _f19_rows.pkl (ema9/ema21/rsi14
per TLB entry) and _f18f_rows.pkl (raw rsi14) — both already parity-matched. Filters TLB_LONG
native entries by 4 momentum gates + baseline, across FULL/TRAIN/TEST/RECENT windows.
No look-ahead: gate evaluated on the CLOSED entry bar snapshot. Touches no live code."""
import pickle, numpy as np, pandas as pd

r19=pickle.load(open("/root/v9/bt/_f19_rows.pkl","rb"))
r18=pickle.load(open("/root/v9/bt/_f18f_rows.pkl","rb"))
t19=[r for r in r19 if r["sleeve"]=="TLB_LONG"]
t18=[r for r in r18 if r["sleeve"]=="TLB_LONG"]

POINT=2.0; QTY=1  # MNQ $2/pt, qty=1
def usd(r): return r["pnl_points"]*POINT*QTY

# ---- align f19 & f18f by entry_utc, build merged records ----
m18={r["entry_utc"]:r for r in t18}
merged=[]
rsi_mismatch=0
for r in t19:
    e=r["entry_utc"]; s=r["sig19"]; o=m18.get(e)
    if o is None: raise SystemExit(f"UNALIGNED entry {e} missing in f18f")
    rsi19=s["rsi14"]; rsi18=o["sig18"]["rsiRaw"]
    if abs(rsi19-rsi18)>1e-6: rsi_mismatch+=1
    merged.append(dict(entry_utc=e, pnl_points=r["pnl_points"], R=r["R"],
                       ema9=s["ema9"], ema21=s["ema21"], rsi=rsi18))  # use f18f raw rsi for the gate
print(f"TLB f19 n={len(t19)} f18f n={len(t18)} merged n={len(merged)} rsi19!=rsi18 count={rsi_mismatch}")

# ---- baseline parity check ----
def stats(rs):
    if not rs: return dict(n=0,win=0,pf=0,usd=0,sumR=0,mdd=0,rdd=0)
    R=np.array([x["R"] for x in rs]); u=np.array([usd(x) for x in rs])
    gp=u[u>0].sum(); gl=-u[u<0].sum(); pf=gp/gl if gl>0 else float('inf')
    rr=sorted(rs,key=lambda x:x["entry_utc"]); cum=np.cumsum([usd(x) for x in rr])
    peak=np.maximum.accumulate(cum); mdd=(cum-peak).min() if len(cum) else 0.0
    net=u.sum(); rdd=(net/abs(mdd)) if mdd<0 else float('inf')
    return dict(n=len(rs),win=100*(R>0).mean(),pf=pf,usd=net,sumR=R.sum(),mdd=mdd,rdd=rdd)

full=stats(merged)
print(f"\nPARITY (FULL, no gate): n={full['n']} PF={full['pf']:.2f} net=${full['usd']:,.0f} "
      f"maxDD=${full['mdd']:,.0f} win%={full['win']:.1f} sumR={full['sumR']:.2f}")
print("  target: n=285 PF 1.10 net $1,782 maxDD -$3,022")

# ---- gates (evaluated on closed entry bar) ----
def g0(x): return True
def g1(x): return x["ema9"]>x["ema21"]
def g2(x): return x["rsi"]>50
def g3(x): return (x["ema9"]>x["ema21"]) and (x["rsi"]>50)
def g4(x): return (x["ema9"]>x["ema21"]) or (x["rsi"]>50)
GATES=[("G0 BASELINE",g0),("G1 EMA9>21",g1),("G2 RSI>50",g2),("G3 BOTH(AND)",g3),("G4 EITHER(OR)",g4)]

# ---- windows (by entry_utc, tz-aware UTC) ----
TS=lambda s: pd.Timestamp(s,tz="UTC")
WINDOWS=[
 ("(a) FULL   2023-12->2026-08", lambda e: True),
 ("(b) TRAIN  2024-01->2025-12", lambda e: TS("2024-01-01")<=e<TS("2026-01-01")),
 ("(c) TEST   2026-01->present", lambda e: e>=TS("2026-01-01")),
 ("(d) RECENT 2026-06-01->pres", lambda e: e>=TS("2026-06-01")),
]

def fmt(name,st,nbase):
    if st["n"]==0: return f"  {name:14} |    0   0% |   none"
    pf="inf" if st["pf"]==float('inf') else f"{st['pf']:.2f}"
    rdd="inf" if st["rdd"]==float('inf') else f"{st['rdd']:.2f}"
    pct=f"{100*st['n']/nbase:3.0f}%" if nbase else "  -"
    return (f"  {name:14} | {st['n']:4d} {pct} | win {st['win']:5.1f}% | PF {pf:>5} | "
            f"net ${st['usd']:8,.0f} | sumR {st['sumR']:7.2f} | maxDD ${st['mdd']:8,.0f} | ret/DD {rdd:>5}")

for wname,wfilt in WINDOWS:
    pool=[x for x in merged if wfilt(x["entry_utc"])]
    nbase=len(pool)
    print("\n"+"="*132); print(f"### WINDOW {wname}   (baseline pool n={nbase})"); print("="*132)
    print(f"  {'variant':14} | {'n':>4} {'%':>4} | {'win':>7} | {'PF':>6} | {'net$':>11} | {'sumR':>9} | {'maxDD$':>11} | ret/DD")
    print("  "+"-"*128)
    for gn,gf in GATES:
        kept=[x for x in pool if gf(x)]
        print(fmt(gn,stats(kept),nbase))
    # dropped-trade P&L per gate (quality separation check)
    print("  "+"-"*128)
    print("  DROPPED trades (P&L of entries the gate removes) — net-positive dropped => null/proportional:")
    for gn,gf in GATES[1:]:
        dropped=[x for x in pool if not gf(x)]
        d=stats(dropped)
        if d["n"]==0: print(f"    {gn:14} drops 0"); continue
        pf="inf" if d["pf"]==float('inf') else f"{d['pf']:.2f}"
        print(f"    {gn:14} drops {d['n']:4d} | win {d['win']:5.1f}% | PF {pf:>5} | net ${d['usd']:8,.0f} | sumR {d['sumR']:7.2f}")
