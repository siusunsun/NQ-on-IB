"""v9_tlb_bear_short.py — TLB Bear Short detector, copied from bt/ for live import.

This is a verbatim copy of /root/v9/bt/v9_tlb_bear_short.py so the live bot can
import it without reaching into the bt/ directory. Keep in sync with the backtest version.
"""
from __future__ import annotations
from collections import deque
from datetime import datetime
from typing import Optional
import pandas as pd
import pytz

ET = pytz.timezone("America/New_York")
TBS_NAME = "TLB_BEAR_SHORT"
SESSIONS = {"PRE": ((480, 570), 4.0, 4.0), "OPEN": ((570, 660), 3.0, 2.0), "MIDDAY": ((660, 840), 4.0, 3.0)}
MAX_PER_SESSION, MAX_PER_DAY = 1, 3


def session_at(et_dt: datetime) -> Optional[str]:
    m = et_dt.hour * 60 + et_dt.minute
    for name, ((lo, hi), _k, _rr) in SESSIONS.items():
        if lo <= m < hi:
            return name
    return None


def session_end_minutes(name: str) -> int:
    return SESSIONS[name][0][1]


def to_et(ts) -> datetime:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.tz_convert(ET).to_pydatetime()


class TlbBearShortLive:
    """Causal state machine over completed 1-min bars (+ an optional 5-sec touch path)."""

    def __init__(self, k: int = 3, atr_len: int = 14, line_min: int = 15):
        self.k = k; self.atr_len = atr_len; self.line_min = line_min
        self.keys: list[pd.Timestamp] = []
        self.idx: dict[pd.Timestamp, int] = {}
        self.l15: list[float] = []
        self.h15: list[float] = []
        self.cur: Optional[dict] = None
        self.prev_close: Optional[float] = None
        self.tr: deque = deque(maxlen=atr_len)
        self.cur_min: Optional[pd.Timestamp] = None
        self.cur_open: Optional[float] = None
        self.pivots: list[tuple[int, float]] = []
        self.line: Optional[dict] = None
        self.n_lines = 0; self.n_consumed = 0

    @property
    def atr(self) -> Optional[float]:
        if len(self.tr) < self.atr_len:
            return None
        return float(sum(self.tr) / self.atr_len)

    def _bucket_of(self, ts: pd.Timestamp) -> int:
        key = ts.floor(f"{self.line_min}min")
        i = self.idx.get(key)
        if i is None:
            i = len(self.keys); self.keys.append(key); self.idx[key] = i
        return i

    def _x(self, ts: pd.Timestamp) -> float:
        key = ts.floor(f"{self.line_min}min")
        return self._bucket_of(ts) + (ts - key).total_seconds() / 60.0 / self.line_min

    def line_value(self, ts) -> Optional[float]:
        L = self.line
        if L is None or L["pending"] or not L["valid"] or L["consumed"]:
            return None
        ts = pd.Timestamp(ts).floor("1min")
        return L["v2"] + L["slope"] * (self._x(ts) - L["j2"])

    def _complete_bucket(self) -> None:
        if self.cur is None or self.cur["done"]:
            return
        self.cur["done"] = True
        self.l15.append(self.cur["l"]); self.h15.append(self.cur["h"])
        n = len(self.l15); k = self.k
        rev = n - 1 - k
        if rev < k:
            return
        l = self.l15
        if l[rev] <= min(l[rev - k:rev]) and l[rev] < min(l[rev + 1:rev + k + 1]):
            self.pivots.append((rev, l[rev]))
            self.line = None
            if len(self.pivots) >= 2:
                (j1, v1), (j2, v2) = self.pivots[-2], self.pivots[-1]
                if v2 > v1:
                    self.n_lines += 1
                    self.line = dict(j1=j1, v1=v1, j2=j2, v2=v2, slope=(v2 - v1) / (j2 - j1),
                                     pending=True, valid=False, consumed=False, act_min=None)

    def _resolve_pending(self, ts_min: pd.Timestamp) -> None:
        L = self.line
        if L is None or not L["pending"]:
            return
        L["pending"] = False; L["act_min"] = ts_min
        lv = L["v2"] + L["slope"] * (self._x(ts_min) - L["j2"])
        L["valid"] = self.prev_close is not None and self.prev_close > lv
        if not L["valid"]:
            self.line = None

    def _touch(self, ts_min: pd.Timestamp, open_px: float, low: float) -> Optional[dict]:
        lv = self.line_value(ts_min)
        if lv is None or low > lv:
            return None
        L = self.line; L["consumed"] = True; self.n_consumed += 1
        et = to_et(ts_min); sess = session_at(et)
        atr = self.atr
        ev = dict(ts=ts_min, et=et, session=sess, line=lv, entry_price=min(open_px, lv), atr=atr, side="short",
                  stop_price=None, target_price=None)
        if sess is not None and atr is not None and atr > 0:
            _w, ks, rr = SESSIONS[sess]
            ev["stop_price"] = ev["entry_price"] + ks * atr
            ev["target_price"] = ev["entry_price"] - rr * ks * atr
        return ev

    def on_1min(self, ts, o: float, h: float, l: float, c: float, live: bool = False) -> Optional[dict]:
        ts = pd.Timestamp(ts); ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        ts = ts.floor("1min")
        key = ts.floor(f"{self.line_min}min")
        if self.cur is not None and self.cur["key"] != key:
            self._complete_bucket()
        if self.cur is None or self.cur["key"] != key:
            self._bucket_of(ts)
            self.cur = dict(key=key, h=h, l=l, done=False)
        else:
            self.cur["h"] = max(self.cur["h"], h); self.cur["l"] = min(self.cur["l"], l)
        self._resolve_pending(ts)
        ev = None if live else self._touch(ts, o, l)
        tr = h - l if self.prev_close is None else max(h - l, abs(h - self.prev_close), abs(l - self.prev_close))
        self.tr.append(tr); self.prev_close = c
        if ts.minute % self.line_min == self.line_min - 1:
            self._complete_bucket()
        return ev

    def on_5s(self, ts, o: float, h: float, l: float, c: float) -> Optional[dict]:
        ts = pd.Timestamp(ts); ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        m = ts.floor("1min")
        if self.cur_min != m:
            self.cur_min, self.cur_open = m, o
        self._bucket_of(m)
        self._resolve_pending(m)
        return self._touch(m, self.cur_open, l)
