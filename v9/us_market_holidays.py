"""US equity-index-futures (CME) market-holiday calendar — computed algorithmically
so it needs NO annual maintenance. Covers full closures AND early-close days.

Policy for V9: on any date flagged here as a FULL closure, stand down ALL sleeves
(no new entries). Early-close days also stand down (thin/half-session liquidity is
unsafe for the mean-reversion/breakout sleeves, and the regime gate assumes a normal
prior RTH close). Existing positions are unaffected (they run to their server-side
OCA brackets).

Full holidays (NQ fully closed, ~0 bars all day):
  MLK, Presidents', Good Friday, Memorial, Labor, Thanksgiving — these are all
  weekday-defined (n-th Monday/Thursday), so they never land on a weekend and never
  need an observed-shift.
Early-close days (NQ halts ~13:00 ET, ~780 bars/day) — also treated as no-trade:
  New Year's eve, Independence Day eve, day after Thanksgiving, Christmas Eve.

FIXED-DATE holiday nuance (2026-07-08 fix): New Year's (1/1), Juneteenth (6/19),
Independence Day (7/4), and Christmas (12/25) are fixed calendar dates that can land
on a weekend. CME Globex is a near-24h market and does NOT observe the NYSE-style
"Saturday holiday -> full closure Friday" shift — verified against real data: Jul 4
2026 fell on Saturday, and Jul 3 (the preceding Friday) traded a real 780-bar
half-day (13:00 ET early close), not a 0-bar full closure. Policy used here:
  - holiday on Saturday  -> preceding Friday is an EARLY CLOSE (not full closure)
  - holiday on Sunday    -> following Monday is a FULL closure (standard, unchanged)
  - holiday on a weekday -> that weekday is a FULL closure; the PRECEDING weekday
    is an early close for New Year's Eve / Independence Day eve / Christmas Eve
    (Juneteenth has no established eve-early-close convention -> none added)

Self-test:  python us_market_holidays.py   (prints no-trade dates + expected bar counts)
"""
from __future__ import annotations
import datetime as dt


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """n-th `weekday` (Mon=0) of month, e.g. 3rd Monday of January."""
    d = dt.date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    """Last `weekday` (Mon=0) of month, e.g. last Monday of May."""
    if month == 12:
        d = dt.date(year, 12, 31)
    else:
        d = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - dt.timedelta(days=offset)


def _easter(year: int) -> dt.date:
    """Gregorian Easter Sunday (Anonymous/Meeus algorithm)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def _fixed_date_holiday(base: dt.date):
    """Classify a FIXED-calendar-date holiday (New Year's/Juneteenth/Independence/Christmas)
    per the CME-Globex weekend rule documented above. Returns (full_closure_date_or_None,
    early_close_eve_date_or_None)."""
    if base.weekday() == 5:        # Saturday -> Friday early close, no full closure
        return None, base - dt.timedelta(days=1)
    if base.weekday() == 6:        # Sunday -> Monday full closure (standard)
        return base + dt.timedelta(days=1), None
    return base, base - dt.timedelta(days=1)   # weekday -> itself closed, eve early-close


def holiday_map(year: int) -> dict:
    """{date: name} of FULL-closure US market holidays for `year`."""
    m = {}
    weekday_holidays = {
        _nth_weekday(year, 1, 0, 3):   "MLK Jr. Day",
        _nth_weekday(year, 2, 0, 3):   "Presidents' Day",
        _easter(year) - dt.timedelta(days=2): "Good Friday",
        _last_weekday(year, 5, 0):     "Memorial Day",
        _nth_weekday(year, 9, 0, 1):   "Labor Day",
        _nth_weekday(year, 11, 3, 4):  "Thanksgiving",
    }
    m.update(weekday_holidays)
    for base, name in [(dt.date(year, 1, 1), "New Year's Day"),
                        (dt.date(year, 6, 19), "Juneteenth"),
                        (dt.date(year, 7, 4), "Independence Day"),
                        (dt.date(year, 12, 25), "Christmas")]:
        full, _eve = _fixed_date_holiday(base)
        if full is not None:
            m[full] = name
    return m


def early_close_map(year: int) -> dict:
    """{date: name} of NQ early-close days (~13:00 ET, ~780 bars) — also treated as no-trade."""
    m: dict = {}
    for base, name in [(dt.date(year, 1, 1), "New Year's Eve"),
                        (dt.date(year, 7, 4), "Independence Day eve"),
                        (dt.date(year, 12, 25), "Christmas Eve")]:
        _full, eve = _fixed_date_holiday(base)
        if eve is not None and eve.weekday() < 5:   # eve must itself be a weekday session
            m[eve] = f"{name} (early close)"
    # Day after Thanksgiving is always a Friday weekday.
    thx = _nth_weekday(year, 11, 3, 4)
    m[thx + dt.timedelta(days=1)] = "Day after Thanksgiving (early close)"
    return m


def no_trade_reason(d: dt.date):
    """Return a human-readable reason string if `d` is a US market no-trade day for V9,
    else None. Weekends return None (handled upstream — no bars arrive)."""
    if d.weekday() >= 5:
        return None
    h = holiday_map(d.year)
    if d in h:
        return f"US market holiday — {h[d]}"
    e = early_close_map(d.year)
    if d in e:
        return f"US market {e[d]}"
    # Dec 31/Jan 1 boundary: an eve keyed to next year (rare) — check neighboring year too.
    h2 = holiday_map(d.year + 1)
    if d in h2:
        return f"US market holiday — {h2[d]}"
    e2 = early_close_map(d.year + 1)
    if d in e2:
        return f"US market {e2[d]}"
    return None


def expected_minute_bars(d: dt.date) -> int:
    """Expected count of 1-min NQ/CME bars whose ET date is `d`.
    Kept for reference/logging — is_day_complete() uses simpler logic now.

    Globex session model: the session labeled for trading-day X opens at X-1 18:00 ET
    and closes at X 17:00 ET (or 13:00 ET on an early-close day X). So calendar date Y
    (00:00-23:59 ET) contains two pieces:
      (a) the TAIL of session Y itself: Y 00:00 -> Y's own close time, present only if
          session Y exists (Y is a valid trading day — not Sat, not a full holiday).
      (b) the HEAD of the NEXT session (Y+1): Y 18:00 -> Y 23:59 (360 min), present
          only if session Y+1 exists (Y+1 is a valid trading day).
    """
    def _tail(day: dt.date) -> int:
        if day.weekday() >= 5:
            return 0
        reason = no_trade_reason(day)
        if reason and "early close" in reason:
            return 780
        if reason:
            return 0
        return 1020

    def _head_exists(day: dt.date) -> bool:
        nxt = day + dt.timedelta(days=1)
        if nxt.weekday() >= 5:
            return False
        reason = no_trade_reason(nxt)
        return reason is None or "early close" in (reason or "")

    tail = _tail(d)
    head = 360 if _head_exists(d) else 0
    return tail + head


def is_half_day_session(d: dt.date) -> bool:
    """True if d is a known CME early-close or modified-hours session.

    Covers the major ones:
      - Eve of New Year's, Independence Day, Christmas (trading day before the holiday)
      - Eve of Juneteenth (trading day before)
      - Day after Thanksgiving (always Friday)
      - Good Friday
      - MLK, Presidents' Day, Memorial Day (modified schedule, ~1140 bars)

    Weekend shift: if a fixed-date holiday falls on Saturday, the preceding
    Friday is the early close. If on Sunday, Monday is the full closure and
    there is no early-close eve.
    """
    year = d.year
    dates = set()

    def _add_eve_of_fixed(month, day):
        holiday = dt.date(year, month, day)
        wd = holiday.weekday()
        if wd == 5:                                      # Sat -> Friday early close
            dates.add(holiday - dt.timedelta(days=1))
        elif wd < 5:                                     # weekday -> day before (if weekday)
            eve = holiday - dt.timedelta(days=1)
            if eve.weekday() < 5:
                dates.add(eve)
        # Sunday -> Monday full closure, no early-close eve

    _add_eve_of_fixed(1, 1)    # New Year's
    _add_eve_of_fixed(6, 19)   # Juneteenth
    _add_eve_of_fixed(7, 4)    # Independence Day
    _add_eve_of_fixed(12, 25)  # Christmas

    # Cross-year: Dec 31 is the eve of next year's New Year's
    ny_next = dt.date(year + 1, 1, 1)
    wd_next = ny_next.weekday()
    if wd_next == 5:                                     # Sat -> Friday (Dec 31) early close
        dates.add(ny_next - dt.timedelta(days=1))
    elif wd_next < 5:                                    # weekday -> day before
        eve = ny_next - dt.timedelta(days=1)
        if eve.weekday() < 5:
            dates.add(eve)

    # Day after Thanksgiving (always a Friday)
    dates.add(_nth_weekday(year, 11, 3, 4) + dt.timedelta(days=1))

    # Good Friday
    dates.add(_easter(year) - dt.timedelta(days=2))

    # Modified-schedule holidays (CME trades ~1140 bars, not a full 1380)
    dates.add(_nth_weekday(year, 1, 0, 3))    # MLK Jr. Day
    dates.add(_nth_weekday(year, 2, 0, 3))    # Presidents' Day
    dates.add(_last_weekday(year, 5, 0))      # Memorial Day

    return d in dates


def is_day_complete(d: dt.date, actual_bars: int, tolerance: float = 0.95):
    """(is_complete, expected) — simplified data-completeness gate.

    Catches data-ingestion failures (Polygon delivering incomplete data).
    Does NOT reject days with legitimately fewer bars (half-day sessions,
    modified-schedule holidays). Principle: if the backtest included half
    days as full days, follow it.

    Logic:
      Saturday:      always complete (0 bars expected)
      Sunday:        complete if bars >= 200 (~360 typical evening session)
      Full holiday:  always complete (legitimate 0-360 bars)
      Weekday:       normal = 1380 (Mon-Thu) or 1020 (Fri)
        bars >= normal * 0.85         -> complete (minor gaps OK)
        bars <  normal * 0.85         -> check if known half-day/modified session
          half-day AND bars >= normal * 0.5 * 0.8  -> complete
          otherwise                                -> INCOMPLETE (bad pull)
    """
    # Saturday: always complete
    if d.weekday() == 5:
        return True, 0

    # Sunday: accept if >= 200 bars (evening session ~360 typical)
    if d.weekday() == 6:
        return actual_bars >= 200, 360

    # Full holidays on weekdays: legitimate 0-360 bars (evening session only)
    hmap = holiday_map(d.year)
    if d in hmap:
        return True, 0

    # Weekday: determine normal bar count
    normal = 1020 if d.weekday() == 4 else 1380   # Friday vs Mon-Thu

    # Full-day tolerance: 85%
    if actual_bars >= normal * 0.85:
        return True, normal

    # Below 85% — accept if it's a known half-day/modified-hours session
    if is_half_day_session(d) and actual_bars >= normal * 0.5 * 0.8:
        return True, normal

    # Incomplete — likely a bad data pull
    return False, normal


if __name__ == "__main__":
    import sys
    y0 = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    y1 = int(sys.argv[2]) if len(sys.argv) > 2 else y0 + 1
    for y in range(y0, y1 + 1):
        print(f"=== {y} V9 NO-TRADE DAYS ===")
        rows = []
        for d, name in holiday_map(y).items():
            rows.append((d, "FULL", name))
        for d, name in early_close_map(y).items():
            rows.append((d, "EARLY", name))
        for d, kind, name in sorted(rows):
            print(f"  {d.isoformat()}  {d.strftime('%a')}  {kind:5s}  {name}  expected_bars={expected_minute_bars(d)}")
