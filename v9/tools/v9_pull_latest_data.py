"""Pull latest NQ 1-min bars from Polygon and append to the data directory.

Called by v9_live_trade.py's refresh_data() before regime computation.
Primary source: Polygon futures API (clean, no gaps — same as kristov18).
Fallback: IB reqHistoricalData (7-day window, prone to gaps/truncation).
"""
import sys, os, logging, json, time, calendar
from pathlib import Path
from datetime import datetime, timedelta, timezone
import urllib.request

sys.path.insert(0, "/root/v9")
from us_market_holidays import is_day_complete
from contract_roll import quarterly_expiry     # 2026-09-14: ONE shared expiry calendar

log = logging.getLogger("v9_pull")

DATA_DIR = Path("/root/v9/data_1m/NQ")


def _expected_last_bar_utc():
    """The UTC time the most recent CME session bar before today-midnight-ET should
    carry. Weekdays: the 23:59 ET bar (session runs to midnight). If yesterday was
    Friday or Saturday, the last session ended Friday 17:00 ET (no Sat session).
    Used to verify a pull actually contains the full prior session — the REGIME
    depends on this close; never accept a truncated day (incidents Jul 6 + Jul 8)."""
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
    window_end = datetime.now(timezone.utc).astimezone(_ET).replace(
        hour=0, minute=0, second=0, microsecond=0)
    yday = window_end - timedelta(days=1)
    if yday.weekday() == 4:      # Friday -> session ends 17:00 ET Friday
        expected = yday.replace(hour=17, minute=0)
    elif yday.weekday() == 5:    # Saturday -> last session ended Friday 17:00 ET
        expected = (yday - timedelta(days=1)).replace(hour=17, minute=0)
    else:
        expected = window_end    # bar labelled 23:59 ET is 1 min before this
    return expected.astimezone(timezone.utc)
POLYGON_KEY_FILE = Path("/root/v9/.polygon_key")
ROLL_DAYS = 7


def _third_friday(year, month):
    """Quarterly EXPIRY (name kept for its callers). 2026-09-14 (Codex #13): was a bare 3rd-Friday
    calc, so when that Friday was a market holiday (Juneteenth 2026-06-19) the puller rolled a day
    LATER than the bots, which take IB's real lastTradeDate (Thu 2026-06-18). Now the shared,
    holiday-adjusted contract_roll.quarterly_expiry()."""
    return quarterly_expiry(year, month)

def _polygon_front_ticker(today=None):
    """Determine NQ front-month Polygon ticker from the quarterly schedule.
    Same roll logic as contract_roll.pick_front_contract: roll ROLL_DAYS before
    the 3rd-Friday expiry so we always pull the liquid contract."""
    if today is None:
        from zoneinfo import ZoneInfo
        today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
    quarters = [(3, 'H'), (6, 'M'), (9, 'U'), (12, 'Z')]
    candidates = []
    for dy in [today.year - 1, today.year, today.year + 1]:
        for month, letter in quarters:
            exp = _third_friday(dy, month)
            ticker = f"NQ{letter}{dy % 10}"
            candidates.append((exp, ticker))
    candidates.sort()

    for exp, ticker in candidates:
        days_left = (exp - today).days
        if days_left <= 0:
            continue
        if days_left <= ROLL_DAYS:
            continue
        return ticker
    future = [(exp, t) for exp, t in candidates if (exp - today).days > 0]
    return future[0][1] if future else candidates[-1][1]


def _polygon_prev_ticker(today=None):
    """The ticker that was front BEFORE the current roll (one quarter earlier)."""
    if today is None:
        from zoneinfo import ZoneInfo
        today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
    quarters = [(3, 'H'), (6, 'M'), (9, 'U'), (12, 'Z')]
    cands = []
    for dy in [today.year - 1, today.year, today.year + 1]:
        for month, letter in quarters:
            cands.append((_third_friday(dy, month), "NQ%s%d" % (letter, dy % 10)))
    cands.sort()
    front = _polygon_front_ticker(today)
    for i, (exp, tk) in enumerate(cands):
        if tk == front and i > 0:
            return cands[i - 1][1]
    return None


