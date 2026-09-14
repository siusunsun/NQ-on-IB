"""_ra_common.py - shared loaders + fast simulators for the ROBUSTNESS AUDIT (READ-ONLY).
Reuses _hist_bt_nq.py (full-history harness). Creates nothing outside /root/v9/bt/_ra_*.
"""
import sys, importlib.util, warnings, math
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, pytz
ET = pytz.timezone("America/New_York")
sys.path.insert(0, "/root/v9/bt"); sys.path.insert(0, "/root/v9")

def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p); m = importlib.util.module_from_spec(s)
    sys.modules[n] = m; s.loader.exec_module(m); return m

bt  = _load("bt_nq", "/root/v9/bt/_hist_bt_nq.py")
vsl = _load("vsl", "/root/v9/v9_strategy_lib.py")

MNQ = 2.0
COST_RT = 2.04          # $/round-turn/MNQ: 1.04 comm + 1 tick slip per side
VWAP_MAX_STOP_PTS = 200.0

_CACHE = {}

def load_env():
    if "df" in _CACHE:
        return _CACHE
    df = bt.load_all()
    daily, regime = bt.build_daily_and_regime(df)
    bars5 = bt.build_5min(df)
    idx = df.index
    etidx = idx.tz_convert(ET)
    e_min = np.array([t.hour * 60 + t.minute for t in etidx], dtype=np.int32)
    e_hour = np.array([t.hour for t in etidx], dtype=np.int32)
    _CACHE.update(dict(
        df=df, daily=daily, regime=regime, bars5=bars5,
        idx=idx, hi=df["high"].values.astype(float), lo=df["low"].values.astype(float),
        cl=df["close"].values.astype(float),
        e_min=e_min, e_hour=e_hour,
        flat_mask=((e_min >= 15 * 60 + 55) & (e_hour < 17)),
        et_date=np.array([t.date() for t in etidx]),
    ))
    return _CACHE


# ---------------- fast VWAP exit sim (identical semantics to bt.sim_vwap) -------------
def sim_vwap_fast(E, pos, direction, entry, stop, target, horizon=30000):
    hi = E["hi"]; lo = E["lo"]; cl = E["cl"]; idx = E["idx"]; fm = E["flat_mask"]
    n = len(hi); a = pos; b = min(n, pos + horizon)
    H = hi[a:b]; L = lo[a:b]; C = cl[a:b]; F = fm[a:b]
    if direction == "long":
        ms = L <= stop; mt = H >= target
    else:
        ms = H >= stop; mt = L <= target
    BIG = 10 ** 9
    i_s = int(np.argmax(ms)) if ms.any() else BIG
    i_t = int(np.argmax(mt)) if mt.any() else BIG
    i_f = int(np.argmax(F)) if F.any() else BIG
    if i_s <= i_t and i_s <= i_f and i_s < BIG:
        k = i_s; px = stop; why = "STOP"
    elif i_t <= i_f and i_t < BIG:
        k = i_t; px = target; why = "TARGET"
    elif i_f < BIG:
        k = i_f; px = float(C[i_f]); why = "GAP_FLATTEN"
    else:
        k = len(C) - 1; px = float(C[-1]); why = "DATA_END"
    sgn = 1.0 if direction == "long" else -1.0
    pts = sgn * (px - entry)
    return dict(exit_pos=a + k, exit_utc=idx[a + k], exit_px=px, exit_reason=why,
                pnl_points=pts, pnl_usd=pts * MNQ)


# ---------------- stats helpers ----------------
def pf(x):
    x = np.asarray(x, float)
    g = x[x > 0].sum(); l = -x[x < 0].sum()
    return (g / l) if l > 0 else float("inf")

def stat_block(pnl, cost=COST_RT, mult=1):
    """pnl = per-trade gross $ at 1 MNQ. Returns net stats at `mult` contracts."""
    x = np.asarray(pnl, float) * mult - cost * mult
    n = len(x)
    if n == 0:
        return dict(n=0, net=0.0, pf=0.0, wr=0.0, avg=0.0, sr=0.0)
    sd = x.std(ddof=1) if n > 1 else 0.0
    return dict(n=n, net=float(x.sum()), pf=float(pf(x)), wr=float((x > 0).mean()),
                avg=float(x.mean()), sr=float(x.mean() / sd) if sd > 0 else 0.0)

def maxdd(cum):
    cum = np.asarray(cum, float)
    peak = np.maximum.accumulate(cum)
    return float((cum - peak).min())

def dsr(sr_obs, n_trials, var_sr_trials, T, skew, kurt):
    """Bailey & Lopez de Prado deflated Sharpe. sr_obs per-trade."""
    from math import sqrt, log, exp
    from statistics import NormalDist
    Z = NormalDist().inv_cdf; Phi = NormalDist().cdf
    g = 0.5772156649
    if n_trials < 2 or var_sr_trials <= 0:
        sr0 = 0.0
    else:
        sr0 = sqrt(var_sr_trials) * ((1 - g) * Z(1 - 1.0 / n_trials) +
                                     g * Z(1 - 1.0 / (n_trials * exp(1))))
    denom = 1.0 - skew * sr_obs + (kurt - 1.0) / 4.0 * sr_obs ** 2
    if denom <= 0 or T < 2:
        return sr0, float("nan")
    return sr0, Phi((sr_obs - sr0) * sqrt(T - 1) / sqrt(denom))
