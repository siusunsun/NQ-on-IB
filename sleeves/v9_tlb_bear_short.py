"""v9_tlb_bear_short.py — TLB BEAR SHORT sleeve (paper forward test in Strategy A, armed 2026-09-16).

Pure detector, no IB. Mirrors `smc-backtest/scratch_tlb_short_bear_only.py` (BB gate) exactly — spec in
`smc-backtest/tlb_bear_short_spec.md`; research record 141 trades / +$55,044 / expR +0.41 / maxDD −$4,976 at 1 NQ
(2021-08..2026-09, measured costs); gate placebo 100th pctile; ex the two bear episodes −$1,235.

THE RULE (short only, BOTH_BEAR days only — the regime gate lives in DailyRegime.active_sleeves):
  15-min bars on UTC quarter-hours (a bucket exists iff a 1-min bar fell in it; index = compact count, gaps dropped —
  the research's resample+dropna). Pivot low at bucket j (k=3): l[j] <= min(l[j-3..j-1]) and l[j] < min(l[j+1..j+3]),
  CONFIRMED when bucket j+3 completes. The LINE = through the last two confirmed pivot lows (j1,v1),(j2,v2); used
  only if RISING (v2 > v1); value at a 1-min bar = v2 + slope*(x - j2) with x = bucket index + minutes-into-bucket/15.
  ACTIVE from the first 1-min bar at/after the confirmation of j2 until the NEXT pivot low confirms (any slope).
  VALID only if the last 1-min close before activation is ABOVE the line at the activation bar. ONE entry per line:
  the FIRST 1-min bar (any session) whose LOW <= line consumes it; modelled fill = min(open of that minute, line) —
  live = the first 5-sec bar whose low <= the minute's line value -> MKT sell. ATR = mean true range of the 14 completed
  1-min bars BEFORE the touch minute. Stop = fill + k*ATR, target = fill - RR*k*ATR, k/RR per session (PRE 08:00-09:30
  ET 4/4 · OPEN 09:30-11:00 3/2 · MIDDAY 11:00-14:00 4/3); flat at the session end; 1 per session, 3 per day, single
  position; no minimum slope (tested: the flattest lines are the best quartile).
PARITY HAZARDS (documented, accepted): the bot's server-side stop can fire inside the entry minute (the research stops
the entry bar only on its close); if the :14 minute of a bucket has no trades the bucket completes on the next bar and
the new line activates one minute late; IB 1-min bars exist only for minutes with trades (the store's do too).
"""
from __future__ import annotations
from collections import deque
from datetime import datetime
from typing import Optional
import pandas as pd
import pytz

ET = pytz.timezone("America/New_York")
TBS_NAME = "TLB_BEAR_SHORT"
# session -> ((start_min, end_min) ET, k stop in ATR, reward ratio)
SESSIONS = {"PRE": ((480, 570), 4.0, 4.0), "OPEN": ((570, 660), 3.0, 2.0), "MIDDAY": ((660, 840), 4.0, 3.0)}
MAX_PER_SESSION, MAX_PER_DAY = 1, 3


def session_at(et_dt: datetime) -> Optional[str]:
    """Traded session containing an ET datetime, else None."""
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


def sessions_done_from_fills(fills: list, date_et: str, sleeve: str = TBS_NAME) -> set:
    """Restart-safety for the per-session cap: sessions already ENTERED today, read back from the trade log
    (record_v9_fill stamps UTC). Pure function of the fills list."""
    done = set()
    for f in fills or []:
        if f.get("instrument") != sleeve or not str(f.get("label", "")).endswith("_ENTRY"):
            continue
        try:
            t = pd.Timestamp(f["time"]).tz_localize("UTC").tz_convert(ET)
        except Exception:
            continue
        if t.strftime("%Y-%m-%d") == date_et:
            s = session_at(t.to_pydatetime())
            if s:
                done.add(s)
    return done


