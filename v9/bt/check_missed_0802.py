"""check_missed_0802.py -- did OUR V9 VWAP_LONG_R3 fire a LONG around 2026-08-02 23:25 UTC?

Context: live V9 bot was DOWN during its daily restart gap (23:15:00 -> 23:30:02 UTC) and
missed a signal the friend's identical-strategy bot took (BUY NQ @ 28593.00 @ 23:25 UTC).

The live bot's seam files (built by tools/v9_pull_latest_data.py) DELIBERATELY cap at
today-midnight-ET, so they NEVER contain the live (still-forming) session. Live signals run
off the realtime 5s->5min IB subscription and are not persisted anywhere. So we cannot replay
from the existing harness seam files (their newest bar is 2026-07-31 20:59 UTC).

To confirm independently we pull the Aug-2 session 1-min bars straight from Polygon (the SAME
provider + SAME front ticker NQU6 the live bot uses), aggregate to 5-min with the live
FiveMinAggregator, and drive the EXACT live VwapState for VWAP_LONG_R3 with the live params.
No signal logic is reimplemented. Read-only; writes nothing under live paths.
"""
from __future__ import annotations
import sys, os, json, time
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta, timezone
import urllib.request, urllib.error
import numpy as np
import pandas as pd
import pytz

ET = pytz.timezone("America/New_York")


def _load(mod_name, path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    m = importlib.util.module_from_spec(spec); sys.modules[mod_name] = m
    spec.loader.exec_module(m); return m


vsl = _load("v9_strategy_lib_live", "/root/v9/v9_strategy_lib.py")
vex = _load("v9_execution_a1", "/root/nq_options/v9_execution.py")
VwapState = vsl.VwapState
is_in_window = vsl.is_in_window
FiveMinAggregator = vex.FiveMinAggregator

# ---- live params (v9_config_live_user.VWAP_LONG) ----
VWAP_LONG = dict(bar_min=5, entry_band_k=2.0, n_bars_outside=5, swing_lookback=20,
                 r_multiple=3.0, anchor_hour_utc=0,
                 active_windows_et=[(0, 360), (570, 720), (720, 870), (1140, 1440)])
TICKER = "NQU6"                       # live "Resolved DATA contract: NQU6"
KEYF = Path("/root/v9/.polygon_key")

# Pull a wide continuous window so the 20-bar swing-low history + session VWAP anchor
# are populated exactly as a continuous live feed would have them.
START_UTC = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)
END_UTC   = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)


def polygon_pull(ticker, start_utc, end_utc):
    key = KEYF.read_text().strip() if KEYF.exists() else os.environ.get("POLYGON_API_KEY")
    if not key:
        raise SystemExit("No Polygon key")
    start_ns = int(start_utc.timestamp() * 1_000_000_000)
    end_ns   = int(end_utc.timestamp() * 1_000_000_000)
    url = (f"https://api.polygon.io/futures/v1/aggs/{ticker}"
           f"?resolution=1minute&window_start.gte={start_ns}&window_start.lt={end_ns}"
           f"&order=asc&limit=50000&apiKey={key}")
    rows = []
    for _ in range(20):
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        rows.extend(data.get("results", []))
        nxt = data.get("next_url")
        if not nxt:
            break
        url = nxt + f"&apiKey={key}"; time.sleep(1)
    return rows


def to_df(rows):
    recs = []
    for r in rows:
        ts_ns = r.get("window_start") or r.get("t")
        if not ts_ns:
            continue
        ts = pd.Timestamp(int(ts_ns), unit="ns", tz="UTC")
        recs.append((ts, r.get("open", r.get("o")), r.get("high", r.get("h")),
                     r.get("low", r.get("l")), r.get("close", r.get("c")),
                     float(r.get("volume", r.get("v", 0)) or 0)))
    df = pd.DataFrame(recs, columns=["time", "open", "high", "low", "close", "volume"])
    df = df.dropna(subset=["time"]).set_index("time").sort_index()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})


def build_5min(df):
    agg = FiveMinAggregator(5); out = []
    idx = df.index
    o = df["open"].values; h = df["high"].values; l = df["low"].values
    c = df["close"].values; v = df["volume"].values
    for i in range(len(df)):
        done = agg.add(idx[i], o[i], h[i], l[i], c[i], v[i])
        if done is not None:
            out.append(done)
    return out


