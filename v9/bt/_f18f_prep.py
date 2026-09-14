"""#18 "RSI Entry Engine" filter prep. Read-only scratch. Regenerates baseline trades
via the harness (exact parity), computes #18 RSI engine on 5-min bars, snapshots the
directional STATE (S1/S2/S3) as-of the closed entry bar (no look-ahead), and captures
per-trade fields needed for Part B (entry_px, native stop/target, ATR@entry, side)."""
import sys, importlib.util, pickle
import numpy as np, pandas as pd, pytz

sys.path.insert(0,"/root/v9/bt")
spec=importlib.util.spec_from_file_location("bt_nq","/root/v9/bt/bt_nq.py")
bt=importlib.util.module_from_spec(spec); sys.modules["bt_nq"]=bt; spec.loader.exec_module(bt)
ET=bt.ET

# ---- monkeypatch sims to capture native stop/target (VWAP + TLB static brackets) ----
_ov=bt.sim_vwap
def sim_vwap_cap(df,sig,entry_utc):
    tr=_ov(df,sig,entry_utc)
    tr["native_stop"]=float(sig["stop_price"]); tr["native_target"]=float(sig["target_price"])
    return tr
bt.sim_vwap=sim_vwap_cap
_ot=bt.sim_tlb
def sim_tlb_cap(bars5,start_j,sig,entry_utc):
    tr=_ot(bars5,start_j,sig,entry_utc)
    tr["native_stop"]=float(sig["stop_price"]); tr["native_target"]=float(sig["target_price"])
    return tr
bt.sim_tlb=sim_tlb_cap

df=bt.load_all()
daily,regime=bt.build_daily_and_regime(df)
bars5=bt.build_5min(df)
vt,_=bt.run_vwap_tlb(df,bars5,regime)
orb_tr=bt.run_orb(df)
print(f"baseline: VWAP_LONG={len(vt['VWAP_LONG_R3'])} VWAP_SHORT={len(vt['VWAP_SHORT_R1'])} "
      f"TLB={len(vt['TLB_LONG'])} ORB={len(orb_tr)}")

# ---- #18 engine over 5-min bars (Pine ta.* semantics) ----
n=len(bars5)
O=np.array([b[1] for b in bars5]); H=np.array([b[2] for b in bars5])
L=np.array([b[3] for b in bars5]); C=np.array([b[4] for b in bars5])
ts5=[b[0] for b in bars5]

def ema(x,p):
    a=2/(p+1); out=np.full(len(x),np.nan)
    # seed at first non-nan (Pine ta.ema begins once source is valid)
    start=next((i for i in range(len(x)) if not np.isnan(x[i])),None)
    if start is None: return out
    out[start]=x[start]
    for i in range(start+1,len(x)): out[i]=a*x[i]+(1-a)*out[i-1]
    return out
def rma(x,p):  # Wilder
    out=np.full(len(x),np.nan)
    if len(x)<p: return out
    out[p-1]=np.nanmean(x[:p])
    for i in range(p,len(x)): out[i]=(out[i-1]*(p-1)+x[i])/p
    return out
def rsi(x,p=14):
    d=np.diff(x,prepend=x[0]); up=np.where(d>0,d,0.0); dn=np.where(d<0,-d,0.0)
    ru=rma(up,p); rd=rma(dn,p); rs=np.where(rd==0,np.inf,ru/rd)
    return 100-100/(1+rs)

rsiRaw=rsi(C,14)
rsiSmooth=ema(rsiRaw,5)          # smoothMode default
signal=ema(rsiSmooth,9)
signalRaw=ema(rsiRaw,9)          # raw-variant momentum line
# ATR(14) Wilder over 5-min TR
prevC=np.concatenate([[C[0]],C[:-1]])
TR=np.maximum(H-L,np.maximum(np.abs(H-prevC),np.abs(L-prevC)))
atr=rma(TR,14)

def latch(line):  # S2 regime: +1 after crossover(line,20), -1 after crossunder(line,80)
    st=np.zeros(len(line),dtype=int); cur=0
    for i in range(1,len(line)):
        a,b=line[i-1],line[i]
        if not (np.isnan(a) or np.isnan(b)):
            if a<=20 and b>20: cur=1
            elif a>=80 and b<80: cur=-1
        st[i]=cur
    return st
s2_smooth=latch(rsiSmooth); s2_raw=latch(rsiRaw)

ts2idx={ts5[i]:i for i in range(n)}
close_arr=np.array([(ts5[i]+pd.Timedelta(minutes=5)).value for i in range(n)])
def idx_for_entry(sleeve,entry_utc):
    if sleeve in ("VWAP_LONG_R3","VWAP_SHORT_R1","TLB_LONG"):
        return ts2idx.get(entry_utc-pd.Timedelta(minutes=5))
    tv=entry_utc.value; pos=np.searchsorted(close_arr,tv,side="right")-1
    return pos if pos>=0 else None

def snap(j):
    return dict(j=int(j),
        rsiRaw=float(rsiRaw[j]),rsiSmooth=float(rsiSmooth[j]),signal=float(signal[j]),
        signalRaw=float(signalRaw[j]),s2_smooth=int(s2_smooth[j]),s2_raw=int(s2_raw[j]),
        atr=float(atr[j]) if not np.isnan(atr[j]) else None)

rows=[]
def add(sleeve,tr):
    j=idx_for_entry(sleeve,tr["entry_utc"]); s=snap(j) if j is not None else None
    rows.append(dict(sleeve=sleeve,direction=tr["direction"],entry_utc=tr["entry_utc"],
        entry_px=float(tr["entry_px"]),R=tr["R"],pnl_usd=tr["pnl_usd"],pnl_points=tr["pnl_points"],
        exit_reason=tr["exit_reason"],
        native_stop=tr.get("native_stop"),native_target=tr.get("native_target"),
        sig18=s))
for s in ("VWAP_LONG_R3","VWAP_SHORT_R1","TLB_LONG"):
    for tr in vt[s]: add(s,tr)
for tr in orb_tr: add("ORB",tr)
miss=sum(1 for r in rows if r["sig18"] is None or r["sig18"]["atr"] is None)
print(f"rows={len(rows)} missing_state_or_atr={miss}")
pickle.dump(rows,open("/root/v9/bt/_f18f_rows.pkl","wb"))
print("saved _f18f_rows.pkl")
for s in ("VWAP_LONG_R3","VWAP_SHORT_R1","TLB_LONG","ORB"):
    rs=[r for r in rows if r["sleeve"]==s]
    u=np.array([r["pnl_usd"] for r in rs]); R=np.array([r["R"] for r in rs])
    gp=u[u>0].sum(); gl=-u[u<0].sum(); pf=gp/gl if gl>0 else float('inf')
    print(f"{s:15} n={len(rs):4d} sumR={R.sum():8.2f} PF={pf:6.2f} net$={u.sum():10,.0f} win%={100*(R>0).mean():5.1f}")
