"""ib_replay_0802.py -- fetch the Aug-2 Sunday session from IB (the feed the live bot
trades on; Polygon has not yet ingested it) and drive the EXACT live VwapState for
VWAP_LONG_R3. Read-only: one reqHistoricalData on an unused clientId, writes only under
/root/v9/bt/. No live code modified.
"""
from __future__ import annotations
import sys, importlib.util
from pathlib import Path
from datetime import datetime, timezone
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

VWAP_LONG = dict(entry_band_k=2.0, n_bars_outside=5, swing_lookback=20,
                 r_multiple=3.0, anchor_hour_utc=0,
                 active_windows_et=[(0, 360), (570, 720), (720, 870), (1140, 1440)])

sys.path.insert(0, "/root/v9")
from ib_async import IB, Future
from contract_roll import pick_front_contract


def fetch_ib():
    ib = IB()
    ib.connect("127.0.0.1", 7497, clientId=96, timeout=20)
    try:
        base = Future(symbol="NQ", exchange="CME", currency="USD")
        details = ib.reqContractDetails(base)
        ch = pick_front_contract(details, 7, datetime.now().strftime("%Y%m%d"))
        con = ch.contract
        print(f"resolved contract: {con.localSymbol} expiry={con.lastTradeDateOrContractMonth}")
        # end just after the friend's fill; 4 days back gives continuous history for the
        # 20-bar swing-low carryover + session VWAP anchor.
        end_str = "20260803-01:30:00"
        bars = ib.reqHistoricalData(con, endDateTime=end_str, durationStr="4 D",
                                    barSizeSetting="1 min", whatToShow="TRADES",
                                    useRTH=False, formatDate=2)
        return bars
    finally:
        ib.disconnect()


def bars_to_df(bars):
    recs = []
    for b in bars:
        d = b.date
        if hasattr(d, "astimezone"):
            ts = pd.Timestamp(d).tz_convert("UTC") if pd.Timestamp(d).tzinfo else pd.Timestamp(d, tz="UTC")
        else:
            ts = pd.Timestamp(int(d), unit="s", tz="UTC")
        recs.append((ts, float(b.open), float(b.high), float(b.low), float(b.close), float(b.volume)))
    df = pd.DataFrame(recs, columns=["time", "open", "high", "low", "close", "volume"])
    df = df.set_index("time").sort_index()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


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
    if vs.cumv <= 0:
        return (float("nan"),) * 3
    vwap = vs.cumvp / vs.cumv
    sd = float(np.sqrt(max(vs.cumsq / vs.cumv - vwap ** 2, 0)))
    return vwap, sd, vwap - vs.entry_band_k * sd


def main():
    bars = fetch_ib()
    df = bars_to_df(bars)
    df.to_csv("/root/v9/bt/NQ_ib_aug2_1min.csv")
    print(f"IB bars: {len(df)}; range {df.index.min()} -> {df.index.max()}")
    sun = df.loc["2026-08-02 22:00":"2026-08-03 01:30"]
    print(f"Sunday-session bars 2026-08-02 22:00 -> 2026-08-03 01:30 UTC: {len(sun)}"
          + (f" (first {sun.index.min()}, last {sun.index.max()})" if len(sun) else " <-- EMPTY"))

    bars5 = build_5min(df)
    print(f"5-min bars: {len(bars5)}\n")

    vlong = VwapState(side="LONG", entry_band_k=VWAP_LONG["entry_band_k"],
                      n_bars_outside=VWAP_LONG["n_bars_outside"],
                      swing_lookback=VWAP_LONG["swing_lookback"])

    W0 = pd.Timestamp("2026-08-02 00:00", tz="UTC")
    W1 = pd.Timestamp("2026-08-03 01:00", tz="UTC")
    P0 = pd.Timestamp("2026-08-02 22:55", tz="UTC")
    P1 = pd.Timestamp("2026-08-02 23:45", tz="UTC")

    print("=== 5-min VWAP-band development (Sunday reopen into the signal) ===")
    print(f"{'bar_close_utc':<26}{'o/h/l/c':<36}{'vwap':>10}{'lower':>10}{'streak':>8}  note")
    signals = []
    for (bts, bo, bh, bl, bc, bv) in bars5:
        sig = vlong.on_bar(bts.to_pydatetime(), bh, bl, bc, bv,
                           anchor_hour_utc=VWAP_LONG["anchor_hour_utc"])
        vwap, sd, lower = band_state(vlong)
        bar_close = bts + pd.Timedelta(minutes=5)
        if P0 <= bar_close <= P1:
            ohlc = f"{bo:.2f}/{bh:.2f}/{bl:.2f}/{bc:.2f}"
            note = "" if np.isnan(lower) else ("CLOSE<lower" if bc < lower else "inside")
            print(f"{str(bar_close):<26}{ohlc:<36}{vwap:>10.2f}{lower:>10.2f}"
                  f"{vlong.consecutive_outside:>8}  {note}")
        if sig and (W0 <= bar_close <= W1):
            in_win = is_in_window(bar_close.tz_convert(ET).time(), VWAP_LONG["active_windows_et"])
            signals.append((bar_close, sig, vwap, lower, in_win))

    print("\n=== LONG signals in 2026-08-02 00:00 -> 2026-08-03 01:00 UTC ===")
    if not signals:
        print("  *** NONE ***")
    for bar_close, sig, vwap, lower, in_win in signals:
        risk = sig["entry_price"] - sig["stop_price"]
        print(f"  signal-bar-close (UTC) : {bar_close}   (ET {bar_close.tz_convert(ET)})")
        print(f"  entry_utc (fill time)  : {bar_close}")
        print(f"  side/entry/stop/target : LONG  {sig['entry_price']:.2f} / "
              f"{sig['stop_price']:.2f} / {sig['target_price']:.2f}   (risk {risk:.2f} pts, R=3)")
        print(f"  exhaustion streak      : {sig['exhaustion']}")
        print(f"  vwap / lower @ signal  : {vwap:.2f} / {lower:.2f}")
        print(f"  in VWAP_LONG active ET window? {in_win}   (regime ZONE_A -> VWAP_LONG_R3 ACTIVE)")
        print()


if __name__ == "__main__":
    main()
