# -*- coding: utf-8 -*-
"""Handoff item 4b, phase 1: build the OVERNIGHT (Globex) feature from the 1-min archive.

For each ET trading date D:
  prior_close = last 1-min close at or before 16:00 ET on the previous session
  rth_open    = first 1-min open at or after 09:30 ET on D
  on_ret      = rth_open / prior_close - 1        (KNOWN at 09:30 ET on D)

LOOK-AHEAD: on_ret is complete at 09:30 ET, so it may only be applied to trades entered
at/after 09:30 ET on the same date. Trades entered overnight are excluded downstream.

Chunked read -> small memory. Writes /root/v9/bt/_nq_overnight.csv
"""
import sys, glob, os, resource
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pandas as pd

DIRS = ["/root/tlbrc_paper/ref/data_full/NQ", "/root/v9/data_1m/NQ"]
ET = "America/New_York"
rows = {}

seen = set()
files = []
for d in DIRS:
    for p in sorted(glob.glob(os.path.join(d, "*.csv"))):
        rp = os.path.realpath(p)
        if rp in seen:
            continue
        seen.add(rp)
        files.append(rp)
print(f"{len(files)} archive files", flush=True)

for p in files:
    for ch in pd.read_csv(p, chunksize=300000):
        cols = {c.lower(): c for c in ch.columns}
        tc = cols.get("time") or cols.get("date") or list(ch.columns)[0]
        t = pd.to_datetime(ch[tc], utc=True, errors="coerce")
        ch = ch.assign(_t=t).dropna(subset=["_t"])
        if not len(ch):
            continue
        e = ch["_t"].dt.tz_convert(ET)
        ch = ch.assign(_d=e.dt.date, _m=e.dt.hour * 60 + e.dt.minute)
        # last close at/before 16:00 ET
        cl = ch[ch._m <= 960]
        if len(cl):
            g = cl.sort_values("_t").groupby("_d").tail(1)
            for d, c, tt in zip(g._d, g[cols.get("close", "close")], g._t):
                cur = rows.setdefault(d, {})
                if "clt" not in cur or tt > cur["clt"]:
                    cur["close"], cur["clt"] = float(c), tt
        # first open at/after 09:30 ET
        op = ch[ch._m >= 570]
        if len(op):
            g = op.sort_values("_t").groupby("_d").head(1)
            for d, o, tt in zip(g._d, g[cols.get("open", "open")], g._t):
                cur = rows.setdefault(d, {})
                if "opt" not in cur or tt < cur["opt"]:
                    cur["open"], cur["opt"] = float(o), tt

df = pd.DataFrame([{"date": d, "prior_close_src": v.get("close"), "rth_open": v.get("open")}
                   for d, v in sorted(rows.items())])
df = df.dropna(subset=["rth_open"]).reset_index(drop=True)
df["prior_close"] = df["prior_close_src"].shift(1)
df = df.dropna(subset=["prior_close"])
df["on_ret"] = df["rth_open"] / df["prior_close"] - 1.0
df[["date", "prior_close", "rth_open", "on_ret"]].to_csv("/root/v9/bt/_nq_overnight.csv", index=False)
print(f"wrote {len(df)} days  {df.date.min()} .. {df.date.max()}")
print(df.tail(3).to_string(index=False))
print(f"peakRSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024}MB")
