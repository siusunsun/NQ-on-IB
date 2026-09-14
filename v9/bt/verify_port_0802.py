"""verify_port_0802.py -- verify the 4 ported kristov18 fixes against the real
2026-08-02 incident bars. Read-only: imports the NOW-PATCHED live VwapState from
/root/v9/v9_strategy_lib.py and the live FiveMinAggregator from /root/v9/v9_execution.py,
drives them over the cached real-IB 1-min bars (Fri 07-31 .. Sun 08-02 23:30 UTC window).

Checks:
  1. GAP GUARD  : at the 23:25 UTC signal, stop must be ~28,545 (44.5pt), NOT 28,258.25 (331pt);
                  swing-low must come from the Sunday session, not Friday.
  2. BLACKOUT   : long state with post_halt_blackout_bars=24 must produce NO SIGNAL at 23:25 UTC.
  3. SEAM GUARD : feeding two bars with the SAME timestamp to the real on_5min must reject the 2nd.
"""
from __future__ import annotations
import sys, importlib.util
import numpy as np, pandas as pd, pytz

sys.path.insert(0, "/root/v9")
ET = pytz.timezone("America/New_York")
CAP = pd.Timestamp("2026-08-02 23:25", tz="UTC")   # signal bar-close (bucket start 23:20)
ANCHOR = 0

