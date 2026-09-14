import pickle, numpy as np, pandas as pd, pytz
ET=pytz.timezone("America/New_York")
E=pickle.load(open("/root/v9/bt/_atr_entries.pkl","rb"))["entries"]
bars5=pickle.load(open("/root/v9/bt/_atr_bars5.pkl","rb"))["bars5"]
df=pd.read_pickle("/root/v9/bt/_atr_df.pkl")
QTY={"VWAP_LONG_R3":2,"TLB_LONG":1}; PT=2.0
RECENT=pd.Timestamp("2026-06-01",tz="UTC")

def sim_vwap_exit(entry_utc,entry,stop,target):
    sub=df.loc[df.index>=entry_utc]; hi=sub["high"].values; lo=sub["low"].values; cl=sub["close"].values; ts=sub.index
    for k in range(len(sub)):
        et=ts[k].tz_convert(ET); m=et.hour*60+et.minute
        if lo[k]<=stop: return stop,"STOP"
        if hi[k]>=target: return target,"TARGET"
        if m>=15*60+55 and et.hour<17: return cl[k],"GAP_FLATTEN"
    return cl[-1],"DATA_END"

def sim_tlb_exit(start_j,entry,stop,target):
    for j in range(start_j+1,len(bars5)):
        bts,bo,bh,bl,bc,bv=bars5[j]; et=bts.tz_convert(ET); hm=(et.hour,et.minute)
        if bc<=stop: return bc,"STOP"
        if bc>=target: return bc,"TARGET"
        if hm>=(15,20): return bc,"EOD"
    return bars5[-1][4],"DATA_END"

def simulate(e,stop,target):
    if e["sleeve"]=="VWAP_LONG_R3": xpx,reason=sim_vwap_exit(e["entry_utc"],e["sig"]["entry_price"],stop,target)
    else: xpx,reason=sim_tlb_exit(e["j"],e["sig"]["entry_price"],stop,target)
    entry=e["sig"]["entry_price"]; pts=xpx-entry; risk=entry-stop
    return dict(pts=pts,R=(pts/risk if risk else 0),usd=pts*PT*QTY[e["sleeve"]],reason=reason,entry_utc=e["entry_utc"])

def build(scheme,e):
    entry=e["sig"]["entry_price"]; atr=e["atr"]; orig_stop=e["sig"]["stop_price"]; orig_tgt=e["sig"]["target_price"]
    if np.isnan(atr): return None
    astop=entry-1.5*atr
    if scheme=="baseline": return simulate(e,orig_stop,orig_tgt)
    if scheme=="stoponly": return simulate(e,astop,orig_tgt)
    if scheme.startswith("both"): N=int(scheme[4:]); return simulate(e,astop,entry+N*1.5*atr)
    if scheme.startswith("tgt"): N=int(scheme[3:]); return simulate(e,orig_stop,entry+N*1.5*atr)

def stats(rows):
    if not rows: return None
    R=np.array([r["R"] for r in rows]); usd=np.array([r["usd"] for r in rows])
    wins=(R>0).sum(); gp=usd[usd>0].sum(); gl=-usd[usd<0].sum()
    rr=sorted(rows,key=lambda r:r["entry_utc"]); cum=np.cumsum([r["usd"] for r in rr]); peak=np.maximum.accumulate(cum)
    mdd=(cum-peak).min() if len(cum) else 0
    return dict(n=len(rows),win=wins/len(rows)*100,meanR=R.mean(),sumR=R.sum(),
                pf=(gp/gl if gl>0 else float("inf")),usd=usd.sum(),mdd=mdd)

schemes=["baseline","stoponly"]+[f"both{n}" for n in range(1,6)]+[f"tgt{n}" for n in range(1,6)]

def report(title,filt):
    print("\n"+"="*100); print(title); print("="*100)
    for grp in ["VWAP_LONG_R3","TLB_LONG","COMBINED"]:
        print(f"\n--- {grp} ---")
        print(f"{'scheme':10} {'n':>4} {'win%':>6} {'meanR':>7} {'sumR':>8} {'PF':>6} {'$':>10} {'maxDD$':>10}")
        for sc in schemes:
            rows=[]
            for e in E:
                if grp!="COMBINED" and e["sleeve"]!=grp: continue
                if not filt(e): continue
                r=build(sc,e)
                if r: rows.append(r)
            st=stats(rows)
            if not st: print(f"{sc:10}  none"); continue
            pf=f"{st['pf']:.2f}" if st['pf']!=float('inf') else "inf"
            print(f"{sc:10} {st['n']:>4} {st['win']:>6.1f} {st['meanR']:>7.2f} {st['sumR']:>8.1f} {pf:>6} {st['usd']:>10,.0f} {st['mdd']:>10,.0f}")

report("FULL PERIOD",lambda e:True)
report("RECENT (2026-06-01 onward)",lambda e:e["entry_utc"]>=RECENT)

# geometry: median stop distances
print("\n"+"="*100); print("STOP GEOMETRY (median distance, points)"); print("="*100)
for grp in ["VWAP_LONG_R3","TLB_LONG"]:
    orig=[]; atrd=[]
    for e in E:
        if e["sleeve"]!=grp or np.isnan(e["atr"]): continue
        orig.append(e["sig"]["entry_price"]-e["sig"]["stop_price"]); atrd.append(1.5*e["atr"])
    print(f"{grp}: median orig-stop={np.median(orig):.1f}pt  median 1.5xATR-stop={np.median(atrd):.1f}pt  (n={len(orig)})")
    # recent only
    orig=[]; atrd=[]
    for e in E:
        if e["sleeve"]!=grp or np.isnan(e["atr"]) or e["entry_utc"]<RECENT: continue
        orig.append(e["sig"]["entry_price"]-e["sig"]["stop_price"]); atrd.append(1.5*e["atr"])
    if orig: print(f"   RECENT: median orig-stop={np.median(orig):.1f}pt  median 1.5xATR-stop={np.median(atrd):.1f}pt  (n={len(orig)})")
