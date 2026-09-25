"""v9_execution.py — live execution primitives for the v9.1 bot.

Pure, dependency-light helpers wired into v9_live_trade.py:
  - 5-second -> 5-minute bar aggregation (reqRealTimeBars delivers 5s bars)
  - causal TLB-LONG pivot/trend-line-break detector (mirrors
    smc-backtest/src/strategies/trendline_break.py exactly)
  - IB order helpers (MKT entry, server-side STP, LMT target with OCA group)
  - v9 fill recorder -> v9_trade_log.json (so the dashboard + Telegram pick it up)

The VWAP sleeves reuse the already-implemented VwapState from v9_strategy_lib.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import math
from ib_async import IB, Contract, MarketOrder, StopOrder, LimitOrder

HERE = Path(__file__).resolve().parent
# Trade-log path is CONFIG-DRIVEN (2026-06-11) so the LIVE bot records fills to its OWN file
# (v9_config_live.TRADE_LOG_FILE = "v9_live_real_trade_log.json") instead of polluting the PAPER
# bot's "v9_trade_log.json". Paper is unchanged (its config keeps the default name). Resolved at
# import; the live launcher injects v9_config_live as v9_config before this module loads. Takes
# effect on the next bot restart.
try:
    import v9_config as _cfg
    TRADE_LOG = HERE / getattr(_cfg, "TRADE_LOG_FILE", "v9_trade_log.json")
except Exception:
    TRADE_LOG = HERE / "v9_trade_log.json"


# ---------------- 5-sec -> 5-min aggregation ----------------

class FiveMinAggregator:
    """Accumulate 5-second realtime bars into completed 5-minute bars.

    add() returns a completed (ts, o, h, l, c, v) tuple at each 5-min boundary
    rollover, else None. ts is the bucket START (UTC pandas Timestamp).
    """
    def __init__(self, minutes: int = 5):
        self.minutes = minutes
        self.bucket: Optional[pd.Timestamp] = None
        self.o = self.h = self.l = self.c = None
        self.v = 0.0
        self.emitted = False   # has the current bucket already been emitted via the fast path?

    def add(self, ts: pd.Timestamp, o: float, h: float, l: float, c: float, v: float):
        """Emit a completed (ts,o,h,l,c,v) bucket. FAST-EMIT (2026-06-15): the instant the FINAL
        5-sec slot of a bucket arrives (e.g. the :55 bar, delivered by IB ~2-3s after the boundary)
        we emit immediately, instead of waiting for the NEXT bucket's first 5-sec bar (~5s later) to
        trigger the rollover. The emitted bar is IDENTICAL either way (close = last sub-bar's close),
        so this is a pure ~5s latency cut on entries, no change to the bar/signal. If IB skips the
        final slot, the next-bucket rollover still emits it (fallback, == old behaviour)."""
        floor = ts.floor(f"{self.minutes}min")
        completed = None
        if self.bucket is None:
            self.bucket, self.o, self.h, self.l, self.c, self.v = floor, o, h, l, c, v
            self.emitted = False
        elif floor == self.bucket:
            self.h = max(self.h, h); self.l = min(self.l, l); self.c = c; self.v += v
        else:  # rolled into a new bucket
            if not self.emitted:   # fallback: IB skipped the final slot -> emit on rollover (old path)
                completed = (self.bucket, self.o, self.h, self.l, self.c, self.v)
            self.bucket, self.o, self.h, self.l, self.c, self.v = floor, o, h, l, c, v
            self.emitted = False
        # FAST PATH: the just-added bar is the final 5-sec slot of this bucket -> emit now, don't wait
        if completed is None and not self.emitted and floor == self.bucket:
            last_slot = self.bucket + pd.Timedelta(minutes=self.minutes) - pd.Timedelta(seconds=5)
            if ts >= last_slot:
                completed = (self.bucket, self.o, self.h, self.l, self.c, self.v)
                self.emitted = True
        return completed


# ---------------- TLB-LONG causal detector ----------------

class TLBLive:
    """Live trend-line-break detector. Faithful to trendline_break.backtest:
      - pivot high at i confirmed k bars later: h[i] >= max(h[i-k:i]) and h[i] > max(h[i+1:i+k+1])
      - keep last 2 pivot highs + last 2 pivot lows
      - LONG when prior close <= line_prev and current close > line_now, where the line
        runs through the last two pivot highs (require_slope_dir=False -> any slope)
      - stop = most recent confirmed pivot low; target = entry + r_multiple * risk
      - HYBRID STOP (adopted 2026-08-12, mirrors trendline_break.P.hybrid_stop_pts): if the
        pivot-stop risk is below hybrid_stop_pts, switch the stop to the rolling
        hybrid_lookback-bar low INCLUDING the signal bar. Fixes the pivot-confirmation-race
        staleness (the 2026-08-12 second trade: an 8.25pt stop at an already-broken level while
        the true flush low sat unconfirmed). Walk-forward-validated; 0.0 = off = old behaviour.
        ⚠️CONVENTION FORK: published backtest figures remain pivot-stop; live-mirror
        reconstructions from 2026-08-13 must set hybrid_stop_pts=40.
    """
    def __init__(self, k: int = 3, r_multiple: float = 1.0,
                 hybrid_stop_pts: float = 0.0, hybrid_lookback: int = 20):
        self.k = k
        self.r_multiple = r_multiple
        self.hybrid_stop_pts = hybrid_stop_pts
        self.hybrid_lookback = hybrid_lookback
        self.h: list[float] = []
        self.l: list[float] = []
        self.c: list[float] = []
        self.last_ph: list[tuple[int, float]] = []
        self.last_pl: list[tuple[int, float]] = []

    def add_bar(self, high: float, low: float, close: float) -> Optional[dict]:
        self.h.append(high); self.l.append(low); self.c.append(close)
        n = len(self.h)
        k = self.k
        # Reveal the pivot confirmed at this bar (index n-1-k), needs n >= 2k+1
        rev = n - 1 - k
        if rev >= k:
            i = rev
            if self.h[i] >= max(self.h[i-k:i]) and self.h[i] > max(self.h[i+1:i+k+1]):
                self.last_ph.append((i, self.h[i]))
                if len(self.last_ph) > 2: self.last_ph.pop(0)
            if self.l[i] <= min(self.l[i-k:i]) and self.l[i] < min(self.l[i+1:i+k+1]):
                self.last_pl.append((i, self.l[i]))
                if len(self.last_pl) > 2: self.last_pl.pop(0)
        # Entry check at the current bar
        if n < 2:
            return None
        i_now, i_prev = n - 1, n - 2
        if len(self.last_ph) == 2 and len(self.last_pl) >= 1:
            (i1, v1), (i2, v2) = self.last_ph
            slope = (v2 - v1) / max(1, i2 - i1)
            line_now = v2 + slope * (i_now - i2)
            line_prev = v2 + slope * (i_prev - i2)
            if self.c[i_prev] <= line_prev and self.c[i_now] > line_now:
                entry = self.c[i_now]
                stop = self.last_pl[-1][1]
                if self.hybrid_stop_pts > 0 and (entry - stop) < self.hybrid_stop_pts:
                    stop = min(self.l[max(0, n - self.hybrid_lookback):n])
                risk = entry - stop
                if risk > 0:
                    return dict(side="long", entry_price=entry, stop_price=stop,
                                target_price=entry + self.r_multiple * risk)
        return None


# ---------------- IB order helpers ----------------

NQ_TICK = 0.25

def _tick_round(px: float, direction: str, order_side: str) -> float:
    """Round a price to the NQ/MNQ 0.25-pt tick grid.
    For stops: round AWAY from entry (long stop floors down, short stop ceils up).
    For targets: round TOWARD entry (long target floors down, short target ceils up)."""
    n = px / NQ_TICK
    if order_side == "stop":
        k = math.floor(n + 1e-9) if direction == "long" else math.ceil(n - 1e-9)
    else:
        k = math.floor(n + 1e-9) if direction == "long" else math.ceil(n - 1e-9)
    return round(k * NQ_TICK, 4)


def place_market_entry(ib: IB, contract: Contract, direction: str, qty: int):
    action = "BUY" if direction == "long" else "SELL"
    return ib.placeOrder(contract, MarketOrder(action, qty))


def place_protective_stop(ib: IB, contract: Contract, direction: str, qty: int,
                          stop_px: float, oca: str):
    action = "SELL" if direction == "long" else "BUY"
    stop_px = _tick_round(stop_px, direction, "stop")
    o = StopOrder(action, qty, stop_px)
    o.tif = "GTC"; o.outsideRth = True   # GTC so the bracket survives the CME 17:00 ET roll + Gateway logoff (overnight VWAP holds)
    o.ocaGroup = oca; o.ocaType = 1  # cancel-with-block: filling one cancels the sibling
    return ib.placeOrder(contract, o)


def place_limit_target(ib: IB, contract: Contract, direction: str, qty: int,
                       target_px: float, oca: str):
    action = "SELL" if direction == "long" else "BUY"
    target_px = _tick_round(target_px, direction, "target")
    o = LimitOrder(action, qty, target_px)
    o.tif = "GTC"; o.outsideRth = True   # GTC so the bracket survives the CME 17:00 ET roll + Gateway logoff (overnight VWAP holds)
    o.ocaGroup = oca; o.ocaType = 1
    return ib.placeOrder(contract, o)


def cancel_order(ib: IB, trade) -> None:
    try:
        if trade is not None:
            ib.cancelOrder(trade.order)
    except Exception:
        pass


def market_flatten(ib: IB, contract: Contract, direction: str, qty: int):
    action = "SELL" if direction == "long" else "BUY"
    return ib.placeOrder(contract, MarketOrder(action, qty))


# ---------------- fill recorder (-> v9_trade_log.json) ----------------

def record_v9_fill(instrument: str, side: str, qty: int, price: float, label: str,
                   realized: Optional[float] = None, realized_R: Optional[float] = None,
                   *, signal_px: Optional[float] = None,
                   stop: Optional[float] = None, target: Optional[float] = None,
                   reason: Optional[str] = None) -> None:
    """Append a fill to v9_trade_log.json in the {fills:[...]} shape the dashboard reads."""
    try:
        data = {"fills": []}
        if TRADE_LOG.exists():
            txt = TRADE_LOG.read_text(encoding="utf-8").strip()
            if txt:
                data = json.loads(txt)
        entry = {
            "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "instrument": instrument, "side": side, "qty": qty, "price": price,
            "label": label, "realized": realized, "realized_R": realized_R,
        }
        if signal_px is not None:
            entry["signal_px"] = round(signal_px, 2)
        if stop is not None:
            entry["stop"] = round(stop, 2)
        if target is not None:
            entry["target"] = round(target, 2)
        if reason is not None:
            entry["reason"] = reason
        data.setdefault("fills", []).append(entry)
        TRADE_LOG.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass
