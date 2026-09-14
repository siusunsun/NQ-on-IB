"""v9.1 Strategy Library — pure functions, no IB dependencies.

Implements the regime gate + per-cell sleeve allocation logic.
Tested without live data; mirrors the backtest implementation exactly.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Optional
import numpy as np
import pandas as pd
import pytz

ET = pytz.timezone("America/New_York")


@dataclass
class DailyRegime:
    """Computed once per day from the prior session's daily close."""
    date_et: str               # YYYY-MM-DD
    close: float
    sma200: float
    ret20: float
    s200: bool                 # close > sma200
    r20: bool                  # ret20 > 0

    @property
    def cell(self) -> str:
        if self.s200 and self.r20:        return "BOTH_BULL"
        if self.s200 and not self.r20:    return "ZONE_A"
        if not self.s200 and self.r20:    return "ZONE_B"
        return "BOTH_BEAR"

    @property
    def active_sleeves(self) -> list[str]:
        c = self.cell
        if c == "BOTH_BULL": return ["TLB_LONG", "VWAP_LONG_R3"]
        if c == "ZONE_A":    return ["VWAP_LONG_R3"]
        if c == "ZONE_B":    return ["TLB_LONG"]
        if c == "BOTH_BEAR": return ["VWAP_SHORT_R1"]
        return []


def compute_regime(daily_closes: pd.Series, sma_window: int = 200,
                   mom_window: int = 20) -> DailyRegime:
    """Given a Series of daily closes (indexed by ET date),
    compute the regime to apply for today's session.

    Uses the PRIOR session's COMPLETE close + SMA + return (lagged 1 day, no look-ahead).

    IMPORTANT: today's partial close must be excluded from `daily_closes` before
    calling. The caller is responsible for filtering out the current ET date.
    """
    if len(daily_closes) < sma_window + 5:
        raise ValueError(f"Need at least {sma_window+5} daily bars, got {len(daily_closes)}")
    closes = daily_closes.sort_index()
    # Defensive: drop today's entry if accidentally included
    today_et = pd.Timestamp.now(tz=ET).date()
    closes = closes[closes.index.date < today_et] if hasattr(closes.index, 'date') else closes
    if len(closes) < sma_window + 5:
        raise ValueError(f"After excluding today, need >= {sma_window+5} bars, got {len(closes)}")
    last_idx = closes.index[-1]
    prev_close = closes.iloc[-1]
    sma200 = closes.iloc[-sma_window:].mean()
    ret20 = (closes.iloc[-1] / closes.iloc[-(mom_window+1)]) - 1.0
    return DailyRegime(
        date_et=str(last_idx)[:10],
        close=float(prev_close),
        sma200=float(sma200),
        ret20=float(ret20),
        s200=bool(prev_close > sma200),
        r20=bool(ret20 > 0),
    )


def is_in_window(t: dtime, windows: list[tuple[int, int]]) -> bool:
    """Check if ET time falls within any (start_min, end_min) window."""
    t_min = t.hour * 60 + t.minute
    return any(lo <= t_min < hi for lo, hi in windows)


# ---------- TLB-LONG sleeve ----------

@dataclass
class TLBState:
    """Rolling state for the TLB-LONG sleeve."""
    pivot_highs: list[tuple[int, float]]    # (bar_index, value), max 2 entries
    pivot_lows:  list[tuple[int, float]]
    pivot_k: int = 3

    def add_bar(self, idx: int, high: float, low: float,
                prior_highs: list[float], future_highs_len: int,
                prior_lows: list[float], future_lows_len: int) -> None:
        """Notional pivot-detection step. Use the offline backtest engine to feed
        confirmed pivots in live — kept here for documentation."""
        pass

    def line_value_now(self, current_idx: int) -> Optional[float]:
        if len(self.pivot_highs) < 2: return None
        (i1, v1), (i2, v2) = self.pivot_highs[-2:]
        slope = (v2 - v1) / max(1, i2 - i1)
        return v2 + slope * (current_idx - i2)


# ---------- VWAP-LONG-R3 + VWAP-SHORT-R1 sleeve ----------

def update_session_vwap(prior_cumv: float, prior_cumvp: float, prior_cumsq: float,
                        bar_high: float, bar_low: float, bar_close: float,
                        bar_volume: float) -> tuple[float, float, float, float, float]:
    """Incremental session-anchored VWAP update.

    Returns: (cumv, cumvp, cumsq, vwap, std) — the running sums + current VWAP and SD.

    Pass prior_* = 0 at the start of each session.
    """
    tp = (bar_high + bar_low + bar_close) / 3.0
    cumv  = prior_cumv  + bar_volume
    cumvp = prior_cumvp + tp * bar_volume
    cumsq = prior_cumsq + (tp ** 2) * bar_volume
    if cumv <= 0:
        return cumv, cumvp, cumsq, float("nan"), float("nan")
    vwap = cumvp / cumv
    var = cumsq / cumv - vwap ** 2
    std = float(np.sqrt(max(var, 0)))
    return cumv, cumvp, cumsq, vwap, std


