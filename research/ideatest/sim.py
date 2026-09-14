"""Management-capable portfolio simulator for the Codex idea tests (see PREREG.md).

Reuses the verified detectors / regime table from repro/repro/backtest.py unchanged and
re-implements the portfolio loop with hooks for exits, partial cuts, skips and admission.
With Rules() (nothing active) it must reproduce repro/out/trades_*.csv exactly (gate 0).
"""
from __future__ import annotations

import heapq
import math
import pickle
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPRO = HERE.parent / "repro"
sys.path.insert(0, str(REPRO / "repro"))
import backtest as bt  # noqa: E402

CACHE = HERE / "cache" / "signals.pkl"
BASE_EQUITY = 22_500.0
COST = 5.40          # $ per contract round-turn, all-in
V = 2.0              # $ per point, MNQ
COST_PTS = COST / V  # 2.7 pt, fixed inside rules
SIZE = {"VWAP_LONG_R3": 2, "VWAP_SHORT_R1": 1, "TLB_HYB40": 1, "R3_RE": 1}
NAMES = ("VWAP_LONG_R3", "VWAP_SHORT_R1", "TLB_HYB40")


# ----------------------------------------------------------------------------- data
class D:
    """Module-level data holder (filled by load())."""
    b1 = b5 = None
    vwap5 = upper5 = None
    lookup5 = None
    cells = holidays = None
    signals = None        # list of (i, name, Signal, pivots or None)
    q_recent = q_prior = q_ok = None   # #6 downside semivariance
    sig60 = None                       # #8 sigma of last 60 increments


def _prepare():
    raw = pd.read_csv(REPRO / "data" / "nq_1min.csv.gz")
    raw.index = pd.DatetimeIndex(pd.to_datetime(raw.pop("time"), utc=True))
    b1 = bt.Bars.from_frame(raw)
    grouped = raw.groupby(raw.index.floor("5min"), sort=True).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    b5 = bt.Bars.from_frame(grouped.iloc[:-1])
    holidays = set(pd.read_csv(REPRO / "data" / "no_trade_dates.csv").et_date)
    _, cells, _ = bt.regime_table(b1)

    long_det, short_det, tlb_det = bt.VWAPDetector("LONG"), bt.VWAPDetector("SHORT"), bt.TLBDetector()
    overlay = bt.SessionVWAP()
    vwap5 = np.full(len(b5.t), np.nan)
    upper5 = np.full(len(b5.t), np.nan)
    signals = []
    for i, t in enumerate(b5.t):
        args = (t, b5.h[i], b5.l[i], b5.c[i], b5.v[i])
        _, vw, sd = overlay.update(*args)
        vwap5[i], upper5[i] = vw, vw + 2 * sd
        sl, ss = long_det.update(*args), short_det.update(*args)
        st = tlb_det.update(b5.h[i], b5.l[i], b5.c[i])
        if sl is not None:
            signals.append((i, "VWAP_LONG_R3", sl, None))
        if ss is not None:
            signals.append((i, "VWAP_SHORT_R1", ss, None))
        if st is not None:
            signals.append((i, "TLB_HYB40", st, tuple(tlb_det.pivot_highs)))
    CACHE.parent.mkdir(exist_ok=True)
    with open(CACHE, "wb") as f:
        pickle.dump(dict(b1=b1, b5=b5, vwap5=vwap5, upper5=upper5, cells=cells,
                         holidays=holidays, signals=signals), f, protocol=5)


def load():
    if D.b1 is not None:
        return
    if not CACHE.exists():
        _prepare()
    with open(CACHE, "rb") as f:
        d = pickle.load(f)
    for k, v in d.items():
        setattr(D, k, v)
    D.lookup5 = {int(t): i for i, t in enumerate(D.b5.t)}
    c, t = D.b1.c, D.b1.t
    x = np.zeros(len(c))
    x[1:] = np.diff(c)
    gap = np.zeros(len(c), dtype=np.int64)
    gap[1:] = (np.diff(t) > 30).astype(np.int64)
    gap[0] = 1
    neg2 = np.minimum(x, 0.0) ** 2
    cs = np.concatenate([[0.0], np.cumsum(neg2)])      # cs[k+1] = sum neg2[0..k]
    cg = np.concatenate([[0], np.cumsum(gap)])
    k = np.arange(len(c))
    ok = k >= 70
    lo = np.clip(k - 69, 0, None)
    ok &= (cg[k + 1] - cg[lo]) == 0                  # no gap across increments k-69..k
    D.q_recent = (cs[k + 1] - cs[np.clip(k - 9, 0, None)]) / 10.0
    D.q_prior = (cs[np.clip(k - 9, 0, None)] - cs[lo]) / 60.0
    D.q_ok = ok
    sq = np.concatenate([[0.0], np.cumsum(x * x)])
    lo60 = np.clip(k - 59, 0, None)
    D.sig60 = np.where(k >= 60, np.sqrt((sq[k + 1] - sq[lo60]) / 60.0), np.nan)


