"""Shared quarterly-futures roll helpers for the v9 + ORB paper bots.

Two jobs, both run at every (re)connect — and since the bots restart daily, the
roll + flush happen automatically by the roll date with no manual intervention:

  pick_front_contract(details, roll_days_before, today_str)
      Choose which contract to TRADE. Returns the nearest unexpired contract,
      BUT rolls to the next quarterly when the nearest is within
      `roll_days_before` days of its last-trade date — so the bots trade the
      LIQUID contract through the thin pre-expiry week (CME equity-index volume
      rolls ~8 days before the 3rd-Friday expiry) and never trade the contract
      on its own expiry day.

  flatten_foreign_positions(ib, symbol, keep_local_symbol, log)
      Market-close + cancel orders on any position/order of `symbol` whose
      localSymbol != the contract we're now trading — i.e. a stranded leg left
      in the OLD contract after a roll. SYMBOL-SCOPED, so the NQ bot never
      touches the MNQ bot's positions and vice-versa (they share the account).
"""
from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

try:
    from ib_async import MarketOrder
except Exception:  # allows the offline self-test below to import without ib_async
    MarketOrder = None

ET = ZoneInfo("America/New_York")
DEFAULT_ROLL_DAYS = 7


def today_et_str() -> str:
    return datetime.now(timezone.utc).astimezone(ET).strftime("%Y%m%d")


def _expiry(detail_or_contract) -> str:
    c = getattr(detail_or_contract, "contract", detail_or_contract)
    return (getattr(c, "lastTradeDateOrContractMonth", "") or "")[:8]


def days_to_expiry(expiry_str: str, today_str: str | None = None) -> int:
    today_str = (today_str or today_et_str())[:8]
    try:
        e = datetime.strptime(expiry_str[:8], "%Y%m%d").date()
        t = datetime.strptime(today_str, "%Y%m%d").date()
        return (e - t).days
    except Exception:
        return 9999


def pick_front_contract(details, roll_days_before: int = DEFAULT_ROLL_DAYS, today_str: str | None = None):
    """Return the ContractDetails to TRADE (rolling early), or None if all expired."""
    today_str = (today_str or today_et_str())[:8]
    alive = [d for d in details if _expiry(d) and _expiry(d) >= today_str]
    if not alive:
        return None
    alive.sort(key=_expiry)
    front = alive[0]
    if days_to_expiry(_expiry(front), today_str) <= roll_days_before and len(alive) >= 2:
        return alive[1]                       # roll early to the next quarterly
    return front


def quarterly_expiry(year: int, month: int):
    """CME equity-index quarterly expiry: the 3rd Friday, stepped back to the prior business day
    when that Friday is a full US-market closure (Juneteenth 2026-06-19 -> NQM6 expired Thu
    2026-06-18, which is what IB reports). 2026-09-14 (Codex #13): the data puller used a bare
    3rd-Friday calc and so rolled a day LATER than the bots. This is now the ONE definition.
    It is only as good as us_market_holidays -- callers cross-check it with expiry_crosscheck()."""
    import calendar
    import datetime as _dt
    cal = calendar.monthcalendar(year, month)
    d = _dt.date(year, month, [w[calendar.FRIDAY] for w in cal if w[calendar.FRIDAY]][2])
    try:
        from us_market_holidays import holiday_map
        hol = set(holiday_map(year))
    except Exception:
        hol = set()
    # 2026-09-14: expiry follows the CASH-index market, which observes a SATURDAY fixed-date holiday
    # on the Friday (us_market_holidays models that Friday as a Globex EARLY CLOSE -- right for
    # session checks, wrong for expiry). Found by expiry_crosscheck on its first live run: IB lists
    # NQM7 expiring Thu 2027-06-17 because Juneteenth 2027 is a Saturday. New Year's is excluded
    # (NYSE does not close Dec 31 for a Saturday Jan 1).
    for mm, dd in ((6, 19), (7, 4), (12, 25)):
        h = _dt.date(year, mm, dd)
        if h.weekday() == 5:
            hol.add(h - _dt.timedelta(days=1))
    while d in hol or d.weekday() >= 5:
        d -= _dt.timedelta(days=1)
    return d