def _roll_boundary_utc(today=None):
    """UTC datetime of the most recent roll (when the current front became front).
    Bars BEFORE this instant belong to the PREVIOUS contract."""
    if today is None:
        from zoneinfo import ZoneInfo
        today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
    quarters = [(3, 'H'), (6, 'M'), (9, 'U'), (12, 'Z')]
    rolls = []
    for dy in [today.year - 1, today.year, today.year + 1]:
        for month, letter in quarters:
            rolls.append(_third_friday(dy, month) - timedelta(days=ROLL_DAYS))
    rolls = sorted(r for r in rolls if r <= today)
    if not rolls:
        return None
    # 2026-09-14 FIX (Codex #14): the boundary was 00:00 UTC on the roll date = 20:00 ET the
    # evening BEFORE (19:00 ET in winter). The bots pick the contract by ET date and switch at their
    # 23:15 UTC restart ON the roll date, and the regime close is the last bar of the ET date -- so
    # the final hours of the prior ET day were written with the NEW month's prices. That is exactly
    # the 2026-09-11 "REGIME CLOSE CROSS-CHECK FAILED 0.71%" false alarm. The boundary is now the CME
    # session open, 18:00 ET on the roll date (DST-correct): the whole prior ET day stays on the old
    # month, and at most ~75 min of the evening open precede the bots' own switch.
    from zoneinfo import ZoneInfo
    r = rolls[-1]
    return datetime(r.year, r.month, r.day, 18, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)


def _polygon_pull(ticker, days=7):
    """Pull 1-min bars from Polygon futures API. Returns (rows, error_msg)."""
    key = None
    if POLYGON_KEY_FILE.exists():
        key = POLYGON_KEY_FILE.read_text().strip()
    if not key:
        key = os.environ.get("POLYGON_API_KEY")
    if not key:
        return None, "No Polygon API key"

    from zoneinfo import ZoneInfo
    _ET = ZoneInfo('America/New_York')
    today_midnight_et = datetime.now(timezone.utc).astimezone(_ET).replace(
        hour=0, minute=0, second=0, microsecond=0)
    end = today_midnight_et.astimezone(timezone.utc)
    start = end - timedelta(days=days)
    start_ns = int(start.timestamp() * 1_000_000_000)
    end_ns = int(end.timestamp() * 1_000_000_000)

    url = (f"https://api.polygon.io/futures/v1/aggs/{ticker}"
           f"?resolution=1minute&window_start.gte={start_ns}&window_start.lt={end_ns}"
           f"&order=asc&limit=50000&apiKey={key}")

    all_rows = []
    for page in range(10):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                log.warning("Polygon rate limited, waiting 15s...")
                time.sleep(15)
                continue
            if all_rows:
                break
            return None, f"Polygon HTTP {e.code}: {e.reason}"
        except Exception as e:
            if all_rows:
                break
            return None, f"Polygon API error: {e}"

        rows = data.get("results", [])
        all_rows.extend(rows)
        next_url = data.get("next_url")
        if not next_url:
            break
        url = next_url + f"&apiKey={key}"
        time.sleep(1)

    if not all_rows:
        return None, "No bars from Polygon"

    return all_rows, None


def run():
    """Pull fresh NQ 1-min bars from Polygon, write as a seam file."""
    ticker = _polygon_front_ticker()
    log.info(f"Polygon pull: front ticker = {ticker}")

    rows, err = _polygon_pull(ticker)
    if err:
        log.warning(f"Polygon pull failed ({err}), falling back to IB")
        return _run_ib_fallback()

    log.info(f"Got {len(rows)} 1-min bars from Polygon for {ticker}")

    # ---- ROLL-CONTAMINATION GUARD (2026-08-28) --------------------------------
    # A 7-day window straddling a roll returns PRE-roll bars priced on the NEW
    # contract. Loaders dedupe keep="last", so those would overwrite correct history.
    # Re-pull the PREVIOUS contract and use it for pre-roll timestamps.
    try:
        _roll = _roll_boundary_utc()
        if _roll is not None and rows:
            _win_start_ns = min(int(r["window_start"]) for r in rows)
            _roll_ns = int(_roll.timestamp() * 1_000_000_000)
            if _win_start_ns < _roll_ns:
                _prev = _polygon_prev_ticker()
                log.warning("ROLL GUARD: window straddles roll %s; re-pulling pre-roll "
                            "bars from previous contract %s" % (_roll.date(), _prev))
                _prows, _perr = _polygon_pull(_prev) if _prev else (None, "no prev ticker")
                if _perr or not _prows:
                    log.error("ROLL GUARD: previous-contract pull failed (%s) - DROPPING "
                              "pre-roll bars rather than writing wrong prices" % _perr)
                    rows = [r for r in rows if int(r["window_start"]) >= _roll_ns]
                else:
                    _pre = [r for r in _prows if int(r["window_start"]) < _roll_ns]
                    _post = [r for r in rows if int(r["window_start"]) >= _roll_ns]
                    log.info("ROLL GUARD: spliced %d pre-roll bars from %s + %d post-roll "
                             "from %s" % (len(_pre), _prev, len(_post), ticker))
                    rows = sorted(_pre + _post, key=lambda r: int(r["window_start"]))
    except Exception as _e:
        log.error("ROLL GUARD failed (%r) - continuing with front-contract bars only" % (_e,))

    # COMPLETENESS CHECK (2026-07-08): a Polygon pull at/near midnight ET can be missing
    # the tail of the just-closed session (ingestion lag — seen 07-08: everything after
    # 16:00 ET absent at 00:00 ET, present hours later). The window cap alone cannot
    # catch this. Rule: the last bar must be within 10 min of the expected session end
    # (weekend-aware); otherwise the day is NOT fully ingested -> use IB (paid data,
    # has closed bars immediately).
    _expected = _expected_last_bar_utc()
    _last_ns = max(r.get("window_start") or r.get("t") or 0 for r in rows)
    _last_dt = datetime.fromtimestamp(_last_ns / 1_000_000_000, tz=timezone.utc)
    _lag_min = (_expected - _last_dt).total_seconds() / 60.0
    if _lag_min > 10:
        log.warning(f"Polygon pull INCOMPLETE: last bar {_last_dt} is {_lag_min:.0f} min "
                    f"before expected session end {_expected} — prior session tail not "
                    f"ingested yet. Falling back to IB (has closed bars immediately).")
        return _run_ib_fallback()
    log.info(f"Polygon completeness OK: last bar {_last_dt} ({_lag_min:.1f} min before expected end)")

    # PER-DAY BAR-COUNT CHECK (2026-07-08): the tail-timestamp check above only proves
    # the LAST bar arrived on time — it cannot catch a gap in the MIDDLE of a session
    # (e.g. a mid-afternoon outage). Group the pulled rows by ET date and compare each
    # of the last 3 days against the calendar-aware expected count (holidays/half-days
    # accounted for). Any incomplete day -> fall back to IB.
    from zoneinfo import ZoneInfo as _ZI3
    _ET3 = _ZI3("America/New_York")
    _by_day = {}
    for r in rows:
        ts_ns = r.get("window_start") or r.get("t")
        if not ts_ns:
            continue
        d = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).astimezone(_ET3).date()
        _by_day[d] = _by_day.get(d, 0) + 1
    _recent_days = sorted(_by_day)[-3:]
    for d in _recent_days:
        ok, expected = is_day_complete(d, _by_day[d])
        if not ok:
            log.warning(f"Polygon pull INCOMPLETE: ET {d} has only {_by_day[d]} bars "
                        f"(expected ~{expected}) — gap inside the session. Falling back to IB.")
            return _run_ib_fallback()
    log.info(f"Per-day bar-count check OK for {_recent_days}")

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    outf = DATA_DIR / f"NQ_1min_seam_{today_str}.csv"

    written = 0
    with open(outf, 'w') as f:
        f.write("time,open,high,low,close,volume\n")
        for r in rows:
            ts_ns = r.get("window_start") or r.get("t")
            if not ts_ns:
                continue
            ts = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
            ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
            o = r.get("open", r.get("o", 0))
            h = r.get("high", r.get("h", 0))
            lo = r.get("low", r.get("l", 0))
            c = r.get("close", r.get("c", 0))
            v = int(r.get("volume", r.get("v", 0)))
            f.write(f"{ts_str},{o},{h},{lo},{c},{v}\n")
            written += 1

    log.info(f"Written {written} bars to {outf}")
    return f"OK (Polygon): {written} bars -> {outf}"


