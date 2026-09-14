"""ATR-stop/target test for V9 (indicator #19 scheme). Read-only, scratch only."""
import sys, importlib.util
from pathlib import Path
import numpy as np, pandas as pd, pytz

sys.path.insert(0, "/root/v9/bt")
spec = importlib.util.spec_from_file_location("bt_nq", "/root/v9/bt/bt_nq.py")
bt = importlib.util.module_from_spec(spec); sys.modules["bt_nq"]=bt; spec.loader.exec_module(bt)

ET = bt.ET
df = bt.load_all()
daily, regime_by_date = bt.build_daily_and_regime(df)
bars5 = bt.build_5min(df)
print(f"1min bars={len(df):,}  5min bars={len(bars5):,}  range {df.index[0]} -> {df.index[-1]}")

# ---- Wilder ATR(14) over 5-min bars (value at index j uses bars[..j] inclusive) ----
N_ATR=14
H=np.array([b[2] for b in bars5]); L=np.array([b[3] for b in bars5]); C=np.array([b[4] for b in bars5])
prevC=np.concatenate([[C[0]], C[:-1]])
TR=np.maximum(H-L, np.maximum(np.abs(H-prevC), np.abs(L-prevC)))
atr=np.full(len(TR), np.nan)
if len(TR)>=N_ATR:
    atr[N_ATR-1]=TR[:N_ATR].mean()
    for i in range(N_ATR, len(TR)):
        atr[i]=(atr[i-1]*(N_ATR-1)+TR[i])/N_ATR
# atr[j] = ATR as of close of bar j (entry bar). No look-ahead: entry executes at bar j close.

# ---- Re-run taken-trade loop, capture (sleeve,dir,entry_utc,sig,j,atr_j) ----
VL=bt.VWAP_LONG; VS=bt.VWAP_SHORT; TC=bt.TLB_CFG
vlong=bt.VwapState(side="LONG",entry_band_k=VL["entry_band_k"],n_bars_outside=VL["n_bars_outside"],swing_lookback=VL["swing_lookback"])
vshort=bt.VwapState(side="SHORT",entry_band_k=VS["entry_band_k"],n_bars_outside=VS["n_bars_outside"],swing_lookback=VS["swing_lookback"])
tlb=bt.TLBLive(k=TC["pivot_k"], r_multiple=TC["r_multiple"])
busy={"VWAP_LONG_R3":None,"TLB_LONG":None}
entries=[]  # list of dict
for j,(bts,bo,bh,bl,bc,bv) in enumerate(bars5):
    et_t=bts.tz_convert(ET).time(); et_date=bts.tz_convert(ET).date()
    reg=regime_by_date.get(et_date); active=reg.active_sleeves if reg else []
    s_long=vlong.on_bar(bts.to_pydatetime(),bh,bl,bc,bv,anchor_hour_utc=VL["anchor_hour_utc"])
    s_short=vshort.on_bar(bts.to_pydatetime(),bh,bl,bc,bv,anchor_hour_utc=VS["anchor_hour_utc"])
    s_tlb=tlb.add_bar(bh,bl,bc)
    entry_utc=bts+pd.Timedelta(minutes=5); holiday=bt.no_trade(et_date)
    if s_long:
        ok=("VWAP_LONG_R3" in active and not holiday and bt.is_in_window(et_t,VL["active_windows_et"])
            and (busy["VWAP_LONG_R3"] is None or entry_utc>=busy["VWAP_LONG_R3"]))
        if ok:
            tr=bt.sim_vwap(df,s_long,entry_utc)  # baseline exit to advance busy
            entries.append(dict(sleeve="VWAP_LONG_R3",j=j,entry_utc=entry_utc,sig=dict(s_long),atr=atr[j],base_exit=tr))
            busy["VWAP_LONG_R3"]=tr["exit_utc"]
    if s_tlb:
        premium=(s_tlb["entry_price"]-s_tlb["stop_price"])*bt.TLB_PT_VALUE
        gate=(reg is not None and reg.cell in ("BOTH_BULL","ZONE_B"))
        ok=(gate and not holiday and bt.is_in_window(et_t,TC["entry_windows_et"])
            and premium<=bt.TLB_MAX_PREMIUM_USD and (busy["TLB_LONG"] is None or entry_utc>=busy["TLB_LONG"]))
        if ok:
            tr=bt.sim_tlb(bars5,j,s_tlb,entry_utc)
            entries.append(dict(sleeve="TLB_LONG",j=j,entry_utc=entry_utc,sig=dict(s_tlb),atr=atr[j],base_exit=tr))
            busy["TLB_LONG"]=tr["exit_utc"]

print(f"captured entries: VWAP={sum(1 for e in entries if e['sleeve']=='VWAP_LONG_R3')}  TLB={sum(1 for e in entries if e['sleeve']=='TLB_LONG')}")
import pickle
pickle.dump(dict(entries=entries), open("/root/v9/bt/_atr_entries.pkl","wb"))
# also stash df and bars5 refs won't pickle well; save needed arrays
pickle.dump(dict(bars5=bars5), open("/root/v9/bt/_atr_bars5.pkl","wb"))
df.to_pickle("/root/v9/bt/_atr_df.pkl")
print("saved pickles")