def expiry_crosscheck(details, log) -> list:
    """Compare IB's lastTradeDate for every listed quarterly with quarterly_expiry(). A mismatch
    means our holiday calendar is wrong for that quarter (e.g. a Saturday holiday observed on the
    Friday), so the puller would roll on a different day from the bots. WARNING -> Telegram,
    months before it can bite, because IB lists contracts well ahead of expiry."""
    bad = []
    for d in details or []:
        e = _expiry(d)
        if len(e) < 8 or int(e[4:6]) not in (3, 6, 9, 12):
            continue
        ours = quarterly_expiry(int(e[:4]), int(e[4:6])).strftime("%Y%m%d")
        if ours != e:
            bad.append((getattr(getattr(d, "contract", d), "localSymbol", "?"), e, ours))
    for ls, ib_e, ours in bad:
        log.warning(f"EXPIRY CALENDAR MISMATCH {ls}: IB lastTradeDate {ib_e} vs our calendar {ours} "
                    f"-- the data puller would roll on the wrong day. Fix us_market_holidays.")
    return bad


def close_symbol_for_date(d, root: str = "NQ", roll_days: int = DEFAULT_ROLL_DAYS):
    """localSymbol of the quarterly that printed the LAST bar of ET date `d` in our archive.

    The data puller switches months at 18:00 ET on the roll date R (= quarterly_expiry - roll_days).
    The regime close is the last bar of the ET date. So: d < R -> old month; d > R -> new month;
    and on R itself it depends on whether that session trades past 18:00 ET -- a FRIDAY halts at
    17:00 ET, so R's close is still the OLD month; a Thursday R (holiday-moved expiry) trades on
    past 18:00 ET, so its close is already the NEW month. Used by the V9 regime referee so it
    compares like with like across a roll."""
    import datetime as _dt
    if isinstance(d, str):
        d = _dt.date.fromisoformat(d[:10])
    letters = {3: "H", 6: "M", 9: "U", 12: "Z"}
    qs = [(y, m) for y in (d.year - 1, d.year, d.year + 1) for m in (3, 6, 9, 12)]
    for i, (y, m) in enumerate(qs):
        roll = quarterly_expiry(y, m) - _dt.timedelta(days=roll_days)
        if d < roll or (d == roll and d.weekday() == 4):
            return f"{root}{letters[m]}{y % 10}"
    return None


