"""fix_compare_0802.py -- quantify the last_lows/last_highs session-boundary reset fix.
Read-only. Reuses cached real-IB 1-min bars (NQU6 TRADES) and drives BOTH the current
(buggy) VwapState and a PATCHED subclass that also resets last_lows/last_highs at the
session boundary. Writes nothing to live code.
"""
from __future__ import annotations
import sys, importlib.util
from datetime import datetime
import numpy as np, pandas as pd, pytz

ET = pytz.timezone("America/New_York")

def _load(mod_name, path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    m = importlib.util.module_from_spec(spec); sys.modules[mod_name] = m
    spec.loader.exec_module(m); return m

vsl = _load("v9_strategy_lib_live", "/root/v9/v9_strategy_lib.py")
vex = _load("v9_execution_a1", "/root/nq_options/v9_execution.py")
VwapState = vsl.VwapState
session_for_utc_bar = vsl.session_for_utc_bar
update_session_vwap = vsl.update_session_vwap
FiveMinAggregator = vex.FiveMinAggregator

PARAMS = dict(side="LONG", entry_band_k=2.0, n_bars_outside=5, swing_lookback=20)
ANCHOR = 0

# ---- PATCHED subclass: identical on_bar EXCEPT it also clears last_lows/last_highs
#      at the session boundary. (Copy of the live on_bar with the two added lines.) ----
class VwapStateFixed(VwapState):
    def on_bar(self, bar_time_utc, high, low, close, volume, anchor_hour_utc=0):
        sess = session_for_utc_bar(bar_time_utc, anchor_hour_utc)
        if sess != self.session:
            self.session = sess
            self.cumv = self.cumvp = self.cumsq = 0.0
            self.consecutive_outside = 0
            self.last_lows = []; self.last_highs = []      # <<< THE FIX
        self.cumv, self.cumvp, self.cumsq, vwap, sd = update_session_vwap(
            self.cumv, self.cumvp, self.cumsq, high, low, close, volume)
        self.last_lows.append(low); self.last_highs.append(high)
        if len(self.last_lows)  > self.swing_lookback: self.last_lows.pop(0)
        if len(self.last_highs) > self.swing_lookback: self.last_highs.pop(0)
        if np.isnan(vwap) or np.isnan(sd) or sd == 0:
            return None
        lower = vwap - self.entry_band_k * sd
        if close < lower:
            self.consecutive_outside += 1
            return None
        if close > lower and self.consecutive_outside >= self.n_bars_outside:
            entry = close
            stop  = min(self.last_lows[:-1]) if len(self.last_lows) > 1 else low
            risk  = entry - stop
            if risk > 0:
                sig = dict(side='LONG', entry_price=entry, stop_price=stop,
                           target_price=entry + 3.0 * risk,
                           exhaustion=self.consecutive_outside)
                self.consecutive_outside = 0
                return sig
        self.consecutive_outside = 0
        return None

def build_5min(df):
    agg = FiveMinAggregator(5); out = []
    idx=df.index; o=df["open"].values; h=df["high"].values; l=df["low"].values
    c=df["close"].values; v=df["volume"].values
    for i in range(len(df)):
        done = agg.add(idx[i], o[i], h[i], l[i], c[i], v[i])
        if done is not None: out.append(done)
    return out

def run(state, bars5, capture_close):
    """Drive `state` over bars5; return (signal, stop_provenance) at capture_close.
    Records, for the bar that produced a signal, the (time,low) of the swing-low used."""
    result = None
    # We also track, per 5-min bar, its (close_time, low) so we can identify which
    # bar the winning stop came from.
    bar_lows = []  # list of (bar_close_ts, low)
    for (bts, bo, bh, bl, bc, bv) in bars5:
        bar_close = bts + pd.Timedelta(minutes=5)
        bar_lows.append((bar_close, bl))
        # snapshot lookback BEFORE on_bar mutates? on_bar appends current bar then the
        # signal uses last_lows[:-1] (excludes current bar). We snapshot after.
        sig = state.on_bar(bts.to_pydatetime(), bh, bl, bc, bv, anchor_hour_utc=ANCHOR)
        if sig and abs((bar_close - capture_close).total_seconds()) < 1:
            # identify swing-low source: the min over last_lows[:-1]
            lows_window = list(state.last_lows)  # after append; signal used [:-1]
            used = lows_window[:-1]
            stop_val = sig["stop_price"]
            # map stop_val back to a bar_close time: find most recent bar in window with that low
            # window corresponds to the last len(used) prior 5-min bars (excluding current)
            n = len(used)
            prior_bars = bar_lows[-(n+1):-1]  # exclude current bar
            src = None
            for (t, lw) in prior_bars:
                if abs(lw - stop_val) < 1e-6:
                    src = (t, lw)  # keep last match (most recent)
            result = (sig, src, bar_close, n)
    return result

def main():
    df = pd.read_csv("/root/v9/bt/NQ_ib_aug2_1min.csv", index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    print(f"IB 1-min bars: {len(df)}  range {df.index.min()} -> {df.index.max()}")
    bars5 = build_5min(df)
    print(f"5-min bars: {len(bars5)}")

    cap = pd.Timestamp("2026-08-02 23:25", tz="UTC")  # signal bar-close

    old = VwapState(**PARAMS)
    new = VwapStateFixed(**PARAMS)
    r_old = run(old, bars5, cap)
    r_new = run(new, build_5min(df), cap)  # fresh bars5 fine (same data)

    def show(tag, r):
        if r is None:
            print(f"\n[{tag}] NO signal captured at {cap}"); return None
        sig, src, bc, n = r
        risk = sig["entry_price"] - sig["stop_price"]
        print(f"\n[{tag}] signal @ bar_close {bc} UTC  (ET {bc.tz_convert(ET)})")
        print(f"   entry  = {sig['entry_price']:.2f}")
        print(f"   stop   = {sig['stop_price']:.2f}")
        print(f"   risk   = {risk:.2f} pts")
        print(f"   target = {sig['target_price']:.2f}   (R=3)")
        print(f"   exhaustion streak = {sig['exhaustion']}   lookback window used = {n} bars")
        if src:
            t, lw = src
            print(f"   stop swing-low sourced from 5-min bar close {t} UTC (ET {t.tz_convert(ET)}), low={lw:.2f}")
        else:
            print(f"   stop swing-low source: (could not map exactly)")
        return sig, risk, src

    o = show("OLD  (buggy, current live)", r_old)
    n_ = show("NEW  (fixed)", r_new)

    print("\n================= SIDE-BY-SIDE =================")
    if o and n_:
        (so, ro, srco), (sn, rn, srcn) = o, n_
        print(f"{'':<10}{'OLD':>14}{'NEW':>14}")
        print(f"{'entry':<10}{so['entry_price']:>14.2f}{sn['entry_price']:>14.2f}")
        print(f"{'stop':<10}{so['stop_price']:>14.2f}{sn['stop_price']:>14.2f}")
        print(f"{'risk pts':<10}{ro:>14.2f}{rn:>14.2f}")
        print(f"{'target':<10}{so['target_price']:>14.2f}{sn['target_price']:>14.2f}")
        print(f"\nrisk reduction: {ro - rn:.2f} pts  ({(1-rn/ro)*100:.1f}% tighter)")
        if srco: print(f"OLD stop from: {srco[0]}  (session {session_for_utc_bar(srco[0].to_pydatetime(),0)})")
        if srcn: print(f"NEW stop from: {srcn[0]}  (session {session_for_utc_bar(srcn[0].to_pydatetime(),0)})")

if __name__ == "__main__":
    main()
