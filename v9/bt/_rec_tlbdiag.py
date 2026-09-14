import sys; sys.path.insert(0,"/root/v9/bt")
import pandas as pd, dash_lib as L
df,c,n = L.load_window()
bt = L.harness()["bt"]
bars5 = bt.build_5min(df)
cached=L.load_daily_closes(); fresh=L.daily_from_window(df)
daily=cached.copy()
for k,v in fresh.items(): daily.loc[k]=float(v)
daily=daily.sort_index(); daily=daily[~daily.index.duplicated(keep="last")]
regime=L.regimes_for(daily,set(d.date() for d in fresh.index))
import datetime
Hyb=L._make_hybrid_cls()
tlb=Hyb(k=bt.TLB_CFG["pivot_k"], r_multiple=bt.TLB_CFG["r_multiple"], hybrid_stop_pts=None)
for j,(bts,bo,bh,bl,bc,bv) in enumerate(bars5):
    s=tlb.add_bar(bh,bl,bc)
    et=bts.tz_convert(L.ET); d=et.date()
    if s and d in (datetime.date(2026,6,24),):
        reg=regime.get(d)
        prem=(s["entry_price"]-s["stop_price"])*bt.TLB_PT_VALUE
        print(bts, et.time(), "sig entry=%.2f stop=%.2f prem=%.0f"%(s["entry_price"],s["stop_price"],prem),
              "reg=", (reg.cell if reg else None), "inwin=", bt.is_in_window(et.time(), bt.TLB_CFG["entry_windows_et"]),
              "notrade=", bt.no_trade(d))
print("--- regimes around 6/24 ---")
for d in sorted(regime):
    if datetime.date(2026,6,20)<=d<=datetime.date(2026,6,30): print(d, regime[d].cell)
