import sys, importlib.util
import numpy as np, pandas as pd
sys.path.insert(0,"/root/v9/bt")
spec=importlib.util.spec_from_file_location("bt_nq","/root/v9/bt/bt_nq.py")
bt=importlib.util.module_from_spec(spec); sys.modules["bt_nq"]=bt; spec.loader.exec_module(bt)
df=bt.load_all(); daily,regime=bt.build_daily_and_regime(df); bars5=bt.build_5min(df)
vt,_=bt.run_vwap_tlb(df,bars5,regime)
tlb=vt["TLB_LONG"]
usd=np.array([t["pnl_points"] for t in tlb])*2.0
gp=usd[usd>0].sum(); gl=-usd[usd<0].sum()
cum=np.cumsum(usd); mdd=(cum-np.maximum.accumulate(cum)).min()
print(f"HARNESS run_vwap_tlb TLB: n={len(tlb)} net=${usd.sum():,.0f} PF={gp/gl:.3f} win%={100*(usd>0).mean():.1f} maxDD=${mdd:,.0f}")
# trades on/before 2026-08-20 (scratch snapshot date)
cut=pd.Timestamp("2026-08-20",tz="UTC")
old=[t for t in tlb if t["entry_utc"]<cut]
uo=np.array([t["pnl_points"] for t in old])*2.0
go=uo[uo>0].sum(); lo=-uo[uo<0].sum(); cumo=np.cumsum(uo); mo=(cumo-np.maximum.accumulate(cumo)).min()
print(f"HARNESS TLB up to 2026-08-20: n={len(old)} net=${uo.sum():,.0f} PF={go/lo:.3f} win%={100*(uo>0).mean():.1f} maxDD=${mo:,.0f}")