def _run_ib_fallback():
    """Fallback: pull from IB if Polygon is unavailable."""
    try:
        from ib_async import IB, Future
    except ImportError:
        return "IB fallback failed: ib_async not installed"

    ib = IB()
    try:
        ib.connect('127.0.0.1', 7497, clientId=98, timeout=15)
    except Exception as e:
        return f"IB fallback connect failed: {e}"

    try:
        sys.path.insert(0, "/root/v9")
        from contract_roll import pick_front_contract

        contract = Future(symbol='NQ', exchange='CME', currency='USD')
        details = ib.reqContractDetails(contract)
        if not details:
            return "IB fallback: no NQ contract details"

        today = datetime.now().strftime("%Y%m%d")
        ch = pick_front_contract(details, 7, today)
        if ch is None:
            return "IB fallback: could not resolve front contract"
        contract = ch.contract
        log.info(f"IB fallback: pulling data for {contract.localSymbol}")

        # Cap at today midnight ET — only fully closed sessions (same rule as Polygon path)
        from zoneinfo import ZoneInfo as _ZI
        _ET2 = _ZI("America/New_York")
        _end_et = datetime.now(timezone.utc).astimezone(_ET2).replace(
            hour=0, minute=0, second=0, microsecond=0)
        _end_str = _end_et.astimezone(timezone.utc).strftime("%Y%m%d-%H:%M:%S")
        bars = ib.reqHistoricalData(
            contract, endDateTime=_end_str, durationStr='7 D',
            barSizeSetting='1 min', whatToShow='TRADES',
            useRTH=False, formatDate=2)

        if not bars:
            return "IB fallback: no bars returned"

        # ---- ROLL-CONTAMINATION GUARD (2026-08-28), same rule as the Polygon path ----
        try:
            _roll = _roll_boundary_utc()
            if _roll is not None and bars:
                def _bts(b):
                    _d = b.date
                    if hasattr(_d, "astimezone"):
                        return _d.astimezone(timezone.utc)
                    return datetime.strptime(str(_d), "%Y%m%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if _bts(bars[0]) < _roll:
                    _alive = [d for d in details if d.contract.lastTradeDateOrContractMonth]
                    _alive.sort(key=lambda d: d.contract.lastTradeDateOrContractMonth)
                    _prev_cd = None
                    for _i, _d in enumerate(_alive):
                        if _d.contract.localSymbol == contract.localSymbol and _i > 0:
                            _prev_cd = _alive[_i - 1]
                            break
                    if _prev_cd is None:
                        log.error("ROLL GUARD (IB): no previous contract - dropping pre-roll bars")
                        bars = [b for b in bars if _bts(b) >= _roll]
                    else:
                        log.warning("ROLL GUARD (IB): window straddles roll %s; re-pulling "
                                    "pre-roll bars from %s"
                                    % (_roll.date(), _prev_cd.contract.localSymbol))
                        _pbars = ib.reqHistoricalData(
                            _prev_cd.contract, endDateTime=_end_str, durationStr='7 D',
                            barSizeSetting='1 min', whatToShow='TRADES',
                            useRTH=False, formatDate=2)
                        if not _pbars:
                            log.error("ROLL GUARD (IB): previous-contract pull empty - "
                                      "dropping pre-roll bars")
                            bars = [b for b in bars if _bts(b) >= _roll]
                        else:
                            _pre = [b for b in _pbars if _bts(b) < _roll]
                            _post = [b for b in bars if _bts(b) >= _roll]
                            log.info("ROLL GUARD (IB): spliced %d pre-roll + %d post-roll bars"
                                     % (len(_pre), len(_post)))
                            bars = sorted(_pre + _post, key=_bts)
        except Exception as _e:
            log.error("ROLL GUARD (IB) failed (%r) - continuing with front-contract bars only" % (_e,))

        # COMPLETENESS CHECK (2026-07-08): an IB disconnect mid-pull truncates the data
        # (the 06-30 incident). A truncated file silently poisons the regime close —
        # REFUSE to write it. Better no new seam file (regime falls back to the locked
        # cache + loud warnings) than a wrong close.
        _expected = _expected_last_bar_utc()
        _last_bar = bars[-1].date
        if hasattr(_last_bar, "astimezone"):
            _last_utc = _last_bar.astimezone(timezone.utc)
        else:
            _last_utc = datetime.strptime(str(_last_bar), "%Y%m%d %H:%M:%S").replace(tzinfo=timezone.utc)
        _lag_min = (_expected - _last_utc).total_seconds() / 60.0
        if _lag_min > 10:
            log.error(f"IB pull INCOMPLETE: last bar {_last_utc} is {_lag_min:.0f} min before "
                      f"expected session end {_expected} — REFUSING to write truncated seam file. "
                      f"Regime will use existing data / locked cache.")
            return (f"FAILED: IB pull incomplete (last bar {_last_utc}, {_lag_min:.0f} min short) "
                    f"— no seam file written")
        log.info(f"IB completeness OK: last bar {_last_utc} ({_lag_min:.1f} min before expected end)")

        # PER-DAY BAR-COUNT CHECK (2026-07-08): same mid-session-gap protection as the
        # Polygon path — the tail-timestamp check alone cannot catch a gap earlier in
        # the session (e.g. IB reconnect lost part of the afternoon).
        _by_day2 = {}
        for b in bars:
            if hasattr(b.date, "astimezone"):
                d2 = b.date.astimezone(_ET2).date()
            else:
                d2 = datetime.strptime(str(b.date), "%Y%m%d %H:%M:%S").astimezone(_ET2).date()
            _by_day2[d2] = _by_day2.get(d2, 0) + 1
        for d2 in sorted(_by_day2)[-3:]:
            ok2, expected2 = is_day_complete(d2, _by_day2[d2])
            if not ok2:
                log.error(f"IB pull INCOMPLETE: ET {d2} has only {_by_day2[d2]} bars "
                          f"(expected ~{expected2}) — gap inside session. REFUSING to write.")
                return (f"FAILED: IB pull has a gap on {d2} ({_by_day2[d2]} bars, "
                        f"expected ~{expected2}) — no seam file written")
        log.info(f"IB per-day bar-count check OK for {sorted(_by_day2)[-3:]}")

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        outf = DATA_DIR / f"NQ_1min_seam_{today_str}.csv"
        with open(outf, 'w') as f:
            f.write("time,open,high,low,close,volume\n")
            for b in bars:
                if hasattr(b.date, 'astimezone'):
                    ts_utc = b.date.astimezone(timezone.utc)
                    ts_str = ts_utc.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    ts_str = str(b.date)
                f.write(f"{ts_str},{b.open},{b.high},{b.low},{b.close},{int(b.volume)}\n")

        log.info(f"IB fallback: written {len(bars)} bars to {outf}")
        return f"OK (IB fallback): {len(bars)} bars -> {outf}"

    finally:
        ib.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
