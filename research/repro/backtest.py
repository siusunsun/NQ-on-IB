"""Independent, literal implementation of REPRO_SPEC.md. Run from any directory."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
import math
import time

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
TRADE_COLUMNS = [
    "entry_time_utc", "exit_time_utc", "direction", "entry_px", "exit_px",
    "exit_reason", "pnl_points",
]
ALLOWED = {
    "BOTH_BULL": {"TLB_LONG", "VWAP_LONG_R3"},
    "ZONE_A": {"VWAP_LONG_R3"},
    "ZONE_B": {"TLB_LONG"},
    "BOTH_BEAR": {"VWAP_SHORT_R1"},
}


@dataclass
class Bars:
    # All times are integer UTC minutes since the Unix epoch.
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    dates: np.ndarray
    minute: np.ndarray

    @classmethod
    def from_frame(cls, frame):
        idx = frame.index
        et = idx.tz_convert("America/New_York")
        return cls(
            idx.as_unit("ns").asi8 // 60_000_000_000,
            *[frame[col].to_numpy(dtype=np.float64) for col in
              ("open", "high", "low", "close", "volume")],
            np.asarray(et.strftime("%Y-%m-%d")),
            np.asarray(et.hour * 60 + et.minute, dtype=np.int16),
        )


@dataclass
class Signal:
    direction: str
    entry: float
    stop: float
    target: float

    @property
    def risk(self):
        return abs(self.entry - self.stop)


@dataclass
class Trade:
    entry_time: int
    exit_time: int
    direction: str
    entry: float
    exit: float
    reason: str
    stop: float

    @property
    def pnl(self):
        return (self.exit - self.entry) * (1 if self.direction == "LONG" else -1)


class SessionVWAP:
    def __init__(self):
        self.session = None
        self.cumv = self.cumvp = self.cumsq = 0.0

    def update(self, t, high, low, close, volume):
        session = int(t) // 1440
        changed = session != self.session
        if changed:
            self.session = session
            self.cumv = self.cumvp = self.cumsq = 0.0
        tp = (float(high) + float(low) + float(close)) / 3
        volume = float(volume)
        self.cumv += volume
        self.cumvp += tp * volume
        self.cumsq += tp * tp * volume
        if self.cumv <= 0:
            return changed, math.nan, math.nan
        vwap = self.cumvp / self.cumv
        sd = math.sqrt(max(self.cumsq / self.cumv - vwap * vwap, 0.0))
        return changed, vwap, sd


class VWAPDetector:
    def __init__(self, direction):
        self.direction = direction
        self.sums = SessionVWAP()
        self.streak = 0
        self.lows = deque(maxlen=20)
        self.highs = deque(maxlen=20)
        self.last_bar_time = None
        self.bars_since_halt = 0

    def update(self, t, high, low, close, volume):
        # update() resets the session sums before adding the current bar.
        changed, vwap, sd = self.sums.update(t, high, low, close, volume)
        if changed:
            self.streak = 0
        if self.last_bar_time is not None and t - self.last_bar_time > 180:
            self.lows.clear()
            self.highs.clear()
            self.bars_since_halt = 0
        self.bars_since_halt += 1
        self.last_bar_time = t
        self.lows.append(float(low))
        self.highs.append(float(high))
        if math.isnan(vwap) or math.isnan(sd) or sd == 0:
            return None
        if self.direction == "LONG" and self.bars_since_halt < 24:
            return None
        long = self.direction == "LONG"
        band = vwap - 2 * sd if long else vwap + 2 * sd
        beyond = close < band if long else close > band
        recross = close > band if long else close < band
        if beyond:
            self.streak += 1
            return None
        threshold = 5 if long else 3
        if recross and self.streak >= threshold:
            history = list(self.lows if long else self.highs)
            prior = history[:-1] if len(history) > 1 else history
            stop = min(prior) if long else max(prior)
            risk = close - stop if long else stop - close
            self.streak = 0
            if risk > 0:
                target = close + 3 * risk if long else close - risk
                return Signal(self.direction, float(close), stop, float(target))
            return None
        self.streak = 0
        return None


class TLBDetector:
    def __init__(self):
        self.h, self.l, self.c = [], [], []
        self.pivot_highs = deque(maxlen=2)
        self.pivot_lows = deque(maxlen=2)
        self.hybrid_replacements = 0

    def update(self, high, low, close):
        self.h.append(float(high))
        self.l.append(float(low))
        self.c.append(float(close))
        n = len(self.c)
        i = n - 4
        if i >= 3:
            if self.h[i] >= max(self.h[i-3:i]) and self.h[i] > max(self.h[i+1:i+4]):
                self.pivot_highs.append((i, self.h[i]))
            if self.l[i] <= min(self.l[i-3:i]) and self.l[i] < min(self.l[i+1:i+4]):
                self.pivot_lows.append((i, self.l[i]))
        if n < 2 or len(self.pivot_highs) < 2 or not self.pivot_lows:
            return None
        (i1, v1), (i2, v2) = self.pivot_highs
        slope = (v2 - v1) / max(1, i2 - i1)
        now, prev = n - 1, n - 2
        if not (self.c[prev] <= v2 + slope * (prev - i2)
                and self.c[now] > v2 + slope * (now - i2)):
            return None
        entry, stop = self.c[now], self.pivot_lows[-1][1]
        risk = entry - stop
        if risk <= 0:
            return None
        if risk < 40:
            new_stop = min(self.l[max(0, now-19):now+1])
            if entry - new_stop > 0:
                stop = new_stop
                self.hybrid_replacements += 1
        return Signal("LONG", entry, stop, entry + (entry - stop))


def make_trade(signal, entry_time, exit_time, price, reason):
    return Trade(int(entry_time), int(exit_time), signal.direction,
                 float(signal.entry), float(price), reason, float(signal.stop))


def exit_vwap(signal, entry_time, b):
    start = int(np.searchsorted(b.t, entry_time, side="left"))
    long = signal.direction == "LONG"
    for k in range(start, len(b.t)):
        stop_hit = b.l[k] <= signal.stop if long else b.h[k] >= signal.stop
        target_hit = b.h[k] >= signal.target if long else b.l[k] <= signal.target
        if stop_hit:
            return make_trade(signal, entry_time, b.t[k], signal.stop, "STOP")
        if target_hit:
            return make_trade(signal, entry_time, b.t[k], signal.target, "TARGET")
        if 955 <= b.minute[k] < 1020:
            return make_trade(signal, entry_time, b.t[k], b.c[k], "GAP_FLATTEN")
    return make_trade(signal, entry_time, b.t[-1], b.c[-1], "DATA_END")


def exit_tlb(signal, signal_index, b):
    entry_time = b.t[signal_index] + 5
    for k in range(signal_index + 1, len(b.t)):
        if b.l[k] <= signal.stop:
            return make_trade(signal, entry_time, b.t[k] + 5, signal.stop, "STOP")
        if b.h[k] >= signal.target:
            return make_trade(signal, entry_time, b.t[k] + 5, signal.target, "TARGET")
        if b.minute[k] >= 920:
            return make_trade(signal, entry_time, b.t[k] + 5, b.c[k], "EOD")
    return make_trade(signal, entry_time, b.t[-1] + 5, b.c[-1], "DATA_END")


def exit_reentry(signal, entry_time, sess, kind, b1, b5, lookup5, vwap5, upper5):
    start = int(np.searchsorted(b1.t, entry_time, side="left"))
    levels = vwap5 if kind == "VWAP" else upper5
    for k in range(start, len(b1.t)):
        if b1.t[k] // 1440 != sess:
            price = signal.entry if k == start else b1.c[k-1]
            # "previous bar's timestamp" means the preceding input row even
            # when the first scanned row belongs to the next session.
            stamp = b1.t[k-1] if k > 0 else entry_time
            return make_trade(signal, entry_time, stamp, price, "SESS_END")
        if b1.minute[k] >= 955:
            return make_trade(signal, entry_time, b1.t[k], b1.c[k], "FLATTEN")
        if b1.l[k] <= signal.stop:
            return make_trade(signal, entry_time, b1.t[k], signal.stop, "STOP")
        lag_index = lookup5.get(int(b1.t[k] // 5 * 5 - 5))
        lvl = math.nan if lag_index is None else levels[lag_index]
        if not math.isnan(lvl) and lvl > signal.entry and b1.h[k] >= lvl:
            return make_trade(signal, entry_time, b1.t[k], lvl, "TARGET")
    return make_trade(signal, entry_time, b1.t[-1], b1.c[-1], "DATA_END")


def reentries(parents, b1, b5, vwap5, upper5):
    lookup = {int(t): i for i, t in enumerate(b5.t)}
    trades = []
    stats = Counter()
    for parent in parents:
        if parent.reason != "STOP":
            continue
        stats["stopped_parents"] += 1
        si = lookup.get((parent.entry_time - 5) // 5 * 5)
        ki = lookup.get(parent.exit_time // 5 * 5)
        if si is None or ki is None:
            stats["missing_parent_bucket"] += 1
            continue
        sess = b5.t[ki] // 1440
        j, scanned = ki + 2, 0
        while j < len(b5.t) and b5.t[j] // 1440 == sess and b5.minute[j] < 955:
            if scanned >= 48:
                stats["wait_limit"] += 1
                break
            runmax = float(np.max(b5.h[ki:j]))
            if b5.h[j] > runmax:
                fill = max(float(b5.o[j]), runmax)
                stop = float(np.min(b5.l[si:j]))
                lag = vwap5[j-1]
                if math.isnan(lag):
                    j += 1
                    scanned += 1
                    continue
                kind = "VWAP" if fill < lag else "UPPER"
                if fill - stop <= 0:
                    stats["nonpositive_risk"] += 1
                    break
                signal = Signal("LONG", fill, stop, math.nan)
                trade = exit_reentry(signal, b5.t[j] + 5, sess, kind,
                                     b1, b5, lookup, vwap5, upper5)
                trades.append(trade)
                stats["kind_" + kind] += 1
                break
            j += 1
            scanned += 1
    return trades, stats


def regime_table(b1):
    daily = pd.Series(b1.c, index=b1.dates).groupby(level=0, sort=True).last()
    rows = []
    values = daily.to_numpy()
    for i in range(205, len(daily)):
        prior = values[:i]
        prev = float(prior[-1])
        sma = float(np.mean(prior[-200:]))
        ret = float(prior[-1] / prior[-21] - 1)
        s200, r20 = prev > sma, ret > 0
        cell = ("BOTH_BULL" if r20 else "ZONE_A") if s200 else (
            "ZONE_B" if r20 else "BOTH_BEAR")
        rows.append((daily.index[i], prev, sma, ret, cell))
    frame = pd.DataFrame(rows, columns=["et_date", "prev_close", "sma200", "ret20", "cell"])
    return frame, dict(zip(frame.et_date, frame.cell)), len(daily)


def utc_text(t):
    return pd.Timestamp(int(t), unit="m", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def write_trades(path, trades):
    rows = [(utc_text(t.entry_time), utc_text(t.exit_time), t.direction, t.entry,
             t.exit, t.reason, t.pnl) for t in trades]
    frame = pd.DataFrame(rows, columns=TRADE_COLUMNS)
    frame.to_csv(path, index=False)
    return frame


AMBIGUITIES = """# Ambiguities and literal resolutions

