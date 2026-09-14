"""PART B: adopt #18's ATR-stop + R-target on our trades, re-simulating exits.
SL = entry -/+ ATR(14)@entry * atrMult ; TP = entry +/- risk*rrTarget  (risk=ATR*atrMult).
STOP-ONLY (ATR sl + native tp), TARGET-ONLY (native sl + ATR tp), BOTH.
Re-sim uses each sleeve's native execution: VWAP/ORB 1-min intrabar (STOP wins ties) with
native EOD flatten; TLB 5-min close with 15:20 ET EOD. Applied to ALL trades per sleeve
(exit-scheme test, matching the earlier ATR-test basis). Read-only scratch."""
import sys, importlib.util, pickle
import numpy as np, pandas as pd
sys.path.insert(0,"/root/v9/bt")
spec=importlib.util.spec_from_file_location("bt_nq","/root/v9/bt/bt_nq.py")
bt=importlib.util.module_from_spec(spec); sys.modules["bt_nq"]=bt; spec.loader.exec_module(bt)
ET=bt.ET
rows=pickle.load(open("/root/v9/bt/_f18f_rows.pkl","rb"))
RECENT=pd.Timestamp("2026-06-01",tz="UTC"); POINT=2.0
QTY={"VWAP_LONG_R3":2,"VWAP_SHORT_R1":2,"TLB_LONG":1,"ORB":1}

df=bt.load_all(); bars5=bt.build_5min(df)
idx_ns=df.index.values.astype("datetime64[ns]").astype("int64")  # index is us; normalize to ns
H=df["high"].values; L=df["low"].values; Cl=df["close"].values
et=df.index.tz_convert(ET); etmin=np.array([t.hour*60+t.minute for t in et]); ethr=np.array([t.hour for t in et])
b5_ns=np.array([b[0].value for b in bars5])
b5H=np.array([b[2] for b in bars5]); b5L=np.array([b[3] for b in bars5]); b5C=np.array([b[4] for b in bars5])
b5et=[bars5[i][0].tz_convert(ET) for i in range(len(bars5))]
b5min=np.array([t.hour*60+t.minute for t in b5et])

def sim_intraday(entry_utc,entry,direction,sl,tp,eod_start,gap_end_hr):
    """1-min intrabar. STOP wins ties. Flatten at close once etmin>=eod_start (& hour<gap_end_hr)."""
    p=np.searchsorted(idx_ns,entry_utc.value,side="left")
    long=direction=="long"
    for k in range(p,len(idx_ns)):
        if long:
            hs=L[k]<=sl; ht=(tp is not None) and H[k]>=tp
        else:
            hs=H[k]>=sl; ht=(tp is not None) and L[k]<=tp
        if hs: return sl
        if ht: return tp
        if etmin[k]>=eod_start and ethr[k]<gap_end_hr: return Cl[k]
    return Cl[-1]

def sim_tlb(entry_utc,jbar,entry,sl,tp):
    """5-min close vs sl/tp; EOD flatten 15:20 ET at close."""
    for j in range(jbar+1,len(bars5)):
        bc=b5C[j]
        if bc<=sl: return bc
        if bc>=tp if tp is not None else False: return bc
        if b5min[j]>=15*60+20: return bc
    return b5C[-1]

def exit_price(r,sl,tp):
    sl_slv=r["sleeve"]; d=r["direction"]; e=r["entry_px"]; eu=r["entry_utc"]
    if sl_slv=="TLB_LONG":
        return sim_tlb(eu,r["sig18"]["j"],e,sl,tp)
    if sl_slv in ("VWAP_LONG_R3","VWAP_SHORT_R1"):
        return sim_intraday(eu,e,d,sl,tp,15*60+55,17)   # gap flatten 15:55-16:59
    return sim_intraday(eu,e,d,sl,tp,15*60+55,17)       # ORB EOD 15:55

def brackets(r,atrMult,rr):
    e=r["entry_px"]; atr=r["sig18"]["atr"]; risk=atr*atrMult
    if r["direction"]=="long": return e-risk, e+risk*rr, risk
    return e+risk, e-risk*rr, risk

def pnl_pts(r,xpx):
    sgn=1.0 if r["direction"]=="long" else -1.0; return sgn*(xpx-r["entry_px"])

def stats(items):  # items: list of (r, pnl_points)
    if not items: return None
    u=np.array([p*POINT*QTY[r["sleeve"]] for r,p in items])
    risk=np.array([abs(r["entry_px"]-r["native_stop"]) if r["native_stop"] is not None else np.nan for r,_ in items])
    R=np.array([p/rk if rk and not np.isnan(rk) else np.nan for (r,p),rk in zip(items,risk)])
    gp=u[u>0].sum(); gl=-u[u<0].sum(); pf=gp/gl if gl>0 else float('inf')
    order=sorted(range(len(items)),key=lambda i:items[i][0]["entry_utc"])
    cum=np.cumsum([u[i] for i in order]); peak=np.maximum.accumulate(cum); mdd=(cum-peak).min() if len(cum) else 0
    sr=np.nansum(R) if not np.all(np.isnan(R)) else float('nan')
    return dict(n=len(items),pf=pf,usd=u.sum(),sumR=sr,win=100*(u>0).mean(),mdd=mdd)

def line(name,st):
    if st is None: return f"    {name:20} none"
    pf="inf " if st['pf']==float('inf') else f"{st['pf']:.2f}"
    sr="  nan" if np.isnan(st['sumR']) else f"{st['sumR']:6.1f}"
    return f"    {name:20} n={st['n']:4d} PF={pf:>5} net$={st['usd']:9,.0f} sumR={sr} win%={st['win']:5.1f} DD={st['mdd']:8,.0f}"

def native_items(base): return [(r,r["pnl_points"]) for r in base]

MULTS=[0.5,1.0,1.5]; RRS=[1,2,3,5]
def run(title,tfilt):
    print("\n"+"#"*100); print(f"### PART B  {title}"); print("#"*100)
    for sl in ("VWAP_LONG_R3","VWAP_SHORT_R1","TLB_LONG","ORB"):
        base=[r for r in rows if r["sleeve"]==sl and tfilt(r)]
        if not base: print(f"\n  {sl}: no trades"); continue
        has_tgt = base[0]["native_target"] is not None
        print(f"\n  {sl}  (n={len(base)}, qty={QTY[sl]}, native_target={'yes' if has_tgt else 'NONE'})")
        print(line("NATIVE",stats(native_items(base))))
        for am in MULTS:
            for rr in RRS:
                both=[]; stopo=[]; targo=[]
                for r in base:
                    slp,tpp,_=brackets(r,am,rr)
                    both.append((r,pnl_pts(r,exit_price(r,slp,tpp))))
                    stopo.append((r,pnl_pts(r,exit_price(r,slp,r["native_target"]))))
                    if r["native_stop"] is not None:
                        targo.append((r,pnl_pts(r,exit_price(r,r["native_stop"],tpp))))
                tag=f"a{am}xR{rr}"
                print(line(f"BOTH  {tag}",stats(both)))
                print(line(f"STOP  {tag}",stats(stopo)))
                if targo: print(line(f"TARG  {tag}",stats(targo)))

run("FULL PERIOD",lambda r:True)
run("RECENT (2026-06-01+)",lambda r:r["entry_utc"]>=RECENT)
