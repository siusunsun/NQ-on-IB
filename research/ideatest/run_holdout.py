"""Holdout stage (PREREG.md). Refuses to run until out/DEV_VERDICT.md exists."""
import json
import sys

import numpy as np
import pandas as pd

import sim
from run_dev import CANDS, OUT, maxdd

H0, H1 = "2025-08-27", "2026-08-25"

if not (OUT / "DEV_VERDICT.md").exists() or not (sim.HERE / "UNSEAL_HOLDOUT").exists():
    sys.exit("Holdout sealed: needs out/DEV_VERDICT.md AND the harness red team closed (UNSEAL_HOLDOUT).")


def series(trades, days, mult):
    s = sim.daily_pnl(trades, mult)
    s = s[(s.index >= H0) & (s.index <= H1)]
    return s.reindex(days, fill_value=0.0).to_numpy()


def main():
    sim.load()
    days = pd.Index(sorted(k for k in sim.D.cells.keys() if H0 <= k <= H1))
    dev = json.loads((OUT / "dev_results.json").read_text(encoding="utf-8"))
    ctrl_t, _ = sim.run(sim.Rules())
    ctrl = {m: series(ctrl_t, days, m) for m in (1, 2, 3)}
    idx = pd.to_datetime(days)
    folds = [np.where(idx < pd.Timestamp("2026-02-27"))[0], np.where(idx >= pd.Timestamp("2026-02-27"))[0]]
    L = ["# Codex ideas — HOLDOUT stage (2025-08-27 .. 2026-08-25)", "",
         "Opened only after DEV_VERDICT.md was written. Only DEV passers are eligible to deploy.", "",
         f"Control holdout: net 1x ${ctrl[1].sum():,.0f}, 2x ${ctrl[2].sum():,.0f}, 3x ${ctrl[3].sum():,.0f}; "
         f"max DD 1x ${maxdd(ctrl[1]):,.0f}", "",
         "| id | obj | DEV | Δnet 1x | Δnet 2x | Δnet 3x | DD ctrl→cand 1x | DD ctrl→cand 2x | fold Δ | holdout ok |",
         "|---|---|---|---:|---:|---:|---|---|---|---|"]
    res = {}
    for cid, (kind, kw) in CANDS.items():
        tr, _ = sim.run(sim.Rules(**kw))
        cand = {m: series(tr, days, m) for m in (1, 2, 3)}
        d = {m: cand[m].sum() - ctrl[m].sum() for m in (1, 2, 3)}
        dd = {m: (maxdd(ctrl[m]), maxdd(cand[m])) for m in (1, 2, 3)}
        fd = [float((cand[1] - ctrl[1])[f].sum()) for f in folds]
        if kind == "Profit":
            ok = d[1] > 0 and d[2] > 0
        else:
            ok = all(dd[m][1] < dd[m][0] and (cand[m].sum() >= 0.95 * ctrl[m].sum() if ctrl[m].sum() > 0
                                               else cand[m].sum() >= ctrl[m].sum()) for m in (1, 2))
        res[cid] = dict(delta={str(m): float(v) for m, v in d.items()}, dd={str(m): v for m, v in dd.items()},
                        fold_delta=fd, holdout_ok=bool(ok), dev_pass=dev[cid]["DEV_PASS"])
        L.append(f"| {cid} | {kind} | {'PASS' if dev[cid]['DEV_PASS'] else 'fail'} | {d[1]:+,.0f} | {d[2]:+,.0f} | "
                 f"{d[3]:+,.0f} | {dd[1][0]:,.0f}→{dd[1][1]:,.0f} | {dd[2][0]:,.0f}→{dd[2][1]:,.0f} | "
                 f"{', '.join(f'{x:+.0f}' for x in fd)} | {'yes' if ok else 'no'} |")
    (OUT / "holdout_results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    (OUT / "HOLDOUT_REPORT.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