# ----------------------------------------------------------------------------- model
@dataclass
class Rules:
    c1: bool = False
    c2: bool = False
    c3: bool = False
    c4: bool = False
    c5: bool = False
    c6: bool = False
    c8: bool = False
    c9: bool = False
    r3_qty: int = 2                                   # B1 sets 1
    rand_exit: dict = field(default_factory=dict)     # key -> decision time (bucket end / 1-min close)
    rand_cut: dict = field(default_factory=dict)      # key -> 1-min bar start time (C6 null)
    rand_skip: set = field(default_factory=set)       # keys (C9 null)
    record: bool = False                              # collect decision points (control run)
    c7_events: tuple = ()                             # C7: event times, UTC epoch minutes (real or pseudo)


@dataclass
class Trade:
    sleeve: str
    key: tuple
    entry_time: int
    entry: float
    stop: float
    target: float
    s: int
    qty: int
    legs: list = field(default_factory=list)          # (qty, px, time, reason)
    parent_key: tuple | None = None

    # legs are appended in execution order; the last one is terminal (a cut and a stop can share a minute)
    @property
    def exit_time(self):
        return self.legs[-1][2]

    @property
    def reason(self):
        return self.legs[-1][3]

    @property
    def exit(self):
        return self.legs[-1][1]

    @property
    def pts(self):   # contract-weighted points
        return sum(q * self.s * (px - self.entry) for q, px, _, _ in self.legs)

    def gross(self):
        return self.pts * V


class Log:
    def __init__(self):
        self.acts = []      # executed management actions: (rule, sleeve, key, dec_time, age, clock)
        self.points = []    # recorded decision points (control): (kind, sleeve, key, dec_time, age, clock, losing, qty)
        self.skips = []     # (rule, sleeve, key)
        self.admit = []     # C5: (sleeve, key, requested, admitted)


def _settle_pending(o, stop, tgt, long):
    gap_stop = o <= stop if long else o >= stop
    gap_tgt = (o >= tgt if long else o <= tgt) if not math.isnan(tgt) else False
    return not (gap_stop or gap_tgt)


class C7:
    """Event state for one run: exit-trigger bars and entry-suspension windows."""
    flag = None          # bool per 1-min row: last completed close at or before e-5
    starts = ends = None

    @classmethod
    def setup(cls, events):
        if not events:
            cls.flag = None
            return
        t = D.b1.t
        ev = np.array(sorted(set(int(e) for e in events)), dtype=np.int64)
        cls.flag = np.zeros(len(t), dtype=bool)
        # a bar starting at t closes at t+1; we need the last bar whose close t+1 <= e-5 -> t <= e-6
        k = np.searchsorted(t, ev - 6, side="right") - 1
        cls.flag[k[k >= 0]] = True
        cls.starts, cls.ends = ev - 5, ev + 10

    @classmethod
    def overlaps(cls, a, b):
        """True if [a, b) overlaps any suspension window [e-5, e+10)."""
        if cls.flag is None:
            return False
        i = int(np.searchsorted(cls.starts, b, side="left"))   # windows starting before b
        return bool(i > 0 and cls.ends[i - 1] > a)   # windows are disjoint and sorted


