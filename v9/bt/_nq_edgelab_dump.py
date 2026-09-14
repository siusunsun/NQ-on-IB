# -*- coding: utf-8 -*-
"""Handoff item 4a, phase 1: build an EXIT-AGNOSTIC 5-min feature table for NQ.

Exit-agnostic = no stops, no targets. We score candidate ENTRY conditions purely by the
forward return that follows them, so entry quality is isolated from the exit rules.

Session VWAP / sigma use the EXACT live objects (v9_strategy_lib.update_session_vwap,
session anchored 00:00 UTC) so "distance from the band" means the same thing the deployed
VWAP_LONG_R3 sleeve means by it.

Forward returns are capped at the session boundary (NaN if the horizon crosses it) so no
candidate can bank a move that the sleeve's 15:55 ET flatten would never have reached.

Writes /root/v9/bt/_nq_edgelab.csv  (compact; phase 2 is light).
"""
import sys, importlib.util, resource
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np, pandas as pd

BT = "/root/v9/bt/"
sys.path.insert(0, BT)
sys.path.insert(0, "/root/v9")


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


bt = _load("bt_nq", BT + "bt_nq.py")
vsl = _load("vsl_el", "/root/v9/v9_strategy_lib.py")
usv = vsl.update_session_vwap
sess_key = vsl.session_for_utc_bar

C = _load("_ra_common", BT + "_ra_common.py")
df = C.load_1m() if hasattr(C, "load_1m") else None
if df is None:
    import glob, os
    parts = []
    seen = set()
    for d in ("/root/tlbrc_paper/ref/data_full/NQ", "/root/v9/data_1m/NQ"):
        for p in sorted(glob.glob(os.path.join(d, "*.csv"))):
            rp = os.path.realpath(p)
            if rp in seen:
                continue
            seen.add(rp)
            parts.append(pd.read_csv(rp))
    df = pd.concat(parts)
    tc = [c for c in df.columns if c.lower() in ("time", "date")][0]
    df[tc] = pd.to_datetime(df[tc], utc=True, errors="coerce")
    df = df.dropna(subset=[tc]).set_index(tc)
    df = df[~df.index.duplicated(keep="last")].sort_index()
print(f"1-min bars {len(df):,}  {df.index.min()} .. {df.index.max()}", flush=True)

b5 = bt.build_5min(df)
del df
n = len(b5)
print(f"5-min bars {n:,}", flush=True)

ts = [b[0] for b in b5]
o = np.array([b[1] for b in b5]); h = np.array([b[2] for b in b5])
l = np.array([b[3] for b in b5]); c = np.array([b[4] for b in b5])
v = np.array([b[5] for b in b5])

vwap = np.full(n, np.nan); sd = np.full(n, np.nan)
skey = np.empty(n, dtype=object)
cur = None; cumv = cumvp = cumsq = 0.0
for i in range(n):
    sk = sess_key(ts[i].to_pydatetime(), 0)
    skey[i] = sk
    if sk != cur:
        cur = sk; cumv = cumvp = cumsq = 0.0
    cumv, cumvp, cumsq, vw, s_ = usv(cumv, cumvp, cumsq, h[i], l[i], c[i], v[i])
    vwap[i] = vw; sd[i] = s_

lower = vwap - 2.0 * sd
upper = vwap + 2.0 * sd
zs = np.where(sd > 0, (c - vwap) / sd, np.nan)

# RSI(14) on 5-min closes (Wilder)
d1 = np.diff(c, prepend=c[0])
up = np.where(d1 > 0, d1, 0.0); dn = np.where(d1 < 0, -d1, 0.0)
au = np.full(n, np.nan); ad = np.full(n, np.nan)
au[13] = up[1:14].mean(); ad[13] = dn[1:14].mean()
for i in range(14, n):
    au[i] = (au[i - 1] * 13 + up[i]) / 14.0
    ad[i] = (ad[i - 1] * 13 + dn[i]) / 14.0
rsi = 100 - 100 / (1 + np.where(ad > 0, au / ad, np.inf))

# consecutive completed closes below the lower band, reset per session
nb = np.zeros(n, dtype=int)
for i in range(n):
    if i and skey[i] == skey[i - 1] and c[i] < lower[i]:
        nb[i] = nb[i - 1] + 1
    elif c[i] < lower[i]:
        nb[i] = 1

# forward returns in POINTS, NaN if the horizon leaves the session
out = {"ts": ts, "close": c, "vwap": vwap, "sd": sd, "z": zs, "rsi": rsi,
       "nbelow": nb, "sess": skey}
et = pd.DatetimeIndex(ts).tz_convert("America/New_York")
out["etmin"] = et.hour * 60 + et.minute
for M in (6, 12, 24, 48):
    f = np.full(n, np.nan)
    for i in range(n - M):
        if skey[i + M] == skey[i]:
            f[i] = c[i + M] - c[i]
    out[f"fwd{M}"] = f

T = pd.DataFrame(out)
T.to_csv(BT + "_nq_edgelab.csv", index=False)
print(f"wrote {len(T):,} rows -> {BT}_nq_edgelab.csv")
print(f"peakRSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024}MB")
