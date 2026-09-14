"""_r3re_overlay.py — kristov18 R3 VWAP RE-ENTRY overlay on OUR data. READ-ONLY, no live code.
Reuses bt_nq harness (load_all/build_5min/build_daily_and_regime/run_vwap_tlb -> our R3 sleeve)."""
import sys, importlib.util
from pathlib import Path
import numpy as np, pandas as pd, pytz
ET = pytz.timezone("America/New_York")
sys.path.insert(0, "/root/v9/bt"); sys.path.insert(0, "/root/v9")

def _load(n,p):
    s=importlib.util.spec_from_file_location(n,p); m=importlib.util.module_from_spec(s)
    sys.modules[n]=m; s.loader.exec_module(m); return m
bt = _load("bt_nq","/root/v9/bt/bt_nq.py")
vsl= _load("vsl","/root/v9/v9_strategy_lib.py")
usv= vsl.update_session_vwap; sess_key=vsl.session_for_utc_bar

FULL_USD=20.0   # full-NQ $/pt (his convention, for step-1 ballpark)
MNQ_USD =2.0    # our harness convention (MNQ) — used for book combination (ratios are invariant)
K=2.0           # entry_band_k

print("Loading data + building R3 sleeve via bt_nq harness ...")
df = bt.load_all()
daily, regime_by_date = bt.build_daily_and_regime(df)
bars5 = bt.build_5min(df)
vt, sigs = bt.run_vwap_tlb(df, bars5, regime_by_date)
r3 = vt["VWAP_LONG_R3"]
print(f"  R3 trades={len(r3)}  bars5={len(bars5)}  range {bars5[0][0]} -> {bars5[-1][0]}")

# ---- per-5min-bar LONG session vwap/sd (anchor 00:00 UTC), same math the sleeve uses ----
n=len(bars5)
bts=[b[0] for b in bars5]
o5=np.array([b[1] for b in bars5]); h5=np.array([b[2] for b in bars5])
l5=np.array([b[3] for b in bars5]); c5=np.array([b[4] for b in bars5]); v5=np.array([b[5] for b in bars5])
vwap5=np.full(n,np.nan); sd5=np.full(n,np.nan)
cur=None; cumv=cumvp=cumsq=0.0
for i in range(n):
    sk=sess_key(bts[i].to_pydatetime(),0)
    if sk!=cur: cur=sk; cumv=cumvp=cumsq=0.0
    cumv,cumvp,cumsq,vw,sd=usv(cumv,cumvp,cumsq,h5[i],l5[i],c5[i],v5[i])
    vwap5[i]=vw; sd5[i]=sd
upper5=vwap5+K*sd5

bucket_idx={bts[i]:i for i in range(n)}
def floor5(ts): 
    t=pd.Timestamp(ts); t=t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC"); return t.floor("5min")
def et_min(ts): e=ts.tz_convert(ET); return e.hour*60+e.minute
FLAT=15*60+55

# 1-min arrays for exit leg
idx1=df.index; o1=df["open"].values;h1=df["high"].values;l1=df["low"].values;c1=df["close"].values

