"""#19 (Sniper V.02) filter prep. Read-only, scratch. Regenerates baseline trades via
the harness (exact parity) and computes indicator-#19 STATE as-of the entry bar."""
import sys, importlib.util, pickle
from pathlib import Path
import numpy as np, pandas as pd, pytz

sys.path.insert(0,"/root/v9/bt")
spec=importlib.util.spec_from_file_location("bt_nq","/root/v9/bt/bt_nq.py")
bt=importlib.util.module_from_spec(spec); sys.modules["bt_nq"]=bt; spec.loader.exec_module(bt)
ET=bt.ET; CHI=pytz.timezone("America/Chicago")

df=bt.load_all()
daily,regime=bt.build_daily_and_regime(df)
bars5=bt.build_5min(df)
print(f"1min={len(df):,} 5min={len(bars5):,} range {df.index[0]} -> {df.index[-1]}")

# ---- baseline trades (EXACT harness parity) ----
vt,_=bt.run_vwap_tlb(df,bars5,regime)
orb_tr=bt.run_orb(df)
print(f"baseline counts: VWAP_LONG={len(vt['VWAP_LONG_R3'])} VWAP_SHORT={len(vt['VWAP_SHORT_R1'])} "
      f"TLB={len(vt['TLB_LONG'])} ORB={len(orb_tr)}")

# ---- indicator arrays over 5-min bars (Pine ta.* semantics) ----
n=len(bars5)
O=np.array([b[1] for b in bars5]); H=np.array([b[2] for b in bars5])
L=np.array([b[3] for b in bars5]); C=np.array([b[4] for b in bars5]); V=np.array([b[5] for b in bars5])
ts5=[b[0] for b in bars5]  # tz-aware UTC bucket start

def ema(x,p):
    a=2/(p+1); out=np.empty(len(x)); out[0]=x[0]
    for i in range(1,len(x)): out[i]=a*x[i]+(1-a)*out[i-1]
    return out
def rma(x,p):  # Wilder
    out=np.full(len(x),np.nan)
    if len(x)<p: return out
    out[p-1]=x[:p].mean()
    for i in range(p,len(x)): out[i]=(out[i-1]*(p-1)+x[i])/p
    return out
def rsi(x,p=14):
    d=np.diff(x,prepend=x[0]); up=np.where(d>0,d,0.0); dn=np.where(d<0,-d,0.0)
    ru=rma(up,p); rd=rma(dn,p)
    rs=np.where(rd==0,np.inf,ru/rd)
    return 100-100/(1+rs)

ema9=ema(C,9); ema21=ema(C,21)
rsi14=rsi(C,14)
# rsi5m: Pine uses request.security 5-min on close[1]; base bars already 5-min -> rsi(close[1],14)
Cshift=np.concatenate([[C[0]],C[:-1]])
rsi5m=rsi(Cshift,14)
# MACD 12,26,9
macdLine=ema(C,12)-ema(C,26); macdSig=ema(macdLine,9)
# DMI/ADX(14,14) Wilder
prevC=np.concatenate([[C[0]],C[:-1]])
TR=np.maximum(H-L,np.maximum(np.abs(H-prevC),np.abs(L-prevC)))
upMove=H-np.concatenate([[H[0]],H[:-1]]); dnMove=np.concatenate([[L[0]],L[:-1]])-L
plusDM=np.where((upMove>dnMove)&(upMove>0),upMove,0.0)
minusDM=np.where((dnMove>upMove)&(dnMove>0),dnMove,0.0)
atrR=rma(TR,14); plusDI=100*rma(plusDM,14)/atrR; minusDI=100*rma(minusDM,14)/atrR
dx=100*np.abs(plusDI-minusDI)/(plusDI+minusDI); dx=np.nan_to_num(dx,nan=0.0)
adx=rma(dx,14)
# volume sma20
volsma=pd.Series(V).rolling(20).mean().values
# session-anchored VWAP: anchor at 17:00 America/Chicago (CME session open)
hlc3=(H+L+C)/3.0
vwap=np.full(n,np.nan); cumPV=0.0; cumV=0.0; cur_sess=None
for i in range(n):
    t_chi=ts5[i].tz_convert(CHI)
    # session date: bars at/after 17:00 belong to next day's session
    sess=t_chi.date() if t_chi.hour>=17 else (t_chi-pd.Timedelta(days=1)).date()
    if sess!=cur_sess: cumPV=0.0; cumV=0.0; cur_sess=sess
    vol=V[i] if V[i]>0 else 1.0  # avoid div0 on zero-volume bars
    cumPV+=hlc3[i]*vol; cumV+=vol
    vwap[i]=cumPV/cumV if cumV>0 else hlc3[i]