def sim_vwap(sig, entry_time, qty, sleeve, key, R: Rules, log: Log):
    b = D.b1
    t, o, h, l, c, minute = b.t, b.o, b.h, b.l, b.c, b.minute
    n = len(t)
    start = int(np.searchsorted(t, entry_time, side="left"))
    long = sig.direction == "LONG"
    s = 1 if long else -1
    E, stop, tgt, risk = sig.entry, sig.stop, sig.target, sig.risk
    tr = Trade(sleeve, key, int(entry_time), E, stop, tgt, s, qty)
    q = qty
    pend_q, pend_reason, pend_info = 0, None, None
    mfe, nb, reduced = 0.0, 0, False
    is_parent = sleeve == "VWAP_LONG_R3"
    for k in range(start, n):
        if pend_q:
            # mandatory flattening takes precedence over a pending discretionary order
            if not (955 <= minute[k] < 1020) and _settle_pending(o[k], stop, tgt, long):
                tr.legs.append((pend_q, float(o[k]), int(t[k]), pend_reason))
                log.acts.append(pend_info)
                q -= pend_q
                if q == 0:
                    return tr
            pend_q = 0
        if (l[k] <= stop) if long else (h[k] >= stop):
            tr.legs.append((q, float(stop), int(t[k]), "STOP"))
            return tr
        if (h[k] >= tgt) if long else (l[k] <= tgt):
            tr.legs.append((q, float(tgt), int(t[k]), "TARGET"))
            return tr
        if 955 <= minute[k] < 1020:
            tr.legs.append((q, float(c[k]), int(t[k]), "GAP_FLATTEN"))
            return tr
        mfe = max(mfe, (h[k] - E) if long else (E - l[k]))
        C = c[k]
        u = s * (C - E) / risk
        age_min = int(t[k] - entry_time + 1)
        if C7.flag is not None and C7.flag[k] and not pend_q:
            pend_q, pend_reason = q, "MANAGEMENT_EXIT"
            pend_info = ("c7", sleeve, key, int(t[k] + 1), age_min, int(minute[k] + 1))
            continue
        # ---- 1-minute decisions (#6)
        if is_parent and q == 2 and not reduced and not pend_q:
            if R.record:
                log.points.append(("m1", sleeve, key, int(t[k]), age_min, 0, C < E, q))
            fire = False
            if R.c6 and D.q_ok[k] and D.q_prior[k] > 0 and D.q_recent[k] >= 4 * D.q_prior[k] and C < E:
                fire = True
            if R.rand_cut.get(key) == int(t[k]):
                fire = True
            if fire:
                pend_q, pend_reason, reduced = 1, "MGMT_CUT", True
                pend_info = ("cut", sleeve, key, int(t[k]), age_min, 0)
        # ---- 5-minute decisions
        if k + 1 == n or t[k + 1] // 5 != t[k] // 5:
            nb += 1
            bstart = int(t[k] // 5 * 5)
            dec = bstart + 5
            em = int(minute[k] // 5 * 5 + 5)
            jb = D.lookup5.get(bstart)
            if R.record:
                log.points.append(("b5", sleeve, key, dec, nb, em, u < 0, q))
            fire = None
            if R.c1 and is_parent and nb >= 15 and mfe < 0.25 * risk and u <= -0.50:
                fire = "c1"
            if (not fire and R.c2 and nb >= 6 and jb is not None and jb >= 6
                    and D.b5.t[jb] // 1440 == D.b5.t[jb - 6] // 1440):
                vt, v6 = D.vwap5[jb], D.vwap5[jb - 6]
                if (not math.isnan(vt) and not math.isnan(v6) and s * (vt - v6) <= -0.25 * risk
                        and s * (C - vt) < 0 and u <= -0.25):
                    fire = "c2"
            if not fire and R.c8 and 925 <= em < 955:
                m = 955 - em
                sg = D.sig60[k]
                if s * (C - E) < 0 and sg > 0 and -s * (C - E) > 2 * sg * math.sqrt(m):
                    fire = "c8"
            if not fire and R.rand_exit.get(key) == dec:
                fire = "rand"
            if fire:
                pend_q, pend_reason = q, "MANAGEMENT_EXIT"
                pend_info = (fire, sleeve, key, dec, nb, em)
    tr.legs.append((q, float(c[-1]), int(t[-1]), "DATA_END"))
    return tr


def sim_tlb(sig, i, pivots, key, R: Rules, log: Log):
    b5, b1 = D.b5, D.b1
    entry_time = int(b5.t[i] + 5)
    E, stop, tgt, risk = sig.entry, sig.stop, sig.target, sig.risk
    tr = Trade("TLB_HYB40", key, entry_time, E, stop, tgt, 1, 1)
    (i1, v1), (i2, v2) = pivots
    slope = (v2 - v1) / max(1, i2 - i1)
    mfe, nb, prev_below = 0.0, 0, False
    for k in range(i + 1, len(b5.t)):
        if b5.l[k] <= stop:
            tr.legs.append((1, float(stop), int(b5.t[k] + 5), "STOP"))
            return tr
        if b5.h[k] >= tgt:
            tr.legs.append((1, float(tgt), int(b5.t[k] + 5), "TARGET"))
            return tr
        if b5.minute[k] >= 920:
            tr.legs.append((1, float(b5.c[k]), int(b5.t[k] + 5), "EOD"))
            return tr
        mfe = max(mfe, b5.h[k] - E)
        nb += 1
        C = b5.c[k]
        u = (C - E) / risk
        dec = int(b5.t[k] + 5)
        if R.record:
            log.points.append(("b5", "TLB_HYB40", key, dec, nb, int(b5.minute[k] + 5), u < 0, 1))
        below = C < v2 + slope * (k - i2) - 0.25 * risk
        fire = None
        if R.c3 and nb >= 2 and below and prev_below and mfe < 0.50 * risk:
            fire = "c3"
        prev_below = below
        if not fire and R.rand_exit.get(key) == dec:
            fire = "rand"
        if fire:
            k1 = int(np.searchsorted(b1.t, dec, side="left"))
            if k1 < len(b1.t) and _settle_pending(b1.o[k1], stop, tgt, True):
                tr.legs.append((1, float(b1.o[k1]), int(b1.t[k1]), "MANAGEMENT_EXIT"))
                log.acts.append((fire, "TLB_HYB40", key, dec, nb, int(b5.minute[k] + 5)))
                return tr
    tr.legs.append((1, float(b5.c[-1]), int(b5.t[-1] + 5), "DATA_END"))
    return tr


def sim_reentry(sig, entry_time, sess, kind, key, parent_key, R: Rules, log: Log):
    b1, b5 = D.b1, D.b5
    t, o, h, l, c, minute = b1.t, b1.o, b1.h, b1.l, b1.c, b1.minute
    n = len(t)
    start = int(np.searchsorted(t, entry_time, side="left"))
    levels = D.vwap5 if kind == "VWAP" else D.upper5
    E, S0 = sig.entry, sig.stop
    risk = E - S0
    tr = Trade("R3_RE", key, int(entry_time), E, S0, math.nan, 1, 1, parent_key=parent_key)
    pending, pend_info, streak, nb = False, None, 0, 0
    for k in range(start, n):
        if t[k] // 1440 != sess:
            price = sig.entry if k == start else c[k - 1]
            stamp = t[k - 1] if k > 0 else entry_time
            tr.legs.append((1, float(price), int(stamp), "SESS_END"))
            return tr
        lag_index = D.lookup5.get(int(t[k] // 5 * 5 - 5))
        lvl = math.nan if lag_index is None else levels[lag_index]
        if pending:
            tgt_gap = not math.isnan(lvl) and lvl > E and o[k] >= lvl
            if not (o[k] <= S0 or tgt_gap) and minute[k] < 955:
                tr.legs.append((1, float(o[k]), int(t[k]), "MANAGEMENT_EXIT"))
                log.acts.append(pend_info)
                return tr
            pending = False
        if minute[k] >= 955:
            tr.legs.append((1, float(c[k]), int(t[k]), "FLATTEN"))
            return tr
        if l[k] <= S0:
            tr.legs.append((1, float(S0), int(t[k]), "STOP"))
            return tr
        if not math.isnan(lvl) and lvl > E and h[k] >= lvl:
            tr.legs.append((1, float(lvl), int(t[k]), "TARGET"))
            return tr
        if C7.flag is not None and C7.flag[k] and not pending:
            pending = True
            pend_info = ("c7", "R3_RE", key, int(t[k] + 1), 0, int(minute[k] + 1))
            continue
        if k + 1 == n or t[k + 1] // 5 != t[k] // 5:
            nb += 1
            bstart = int(t[k] // 5 * 5)
            dec = bstart + 5
            C = c[k]
            u = (C - E) / risk
            if R.record:
                log.points.append(("b5", "R3_RE", key, dec, nb, int(minute[k] // 5 * 5 + 5), u < 0, 1))
            fire = None
            if R.c4:
                jb = D.lookup5.get(bstart)
                tt = math.nan if jb is None else levels[jb]
                if math.isnan(tt):
                    streak = 0
                else:
                    G = max(tt - C, 0.0)
                    Dd = max(C - S0, 0.0)
                    streak = streak + 1 if (u <= 0 and G <= 0.5 * Dd + COST_PTS) else 0
                    if streak >= 2:
                        fire = "c4"
            if not fire and R.rand_exit.get(key) == dec:
                fire = "rand"
            if fire:
                pending = True
                pend_info = (fire, "R3_RE", key, dec, nb, int(minute[k] // 5 * 5 + 5))
    tr.legs.append((1, float(c[-1]), int(t[-1]), "DATA_END"))
    return tr


def reentry_for(parent: Trade, R: Rules, log: Log):
    """Mirror of backtest.reentries() for one parent; returns a Trade or None."""
    if parent.reason != "STOP":
        return None
    b5 = D.b5
    si = D.lookup5.get((parent.entry_time - 5) // 5 * 5)
    ki = D.lookup5.get(parent.exit_time // 5 * 5)
    if si is None or ki is None:
        return None
    sess = b5.t[ki] // 1440
    j, scanned = ki + 2, 0
    while j < len(b5.t) and b5.t[j] // 1440 == sess and b5.minute[j] < 955:
        if scanned >= 48:
            return None
        runmax = float(np.max(b5.h[ki:j]))
        if b5.h[j] > runmax and C7.overlaps(int(b5.t[j]), int(b5.t[j] + 5)):
            log.skips.append(("c7re", "R3_RE", ("R3_RE", int(j), parent.key[1])))
            j += 1
            scanned += 1
            continue
        if b5.h[j] > runmax:
            fill = max(float(b5.o[j]), runmax)
            stop = float(np.min(b5.l[si:j]))
            lag = D.vwap5[j - 1]
            if math.isnan(lag):
                j += 1
                scanned += 1
                continue
            kind = "VWAP" if fill < lag else "UPPER"
            if fill - stop <= 0:
                return None
            sig = bt.Signal("LONG", fill, stop, math.nan)
            key = ("R3_RE", int(j), parent.key[1])
            return sim_reentry(sig, int(b5.t[j] + 5), sess, kind, key, parent.key, R, log)
        j += 1
        scanned += 1
    return None


# ----------------------------------------------------------------------------- C5 equity
class Book:
    def __init__(self):
        self.trades = []

    def equity_and_reserve(self, T, P):
        cash, marked, reserve = BASE_EQUITY, 0.0, 0.0
        for tr in self.trades:
            if tr.entry_time > T:
                continue
            open_q = tr.qty
            for q, px, tm, _ in tr.legs:
                if tm <= T:
                    cash += q * tr.s * (px - tr.entry) * V - q * COST
                    open_q -= q
            if open_q > 0:
                marked += open_q * tr.s * (P - tr.entry) * V
                reserve += open_q * (V * max(tr.s * (P - tr.stop), 0.0) + COST)
        return cash + marked, reserve


def admit_qty(book: Book, T, requested, s, stop_new):
    k = int(np.searchsorted(D.b1.t, T, side="left")) - 1
    P = float(D.b1.c[k])
    A, reserve = book.equity_and_reserve(T, P)
    per = V * max(s * (P - stop_new), 0.0) + COST
    cap = 0.01 * max(A, 0.0)
    for q in range(requested, -1, -1):
        if reserve + q * per <= cap + 1e-9:
            return q
    return 0


# ----------------------------------------------------------------------------- portfolio
WINDOW_VWAP = lambda m: 0 <= m < 360 or 570 <= m < 720 or 720 <= m < 870 or 1140 <= m < 1440  # noqa: E731
WINDOW_TLB = lambda m: 600 <= m < 715 or 895 <= m < 920  # noqa: E731
C5_ORDER = {"VWAP_LONG_R3": 0, "TLB_HYB40": 1, "VWAP_SHORT_R1": 2, "R3_RE": 3}
CTRL_ORDER = {"VWAP_LONG_R3": 0, "VWAP_SHORT_R1": 1, "TLB_HYB40": 2}


def run(R: Rules | None = None):
    load()
    R = R or Rules()
    log = Log()
    C7.setup(R.c7_events)
    b5 = D.b5
    busy = {name: -math.inf for name in NAMES}
    kept = []                    # all realised trades (base + re-entry)
    book = Book()
    pending_re = []              # heap of (entry_time, seq, trade) — C5 only
    seq = 0
    order = C5_ORDER if R.c5 else CTRL_ORDER
    sigs = sorted(D.signals, key=lambda x: (x[0], order[x[1]]))

    def flush_re(upto, inclusive):
        while pending_re and (pending_re[0][0] < upto or (inclusive and pending_re[0][0] == upto)):
            _, _, tr = heapq.heappop(pending_re)
            q = admit_qty(book, tr.entry_time, 1, 1, tr.stop)
            log.admit.append(("R3_RE", tr.key, 1, q))
            if q:
                kept.append(tr)
                book.trades.append(tr)

    for idx, (i, name, sig, piv) in enumerate(sigs):
        m = b5.minute[i]
        T = int(b5.t[i] + 5)
        if R.c5:
            flush_re(T, False)
        cell = D.cells.get(b5.dates[i])
        allowed = bt.ALLOWED.get(cell, set())
        sleeve = "TLB_LONG" if name == "TLB_HYB40" else name
        take = (sleeve in allowed and b5.dates[i] not in D.holidays
                and (WINDOW_TLB(m) if name == "TLB_HYB40" else WINDOW_VWAP(m)))
        if take and name == "TLB_HYB40" and (sig.entry - sig.stop) * 2.0 > 3000:
            take = False
        if take and T < busy[name]:
            take = False
        if take and name != "TLB_HYB40" and C7.overlaps(T, T + 1):
            log.skips.append(("c7", name, (name, int(i))))
            take = False
        if take:
            key = (name, int(i))
            if R.c9 and name in ("TLB_HYB40", "VWAP_SHORT_R1"):
                k = int(np.searchsorted(D.b1.t, T, side="left")) - 1
                P = float(D.b1.c[k])
                rplan = (P - sig.stop) if sig.direction == "LONG" else (sig.stop - P)
                if rplan <= 0 or V * rplan < 5 * COST:
                    log.skips.append(("c9", name, key))
                    take = False
            if take and key in R.rand_skip:
                log.skips.append(("rand", name, key))
                take = False
        if take:
            qty = SIZE[name] if name != "VWAP_LONG_R3" else R.r3_qty
            if name == "TLB_HYB40":
                tr = sim_tlb(sig, i, piv, key, R, log)
            else:
                tr = sim_vwap(sig, T, qty, name, key, R, log)
            busy[name] = tr.exit_time
            capped = name != "TLB_HYB40" and sig.risk > 200
            if not capped and R.c5:
                q = admit_qty(book, T, qty, 1 if sig.direction == "LONG" else -1, sig.stop)
                log.admit.append((name, key, qty, q))
                if q == 0:
                    busy[name] = -math.inf   # skipped: sleeve not occupied
                    capped = True
                elif q < qty:
                    tr = (sim_tlb(sig, i, piv, key, R, log) if name == "TLB_HYB40"
                          else sim_vwap(sig, T, q, name, key, R, log))
                    tr.qty = q
                    if name == "TLB_HYB40":
                        tr.legs = [(q, *leg[1:]) for leg in tr.legs]
            if not capped:
                kept.append(tr)
                book.trades.append(tr)
                if name == "VWAP_LONG_R3":
                    re = reentry_for(tr, R, log)
                    if re is not None:
                        if R.c5:
                            seq += 1
                            heapq.heappush(pending_re, (re.entry_time, seq, re))
                        else:
                            kept.append(re)
        if R.c5:
            nxt = sigs[idx + 1][0] if idx + 1 < len(sigs) else None
            if nxt is None or int(b5.t[nxt] + 5) > T:
                flush_re(T, True)
    if R.c5:
        flush_re(math.inf, True)
    return kept, log


# ----------------------------------------------------------------------------- outputs
_ET_CACHE = {}


def et_date(minute_utc):
    return pd.Timestamp(int(minute_utc), unit="m", tz="UTC").tz_convert("America/New_York").strftime("%Y-%m-%d")


def daily_pnl(trades, cost_mult=1.0):
    """Net $ by ET date of each exit leg (cost charged per contract on its exit leg)."""
    rows = []
    for tr in trades:
        for q, px, tm, _ in tr.legs:
            rows.append((tm, q * tr.s * (px - tr.entry) * V - q * COST * cost_mult))
    if not rows:
        return pd.Series(dtype=float)
    tm = np.array([r[0] for r in rows], dtype=np.int64)
    pnl = np.array([r[1] for r in rows])
    uniq, inv = np.unique(tm, return_inverse=True)
    dates = np.array([_ET_CACHE.setdefault(int(u), et_date(u)) for u in uniq])[inv]
    return pd.Series(pnl).groupby(dates).sum()


def trade_frame(trades):
    rows = []
    for tr in trades:
        rows.append(dict(sleeve=tr.sleeve, key=":".join(map(str, tr.key)), entry_time=tr.entry_time,
                         exit_time=tr.exit_time, entry=tr.entry, exit=tr.exit, reason=tr.reason,
                         qty=tr.qty, pts=tr.pts, legs=len(tr.legs),
                         parent=None if tr.parent_key is None else ":".join(map(str, tr.parent_key))))
    return pd.DataFrame(rows)
