"""v9_cci_live.py — CCI AM live signal detector.

Ports the CCI backtest (_cci_bt.py) to a causal state machine that feeds on
completed 1-min and 5-min bars. AM slot only (09:30-12:00 ET, k_stop=1.0).

Pure detector — no IB, no orders. Returns signal dicts consumed by V9Bot._enter().
"""
from __future__ import annotations
from collections import deque
from datetime import datetime
from typing import Optional
import numpy as np
import pandas as pd
import pytz

ET = pytz.timezone("America/New_York")
CCI_NAME = "CCI_AM"

AM_WINDOW = (570, 720)  # 09:30-12:00 ET in minutes
K_STOP = 1.0
EOD_MIN = 960  # 16:00 ET


def _cci_val(tp_buf: deque, n: int = 20) -> float:
    if len(tp_buf) < n:
        return float("nan")
    arr = list(tp_buf)[-n:]
    sma = sum(arr) / n
    md = sum(abs(v - sma) for v in arr) / n
    if md == 0:
        return 0.0
    return (arr[-1] - sma) / (0.015 * md)


class CciAmLive:
    """Causal CCI detector over completed 1-min bars + 5-min EMA50/ATR context."""

    def __init__(self, cci_n: int = 20, ema_span: int = 50, atr_n: int = 14,
                 norm_window: int = 20, vwap_frac: float = 0.0015):
        self.cci_n = cci_n
        self.ema_span = ema_span
        self.atr_n = atr_n
        self.norm_window = norm_window
        self.vwap_frac = vwap_frac

        # 1-min state
        self.tp_buf: deque = deque(maxlen=cci_n)
        self.prev_cci: float = float("nan")

        # 5-min state (fed externally)
        self.ema50: float = float("nan")
        self.ema50_30ago: float = float("nan")  # 6 bars ago = 30 min
        self.ema50_hist: deque = deque(maxlen=7)
        self.atr5: float = float("nan")
        self.low20: float = float("nan")
        self.high20: float = float("nan")
        self.prev_5m_close: Optional[float] = None
        self._5m_lows: deque = deque(maxlen=20)
        self._5m_highs: deque = deque(maxlen=20)

        # ATR norm: rolling median of ATR at same time-of-day slot
        self._atr_by_slot: dict[int, deque] = {}
        self.atr_norm: float = float("nan")

        # session VWAP (fed externally or computed)
        self.vwap: float = float("nan")

        # daily state
        self.today_et: Optional[str] = None
        self.fired_today: bool = False

    def set_vwap(self, vwap: float) -> None:
        self.vwap = vwap

    def on_5min(self, ts, o: float, h: float, l: float, c: float, v: float,
                vwap: float = float("nan")) -> None:
        """A completed 5-min bar. Updates EMA50, ATR, swing high/low, ATR norm."""
        ts = pd.Timestamp(ts)
        ts_utc = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

        # EMA50
        alpha = 2.0 / (self.ema_span + 1)
        if np.isnan(self.ema50):
            self.ema50 = c
        else:
            self.ema50 = alpha * c + (1 - alpha) * self.ema50
        self.ema50_hist.append(self.ema50)
        self.ema50_30ago = self.ema50_hist[0] if len(self.ema50_hist) >= 7 else float("nan")

        # ATR (Wilder)
        if self.prev_5m_close is not None:
            tr = max(h - l, abs(h - self.prev_5m_close), abs(l - self.prev_5m_close))
        else:
            tr = h - l
        atr_alpha = 1.0 / self.atr_n
        if np.isnan(self.atr5):
            self.atr5 = tr
        else:
            self.atr5 = atr_alpha * tr + (1 - atr_alpha) * self.atr5
        self.prev_5m_close = c

        # Swing low/high (20-bar)
        self._5m_lows.append(l)
        self._5m_highs.append(h)
        self.low20 = min(self._5m_lows)
        self.high20 = max(self._5m_highs)

        # ATR norm by time-of-day slot
        et = ts_utc.tz_convert(ET)
        slot = et.hour * 60 + et.minute
        if slot not in self._atr_by_slot:
            self._atr_by_slot[slot] = deque(maxlen=self.norm_window)
        self._atr_by_slot[slot].append(self.atr5)
        buf = self._atr_by_slot[slot]
        if len(buf) >= 10:
            self.atr_norm = float(np.median(list(buf)[:-1]))

        if not np.isnan(vwap):
            self.vwap = vwap

    def on_1min(self, ts, o: float, h: float, l: float, c: float,
                live: bool = False) -> Optional[dict]:
        """A completed 1-min bar. Checks CCI cross and returns signal if triggered.

        In warmup (live=False), still updates state but doesn't return signals.
        """
        ts = pd.Timestamp(ts)
        ts_utc = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        et = ts_utc.tz_convert(ET)
        et_min = et.hour * 60 + et.minute
        date_str = et.strftime("%Y-%m-%d")

        # Day rollover
        if date_str != self.today_et:
            self.today_et = date_str
            self.fired_today = False

        # Update CCI
        tp = (h + l + c) / 3.0
        self.tp_buf.append(tp)
        cur_cci = _cci_val(self.tp_buf, self.cci_n)

        if not live or self.fired_today:
            self.prev_cci = cur_cci
            return None

        # Window check: AM only
        if et_min < AM_WINDOW[0] or et_min >= AM_WINDOW[1]:
            self.prev_cci = cur_cci
            return None

        # Need valid indicators
        if (np.isnan(self.prev_cci) or np.isnan(cur_cci) or
                np.isnan(self.ema50) or np.isnan(self.vwap) or
                np.isnan(self.atr5) or np.isnan(self.ema50_30ago) or
                np.isnan(self.atr_norm)):
            self.prev_cci = cur_cci
            return None

        # ATR norm filter
        if self.atr5 > 1.0 * self.atr_norm:
            self.prev_cci = cur_cci
            return None

        price = c

        # Long cross: CCI from below -100 to above -100
        long_cross = self.prev_cci < -100 and cur_cci >= -100
        # Short cross: CCI from above 100 to below 100
        short_cross = self.prev_cci > 100 and cur_cci <= 100

        sig = None
        if long_cross and price > self.ema50:
            if (self.ema50 - self.vwap) >= self.vwap_frac * price and self.ema50 > self.ema50_30ago:
                stop = self.low20 - K_STOP * self.atr5
                risk = price - stop
                if risk > 0:
                    sig = dict(side="LONG", entry_price=price, stop_price=stop,
                               target_price=None, risk_pts=risk,
                               sleeve=CCI_NAME, reason="CCI_LONG_AM")

        if short_cross and price < self.ema50:
            if (self.vwap - self.ema50) >= self.vwap_frac * price and self.ema50 < self.ema50_30ago:
                stop = self.high20 + K_STOP * self.atr5
                risk = stop - price
                if risk > 0:
                    sig = dict(side="SHORT", entry_price=price, stop_price=stop,
                               target_price=None, risk_pts=risk,
                               sleeve=CCI_NAME, reason="CCI_SHORT_AM")

        self.prev_cci = cur_cci

        if sig is not None:
            self.fired_today = True
            # CCI exits at EOD (16:00 ET) — no structural target
            sig["eod_exit"] = True
        return sig
