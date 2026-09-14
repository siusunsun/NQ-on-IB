"""_r3re_wf.py — OUR walk-forward: does the overlay clear or decline on our book? READ-ONLY."""
import sys, importlib.util
import numpy as np, pandas as pd, pytz
ET=pytz.timezone("America/New_York")
sys.path.insert(0,"/root/v9/bt"); sys.path.insert(0,"/root/v9")
def _load(n,p):
    s=importlib.util.spec_from_file_location(n,p); m=importlib.util.module_from_spec(s)
    sys.modules[n]=m; s.loader.exec_module(m); return m
bt=_load("bt_nq","/root/v9/bt/bt_nq.py")
MNQ=2.0
print("Rebuilding book ...")
df=bt.load_all(); daily,reg=bt.build_daily_and_regime(df); bars5=bt.build_5min(df)
vt,_=bt.run_vwap_tlb(df,bars5,reg)

# book = our 3 live futures sleeves
book=[]
for name in ("VWAP_LONG_R3","VWAP_SHORT_R1","TLB_LONG"):
    for t in vt[name]:
        book.append(dict(sleeve=name, t=t["entry_utc"], xt=t["exit_utc"], usd=t["pnl_points"]*MNQ))
# overlay fires
fdf=pd.read_csv("/root/v9/bt/_r3re_fires.csv", parse_dates=["entry_utc","exit_utc"])
ov=[dict(sleeve="R3_REENTRY", t=pd.Timestamp(r.entry_utc), xt=pd.Timestamp(r.exit_utc), usd=r.pts*MNQ)
    for r in fdf.itertuples()]

def metrics(trades):
    if not trades: return None
    s=sorted(trades,key=lambda x:x["t"])
    usd=np.array([x["usd"] for x in s])
    cum=np.cumsum(usd); peak=np.maximum.accumulate(cum); mdd=-(cum-peak).min()
    net=usd.sum(); ndd=net/mdd if mdd>0 else float("inf")
    # daily sharpe by exit ET date
    dser={}
    for x in s:
        d=pd.Timestamp(x["xt"]).tz_convert(ET).date() if x["xt"] is not None else pd.Timestamp(x["t"]).tz_convert(ET).date()
        dser[d]=dser.get(d,0.0)+x["usd"]
    dv=np.array(list(dser.values()))
    sharpe=(dv.mean()/dv.std()*np.sqrt(252)) if dv.std()>0 else float("nan")
    return dict(n=len(s),net=net,mdd=mdd,ndd=ndd,sharpe=sharpe,win=(usd>0).mean()*100)

def seg(trades, lo, hi):
    return [x for x in trades if (lo is None or x["t"]>=lo) and (hi is None or x["t"]<hi)]

# split midpoint by book entry dates
alltimes=sorted(x["t"] for x in book)
mid=alltimes[len(alltimes)//2]
print(f"book n={len(book)}  overlay n={len(ov)}  split(mid)={mid}")
print(f"book range {alltimes[0]} -> {alltimes[-1]}")

def show(title, lo, hi):
    b=seg(book,lo,hi); o=seg(ov,lo,hi)
    mb=metrics(b); mbo=metrics(b+o); mo=metrics(o)
    print(f"\n=== {title} ===")
    print(f"  overlay-only: n={mo['n'] if mo else 0} net=${mo['net']:,.0f} mdd=${mo['mdd']:,.0f} net/DD={mo['ndd']:.2f}" if mo else f"  overlay-only: 0")
    print(f"  book ONLY   : n={mb['n']} net=${mb['net']:,.0f} maxDD=${mb['mdd']:,.0f}  net/DD={mb['ndd']:.2f}  Sharpe={mb['sharpe']:.2f}")
    print(f"  book+OVERLAY: n={mbo['n']} net=${mbo['net']:,.0f} maxDD=${mbo['mdd']:,.0f}  net/DD={mbo['ndd']:.2f}  Sharpe={mbo['sharpe']:.2f}")
    dn=mbo['ndd']-mb['ndd']; dd=mbo['mdd']-mb['mdd']; ds=mbo['sharpe']-mb['sharpe']
    print(f"  DELTA net/DD={dn:+.2f}  maxDD={dd:+,.0f} ({'NARROWS' if dd<0 else 'WIDENS/flat'})  Sharpe={ds:+.2f}  net={mbo['net']-mb['net']:+,.0f}")

show("FULL SAMPLE", None, None)
show("IS  (train = first half)", None, mid)
show("OOS (test  = second half)", mid, None)
y26=pd.Timestamp("2026-01-01",tz="UTC"); jun26=pd.Timestamp("2026-06-01",tz="UTC")
show("2026 YTD", y26, None)
show("2026-06+ (recent weak regime)", jun26, None)