- **Initial detector state:** initial values are not explicitly listed. Start VWAP sums and streak at zero, price histories empty, last time/session unset, and bars_since_halt at zero. The first observed bar is count 1, so LONG needs 24 observed bars at startup as well as after a halt. A halt alone does not reset streak; only the stated session/price rules do.
- **Short mirror:** interpret the mirror as close > upper accumulating streak, close < upper after at least 3 triggering, risk = stop - entry, and close == upper resetting streak. Apply the stated common NaN/zero-SD early return before changing streak.
- **Regime output coverage:** regime.csv includes only ET dates present in the input with at least 205 prior observed daily closes. Dates with insufficient history have no regime and are omitted, not assigned a cell. Missing calendar dates do not get synthetic closes.
- **DATA_END timestamp:** use the last 1-minute bar's start for VWAP and R3, and the last retained completed 5-minute bar's start + 5 minutes for TLB. TLB's final price is that retained bar's close. The dropped final 5-minute bucket's constituent minute rows remain usable for daily closes and 1-minute exits.
- **R3 session exit on first scanned minute:** the spec provides fill as the price fallback but does not fully define its timestamp. Use the preceding row's timestamp from the complete 1-minute input (even if before entry); if there is no preceding row, use entry_time. On later session transitions, both price and timestamp come from the previous scanned row. SUMMARY.md reports any resulting exit-before-entry trades.
- **TLB short history:** the parenthetical 'fewer if the list is shorter' governs the hybrid low slice: clamp its left bound to zero instead of using Python negative-index wraparound.
- **Trade CSV conventions:** direction is LONG or SHORT; timestamps are ISO 8601 UTC with Z; no rounding of calculated prices or P&L is applied. Base trades retain signal order; R3 trades retain parent-processing order (each parent's search is independent).
- **Summary conventions:** win rate is trades with pnl_points > 0 divided by all kept trades; zero-P&L trades are not wins. Total points is the unweighted, cost-free sum. Runtime covers loading, simulation, CSV output, and output validation, excluding the separate synthetic tests and final Markdown writes.

## Explicit rules preserved, not treated as ambiguities

- Keep sparse/partial buckets; drop only the final bucket globally. Do not fill missing bars or reset TLB history across gaps.
- UTC dates anchor VWAP/R3 sessions; ET dates determine regime/holidays; ET bar-start times gate base entries. Daily history uses all supplied 1-minute rows.
- VWAP lookbacks hold at most 20 bars *including* the current bar before it is excluded from stop calculation (normally 19 prior lows/highs). Session changes reset sums and streak, not history or halt age.
- Both VWAP detectors and the TLB detector see every completed bar, including bars when their sleeves are busy or disallowed. Detector signals reset streak even when not taken.
- Simulate each accepted VWAP candidate before applying the strictly-greater-than-200 cap, and update its sleeve's busy-until time even when it is discarded. Equality at an exit time permits a new entry.
- TLB's risk filter uses the final hybrid stop and the literal factor 2.0, but no position sizing is applied to P&L.
- R3 starts at ki + 2, counts at most 48 inspected bars, does not inspect the entry bar's intrabar exits, and uses fill < lag for VWAP (equality selects UPPER). Its stop history includes si through j - 1.
- R3's exit target uses the exact timestamp floor5(minute) - 5 minutes; a missing bucket yields NaN, with no carry-forward. Session end, flatten, stop, target priorities are preserved literally. R3 flatten has no hour < 17 restriction.
"""


def validate_outputs(frames, trades_by_name, regime):
    for name, original in frames.items():
        loaded = pd.read_csv(OUT / ("trades_" + name + ".csv"))
        assert loaded.columns.tolist() == TRADE_COLUMNS
        assert len(loaded) == len(original)
        if len(loaded):
            sign = np.where(loaded.direction == "LONG", 1, -1)
            assert np.allclose(loaded.pnl_points,
                               (loaded.exit_px - loaded.entry_px) * sign,
                               rtol=0, atol=1e-9)
            assert np.isfinite(loaded[["entry_px", "exit_px", "pnl_points"]]).all().all()
        if name.startswith("VWAP_"):
            assert all(abs(t.entry - t.stop) <= 200 for t in trades_by_name[name])
        if name != "R3_RE_mw48":
            trades = trades_by_name[name]
            assert all(b.entry_time >= a.exit_time for a, b in zip(trades, trades[1:]))
    loaded_regime = pd.read_csv(OUT / "regime.csv")
    assert len(loaded_regime) == len(regime)
    assert loaded_regime.et_date.is_unique
    assert set(loaded_regime.cell) <= set(ALLOWED)


def main():
    started = time.perf_counter()
    OUT.mkdir(exist_ok=True)
    raw = pd.read_csv(ROOT / "data" / "nq_1min.csv.gz")
    raw.index = pd.DatetimeIndex(pd.to_datetime(raw.pop("time"), utc=True))
    assert raw.index.is_monotonic_increasing and raw.index.is_unique
    assert not raw.isna().any().any()
    assert np.isfinite(raw.to_numpy()).all()
    assert (raw.index.as_unit("ns").asi8 % 60_000_000_000 == 0).all()
    b1 = Bars.from_frame(raw)
    grouped = raw.groupby(raw.index.floor("5min"), sort=True).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    dropped_time = grouped.index[-1].strftime("%Y-%m-%dT%H:%M:%SZ")
    b5 = Bars.from_frame(grouped.iloc[:-1])
    del raw, grouped
    holidays = set(pd.read_csv(ROOT / "data" / "no_trade_dates.csv").et_date)
    regime, cells, daily_count = regime_table(b1)
    print(f"Loaded {len(b1.t):,} minute bars; {len(b5.t):,} completed 5-minute bars; "
          f"{len(regime):,} regime dates.", flush=True)

    long_det, short_det, tlb_det = VWAPDetector("LONG"), VWAPDetector("SHORT"), TLBDetector()
    overlay_vwap = SessionVWAP()
    vwap5 = np.full(len(b5.t), np.nan)
    upper5 = np.full(len(b5.t), np.nan)
    names = ("VWAP_LONG_R3", "VWAP_SHORT_R1", "TLB_HYB40")
    result = {name: [] for name in names}
    busy = {name: -math.inf for name in names}
    stats = {name: Counter() for name in names}
    discarded = {name: [] for name in names[:2]}
    for i, t in enumerate(b5.t):
        args = (t, b5.h[i], b5.l[i], b5.c[i], b5.v[i])
        _, vwap, sd = overlay_vwap.update(*args)
        vwap5[i], upper5[i] = vwap, vwap + 2 * sd
        signals = (long_det.update(*args), short_det.update(*args),
                   tlb_det.update(b5.h[i], b5.l[i], b5.c[i]))
        cell = cells.get(b5.dates[i])
        allowed = ALLOWED.get(cell, set())
        m = b5.minute[i]
        is_holiday = b5.dates[i] in holidays
        entry_time = t + 5
        for name, signal in zip(names, signals):
            if signal is None:
                continue
            stats[name]["detector_signals"] += 1
            sleeve = "TLB_LONG" if name == "TLB_HYB40" else name
            if sleeve not in allowed or is_holiday:
                continue
            window = ((600 <= m < 715 or 895 <= m < 920) if name == "TLB_HYB40"
                      else (0 <= m < 360 or 570 <= m < 720 or 720 <= m < 870
                            or 1140 <= m < 1440))
            if not window:
                continue
            if name == "TLB_HYB40" and (signal.entry - signal.stop) * 2.0 > 3000:
                stats[name]["risk_filtered"] += 1
                continue
            if entry_time < busy[name]:
                stats[name]["busy_blocked"] += 1
                if name in discarded and discarded[name] and entry_time < discarded[name][-1].exit_time:
                    stats[name]["blocked_by_discarded"] += 1
                continue
            trade = exit_tlb(signal, i, b5) if name == "TLB_HYB40" else exit_vwap(signal, entry_time, b1)
            busy[name] = trade.exit_time
            stats[name]["taken_before_cap"] += 1
            if name != "TLB_HYB40" and signal.risk > 200:
                discarded[name].append(trade)
                continue
            result[name].append(trade)
        if (i + 1) % 100_000 == 0:
            print(f"Processed {i + 1:,}/{len(b5.t):,} 5-minute bars "
                  f"({time.perf_counter() - started:.1f}s).", flush=True)

    result["R3_RE_mw48"], re_stats = reentries(result["VWAP_LONG_R3"], b1, b5, vwap5, upper5)
    frames = {name: write_trades(OUT / ("trades_" + name + ".csv"), trades)
              for name, trades in result.items()}
    regime.to_csv(OUT / "regime.csv", index=False)
    validate_outputs(frames, result, regime)
    runtime = time.perf_counter() - started
    lines = ["# Backtest summary", "",
             "Independent implementation from REPRO_SPEC.md and the two supplied data files only.", "",
             "| Sleeve | Trades | Total points | Win rate |",
             "|---|---:|---:|---:|"]
    for name, trades in result.items():
        total = math.fsum(t.pnl for t in trades)
        wins = sum(t.pnl > 0 for t in trades)
        rate = f"{100 * wins / len(trades):.4f}%" if trades else "N/A"
        lines.append(f"| {name} | {len(trades):,} | {total:,.8f} | {rate} |")
    lines += ["", f"Runtime: {runtime:.3f} seconds (Python {__import__('sys').version.split()[0]}, "
              f"pandas {pd.__version__}, NumPy {np.__version__}).",
              "", "Run: `python repro/backtest.py`. Synthetic checks: `python repro/test_backtest.py`.",
              "", "## Input and validation", "",
              f"- 1-minute rows: {len(b1.t):,}; range {utc_text(b1.t[0])} to {utc_text(b1.t[-1])}.",
              f"- Retained 5-minute bars: {len(b5.t):,}; dropped final bucket: {dropped_time}.",
              f"- Observed ET daily closes: {daily_count:,}; defined regime dates: {len(regime):,} "
              f"({regime.et_date.iloc[0]} to {regime.et_date.iloc[-1]}).",
              "- Input order, uniqueness, finite/nonmissing source values and minute alignment checked; "
              "CSV columns/counts, P&L arithmetic, finite output prices, VWAP caps and base-sleeve "
              "busy constraints validated after writing.",
              "", "## Diagnostics and potentially surprising behavior", ""]
    for name in names:
        s = stats[name]
        lines.append(f"- {name}: {s['detector_signals']:,} detector signals; "
                     f"{s['taken_before_cap']:,} taken before VWAP cap; "
                     f"{s['busy_blocked']:,} otherwise-eligible signals blocked while busy.")
        if name in discarded:
            d = discarded[name]
            lines.append(f"  Discarded for risk > 200: {len(d):,}; their simulated P&L "
                         f"(excluded from results): {math.fsum(t.pnl for t in d):,.8f} points; "
                         f"otherwise-eligible signals blocked by those trades: {s['blocked_by_discarded']:,}.")
    lines.append(f"- R3: {re_stats['stopped_parents']:,} kept stopped LONG parents; "
                 f"{re_stats['kind_VWAP']:,} VWAP-kind entries; {re_stats['kind_UPPER']:,} UPPER-kind entries; "
                 f"{re_stats['wait_limit']:,} parents reached the 48-bar limit; "
                 f"{re_stats['missing_parent_bucket']:,} missing parent buckets; "
                 f"{re_stats['nonpositive_risk']:,} breakouts rejected for nonpositive risk.")
    for name, trades in result.items():
        reasons = Counter(t.reason for t in trades)
        lines.append(f"- {name} exit reasons: " + ", ".join(f"{k}={v:,}" for k, v in sorted(reasons.items())) + ".")
    backwards = sum(t.exit_time < t.entry_time for ts in result.values() for t in ts)
    lines += [f"- Exit-before-entry timestamps from literal terminal/session rules: {backwards}.",
              "- R3 trades may overlap each other and base trades; they have no independent regime, "
              "holiday, wide-stop cap or busy filter. No costs or position sizes are applied.", ""]
    (OUT / "AMBIGUITIES.md").write_text(AMBIGUITIES, encoding="utf-8")
    (OUT / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:12]), flush=True)
    print(f"Completed in {runtime:.3f}s. Outputs: {OUT}", flush=True)


if __name__ == "__main__":
    main()
