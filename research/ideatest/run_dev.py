"""Run the 9 pre-registered variants + nulls; report the DEVELOPMENT period only (PREREG.md).

Holdout dates are masked out of every number this script prints or writes.
"""
import json
import math
import random
import time
from collections import defaultdict

import numpy as np
import pandas as pd

import sim
import sys
sys.stdout.reconfigure(encoding="utf-8")

DEV_END = "2025-08-26"
FOLD0 = pd.Timestamp("2021-08-27")
N_NULL = 200
N_BOOT = 5000
OUT = sim.HERE / "out"
OUT.mkdir(exist_ok=True)

CANDS = {
    "C1": ("Risk", dict(c1=True)),
    "C2": ("Profit", dict(c2=True)),
    "C3": ("Profit", dict(c3=True)),
    "C4": ("Profit", dict(c4=True)),
    "C5": ("Risk", dict(c5=True)),
    "C6": ("Risk", dict(c6=True)),
    "C8": ("Risk", dict(c8=True)),
    "C9": ("Profit", dict(c9=True)),
    "B1": ("Risk", dict(r3_qty=1)),
}


def dev_dates():
    d = sorted(k for k in sim.D.cells.keys() if k <= DEV_END)
    return pd.Index(d)


def series(trades, days, mult):
    s = sim.daily_pnl(trades, mult)
    extra = [x for x in s.index if x > DEV_END]
    s = s.drop(extra)
    missing = set(s.index) - set(days)
    assert not missing, f"exit dates outside calendar: {sorted(missing)[:5]}"
    return s.reindex(days, fill_value=0.0).to_numpy()


def maxdd(x):
    eq = np.cumsum(x)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    return float(np.max(peak - eq)) if len(x) else 0.0


def folds(days):
    idx = pd.to_datetime(days)
    out = []
    for k in range(8):
        a, b = FOLD0 + pd.DateOffset(months=6 * k), FOLD0 + pd.DateOffset(months=6 * (k + 1))
        out.append(np.where((idx >= a) & (idx < b))[0])
    return out


def stationary_idx(n, reps, mean_block, rng):
    p = 1.0 / mean_block
    idx = np.empty((reps, n), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, reps)
    for t in range(1, n):
        new = rng.random(reps) < p
        idx[:, t] = np.where(new, rng.integers(0, n, reps), (idx[:, t - 1] + 1) % n)
    return idx


def dd_rows(m):
    eq = np.cumsum(m, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.zeros((m.shape[0], 1)), eq], axis=1), axis=1)[:, 1:]
    return (peak - eq).max(axis=1)


def evaluate(kind, ctrl, cand, fold_ix, boot_idx):
    """ctrl/cand: dict mult -> daily net array (dev days)."""
    r = {}
    for mlt in (1, 2, 3):
        r[f"net_ctrl_{mlt}x"] = float(ctrl[mlt].sum())
        r[f"net_cand_{mlt}x"] = float(cand[mlt].sum())
        r[f"delta_{mlt}x"] = float(cand[mlt].sum() - ctrl[mlt].sum())
        r[f"dd_ctrl_{mlt}x"] = maxdd(ctrl[mlt])
        r[f"dd_cand_{mlt}x"] = maxdd(cand[mlt])
    d1 = cand[1] - ctrl[1]
    r["fold_delta"] = [float(d1[f].sum()) for f in fold_ix]
    r["fold_dd_ctrl"] = [maxdd(ctrl[1][f]) for f in fold_ix]
    r["fold_dd_cand"] = [maxdd(cand[1][f]) for f in fold_ix]
    top = np.argsort(-d1)[:5]
    top = top[d1[top] > 0]
    keep = np.ones(len(d1), bool)
    keep[top] = False
    r["drop5_dates_n"] = int(len(top))
    r["drop5_delta_1x"] = float(d1[keep].sum())
    for mlt in (1, 2):
        r[f"drop5_net_ctrl_{mlt}x"] = float(ctrl[mlt][keep].sum())
        r[f"drop5_net_cand_{mlt}x"] = float(cand[mlt][keep].sum())
        r[f"drop5_dd_ctrl_{mlt}x"] = maxdd(ctrl[mlt][keep])
        r[f"drop5_dd_cand_{mlt}x"] = maxdd(cand[mlt][keep])
    bc, bk = ctrl[1][boot_idx], cand[1][boot_idx]
    if kind == "Profit":
        r["boot_p"] = float(np.mean((bk - bc).mean(axis=1) <= 0))
    else:
        r["boot_p"] = float(np.mean(dd_rows(bk) >= dd_rows(bc)))
    nd = cand[1].sum() / max(maxdd(cand[1]), 1e-9)
    r["ret_dd_cand"] = float(nd)
    r["ret_dd_ctrl"] = float(ctrl[1].sum() / max(maxdd(ctrl[1]), 1e-9))
    return r