# ---- per-bar Signal A + Signal B ----
def I(x): return np.asarray(x,dtype=bool).astype(int)
volsma_f=np.nan_to_num(volsma,nan=np.inf)  # NaN warmup -> volume factor off
bScore=( I(C>vwap)+I(rsi14>50)+I(macdLine>macdSig)+I(ema9>ema21)
        +I((adx>25)&(C>ema9))+I((V>volsma_f)&(C>O))+I(rsi5m>50) )
rScore=( I(C<vwap)+I(rsi14<50)+I(macdLine<macdSig)+I(ema9<ema21)
        +I((adx>25)&(C<ema9))+I((V>volsma_f)&(C<O))+I(rsi5m<50) )
bullPct=bScore/7*100; bearPct=rScore/7*100
alignBull=ema9>ema21; alignBear=ema9<ema21
def bias_text(bp,rp):
    if bp-rp>=40: return "STRONG BULL"
    if rp-bp>=40: return "STRONG BEAR"
    return "MILD BULL" if bp>=rp else "MILD BEAR"
biasText=[bias_text(bullPct[i],bearPct[i]) for i in range(n)]

# map bucket-start ts -> index; and helper to find last CLOSED 5min bar at/before a utc time
ts2idx={ts5[i]:i for i in range(n)}
close_arr=np.array([ (ts5[i]+pd.Timedelta(minutes=5)).value for i in range(n)])  # ns of bar close
def idx_for_entry(sleeve,entry_utc):
    if sleeve in ("VWAP_LONG_R3","VWAP_SHORT_R1","TLB_LONG"):
        j=ts2idx.get(entry_utc-pd.Timedelta(minutes=5))
        return j
    else:  # ORB: last 5min bar closed at/before entry_utc
        tv=entry_utc.value
        pos=np.searchsorted(close_arr,tv,side="right")-1
        return pos if pos>=0 else None

def snap(j):
    return dict(j=j, ema9=ema9[j],ema21=ema21[j],alignBull=bool(alignBull[j]),alignBear=bool(alignBear[j]),
               bullPct=float(bullPct[j]),bearPct=float(bearPct[j]),bias=biasText[j],
               bScore=int(bScore[j]),rScore=int(rScore[j]),
               adx=float(adx[j]),rsi14=float(rsi14[j]),vwap=float(vwap[j]),close=float(C[j]))

rows=[]
def add(sleeve,tr):
    j=idx_for_entry(sleeve,tr["entry_utc"])
    s=snap(j) if j is not None else None
    rows.append(dict(sleeve=sleeve,direction=tr["direction"],entry_utc=tr["entry_utc"],
                     R=tr["R"],pnl_usd=tr["pnl_usd"],pnl_points=tr["pnl_points"],
                     exit_reason=tr["exit_reason"],sig19=s))
for s in ("VWAP_LONG_R3","VWAP_SHORT_R1","TLB_LONG"):
    for tr in vt[s]: add(s,tr)
for tr in orb_tr: add("ORB",tr)

miss=sum(1 for r in rows if r["sig19"] is None)
print(f"rows={len(rows)} missing sig19={miss}")
pickle.dump(rows,open("/root/v9/bt/_f19_rows.pkl","wb"))
print("saved _f19_rows.pkl")
# quick baseline stats sanity
for s in ("VWAP_LONG_R3","VWAP_SHORT_R1","TLB_LONG","ORB"):
    rs=[r for r in rows if r["sleeve"]==s]
    usd=np.array([r["pnl_usd"] for r in rs]); R=np.array([r["R"] for r in rs])
    gp=usd[usd>0].sum(); gl=-usd[usd<0].sum()
    pf=gp/gl if gl>0 else float('inf')
    print(f"{s:15} n={len(rs):4d} sumR={R.sum():8.2f} PF={pf:6.2f} net$={usd.sum():10,.0f} win%={100*(R>0).mean():5.1f}")