def session_for_utc_bar(bar_time_utc: datetime, anchor_hour_utc: int) -> str:
    """Returns the 'session date' for a bar, given the UTC anchor hour."""
    shifted = bar_time_utc - pd.Timedelta(hours=anchor_hour_utc)
    return str(shifted.date())


@dataclass
class VwapState:
    """Rolling state for one VWAP-revert sleeve."""
    side: str                          # 'LONG' or 'SHORT'
    entry_band_k: float                # 2.0
    n_bars_outside: int                # 5 for LONG, 3 for SHORT-in-bear (v9.1)
    consecutive_outside: int = 0       # streak of closes outside band
    session: str = ""                  # current session date
    cumv: float = 0.0
    cumvp: float = 0.0
    cumsq: float = 0.0
    last_lows: list[float] = None      # rolling lookback for swing low (LONG stop)
    last_highs: list[float] = None     # rolling lookback for swing high (SHORT stop)
    swing_lookback: int = 20
    # ---- swing-buffer gap guard + post-halt blackout (ported from kristov18 ee62ef0, 2026-08-03) ----
    # Clear swing buffers across a REAL trading halt (>gap_reset_min) so the stop can't span a weekend
    # gap (the 2026-08-03 incident: 331pt Friday-low stop where 44.5pt was correct). NOT a per-session
    # reset (every 00:00 UTC would leave 2-3 bars -> toxic micro-stops). 180min clears weekends/holidays,
    # ignores the 60-min CME maintenance break. Blackout: after a halt the band has no statistical content
    # yet, so refuse to ARM the streak for N bars (long sleeve only via config; short stays 0).
    gap_reset_min: int = 180
    last_bar_utc: Optional[datetime] = None
    post_halt_blackout_bars: int = 0
    bars_since_halt: int = 0

    def __post_init__(self):
        if self.last_lows is None: self.last_lows = []
        if self.last_highs is None: self.last_highs = []

    def on_bar(self, bar_time_utc: datetime, high: float, low: float,
               close: float, volume: float, anchor_hour_utc: int = 0) -> Optional[dict]:
        """Process a bar; return signal dict if entry triggers, else None.

        Signal dict: {side, entry_price, stop_price, target_price, exhaustion_count}
        """
        # Session boundary -> reset VWAP accumulators
        sess = session_for_utc_bar(bar_time_utc, anchor_hour_utc)
        if sess != self.session:
            self.session = sess
            self.cumv = self.cumvp = self.cumsq = 0.0
            self.consecutive_outside = 0
        self.cumv, self.cumvp, self.cumsq, vwap, sd = update_session_vwap(
            self.cumv, self.cumvp, self.cumsq, high, low, close, volume)
        # ★Clear swing buffers across a real trading halt (gap_reset_min) BEFORE the append, so the
        # incoming bar seeds a fresh, structurally-connected window. Stop can't span the halt.
        if self.gap_reset_min and self.last_bar_utc is not None:
            gap_min = (bar_time_utc - self.last_bar_utc).total_seconds() / 60.0
            if gap_min > self.gap_reset_min:
                self.last_lows.clear(); self.last_highs.clear()
                self.bars_since_halt = 0
        self.bars_since_halt += 1
        self.last_bar_utc = bar_time_utc
        # Track swing high/low
        self.last_lows.append(low); self.last_highs.append(high)
        if len(self.last_lows)  > self.swing_lookback: self.last_lows.pop(0)
        if len(self.last_highs) > self.swing_lookback: self.last_highs.pop(0)
        if np.isnan(vwap) or np.isnan(sd) or sd == 0:
            return None
        # ★post-halt blackout: after a real halt the band has no statistical content yet, so it must
        # not ARM a streak. Returns BEFORE the outside-band counters, exactly like the sd==0 guard.
        if self.post_halt_blackout_bars and self.bars_since_halt < self.post_halt_blackout_bars:
            return None
        upper = vwap + self.entry_band_k * sd
        lower = vwap - self.entry_band_k * sd
        # Outside-band streak update
        if self.side == 'LONG':
            if close < lower:
                self.consecutive_outside += 1
                return None
            if close > lower and self.consecutive_outside >= self.n_bars_outside:
                # Entry trigger: prior streak + close back inside
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
        else:  # SHORT
            if close > upper:
                self.consecutive_outside += 1
                return None
            if close < upper and self.consecutive_outside >= self.n_bars_outside:
                entry = close
                stop  = max(self.last_highs[:-1]) if len(self.last_highs) > 1 else high
                risk  = stop - entry
                if risk > 0:
                    sig = dict(side='SHORT', entry_price=entry, stop_price=stop,
                               target_price=entry - 1.0 * risk,
                               exhaustion=self.consecutive_outside)
                    self.consecutive_outside = 0
                    return sig
            self.consecutive_outside = 0
        return None
