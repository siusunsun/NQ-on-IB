"""C7 development-stage evaluation (PREREG addendum). Dev dates only."""
import json, random, sys
import numpy as np, pandas as pd
import sim, run_dev as rd
sys.stdout.reconfigure(encoding="utf-8")
sim.load()
ev = pd.read_csv(sim.HERE / "events" / "events.csv")
ts = pd.to_datetime(ev.et_date + " " + ev.et_time).dt.tz_localize("America/New_York")
EVM = ((ts.dt.tz_convert("UTC") - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(minutes=1)).astype(int).to_numpy()
ev["m"] = EVM
ev = ev[ev.et_date >= "2021-08-27"]
days = rd.dev_dates(); fold_ix = rd.folds(days)
boot = rd.stationary_idx(len(days), rd.N_BOOT, 10, np.random.default_rng(20260914))
ctrl_t, _ = sim.run(sim.Rules())
ctrl = {m: rd.series(ctrl_t, days, m) for m in (1, 2, 3)}
tr, log = sim.run(sim.Rules(c7_events=tuple(ev.m)))
cand = {m: rd.series(tr, days, m) for m in (1, 2, 3)}
r = rd.evaluate("Risk", ctrl, cand, fold_ix, boot)
r["activations_dev"] = sum(rd.is_dev(a[3]) for a in log.acts)
r.update(rd.decompose(sim.trade_frame(ctrl_t), sim.trade_frame(tr), "Risk"))
# pseudo-event null: same type/clock, random non-event weekday of the same period
hol = sim.D.holidays
wk = [d for d in sim.D.cells if pd.Timestamp(d).dayofweek < 5 and d not in hol and d not in set(ev.et_date)]
pool = {"dev": [d for d in wk if d <= rd.DEV_END], "hold": [d for d in wk if d > rd.DEV_END]}
rng = random.Random(7007)
null = []
for _ in range(rd.N_NULL):
    pseudo = []
    for per, g in ev.groupby(ev.et_date > rd.DEV_END):
        dates = rng.sample(pool["hold" if per else "dev"], len(g))
        for d, tm in zip(dates, g.et_time):
            t = pd.Timestamp(f"{d} {tm}", tz="America/New_York").tz_convert("UTC")
            pseudo.append(int((t - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(minutes=1)))
    ns = rd.series(sim.run(sim.Rules(c7_events=tuple(pseudo)))[0], days, 1)
    null.append(ns.sum() / max(rd.maxdd(ns), 1e-9))
null = np.array(null)
r["null_pct"] = float(100 * np.mean(null < r["ret_dd_cand"]))
r["null_p_plus1"] = float((1 + np.sum(null >= r["ret_dd_cand"])) / (1 + len(null)))
prev = json.loads((rd.OUT / "dev_results.json").read_text(encoding="utf-8"))
pv = {k: v["boot_p"] for k, v in prev.items()}; pv["C7"] = r["boot_p"]
adj, sig = rd.holm(pv, 0.10)
r["boot_p_holm10"] = adj["C7"]
crit = rd.criteria("Risk", r); crit["R5 Holm(10) p<=0.10"] = sig["C7"]
r["criteria"] = crit; r["DEV_PASS"] = all(crit.values())
r["holm10_all"] = adj
(rd.OUT / "dev_results_C7.json").write_text(json.dumps(r, indent=1, default=float), encoding="utf-8")
print(f"C7 dev: acts {r['activations_dev']}  Δnet 1x/2x/3x {r['delta_1x']:+.0f}/{r['delta_2x']:+.0f}/{r['delta_3x']:+.0f}  "
      f"DD {r['dd_ctrl_1x']:.0f}->{r['dd_cand_1x']:.0f}  ret/DD {r['ret_dd_ctrl']:.2f}->{r['ret_dd_cand']:.2f}")
print(f"  fold DD ctrl/cand: {[f'{a:.0f}/{b:.0f}' for a,b in zip(r['fold_dd_ctrl'], r['fold_dd_cand'])]}")
print(f"  boot p {r['boot_p']:.3f}  Holm(10) {r['boot_p_holm10']:.3f}  null pct {r['null_pct']:.1f}  null p+1 {r['null_p_plus1']:.3f}")
print(f"  decomposition: saved {r['dec_saved']:+.0f} forgone {r['dec_forgone']:+.0f} lost re-entries {r['dec_lost_reentries_n']} ({r['dec_lost_reentries_net']:+.0f})")
print("  criteria:", {k: bool(v) for k, v in crit.items()}, "DEV_PASS", r["DEV_PASS"])
print("  Holm over 10 (all):", {k: round(v, 3) for k, v in adj.items()})
