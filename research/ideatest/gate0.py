"""Gate 0: with no rule active the simulator must reproduce repro/out exactly."""
import time
import pandas as pd
import sim

t0 = time.perf_counter()
sim.load()
print(f"load {time.perf_counter()-t0:.1f}s", flush=True)
t0 = time.perf_counter()
trades, log = sim.run(sim.Rules())
print(f"run {time.perf_counter()-t0:.1f}s, {len(trades)} trades", flush=True)
fr = sim.trade_frame(trades)
iso = lambda m: pd.to_datetime(m, unit="m", utc=True).strftime("%Y-%m-%dT%H:%M:%SZ")
files = {"VWAP_LONG_R3": "VWAP_LONG_R3", "VWAP_SHORT_R1": "VWAP_SHORT_R1",
         "TLB_HYB40": "TLB_HYB40", "R3_RE": "R3_RE_mw48"}
ok = True
for sl, fn in files.items():
    ref = pd.read_csv(sim.REPRO / "out" / f"trades_{fn}.csv").sort_values(["entry_time_utc", "exit_time_utc"]).reset_index(drop=True)
    mine = fr[fr.sleeve == sl].copy()
    mine["entry_time_utc"] = iso(mine.entry_time.values)
    mine["exit_time_utc"] = iso(mine.exit_time.values)
    mine = mine.sort_values(["entry_time_utc", "exit_time_utc"]).reset_index(drop=True)
    same = (len(ref) == len(mine)
            and (ref.entry_time_utc.values == mine.entry_time_utc.values).all()
            and (ref.exit_time_utc.values == mine.exit_time_utc.values).all()
            and (abs(ref.entry_px.values - mine.entry.values) < 1e-9).all()
            and (abs(ref.exit_px.values - mine.exit.values) < 1e-9).all()
            and (ref.exit_reason.values == mine.reason.values).all())
    ok &= bool(same)
    print(f"{sl:14s} ref {len(ref):4d} mine {len(mine):4d} identical={same}")
print("GATE 0", "PASS" if ok else "FAIL")