def criteria(kind, r):
    c = {}
    if kind == "Profit":
        c["P1 delta>0 at 1x,2x"] = r["delta_1x"] > 0 and r["delta_2x"] > 0
        c["P2 >=5/8 folds positive"] = sum(x > 0 for x in r["fold_delta"]) >= 5
        c["P3 drop-top-5 still >0"] = r["drop5_delta_1x"] > 0
        c["P5 null >=90th pct"] = r.get("null_pct", 0) >= 90
        c["P6 3x not negative"] = r["delta_3x"] >= 0
    else:
        c["R1 DD lower 1x,2x"] = r["dd_cand_1x"] < r["dd_ctrl_1x"] and r["dd_cand_2x"] < r["dd_ctrl_2x"]
        c["R2 net>=95% 1x,2x"] = all(
            (r[f"net_cand_{m}x"] >= 0.95 * r[f"net_ctrl_{m}x"]) if r[f"net_ctrl_{m}x"] > 0
            else r[f"net_cand_{m}x"] >= r[f"net_ctrl_{m}x"] for m in (1, 2))
        c["R3 fold DD lower >=5/8"] = sum(a < b for a, b in zip(r["fold_dd_cand"], r["fold_dd_ctrl"])) >= 5
        c["R4 drop-top-5 R1,R2 hold"] = all(
            r[f"drop5_dd_cand_{m}x"] < r[f"drop5_dd_ctrl_{m}x"]
            and (r[f"drop5_net_cand_{m}x"] >= 0.95 * r[f"drop5_net_ctrl_{m}x"]
                 if r[f"drop5_net_ctrl_{m}x"] > 0 else r[f"drop5_net_cand_{m}x"] >= r[f"drop5_net_ctrl_{m}x"])
            for m in (1, 2))
        if "null_pct" in r:
            c["R6 null >=90th pct"] = r["null_pct"] >= 90
        if "beats_B1" in r:
            c["R6b beats B1 ret/DD"] = r["beats_B1"]
        c["P6 3x DD still lower"] = r["dd_cand_3x"] < r["dd_ctrl_3x"]
    return c


def holm(pvals, alpha=0.10):
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    adj, running = {}, 0.0
    for rank, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - rank) * p))
        adj[k] = running
    return adj, {k: adj[k] <= alpha for k in adj}


def is_dev(minute_utc):
    return sim.et_date(minute_utc) <= DEV_END


def build_pools(points):
    pools = defaultdict(list)
    for kind, sleeve, key, dec, age, clock, losing, qty in points:
        if not losing:
            continue
        per = "dev" if is_dev(dec) else "hold"
        if kind == "m1":
            if qty == 2:
                pools[("m1", sleeve, age, per)].append((key, dec))
        else:
            pools[("age", sleeve, age, per)].append((key, dec))
            pools[("clock", sleeve, clock, per)].append((key, dec))
    return pools


def draw_null(acts, pools, mode, rng):
    chosen, used = {}, set()
    for rule, sleeve, key, dec, age, clock in acts:
        per = "dev" if is_dev(dec) else "hold"
        base = clock if mode == "clock" else age
        pick = None
        for w in range(0, 6):
            cands = []
            for a in ({base} if w == 0 else {base - w, base + w}):
                cands += [x for x in pools.get((mode, sleeve, a, per), []) if x[0] not in used]
            if cands:
                pick = rng.choice(cands)
                break
        if pick is not None:
            used.add(pick[0])
            chosen[pick[0]] = pick[1]
    return chosen