RISK_CAP_PT=300.0  # $6000 on full NQ = live guard (test 7)
def run_overlay(apply_cap):
    fires=[]
    for tr in r3:
        if tr["exit_reason"]!="STOP": continue
        sig_bucket=floor5(tr["entry_utc"]-pd.Timedelta(minutes=5))
        stop_bucket=floor5(tr["exit_utc"])
        si=bucket_idx.get(sig_bucket); ki=bucket_idx.get(stop_bucket)
        if si is None or ki is None: continue
        sess=sess_key(bts[ki].to_pydatetime(),0)
        entry=None
        j=ki+2
        while j<n and sess_key(bts[j].to_pydatetime(),0)==sess and et_min(bts[j])<FLAT:
            runmax=h5[ki:j].max()
            if h5[j]>runmax:
                fill=max(o5[j],runmax)
                stop_lvl=l5[si:j].min()
                lag_vw=vwap5[j-1]; lag_up=upper5[j-1]
                if np.isnan(lag_vw): j+=1; continue  # no band yet -> can't classify; skip fire
                tp_kind="VWAP" if fill<lag_vw else "UPPER"
                riskpt=fill-stop_lvl
                if riskpt<=0: entry="BADRISK"; break
                if apply_cap and riskpt>RISK_CAP_PT: entry="CAPPED"; break
                entry=dict(j=j,fill=fill,stop=stop_lvl,tp_kind=tp_kind,riskpt=riskpt,sess=sess,r3=tr)
                break
            j+=1
        if entry in (None,"BADRISK","CAPPED"): continue
        # ---- exit leg on 1-min bars, start next completed 5-min boundary ----
        j=entry["j"]; fill=entry["fill"]; stop_lvl=entry["stop"]; kind=entry["tp_kind"]
        start=bts[j]+pd.Timedelta(minutes=5)
        m0=np.searchsorted(idx1.values, np.datetime64(start.tz_convert("UTC").tz_localize(None)))
        ex_px=None; ex_reason=None; ex_ts=None
        m=m0
        while m<len(idx1):
            t=idx1[m]
            if sess_key(t.to_pydatetime(),0)!=entry["sess"]:
                ex_px=c1[m-1] if m>m0 else fill; ex_reason="SESS_END"; ex_ts=idx1[m-1]; break
            if et_min(t)>=FLAT:
                ex_px=c1[m]; ex_reason="FLATTEN"; ex_ts=t; break
            # stop first (conservative)
            if l1[m]<=stop_lvl:
                ex_px=stop_lvl; ex_reason="STOP"; ex_ts=t; break
            # tp: moving lag-1 level as of last completed 5-min bar before t
            b=floor5(t); bi=bucket_idx.get(b-pd.Timedelta(minutes=5))
            lvl=(vwap5[bi] if kind=="VWAP" else upper5[bi]) if bi is not None else np.nan
            if not np.isnan(lvl) and lvl>fill and h1[m]>=lvl:
                ex_px=lvl; ex_reason="TARGET"; ex_ts=t; break
            m+=1
        if ex_px is None:
            ex_px=c1[-1]; ex_reason="DATA_END"; ex_ts=idx1[-1]
        pts=ex_px-fill; R=pts/entry["riskpt"]
        fires.append(dict(entry_utc=bts[j]+pd.Timedelta(minutes=5), entry_et=(bts[j]+pd.Timedelta(minutes=5)).tz_convert(ET),
                          exit_utc=ex_ts, fill=fill, stop=stop_lvl, exit_px=ex_px, reason=ex_reason,
                          tp_kind=kind, pts=pts, R=R, riskpt=entry["riskpt"]))
    return fires

for cap in (False,True):
    fires=run_overlay(cap)
    lbl="WITH $6k cap" if cap else "PURE SPEC (no cap)"
    if not fires: print(f"\n### {lbl}: 0 fires"); continue
    pts=np.array([f["pts"] for f in fires]); R=np.array([f["R"] for f in fires])
    win=(pts>0).mean(); tp=(np.array([f["reason"]=="TARGET" for f in fires])).mean()
    kinds=pd.Series([f["tp_kind"] for f in fires]).value_counts().to_dict()
    reasons=pd.Series([f["reason"] for f in fires]).value_counts().to_dict()
    yrs=(bars5[-1][0]-bars5[0][0]).days/365.25
    print(f"\n### {lbl}: n={len(fires)} ({len(fires)/yrs:.1f}/yr)")
    print(f"  expR={R.mean():+.3f}  win={win*100:.1f}%  TP-hit={tp*100:.1f}%")
    print(f"  net(full $20/pt)=${pts.sum()*FULL_USD:,.0f}  net(MNQ $2/pt)=${pts.sum()*MNQ_USD:,.0f}  expPts={pts.mean():+.2f}")
    print(f"  TP-kind split: {kinds}")
    print(f"  exit reasons: {reasons}")
    # save pure-spec fires for WF
    if not cap:
        pd.DataFrame([{"entry_utc":f["entry_utc"],"entry_et":f["entry_et"].strftime('%Y-%m-%d %H:%M'),
                       "fill":round(f["fill"],2),"stop":round(f["stop"],2),"exit_px":round(f["exit_px"],2),
                       "reason":f["reason"],"tp_kind":f["tp_kind"],"pts":round(f["pts"],2),"R":round(f["R"],3),
                       "exit_utc":f["exit_utc"]} for f in fires]).to_csv("/root/v9/bt/_r3re_fires.csv",index=False)
        print("  -> wrote /root/v9/bt/_r3re_fires.csv")
