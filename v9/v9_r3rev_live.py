"""v9_r3rev_live.py — R3 Re-entry (R3_ReV) live signal detector.

Fires AFTER a VWAP_LONG_R3 stop-out in the same session. Watches for a
breakout above the running max high since stop-out, within max_wait bars.

Target: adaptive — VWAP if fill < lag VWAP, else upper band (VWAP + 2*SD).

Pure detector — no IB, no orders. Returns signal dicts consumed by V9Bot._enter().
"""
from __future__ import annotations
from typing import Optional
import numpy as np
import pandas as pd
import pytz

ET = pytz.timezone("America/New_York")
R3REV_NAME = "R3_REV"

FLAT_MIN = 955   # 15:55 ET — flatten before close
MAX_WAIT = 48    # max bars to wait for re-entry after stop-out


class R3RevLive:
    """State machine that watches for re-entry after a VWAP_R3 stop-out.

    Lifecycle:
    1. V9Bot calls `on_r3_stopout()` when VWAP_R3 exits via stop.
    2. Each subsequent 5-min bar, V9Bot calls `on_5min()`.
    3. If breakout fires within max_wait bars in the same session, returns a signal.
    4. Signal is consumed (or session ends / max_wait exceeded) → reset.
    """

    def __init__(self, max_wait: int = MAX_WAIT, wait_bars: int = 2):
        self.max_wait = max_wait
        self.wait_bars = wait_bars  # skip this many bars after stop-out before scanning

        # Armed state
        self._armed = False
        self._stop_session: Optional[str] = None  # session key when R3 stopped out
        self._stop_bar_idx: int = 0   # bar index when R3 stopped out
        self._orig_entry_low: Optional[float] = None  # swing low from original R3 entry bar
        self._running_max_high: float = -np.inf
        self._running_min_low: float = np.inf
        self._bars_since_stop: int = 0
        self._bar_lows: list[float] = []

        # Today tracking
        self.today_et: Optional[str] = None
        self.fired_today: bool = False

    def on_r3_stopout(self, session_key: str, orig_entry_bar_low: float,
                      bars_since_entry: list[float]) -> None:
        """Called by V9Bot when VWAP_R3 exits via stop.

        Args:
            session_key: session date string for session boundary check
            orig_entry_bar_low: the low of the 5-min bar at VWAP_R3 entry (for stop)
            bars_since_entry: list of 5-min lows from R3 entry to stop-out
        """
        self._armed = True
        self._stop_session = session_key
        self._bars_since_stop = 0
        self._running_max_high = -np.inf
        self._bar_lows = list(bars_since_entry) if bars_since_entry else []
        self._orig_entry_low = orig_entry_bar_low

    def reset(self) -> None:
        self._armed = False
        self._stop_session = None
        self._bars_since_stop = 0
        self._running_max_high = -np.inf
        self._bar_lows = []
        self._orig_entry_low = None

    def on_5min(self, ts, o: float, h: float, l: float, c: float,
                session_key: str, vwap: float, upper_band: float,
                live: bool = False) -> Optional[dict]:
        """Feed a completed 5-min bar. Returns signal if breakout fires.

        Args:
            ts: bar timestamp
            o, h, l, c: OHLC
            session_key: current session date string
            vwap: current session VWAP (lagged 1 bar, from prior bar)
            upper_band: VWAP + 2*SD (lagged 1 bar)
            live: if True, can fire signals; if False, warmup only
        """
        ts = pd.Timestamp(ts)
        ts_utc = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        et = ts_utc.tz_convert(ET)
        et_min = et.hour * 60 + et.minute
        date_str = et.strftime("%Y-%m-%d")

        if date_str != self.today_et:
            self.today_et = date_str
            self.fired_today = False

        if not self._armed:
            return None

        # Session boundary check — if we rolled into a new session, cancel
        if session_key != self._stop_session:
            self.reset()
            return None

        # Flatten time check
        if et_min >= FLAT_MIN:
            self.reset()
            return None

        self._bars_since_stop += 1

        # Max wait exceeded
        if self._bars_since_stop > self.max_wait:
            self.reset()
            return None

        # Track running max high and collect lows
        self._bar_lows.append(l)

        # Skip wait_bars after stop-out
        if self._bars_since_stop <= self.wait_bars:
            self._running_max_high = max(self._running_max_high, h)
            return None

        # Check breakout: current high exceeds running max
        prev_max = self._running_max_high
        self._running_max_high = max(self._running_max_high, h)

        if not live or self.fired_today:
            return None

        if h > prev_max and prev_max > -np.inf:
            fill = max(o, prev_max)
            all_lows = self._bar_lows[:-1] if len(self._bar_lows) > 1 else [l]
            if self._orig_entry_low is not None and not np.isnan(self._orig_entry_low):
                all_lows = [self._orig_entry_low] + list(all_lows)
            stop_lvl = min(all_lows)
            risk = fill - stop_lvl
            if risk <= 0:
                self.reset()
                return None

            # Adaptive target: VWAP if fill < lag_vwap, else upper band
            if not np.isnan(vwap) and fill < vwap:
                tp_kind = "VWAP"
                target = vwap
            elif not np.isnan(upper_band):
                tp_kind = "UPPER"
                target = upper_band
            else:
                tp_kind = "VWAP"
                target = fill + 3.0 * risk  # fallback

            if target <= fill:
                self.reset()
                return None

            sig = dict(
                side="LONG",
                entry_price=fill,
                stop_price=stop_lvl,
                target_price=target,
                risk_pts=risk,
                sleeve=R3REV_NAME,
                reason=f"R3_REV_{tp_kind}",
                tp_kind=tp_kind,
            )

            self._armed = False
            self.fired_today = True
            return sig

        return None
