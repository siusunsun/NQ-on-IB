"""_combo_gen_tlbhyb: regenerate hybrid-stop TLB_LONG per-trade series (hyb40) -> CSV.
Read-only; reuses _tlbhyb_run harness logic."""
import sys, importlib.util
import numpy as np, pandas as pd
sys.path.insert(0, "/root/v9/bt")
spec = importlib.util.spec_from_file_location("thr", "/root/v9/bt/_tlbhyb_run.py")
# _tlbhyb_run runs at import (prints tables) -> execute then grab run_tlb
mod = importlib.util.module_from_spec(spec); sys.modules["thr"]=mod
spec.loader.exec_module(mod)
trades = mod.run_tlb(hybrid_stop_pts=40.0, hybrid_lookback=20)
rows=[]
for t in trades:
    rows.append(dict(entry_utc=t["entry_utc"], pnl_points=t["pnl_points"],
                     pnl_usd=t["pnl_usd"], R=t["R"], modified=t["hybrid_modified"]))
d=pd.DataFrame(rows)
d.to_csv("/root/v9/bt/_combo_tlbhyb_trades.csv", index=False)
print("WROTE _combo_tlbhyb_trades.csv n=%d net_usd_1MNQ=%.0f range %s -> %s"%(
    len(d), d.pnl_usd.sum(), d.entry_utc.min(), d.entry_utc.max()))