def main():
    t0 = time.perf_counter()
    sim.load()
    days = dev_dates()
    fold_ix = folds(days)
    rng_np = np.random.default_rng(20260914)
    boot_idx = stationary_idx(len(days), N_BOOT, 10, rng_np)
    ctrl_trades, ctrl_log = sim.run(sim.Rules(record=True))
    ctrl = {m: series(ctrl_trades, days, m) for m in (1, 2, 3)}
    ctrl_fr = sim.trade_frame(ctrl_trades)
    pools = build_pools(ctrl_log.points)
    print(f"control: dev net 1x ${ctrl[1].sum():,.0f}  DD ${maxdd(ctrl[1]):,.0f}  "
          f"({time.perf_counter()-t0:.0f}s)", flush=True)

    results, pvals = {}, {}
    for cid, (kind, kw) in CANDS.items():
        trades, log = sim.run(sim.Rules(**kw))
        cand = {m: series(trades, days, m) for m in (1, 2, 3)}
        r = evaluate(kind, ctrl, cand, fold_ix, boot_idx)
        fr = sim.trade_frame(trades)
        fr.to_csv(OUT / f"trades_{cid}.csv", index=False)
        acts = [a for a in log.acts if a[0] != "rand"]
        r["activations_dev"] = sum(is_dev(a[3]) for a in acts)
        r["activations_by_sleeve_dev"] = dict(pd.Series([a[1] for a in acts if is_dev(a[3])]).value_counts().astype(int)) if acts else {}
        r["skips_dev"] = sum(1 for s in log.skips if is_dev(int(sim.D.b5.t[s[2][1]] + 5)))
        # decomposition (dev, 1x, gross of cost differences handled via net)
        r.update(decompose(ctrl_fr, fr, kind))
        # nulls
        null_vals = []
        rng = random.Random(1000 + list(CANDS).index(cid))
        if cid in ("C1", "C2", "C3", "C4", "C6", "C8"):
            mode = "clock" if cid == "C8" else ("m1" if cid == "C6" else "age")
            for _ in range(N_NULL):
                chosen = draw_null(acts, pools, mode, rng)
                rr = sim.Rules(rand_cut=chosen) if cid == "C6" else sim.Rules(rand_exit=chosen)
                nt, _ = sim.run(rr)
                ns = series(nt, days, 1)
                null_vals.append(ns.sum() - ctrl[1].sum() if kind == "Profit"
                                 else ns.sum() / max(maxdd(ns), 1e-9))
        elif cid == "C9":
            skipped = [s for s in log.skips if s[0] == "c9"]
            by = defaultdict(lambda: [0, 0])
            for _, sl, key in skipped:
                by[sl][0 if is_dev(int(sim.D.b5.t[key[1]] + 5)) else 1] += 1
            taken = defaultdict(lambda: ([], []))
            for tr in ctrl_trades:
                if tr.sleeve in by:
                    taken[tr.sleeve][0 if is_dev(tr.entry_time) else 1].append(tr.key)
            for _ in range(N_NULL):
                skip = set()
                for sl, (nd, nh) in by.items():
                    skip |= set(rng.sample(taken[sl][0], min(nd, len(taken[sl][0]))))
                    skip |= set(rng.sample(taken[sl][1], min(nh, len(taken[sl][1]))))
                nt, _ = sim.run(sim.Rules(rand_skip=skip))
                null_vals.append(series(nt, days, 1).sum() - ctrl[1].sum())
        if null_vals:
            stat = r["delta_1x"] if kind == "Profit" else r["ret_dd_cand"]
            r["null_pct"] = float(100 * np.mean(np.array(null_vals) < stat))
            r["null_median"] = float(np.median(null_vals))
            r["null_p90"] = float(np.percentile(null_vals, 90))
        results[cid] = r
        pvals[cid] = r["boot_p"]
        print(f"{cid} done ({time.perf_counter()-t0:.0f}s)", flush=True)

    for cid in ("C5", "C6"):
        results[cid]["beats_B1"] = results[cid]["ret_dd_cand"] > results["B1"]["ret_dd_cand"]
    adj, sig = holm(pvals, 0.10)
    for cid, (kind, _) in CANDS.items():
        r = results[cid]
        r["boot_p_holm"] = adj[cid]
        crit = criteria(kind, r)
        crit[("P4" if kind == "Profit" else "R5") + " Holm p<=0.10"] = sig[cid]
        r["criteria"] = crit
        r["DEV_PASS"] = all(crit.values())
        r["objective"] = kind
    (OUT / "dev_results.json").write_text(json.dumps(results, indent=1, default=float), encoding="utf-8")
    report(results, ctrl)
    print(f"total {time.perf_counter()-t0:.0f}s")