class TlbBearShortLive:
    """Causal state machine over completed 1-min bars (+ an optional 5-sec touch path for live entries)."""

    def __init__(self, k: int = 3, atr_len: int = 14, line_min: int = 15):
        self.k = k; self.atr_len = atr_len; self.line_min = line_min
        # 15-min buckets: keys in creation order (index = position); completed lows/highs; forming bucket
        self.keys: list[pd.Timestamp] = []
        self.idx: dict[pd.Timestamp, int] = {}
        self.l15: list[float] = []
        self.h15: list[float] = []
        self.cur: Optional[dict] = None            # forming bucket: {key, h, l, done}
        # 1-min
        self.prev_close: Optional[float] = None    # last COMPLETED 1-min close
        self.tr: deque = deque(maxlen=atr_len)
        self.cur_min: Optional[pd.Timestamp] = None
        self.cur_open: Optional[float] = None      # open of the forming minute (from the first 5-sec bar)
        # pivots + the single candidate line
        self.pivots: list[tuple[int, float]] = []  # (bucket index, low) confirmed, in order
        self.line: Optional[dict] = None           # {j1,v1,j2,v2,slope,pending,valid,consumed,act_min}
        self.n_lines = 0; self.n_consumed = 0

    # ---------------- helpers ----------------
    @property
    def atr(self) -> Optional[float]:
        if len(self.tr) < self.atr_len:
            return None
        return float(sum(self.tr) / self.atr_len)

    def _bucket_of(self, ts: pd.Timestamp) -> int:
        """Index of the bucket containing ts, registering it (compact count) if new."""
        key = ts.floor(f"{self.line_min}min")
        i = self.idx.get(key)
        if i is None:
            i = len(self.keys); self.keys.append(key); self.idx[key] = i
        return i

    def _x(self, ts: pd.Timestamp) -> float:
        key = ts.floor(f"{self.line_min}min")
        return self._bucket_of(ts) + (ts - key).total_seconds() / 60.0 / self.line_min

    def line_value(self, ts) -> Optional[float]:
        """The active line's value at the MINUTE containing ts (None if no active, valid, unconsumed line)."""
        L = self.line
        if L is None or L["pending"] or not L["valid"] or L["consumed"]:
            return None
        ts = pd.Timestamp(ts).floor("1min")
        return L["v2"] + L["slope"] * (self._x(ts) - L["j2"])

    def _complete_bucket(self) -> None:
        """The forming bucket is done: append its low/high, reveal a pivot confirmed by it, (re)build the line."""
        if self.cur is None or self.cur["done"]:
            return
        self.cur["done"] = True
        self.l15.append(self.cur["l"]); self.h15.append(self.cur["h"])
        n = len(self.l15); k = self.k
        rev = n - 1 - k                              # the bucket whose pivot status this completion reveals
        if rev < k:
            return
        l = self.l15
        if l[rev] <= min(l[rev - k:rev]) and l[rev] < min(l[rev + 1:rev + k + 1]):
            self.pivots.append((rev, l[rev]))
            # ANY new pivot low retires the current line (the research's z = the next pivot's confirmation)
            self.line = None
            if len(self.pivots) >= 2:
                (j1, v1), (j2, v2) = self.pivots[-2], self.pivots[-1]
                if v2 > v1:                          # rising only
                    self.n_lines += 1
                    self.line = dict(j1=j1, v1=v1, j2=j2, v2=v2, slope=(v2 - v1) / (j2 - j1),
                                     pending=True, valid=False, consumed=False, act_min=None)

    def _resolve_pending(self, ts_min: pd.Timestamp) -> None:
        """Activation bar a = this minute: valid iff the last completed 1-min close is ABOVE the line at x(a)."""
        L = self.line
        if L is None or not L["pending"]:
            return
        L["pending"] = False; L["act_min"] = ts_min
        lv = L["v2"] + L["slope"] * (self._x(ts_min) - L["j2"])
        L["valid"] = self.prev_close is not None and self.prev_close > lv
        if not L["valid"]:
            self.line = None                         # already broken at activation -> discarded

    def _touch(self, ts_min: pd.Timestamp, open_px: float, low: float) -> Optional[dict]:
        """First touch of the active line by a bar of this minute -> consume, return the event (any session)."""
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

    # ---------------- the two feeds ----------------
    def on_1min(self, ts, o: float, h: float, l: float, c: float, live: bool = False) -> Optional[dict]:
        """A COMPLETED 1-min bar (warm-up from IB history, or the live 1-min aggregator). Maintains buckets,
        pivots, lines and the ATR. Returns the touch event when this bar's LOW consumes the line — in live mode
        the 5-sec path has normally consumed it already, so this is the warm-up/replay path (no orders)."""
        ts = pd.Timestamp(ts); ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        ts = ts.floor("1min")
        key = ts.floor(f"{self.line_min}min")
        if self.cur is not None and self.cur["key"] != key:
            self._complete_bucket()                  # rollover (the :14 bar was missing or fast-complete not run)
        if self.cur is None or self.cur["key"] != key:
            self._bucket_of(ts)
            self.cur = dict(key=key, h=h, l=l, done=False)
        else:
            self.cur["h"] = max(self.cur["h"], h); self.cur["l"] = min(self.cur["l"], l)
        self._resolve_pending(ts)
        ev = None if live else self._touch(ts, o, l)
        # ATR/prev_close update AFTER the checks: the touch minute uses the 14 bars BEFORE it
        tr = h - l if self.prev_close is None else max(h - l, abs(h - self.prev_close), abs(l - self.prev_close))
        self.tr.append(tr); self.prev_close = c
        if ts.minute % self.line_min == self.line_min - 1:
            self._complete_bucket()                  # fast completion on the bucket's last minute
        return ev

    def on_5s(self, ts, o: float, h: float, l: float, c: float) -> Optional[dict]:
        """A live 5-sec bar: tracks the forming minute's open and fires the touch on the first 5-sec low <= line."""
        ts = pd.Timestamp(ts); ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        m = ts.floor("1min")
        if self.cur_min != m:
            self.cur_min, self.cur_open = m, o
        self._bucket_of(m)
        self._resolve_pending(m)
        return self._touch(m, self.cur_open, l)
