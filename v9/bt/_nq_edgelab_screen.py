# -*- coding: utf-8 -*-
"""Handoff item 4a, phase 2: exit-agnostic ENTRY screen for the NQ long side.

Question: is there a 5-min entry trigger with better forward returns than the one
VWAP_LONG_R3 deploys (5 consecutive closes below the -2 sigma band, then a close back
inside)? Scored on forward return alone -- no stop, no target -- so entry quality is
isolated from the exit rules.

HONESTY CONTROLS
  * ONE signal per session per candidate (first occurrence). Consecutive qualifying bars
    are autocorrelated; counting them all inflates n and fakes significance.
  * IS = up to 2023-12-31, OOS = 2024-01-01 onward. A candidate must win in BOTH.
  * Forward returns never cross the session boundary (built that way in phase 1).
  * No regime gate is applied -- this screens the TRIGGER, not the deployed sleeve. Any
    winner still has to survive the full harness afterwards.
  * Trial count is printed. With ~20 candidates the best one is expected to look good.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np, pandas as pd

BT = "/root/v9/bt/"
T = pd.read_csv(BT + "_nq_edgelab.csv", parse_dates=["ts"])
T["lower"] = T.vwap - 2.0 * T.sd
T["inside"] = T.close >= T.lower
T["nb_prev"] = T.groupby("sess")["nbelow"].shift(1).fillna(0)
IS_END = pd.Timestamp("2024-01-01", tz="UTC")
print(f"bars {len(T):,}   {T.ts.min()} .. {T.ts.max()}")

CAND = {}
# the deployed trigger, and the same shape at other N
for N in (2, 3, 4, 5, 6, 8):
    CAND[f"DEPLOYED-shape N={N} back-inside"] = (T.nb_prev >= N) & T.inside
# raw depth triggers (no re-entry confirmation)
for N in (3, 5, 8):
    CAND[f"{N} closes below band (no confirm)"] = T.nbelow >= N
# distance from VWAP in sigma
for k in (1.5, 2.0, 2.5, 3.0):
    CAND[f"z < -{k}"] = T.z < -k
# RSI extremes  (the FX winner)
for r in (15, 20, 25, 30):
    CAND[f"RSI(14) < {r}"] = T.rsi < r
# RSI extreme + back inside the band
for r in (20, 25, 30):
    CAND[f"RSI < {r} AND back inside"] = (T.rsi < r) & T.inside & (T.nb_prev >= 1)


def score(mask, horizon="fwd24"):
    d = T[mask & T[horizon].notna()]
    if not len(d):
        return None
    d = d.sort_values("ts").groupby("sess", as_index=False).head(1)   # one per session
    is_ = d[d.ts < IS_END][horizon]
    oos = d[d.ts >= IS_END][horizon]
    if len(is_) < 20 or len(oos) < 20:
        return None
    t = oos.mean() / (oos.std(ddof=1) / np.sqrt(len(oos))) if oos.std(ddof=1) > 0 else 0.0
    return dict(n=len(d), n_is=len(is_), n_oos=len(oos),
                is_mean=is_.mean(), oos_mean=oos.mean(),
                is_hit=100 * (is_ > 0).mean(), oos_hit=100 * (oos > 0).mean(), t=t)


for H in ("fwd12", "fwd24"):
    base = T[T[H].notna()]
    b_is = base[base.ts < IS_END][H].mean()
    b_oos = base[base.ts >= IS_END][H].mean()
    print(f"\n{'='*104}")
    print(f"HORIZON {H} ({int(H[3:])*5} min)   unconditional drift: IS {b_is:+.2f} pt   OOS {b_oos:+.2f} pt")
    print(f"  {'candidate':<38}{'n':>5}{'IS mean':>9}{'OOS mean':>10}{'IS hit':>8}{'OOS hit':>9}{'t(OOS)':>8}  both>drift?")
    res = []
    for name, m in CAND.items():
        s = score(m, H)
        if s is None:
            continue
        ok = (s["is_mean"] > b_is) and (s["oos_mean"] > b_oos)
        res.append((name, s, ok))
    for name, s, ok in sorted(res, key=lambda x: -x[1]["oos_mean"]):
        print(f"  {name:<38}{s['n']:>5}{s['is_mean']:>9.2f}{s['oos_mean']:>10.2f}"
              f"{s['is_hit']:>7.0f}%{s['oos_hit']:>8.0f}%{s['t']:>8.2f}   {'YES' if ok else '-'}")
    print(f"  trials scored: {len(res)}")

print(f"\n{'='*104}")
print("HEAD-TO-HEAD vs the DEPLOYED trigger (fwd24, one signal/session)")
dep = score(CAND["DEPLOYED-shape N=5 back-inside"], "fwd24")
print(f"  DEPLOYED (N=5 back-inside): n={dep['n']}  IS {dep['is_mean']:+.2f}  OOS {dep['oos_mean']:+.2f}  "
      f"OOS hit {dep['oos_hit']:.0f}%  t={dep['t']:.2f}")
better = [(n, s) for n, s, _ in
          [(n, score(m, "fwd24"), None) for n, m in CAND.items()]
          if s and s["oos_mean"] > dep["oos_mean"] and s["is_mean"] > dep["is_mean"]]
if better:
    print("  candidates beating it in BOTH halves:")
    for n, s in sorted(better, key=lambda x: -x[1]["oos_mean"]):
        print(f"    {n:<38} n={s['n']:<5} IS {s['is_mean']:+.2f}  OOS {s['oos_mean']:+.2f}  t={s['t']:.2f}")
else:
    print("  NONE beat it in both halves.")