def decompose(ctrl_fr, fr, kind):
    """Dev-period P&L change split by cause (1x net)."""
    def net(df):
        return df.pts * sim.V - df.qty * sim.COST
    c = ctrl_fr[ctrl_fr.exit_time.map(is_dev)].set_index("key")
    k = fr[fr.exit_time.map(is_dev)].set_index("key")
    both = c.index.intersection(k.index)
    changed = [x for x in both if (k.loc[x, "legs"] != c.loc[x, "legs"] or k.loc[x, "reason"] != c.loc[x, "reason"]
                                   or abs(k.loc[x, "pts"] - c.loc[x, "pts"]) > 1e-9 or k.loc[x, "qty"] != c.loc[x, "qty"])]
    diff = (net(k.loc[changed]) - net(c.loc[changed])) if changed else pd.Series(dtype=float)
    lost = c.index.difference(k.index)
    new = k.index.difference(c.index)
    lost_re = [x for x in lost if x.startswith("R3_RE")]
    lost_other = [x for x in lost if not x.startswith("R3_RE")]
    return {
        "dec_changed_trades": len(changed),
        "dec_saved": float(diff[diff > 0].sum()) if len(diff) else 0.0,
        "dec_forgone": float(diff[diff < 0].sum()) if len(diff) else 0.0,
        "dec_lost_reentries_n": len(lost_re),
        "dec_lost_reentries_net": float(-net(c.loc[lost_re]).sum()) if lost_re else 0.0,
        "dec_lost_other_n": len(lost_other),
        "dec_lost_other_net": float(-net(c.loc[lost_other]).sum()) if lost_other else 0.0,
        "dec_new_trades_n": len(new),
        "dec_new_trades_net": float(net(k.loc[new]).sum()) if len(new) else 0.0,
    }


def report(results, ctrl):
    L = ["# Codex ideas — DEVELOPMENT stage results (2021-08-27 .. 2025-08-26)", "",
         "Holdout (2025-08-27 .. 2026-08-25) is sealed and not in any number below.", "",
         f"Control dev: net 1x ${ctrl[1].sum():,.0f}, 2x ${ctrl[2].sum():,.0f}, 3x ${ctrl[3].sum():,.0f}; "
         f"max DD 1x ${maxdd(ctrl[1]):,.0f}; ret/DD {ctrl[1].sum()/maxdd(ctrl[1]):.2f}", "",
         "| id | obj | acts | Δnet 1x | Δnet 2x | Δnet 3x | DD ctrl→cand 1x | folds + | drop5 Δ | boot p | Holm | null pct | DEV |",
         "|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---|"]
    for cid, r in results.items():
        fp = sum(x > 0 for x in r["fold_delta"]) if r["objective"] == "Profit" else \
            sum(a < b for a, b in zip(r["fold_dd_cand"], r["fold_dd_ctrl"]))
        L.append(f"| {cid} | {r['objective']} | {r['activations_dev'] or r.get('skips_dev', 0)} | "
                 f"{r['delta_1x']:+,.0f} | {r['delta_2x']:+,.0f} | {r['delta_3x']:+,.0f} | "
                 f"{r['dd_ctrl_1x']:,.0f}→{r['dd_cand_1x']:,.0f} | {fp}/8 | {r['drop5_delta_1x']:+,.0f} | "
                 f"{r['boot_p']:.3f} | {r['boot_p_holm']:.3f} | {r.get('null_pct', float('nan')):.0f} | "
                 f"{'PASS' if r['DEV_PASS'] else 'fail'} |")
    L += ["", "Folds + = folds with positive Δnet (Profit) or strictly lower fold DD (Risk).", ""]
    for cid, r in results.items():
        L.append(f"## {cid} ({r['objective']})")
        L.append("- criteria: " + "; ".join(f"{k} {'✓' if v else '✗'}" for k, v in r["criteria"].items()))
        L.append(f"- fold Δnet: {', '.join(f'{x:+.0f}' for x in r['fold_delta'])}")
        L.append(f"- fold DD ctrl/cand: {', '.join(f'{a:.0f}/{b:.0f}' for a, b in zip(r['fold_dd_ctrl'], r['fold_dd_cand']))}")
        L.append(f"- net cand 1x/2x/3x: {r['net_cand_1x']:,.0f} / {r['net_cand_2x']:,.0f} / {r['net_cand_3x']:,.0f}; "
                 f"DD cand 2x {r['dd_cand_2x']:,.0f} vs ctrl {r['dd_ctrl_2x']:,.0f}; ret/DD {r['ret_dd_cand']:.2f} vs {r['ret_dd_ctrl']:.2f}")
        L.append(f"- decomposition: {r['dec_changed_trades']} trades changed (saved {r['dec_saved']:+,.0f}, "
                 f"forgone {r['dec_forgone']:+,.0f}); lost R3 re-entries {r['dec_lost_reentries_n']} "
                 f"({r['dec_lost_reentries_net']:+,.0f}); other trades lost {r['dec_lost_other_n']} "
                 f"({r['dec_lost_other_net']:+,.0f}); new trades {r['dec_new_trades_n']} ({r['dec_new_trades_net']:+,.0f})")
        if "null_pct" in r:
            L.append(f"- null: median {r['null_median']:+,.2f}, p90 {r['null_p90']:+,.2f}, candidate pct {r['null_pct']:.0f}")
        L.append("")
    (OUT / "DEV_REPORT.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:20]))


if __name__ == "__main__":
    main()