def flatten_foreign_positions(ib, symbol: str, keep_local_symbol: str, log,
                              own_client_ids=None, max_close=None) -> list:
    """Close a stranded pre-roll leg: a position on `symbol` whose localSymbol is not the contract
    now traded. Returns [(localSymbol, side, qty, trade)].

    2026-09-14 FIX (Codex #3): this used to be ACCOUNT-wide. V9 and R3 now both trade MNQ on one
    account, so "symbol-scoped" no longer isolates them: V9's roll sweep tried to cancel R3's
    old-month bracket (IB refuses a cross-client cancel; the error was swallowed) and then
    market-sold R3's position as well, leaving R3's bracket live to fill into a short.
      own_client_ids : only cancel orders placed by these clientIds.
      max_close      : SIGNED quantity the CALLER's own state holds on rolled-off contracts
                       (+long / -short). Only that much is closed, only in that direction; any
                       remainder belongs to someone else and is warned about, never traded.
    Both None = the legacy account-wide behaviour (no caller uses it any more)."""
    closed: list = []
    if MarketOrder is None:
        return closed
    try:
        ib.reqPositions(); ib.sleep(1)
        ib.reqAllOpenOrders(); ib.sleep(1)
    except Exception:
        pass
    # 1) cancel OUR stray orders on rolled-off contracts first
    try:
        for tr in list(ib.openTrades()):
            c = tr.contract
            ls = getattr(c, "localSymbol", "") or ""
            if getattr(c, "symbol", "") != symbol or not ls or ls == keep_local_symbol:
                continue
            cid = getattr(tr.order, "clientId", None)
            if own_client_ids is not None and cid not in own_client_ids:
                log.warning(f"ROLL: leaving {ls} order {tr.order.orderId} alone -- it belongs to clientId {cid}")
                continue
            try:
                ib.cancelOrder(tr.order)
                log.warning(f"ROLL: cancelled our stray {symbol} order on {ls} (rolled off)")
            except Exception as e:
                log.error(f"ROLL: cancel of {ls} order {tr.order.orderId} FAILED ({e!r})")
    except Exception as e:
        log.error(f"ROLL: could not scan open orders ({e})")
    if own_client_ids is not None:
        try:
            ib.sleep(2)          # let OUR cancels land before any market exit (no bracket race)
        except Exception:
            pass
    # 2) market-flatten stranded positions on rolled-off contracts -- only what is ours
    budget = None if max_close is None else int(max_close)
    try:
        for pos in list(ib.positions()):
            c = pos.contract
            ls = getattr(c, "localSymbol", "") or ""
            if getattr(c, "symbol", "") != symbol or not ls or ls == keep_local_symbol or pos.position == 0:
                continue
            acct = int(pos.position)
            if budget is None:
                q = acct
            else:
                if budget == 0 or (budget > 0) != (acct > 0):
                    log.warning(f"ROLL: {ls} position {acct:+d} on a rolled-off month is NOT ours to "
                                f"close -- left alone. CHECK IT.")
                    continue
                q = min(abs(acct), abs(budget)) * (1 if acct > 0 else -1)
                if abs(acct) > abs(budget):
                    log.warning(f"ROLL: {ls} account {acct:+d} exceeds our {budget:+d} -- closing only ours")
                budget -= q
            side = "SELL" if q > 0 else "BUY"
            qty = abs(q)
            try:
                ib.qualifyContracts(c)
                o = MarketOrder(side, qty); o.tif = "GTC"; o.outsideRth = True
                tr = ib.placeOrder(c, o)
                closed.append((ls, side, qty, tr))
                log.warning(f"ROLL: flattened stranded {ls} {side} {qty} "
                            f"(rolled to {keep_local_symbol}); exit booked on the fill")
            except Exception as e:
                log.error(f"ROLL: failed to flatten {ls}: {e}")
    except Exception as e:
        log.error(f"ROLL: could not scan positions ({e})")
    return closed


if __name__ == "__main__":
    # Offline self-test of the roll picker (no IB needed).
    class _C:
        def __init__(s, ls, exp): s.localSymbol = ls; s.lastTradeDateOrContractMonth = exp; s.symbol = "NQ"
    class _D:
        def __init__(s, ls, exp): s.contract = _C(ls, exp)
    chain = [_D("NQM6", "20260618"), _D("NQU6", "20260918"), _D("NQZ6", "20261218")]
    cases = {
        "20260605": "NQM6",   # 13d out -> front
        "20260610": "NQM6",   # 8d out  -> still front (>7)
        "20260611": "NQU6",   # 7d out  -> roll early
        "20260615": "NQU6",   # roll window
        "20260618": "NQU6",   # expiry day -> roll (never trade the expiring one)
        "20260619": "NQU6",   # NQM6 expired -> front is NQU6
        "20260911": "NQZ6",   # NQU6 now 7d out -> roll to Dec
    }
    ok = True
    for today, expect in cases.items():
        got = pick_front_contract(chain, 7, today)
        ls = got.contract.localSymbol if got else None
        flag = "OK" if ls == expect else "FAIL"
        ok = ok and (ls == expect)
        print(f"  {today}: pick={ls or '-':5} expect={expect:5} {flag}")
    print("ALL OK" if ok else "SOME FAILED")
