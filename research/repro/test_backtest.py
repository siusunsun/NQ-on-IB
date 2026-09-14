"""Synthetic checks derived exclusively from REPRO_SPEC.md."""

import math
import unittest

import numpy as np

from backtest import (Bars, SessionVWAP, Signal, TLBDetector, Trade,
                      VWAPDetector, exit_reentry, exit_tlb, exit_vwap,
                      reentries, regime_table)


def bars(t, *, o=None, h=None, l=None, c=None, minute=None, dates=None):
    n = len(t)
    def array(value, default):
        return np.asarray(value if value is not None else [default] * n, dtype=float)
    return Bars(np.asarray(t, dtype=np.int64), array(o, 100), array(h, 105),
                array(l, 95), array(c, 100), np.ones(n),
                np.asarray(dates if dates is not None else ["2026-01-01"] * n),
                np.asarray(minute if minute is not None else [600] * n))


class FixedVWAP:
    def update(self, *args):
        return False, 100.0, 5.0  # lower=90, upper=110


class SpecificationTests(unittest.TestCase):
    def test_vwap_weighting_and_utc_reset(self):
        v = SessionVWAP()
        self.assertEqual(v.update(1430, 10, 10, 10, 1), (True, 10, 0))
        changed, mean, sd = v.update(1435, 20, 20, 20, 3)
        self.assertFalse(changed)
        self.assertEqual(mean, 17.5)
        self.assertAlmostEqual(sd, math.sqrt(18.75))
        self.assertEqual(v.update(1440, 30, 30, 30, 1), (True, 30, 0))
        self.assertTrue(math.isnan(SessionVWAP().update(0, 1, 1, 1, 0)[1]))

    def test_long_warmup_and_nineteen_prior_lows(self):
        d = VWAPDetector("LONG")
        d.sums = FixedVWAP()
        for i in range(23):
            self.assertIsNone(d.update(i*5, 100, 80, 89, 1))
        self.assertEqual(d.streak, 0)
        for i in range(23, 28):
            self.assertIsNone(d.update(i*5, 100, 80, 89, 1))
        d.lows = __import__('collections').deque([1] + [80]*19, maxlen=20)
        signal = d.update(140, 100, 0, 91, 1)
        self.assertEqual((signal.entry, signal.stop, signal.target), (91, 80, 124))
        self.assertEqual(d.streak, 0)

    def test_halt_strict_threshold_and_no_extra_streak_reset(self):
        d = VWAPDetector("LONG")
        d.sums = FixedVWAP()
        d.bars_since_halt = 30
        d.streak = 4
        d.last_bar_time = 0
        d.update(180, 100, 80, 89, 1)
        self.assertEqual(d.streak, 5)
        self.assertEqual(d.bars_since_halt, 31)
        d.update(365, 100, 80, 91, 1)
        self.assertEqual(d.bars_since_halt, 1)
        self.assertEqual(d.streak, 5)
        self.assertEqual(list(d.lows), [80])

    def test_short_has_no_warmup_and_equality_resets(self):
        d = VWAPDetector("SHORT")
        d.sums = FixedVWAP()
        for i in range(3):
            self.assertIsNone(d.update(i*5, 120, 100, 111, 1))
        s = d.update(15, 150, 100, 109, 1)
        self.assertEqual((s.stop, s.target), (120, 98))
        d.streak = 3
        self.assertIsNone(d.update(20, 120, 100, 110, 1))
        self.assertEqual(d.streak, 0)

    def test_vwap_exit_priorities_and_flatten_hour_bound(self):
        long = Signal("LONG", 100, 90, 120)
        short = Signal("SHORT", 100, 110, 90)
        both = bars([5], h=[125], l=[85], minute=[955])
        self.assertEqual(exit_vwap(long, 5, both).reason, "STOP")
        self.assertEqual(exit_vwap(short, 5, both).reason, "STOP")
        target = bars([5], h=[125], l=[95], minute=[955])
        self.assertEqual(exit_vwap(long, 5, target).reason, "TARGET")
        later = bars([5, 10, 15], minute=[954, 1020, 1019], c=[101, 102, 103])
        trade = exit_vwap(long, 5, later)
        self.assertEqual((trade.reason, trade.exit_time, trade.exit), ("GAP_FLATTEN", 15, 103))

    def test_vwap_scan_starts_at_entry_and_data_end(self):
        b = bars([0, 5], l=[80, 95], c=[98, 101])
        t = exit_vwap(Signal("LONG", 100, 90, 120), 5, b)
        self.assertEqual((t.reason, t.exit_time, t.exit), ("DATA_END", 5, 101))

    def test_tlb_pivot_left_equality_right_strictness(self):
        d = TLBDetector()
        for h, l in zip([1, 2, 3, 3, 2, 1, 0], [9, 8, 7, 7, 8, 9, 10]):
            d.update(h, l, 5)
        self.assertEqual(list(d.pivot_highs), [(3, 3)])
        self.assertEqual(list(d.pivot_lows), [(3, 7)])
        equal_right = TLBDetector()
        for h in [1, 2, 3, 3, 3, 1, 0]:
            equal_right.update(h, 0, 1)
        self.assertEqual(len(equal_right.pivot_highs), 0)

    def test_tlb_hybrid_and_exact_40_boundary(self):
        def seeded():
            d = TLBDetector()
            d.h, d.l, d.c = [150.0]*20, [95.0]*20, [99.0]*20
            d.l[1] = 70
            d.pivot_highs.extend([(3, 100), (9, 100)])
            d.pivot_lows.append((10, 90))
            return d
        s = seeded().update(150, 95, 101)
        self.assertEqual((s.stop, s.target), (70, 132))
        s = seeded().update(150, 95, 130)
        self.assertEqual((s.stop, s.target), (90, 170))

    def test_tlb_following_bars_and_close_timestamp(self):
        b = bars([0, 5, 10], h=[150, 105, 125], l=[0, 95, 85], minute=[900, 915, 920])
        trade = exit_tlb(Signal("LONG", 100, 90, 120), 0, b)
        self.assertEqual((trade.reason, trade.exit_time), ("STOP", 15))
        b = bars([0, 5], minute=[915, 920], c=[100, 103])
        trade = exit_tlb(Signal("LONG", 100, 90, 120), 0, b)
        self.assertEqual((trade.reason, trade.exit_time, trade.exit), ("EOD", 10, 103))

    def test_reentry_exit_flatten_before_stop(self):
        b1 = bars([5], h=[125], l=[80], c=[99], minute=[955])
        b5 = bars([0])
        trade = exit_reentry(Signal("LONG", 100, 90, math.nan), 5, 0, "VWAP",
                             b1, b5, {0: 0}, [120], [130])
        self.assertEqual((trade.reason, trade.exit), ("FLATTEN", 99))

    def test_reentry_session_precedes_other_exits(self):
        b1 = bars([1439, 1440], l=[95, 0], c=[101, 102], minute=[600, 955])
        b5 = bars([1435])
        s = Signal("LONG", 100, 90, math.nan)
        t = exit_reentry(s, 1439, 0, "VWAP", b1, b5, {1435: 0}, [math.nan], [math.nan])
        self.assertEqual((t.reason, t.exit_time, t.exit), ("SESS_END", 1439, 101))
        t = exit_reentry(s, 1440, 0, "VWAP", b1, b5, {1435: 0}, [math.nan], [math.nan])
        self.assertEqual((t.reason, t.exit_time, t.exit), ("SESS_END", 1439, 100))

    def test_reentry_target_exact_prior_bucket_and_above_fill(self):
        s = Signal("LONG", 100, 90, math.nan)
        b5 = bars([0, 10])  # bucket 5 is absent
        b1 = bars([10, 15], h=[130, 130], c=[101, 102])
        t = exit_reentry(s, 10, 0, "VWAP", b1, b5, {0: 0, 10: 1}, [120, 110], [130, 130])
        self.assertEqual((t.reason, t.exit_time, t.exit), ("TARGET", 15, 110))
        t = exit_reentry(s, 10, 0, "VWAP", b1, b5, {0: 0, 10: 1}, [120, 100], [130, 130])
        self.assertEqual(t.reason, "DATA_END")

    def test_reentry_search_skips_first_bar_and_excludes_entry_bar_low(self):
        b5 = bars([0, 5, 10, 15, 20], o=[100]*5,
                  h=[100, 102, 103, 104, 105], l=[90, 91, 92, 0, 94])
        b1 = bars([15, 20], h=[200, 115], l=[0, 100])
        parent = Trade(5, 5, "LONG", 100, 95, "STOP", 95)
        trades, stats = reentries([parent], b1, b5, np.full(5, 110.0), np.full(5, 120.0))
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual((t.entry_time, t.entry, t.stop, t.exit), (20, 103, 90, 110))
        self.assertEqual(stats["kind_VWAP"], 1)

    def test_reentry_48_bar_limit_includes_48th_excludes_49th(self):
        parent = Trade(5, 5, "LONG", 100, 95, "STOP", 95)
        for breakout, expected in [(50, 1), (51, 0)]:
            highs = [100]*55
            highs[breakout] = 101
            b5 = bars(list(range(0, 275, 5)), h=highs)
            b1 = bars([255, 260, 265])
            trades, _ = reentries([parent], b1, b5, np.full(55, 110.0), np.full(55, 120.0))
            self.assertEqual(len(trades), expected)

    def test_regime_needs_205_prior_closes_and_ignores_today(self):
        dates = [f"D{i:04}" for i in range(207)]
        closes = list(range(1, 207)) + [-10000]
        b = bars(list(range(207)), c=closes, dates=dates)
        frame, cells, count = regime_table(b)
        self.assertEqual(count, 207)
        self.assertEqual(list(frame.et_date), ["D0205", "D0206"])
        self.assertEqual(frame.prev_close.tolist(), [205, 206])
        self.assertEqual(frame.sma200.iloc[0], sum(range(6, 206))/200)
        self.assertAlmostEqual(frame.ret20.iloc[0], 205/185 - 1)
        self.assertEqual(set(cells.values()), {"BOTH_BULL"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
