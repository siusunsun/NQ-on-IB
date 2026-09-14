"""Generic OHLC utilities — reusable across strategies.

Originally this module hosted the SMC strategy logic directly. After the
framework refactor (May 2026), strategy-specific code moved into
src/strategies/<name>.py and this module is kept for utilities any
strategy (or analysis script) might use:

  - load_csv()           : TradingView-style CSV loader (handles Unix epoch time)
  - detect_swings()      : strict fractal pivots (window=3)
  - last_confirmed_swing(): "prior swing" lookup with confirmation lag
  - detect_fvg()         : 3-candle imbalance (bullish/bearish)
  - session_of()         : UTC-hour session label (asia/london/ny/off)

Conventions
-----------
- All timestamps UTC. All math in price points.
- CSV columns expected (case-insensitive): time/timestamp/date, open, high,
  low, close, [volume]. Time may be ISO string or Unix epoch seconds.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


SWING_WINDOW = 3


def load_csv(path: str | Path) -> pd.DataFrame:
    """Load OHLC CSV → UTC-indexed DataFrame [open, high, low, close, volume]."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next((c for c in ("time", "timestamp", "date", "datetime") if c in df.columns), None)
    if tcol is None:
        raise ValueError(f"No time column in {path}")
    for c in ("open", "high", "low", "close"):
        if c not in df.columns:
            raise ValueError(f"Missing '{c}' column in {path}")
    if pd.api.types.is_numeric_dtype(df[tcol]):
        df[tcol] = pd.to_datetime(df[tcol], utc=True, unit="s", errors="coerce")
    else:
        df[tcol] = pd.to_datetime(df[tcol], utc=True, errors="coerce")
    df = df.dropna(subset=[tcol]).set_index(tcol).sort_index()
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return df[["open", "high", "low", "close", "volume"]].astype(
        {"open": float, "high": float, "low": float, "close": float, "volume": float}
    )


def detect_swings(df: pd.DataFrame, window: int = SWING_WINDOW) -> tuple[np.ndarray, np.ndarray]:
    """Strict fractal swings: bar is swing high/low if its extreme exceeds the
    `window` bars on each side. Returns (swing_high_bool, swing_low_bool)."""
    h, l = df["high"].values, df["low"].values
    n = len(df)
    sh = np.zeros(n, dtype=bool)
    sl = np.zeros(n, dtype=bool)
    for i in range(window, n - window):
        if h[i] > h[i - window : i].max() and h[i] > h[i + 1 : i + window + 1].max():
            sh[i] = True
        if l[i] < l[i - window : i].min() and l[i] < l[i + 1 : i + window + 1].min():
            sl[i] = True
    return sh, sl


def last_confirmed_swing(swing_arr: np.ndarray, price_arr: np.ndarray, window: int = SWING_WINDOW) -> np.ndarray:
    """For each bar i, the price of the most recent swing confirmed by bar i.
    A swing at position p is confirmed at bar p+window (right-fractal lag)."""
    n = len(swing_arr)
    out = np.full(n, np.nan)
    last = np.nan
    for i in range(n):
        confirm_idx = i - window
        if confirm_idx >= 0 and swing_arr[confirm_idx]:
            last = price_arr[confirm_idx]
        out[i] = last
    return out


def detect_fvg(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """3-candle imbalance.
    Bullish FVG at bar i: low[i] > high[i-2].
    Bearish FVG at bar i: high[i] < low[i-2]."""
    h, l = df["high"].values, df["low"].values
    n = len(df)
    bull = np.zeros(n, dtype=bool)
    bear = np.zeros(n, dtype=bool)
    for i in range(2, n):
        if l[i] > h[i - 2]:
            bull[i] = True
        if h[i] < l[i - 2]:
            bear[i] = True
    return bull, bear



def session_of(ts: pd.Timestamp) -> str:
    """UTC-hour-based session classification."""
    h = ts.hour
    if 0 <= h < 7:
        return "asia"
    if 7 <= h < 12:
        return "london"
    if 12 <= h < 17:
        return "ny"
    return "off"


def atr(df: pd.DataFrame, window: int = 14) -> np.ndarray:
    """Average True Range (Wilder-style simple mean of TR over `window` bars)."""
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    tr = np.maximum.reduce([h - l, np.abs(h - prev_c), np.abs(l - prev_c)])
    return pd.Series(tr).rolling(window).mean().values


def rsi(close: pd.Series | np.ndarray, period: int = 14) -> np.ndarray:
    """RSI(14) Wilder-smoothed. Returns ndarray same length as close.

    Wilder's smoothing uses EMA with alpha = 1/period (equivalent to the
    classic Welles Wilder formula). First period-1 values are NaN.
    """
    c = pd.Series(close) if not isinstance(close, pd.Series) else close
    delta = c.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_vals = 100.0 - 100.0 / (1.0 + rs)
    return rsi_vals.values


def is_bullish_engulfing(o: np.ndarray, c: np.ndarray, j: int) -> bool:
    """Strict 4-condition bullish engulfing:
        close[j] > open[j]            (current bullish)
        close[j-1] < open[j-1]        (prior bearish)
        close[j] >= open[j-1]
        open[j] <= close[j-1]
    """
    if j == 0:
        return False
    return (
        c[j] > o[j]
        and c[j - 1] < o[j - 1]
        and c[j] >= o[j - 1]
        and o[j] <= c[j - 1]
    )


def is_bearish_engulfing(o: np.ndarray, c: np.ndarray, j: int) -> bool:
    """Strict 4-condition bearish engulfing — mirror of is_bullish_engulfing."""
    if j == 0:
        return False
    return (
        c[j] < o[j]
        and c[j - 1] > o[j - 1]
        and c[j] <= o[j - 1]
        and o[j] >= c[j - 1]
    )
