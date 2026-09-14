# -*- coding: utf-8 -*-
"""Handoff item 4b: overnight (Globex) direction as a SIZING input for the NQ sleeves.

Transfer of the HSI night-session sizing idea (passed there at the 98.5th pct).

RULE (fixed, no per-fold selection, per the handoff's method note):
    trade direction AGREES with the completed overnight move  -> size 1.5x
    trade direction is AGAINST it                             -> size 0.5x
Control: the inverse (0.5 / 1.5). If the idea is real, one should win and the other lose.

LOOK-AHEAD: on_ret = RTH_open / prior_16:00_close - 1, complete at 09:30 ET. Only trades
ENTERED at/after 09:30 ET on the same ET date are eligible; overnight-entered trades are
excluded entirely (they cannot know the number).

Costs scale with size ($5.40 per MNQ round-turn x size).
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np, pandas as pd

QTY = {"TLB_HYB40": 1, "VWAP_LONG_R3": 2, "VWAP_SHORT_R1": 1}
RT = 5.40
rng = np.random.default_rng(20260908)

ON = pd.read_csv("_nq_overnight.csv", parse_dates=["date"])
ON["d"] = ON["date"].dt.date
ONMAP = dict(zip(ON.d, ON.on_ret))

rows = []
for tag, qty in QTY.items():
    d = pd.read_csv(f"_hist_tr_{tag}.csv")
    c = [x for x in d.columns if "entry" in x.lower() and any(k in x.lower() for k in ("utc", "time", "date"))][0]
    t = pd.to_datetime(d[c], utc=True, errors="coerce")
    if t.isna().all():
        t = pd.to_datetime(d[c], errors="coerce").dt.tz_localize("UTC")
    e = t.dt.tz_convert("America/New_York")
    p = [x for x in d.columns if x.lower() in ("pnl_usd", "pnl_usd_1mnq", "net_usd", "usd")][0]
    dirc = d["direction"] if "direction" in d.columns else pd.Series(["long"] * len(d))
    rows.append(pd.DataFrame({"sleeve": tag, "qty": qty, "dt": e.dt.date,
                              "etmin": e.dt.hour * 60 + e.dt.minute,
                              "gross1": d[p].astype(float),
                              "islong": dirc.astype(str).str.lower().str.startswith("l")}))
T = pd.concat(rows).sort_values("dt").reset_index(drop=True)
T["on_ret"] = T.dt.map(ONMAP)

print(f"all sleeve trades           : {len(T)}")
E = T[(T.etmin >= 570) & T.on_ret.notna()].copy()      # RTH-entered only
print(f"eligible (entered >= 09:30 ET, overnight known): {len(E)}  "
      f"({100*len(E)/len(T):.0f}%)   {E.dt.min()} .. {E.dt.max()}")
for s, g in E.groupby("sleeve"):
    print(f"    {s:<15} {len(g):4d}")

E["agree"] = np.where(E.islong, E.on_ret > 0, E.on_ret < 0)


def series(mult_agree, mult_against):
    m = np.where(E.agree, mult_agree, mult_against)
    net = E.gross1 * E.qty * m - RT * E.qty * m
    return pd.Series(net.values, index=E.dt.values), m


def stats(s):
    g = s.groupby(level=0).sum().sort_index()
    x = g.values
    cu = np.cumsum(x)
    dd = (np.maximum.accumulate(cu) - cu).max()
    return x.sum(), (x.sum() / dd if dd > 0 else 0.0), dd


base, _ = series(1.0, 1.0)
bn, br, bdd = stats(base)
print(f"\n{'variant':<34}{'net':>10}{'ret/DD':>9}{'maxDD':>10}{'avg size':>10}")
print(f"{'BASELINE 1.0x':<34}${bn:>9,.0f}{br:>9.2f}${bdd:>9,.0f}{1.00:>10.2f}")
RES = {}
for lbl, a, g in (("V1  agree 1.5 / against 0.5", 1.5, 0.5),
                  ("V2  agree 0.5 / against 1.5 (ctrl)", 0.5, 1.5)):
    s, m = series(a, g)
    n, r, dd = stats(s)
    RES[lbl] = n
    print(f"{lbl:<34}${n:>9,.0f}{r:>9.2f}${dd:>9,.0f}{m.mean():>10.2f}   dNet ${n-bn:+,.0f}")

print(f"\nagreement rate: {100*E.agree.mean():.1f}%   "
      f"(agree n={E.agree.sum()}, against n={(~E.agree).sum()})")
print("\nraw edge by cohort (1x, net of cost):")
for k, g in E.groupby("agree"):
    v = (g.gross1 * g.qty - RT * g.qty)
    print(f"    {'AGREE ' if k else 'AGAINST'}  n={len(g):4d}  net ${v.sum():+8,.0f}  per-trade ${v.mean():+7.2f}")

print("\nNULL: shuffle on_ret across days, 200 reps (keeps trade set + size distribution)")
real = RES["V1  agree 1.5 / against 0.5"] - bn
null = []
days = ON.dropna(subset=["on_ret"]).on_ret.values
for _ in range(200):
    fake = dict(zip(sorted(set(E.dt)), rng.choice(days, size=len(set(E.dt)), replace=True)))
    ag = np.where(E.islong, E.dt.map(fake) > 0, E.dt.map(fake) < 0)
    m = np.where(ag, 1.5, 0.5)
    n = (E.gross1 * E.qty * m - RT * E.qty * m)
    null.append(pd.Series(n.values, index=E.dt.values).groupby(level=0).sum().sum() - bn)
null = np.array(null)
print(f"    real dNet ${real:+,.0f}   null mean ${null.mean():+,.0f}  p95 ${np.percentile(null,95):+,.0f}"
      f"   -> pct {100*(null<real).mean():.1f}  (HSI bar was 98.5)")

print("\nERA SPLIT (dNet of V1 vs baseline)")
half = sorted(set(E.dt))[len(set(E.dt)) // 2]
for lbl, sel in (("era1", E.dt <= half), ("era2", E.dt > half)):
    sub = E[sel]
    m = np.where(sub.agree, 1.5, 0.5)
    v1 = (sub.gross1 * sub.qty * m - RT * sub.qty * m).sum()
    b0 = (sub.gross1 * sub.qty - RT * sub.qty).sum()
    print(f"    {lbl} (n={len(sub):4d}) base ${b0:+8,.0f}  V1 ${v1:+8,.0f}  dNet ${v1-b0:+,.0f}")

print("\nPER-SLEEVE dNet (V1 vs baseline)")
for s, g in E.groupby("sleeve"):
    m = np.where(g.agree, 1.5, 0.5)
    v1 = (g.gross1 * g.qty * m - RT * g.qty * m).sum()
    b0 = (g.gross1 * g.qty - RT * g.qty).sum()
    print(f"    {s:<15} base ${b0:+8,.0f}  V1 ${v1:+8,.0f}  dNet ${v1-b0:+,.0f}")