def _load(mod_name, path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    m = importlib.util.module_from_spec(spec); sys.modules[mod_name] = m
    spec.loader.exec_module(m); return m

vsl = _load("v9_strategy_lib_live", "/root/v9/v9_strategy_lib.py")
vex = _load("v9_execution_live",   "/root/v9/v9_execution.py")
VwapState = vsl.VwapState
session_for_utc_bar = vsl.session_for_utc_bar
FiveMinAggregator = vex.FiveMinAggregator

def build_5min(df):
    agg = FiveMinAggregator(5); out = []
    idx = df.index; o = df["open"].values; h = df["high"].values; l = df["low"].values
    c = df["close"].values; v = df["volume"].values
    for i in range(len(df)):
        done = agg.add(idx[i], o[i], h[i], l[i], c[i], v[i])
        if done is not None: out.append(done)
    # flush the final open bucket so the incident bucket is not stranded
    if agg.bucket is not None and not agg.emitted:
        out.append((agg.bucket, agg.o, agg.h, agg.l, agg.c, agg.v))
    return out

def drive(state, bars5, cap):
    """Return (sig, swing_src, bar_close, n_window) for the bar closing at cap."""
    bar_lows = []
    result = None
    for (bts, bo, bh, bl, bc, bv) in bars5:
        bar_close = bts + pd.Timedelta(minutes=5)
        bar_lows.append((bar_close, bl))
        sig = state.on_bar(bts.to_pydatetime(), bh, bl, bc, bv, anchor_hour_utc=ANCHOR)
        if abs((bar_close - cap).total_seconds()) < 1:
            if sig:
                used = list(state.last_lows)[:-1]
                n = len(used)
                prior = bar_lows[-(n+1):-1]
                src = None
                for (t, lw) in prior:
                    if abs(lw - sig["stop_price"]) < 1e-6:
                        src = (t, lw)
                result = (sig, src, bar_close, n)
            else:
                result = (None, None, bar_close, len(state.last_lows))
            break
    return result

def main():
    df = pd.read_csv("/root/v9/bt/NQ_ib_aug2_1min.csv", index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    print(f"IB 1-min bars: {len(df)}  range {df.index.min()} -> {df.index.max()}")

    # Detect the weekend halt gap in the raw data
    diffs = df.index.to_series().diff().dropna()
    biggest = diffs.sort_values(ascending=False).head(3)
    print("Largest inter-bar gaps (min):")
    for t, d in biggest.items():
        print(f"   {t}  gap={d.total_seconds()/60:.0f} min")

    print("\n" + "="*70)
    print("PART 1 -- GAP GUARD (patched live VwapState, blackout OFF so signal fires)")
    print("="*70)
    st1 = VwapState(side="LONG", entry_band_k=2.0, n_bars_outside=5, swing_lookback=20,
                    post_halt_blackout_bars=0)   # blackout off; gap_reset_min=180 default (ON)
    r1 = drive(st1, build_5min(df), CAP)
    p1_pass = False
    if r1 and r1[0]:
        sig, src, bc, n = r1
        risk = sig["entry_price"] - sig["stop_price"]
        print(f"signal @ bar_close {bc} UTC (ET {bc.tz_convert(ET)})")
        print(f"   entry = {sig['entry_price']:.2f}   stop = {sig['stop_price']:.2f}   risk = {risk:.2f} pts")
        print(f"   target= {sig['target_price']:.2f} (R=3)   exhaustion={sig['exhaustion']}  window={n} bars")
        if src:
            t, lw = src
            sess = session_for_utc_bar(t.to_pydatetime(), ANCHOR)
            print(f"   swing-low from 5-min bar close {t} UTC (ET {t.tz_convert(ET)}) low={lw:.2f}  session={sess}")
            sunday_src = (t.tz_convert('UTC').weekday() == 6) or (t.tz_convert(ET).weekday() == 6)
        else:
            sunday_src = None
            print("   swing-low source: could not map exactly")
        near_expected_stop = abs(sig["stop_price"] - 28545) < 60
        tight_risk = risk < 100
        not_old_bug = abs(sig["stop_price"] - 28258.25) > 60
        print(f"   -> stop~28,545 ({near_expected_stop}) | risk<100pt ({tight_risk}) | "
              f"not old 331pt bug ({not_old_bug}) | swing-low from Sunday ({sunday_src})")
        p1_pass = near_expected_stop and tight_risk and not_old_bug and bool(sunday_src)
    else:
        print("NO signal captured at 23:25 (unexpected with blackout OFF)")
    print(f"PART 1 RESULT: {'PASS' if p1_pass else 'FAIL'}")

    print("\n" + "="*70)
    print("PART 2 -- BLACKOUT (patched live VwapState, LIVE config blackout=24)")
    print("="*70)
    st2 = VwapState(side="LONG", entry_band_k=2.0, n_bars_outside=5, swing_lookback=20,
                    post_halt_blackout_bars=24)
    r2 = drive(st2, build_5min(df), CAP)
    if r2 is None:
        print("bucket at 23:25 not reached -- cannot evaluate"); p2_pass = False
    else:
        sig, src, bc, n = r2
        print(f"bar_close {bc} UTC (ET {bc.tz_convert(ET)})  bars_since_halt={st2.bars_since_halt}")
        print(f"   signal = {sig}")
        p2_pass = sig is None
        print(f"   -> NO SIGNAL during blackout ({p2_pass})")
    print(f"PART 2 RESULT: {'PASS' if p2_pass else 'FAIL'}")

    print("\n" + "="*70)
    print("PART 3 -- SEAM GUARD (real on_5min monotonic cursor)")
    print("="*70)
    p3_pass = seam_test()
    print(f"PART 3 RESULT: {'PASS' if p3_pass else 'FAIL'}")

    print("\n" + "="*70)
    print(f"OVERALL: P1 {'PASS' if p1_pass else 'FAIL'} | "
          f"P2 {'PASS' if p2_pass else 'FAIL'} | P3 {'PASS' if p3_pass else 'FAIL'}")
    print("="*70)

def seam_test():
    """Drive the REAL V9Bot.on_5min via the launcher-style config injection.
    Stub state (halted=True) so on_5min returns right after the detector feed, and
    stub detectors that count how many times they are fed. Second identical ts must
    be rejected BEFORE the feed (feed count stays at 1)."""
    import v9_config_live_user as cfg
    sys.modules["v9_config"] = cfg
    import v9_live_trade as lt

    calls = {"long": 0, "short": 0, "tlb": 0}
    class _Det:
        def __init__(self, key): self.key = key
        def on_bar(self, *a, **k): calls[self.key] += 1; return None
        def add_bar(self, *a, **k): calls[self.key] += 1; return None
    class _Sleeves(dict):
        pass
    class _State:
        halted = True
        sleeves = _Sleeves()

    bot = lt.V9Bot.__new__(lt.V9Bot)
    bot.state = _State()
    bot.regime = object()          # truthy
    bot.vwap_long_state  = _Det("long")
    bot.vwap_short_state = _Det("short")
    bot.tlb_state        = _Det("tlb")

    ts = pd.Timestamp("2026-08-02 23:20", tz="UTC")
    bot.on_5min(ts, 1, 2, 0.5, 1.5, 100.0, live=True)   # 1st: accepted, feeds detectors
    feed_after_1 = calls["long"]
    cursor_after_1 = getattr(bot, "_last_5min_ts", None)
    bot.on_5min(ts, 1, 2, 0.5, 1.5, 100.0, live=True)   # 2nd: SAME ts -> must be rejected
    feed_after_2 = calls["long"]
    cursor_after_2 = getattr(bot, "_last_5min_ts", None)
    # also test an OLDER ts (resend) is rejected
    bot.on_5min(ts - pd.Timedelta(minutes=5), 1, 2, 0.5, 1.5, 100.0, live=True)
    feed_after_3 = calls["long"]

    print(f"   after bar#1 (ts={ts}): long-feed count = {feed_after_1}, cursor = {cursor_after_1}")
    print(f"   after bar#2 (SAME ts):  long-feed count = {feed_after_2} (expect unchanged)")
    print(f"   after bar#3 (OLDER ts): long-feed count = {feed_after_3} (expect unchanged)")
    ok = (feed_after_1 == 1 and feed_after_2 == 1 and feed_after_3 == 1
          and cursor_after_1 == ts and cursor_after_2 == ts)
    print(f"   -> duplicate & resend rejected, cursor pinned at {ts} ({ok})")
    return ok

if __name__ == "__main__":
    main()