def band_state(vs):
    """Read current vwap / sd / lower from the live VwapState AFTER on_bar (no reimpl)."""
    if vs.cumv <= 0:
        return (float("nan"),) * 3
    vwap = vs.cumvp / vs.cumv
    sd = float(np.sqrt(max(vs.cumsq / vs.cumv - vwap ** 2, 0)))
    return vwap, sd, vwap - vs.entry_band_k * sd


def main():
    print(f"Fetching Polygon {TICKER} 1-min {START_UTC} -> {END_UTC} ...")
    rows = polygon_pull(TICKER, START_UTC, END_UTC)
    df = to_df(rows)
    print(f"  got {len(df)} unique 1-min bars; range {df.index.min()} -> {df.index.max()}")
    sun = df.loc["2026-08-02 22:00":"2026-08-03 01:00"]
    print(f"  bars in 2026-08-02 22:00 -> 2026-08-03 01:00 UTC: {len(sun)}"
          + (f" (first {sun.index.min()}, last {sun.index.max()})" if len(sun) else " <-- EMPTY"))

    bars5 = build_5min(df)
    print(f"  aggregated to {len(bars5)} 5-min bars\n")

    vlong = VwapState(side="LONG", entry_band_k=VWAP_LONG["entry_band_k"],
                      n_bars_outside=VWAP_LONG["n_bars_outside"],
                      swing_lookback=VWAP_LONG["swing_lookback"])

    W0 = pd.Timestamp("2026-08-02 00:00", tz="UTC")
    W1 = pd.Timestamp("2026-08-03 01:00", tz="UTC")
    P0 = pd.Timestamp("2026-08-02 22:55", tz="UTC")
    P1 = pd.Timestamp("2026-08-02 23:45", tz="UTC")

    print("=== 5-min VWAP-band development (Sunday reopen into the signal) ===")
    print(f"{'bar_close_utc':<26}{'o/h/l/c':<34}{'vwap':>10}{'lower':>10}{'streak':>8}  note")
    signals = []
    for (bts, bo, bh, bl, bc, bv) in bars5:
        sig = vlong.on_bar(bts.to_pydatetime(), bh, bl, bc, bv,
                           anchor_hour_utc=VWAP_LONG["anchor_hour_utc"])
        vwap, sd, lower = band_state(vlong)
        bar_close = bts + pd.Timedelta(minutes=5)
        entry_utc = bar_close
        if P0 <= bar_close <= P1:
            ohlc = f"{bo:.2f}/{bh:.2f}/{bl:.2f}/{bc:.2f}"
            note = ""
            if not np.isnan(lower):
                note = "CLOSE<lower" if bc < lower else "inside"
            print(f"{str(bar_close):<26}{ohlc:<34}{vwap:>10.2f}{lower:>10.2f}"
                  f"{vlong.consecutive_outside:>8}  {note}")
        if sig and (W0 <= entry_utc <= W1):
            in_win = is_in_window(bar_close.tz_convert(ET).time(), VWAP_LONG["active_windows_et"])
            signals.append((bar_close, entry_utc, sig, vwap, lower, in_win))

    print("\n=== LONG signals generated by VWAP_LONG_R3 in 2026-08-02 00:00 -> 2026-08-03 01:00 UTC ===")
    if not signals:
        print("  *** NONE ***")
    for bar_close, entry_utc, sig, vwap, lower, in_win in signals:
        risk = sig["entry_price"] - sig["stop_price"]
        print(f"  signal-bar-close (UTC) : {bar_close}   (ET {bar_close.tz_convert(ET)})")
        print(f"  entry_utc (fill time)  : {entry_utc}")
        print(f"  side/entry/stop/target : LONG  {sig['entry_price']:.2f} / "
              f"{sig['stop_price']:.2f} / {sig['target_price']:.2f}   (risk {risk:.2f} pts, R=3)")
        print(f"  exhaustion streak      : {sig['exhaustion']}")
        print(f"  vwap / lower @ signal  : {vwap:.2f} / {lower:.2f}")
        print(f"  in VWAP_LONG active ET window? {in_win}   (regime cell was ZONE_A -> VWAP_LONG_R3 ACTIVE)")
        print()


if __name__ == "__main__":
    main()
