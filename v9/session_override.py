"""
session_override.py - read session_override.json (written nightly by preflight_check.py)
and expose the effective last-entry / flatten times for TODAY (ET) to the live bots.

FAIL-SAFE by design: any problem - missing file, parse error, stale date (for_date != today),
malformed times, or a non-half-day status - returns the caller's config DEFAULTS. The bots
therefore behave EXACTLY as before unless there is a fresh, valid half-day override for today.

Used by orb_live_trade.py and v9_live_trade.py.
"""
from __future__ import annotations
import os, json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session_override.json")

_NORMAL = {"is_half_day": False, "status": "normal", "last_entry": None, "eod": None, "note": None}
_cache: dict = {}
_announced: set = set()


def _today_et() -> str:
    return datetime.now(timezone.utc).astimezone(ET).strftime("%Y%m%d")


def _compute(today_str: str) -> dict:
    try:
        with open(PATH, encoding="utf-8") as f:
            o = json.load(f)
    except Exception:
        return dict(_NORMAL)
    if o.get("for_date") != today_str:
        return dict(_NORMAL)                                  # stale / not for today
    status = o.get("status", "normal")
    if status != "half_day":
        return {**_NORMAL, "status": status}
    le, eo = o.get("last_entry"), o.get("eod_flatten")
    if not (isinstance(le, (list, tuple)) and isinstance(eo, (list, tuple))
            and len(le) == 2 and len(eo) == 2):
        return {**_NORMAL, "status": status}                 # malformed -> defaults
    return {"is_half_day": True, "status": "half_day", "note": o.get("note"),
            "last_entry": (int(le[0]), int(le[1])), "eod": (int(eo[0]), int(eo[1]))}


def session_for_today(today_str: str | None = None, log=None) -> dict:
    """Cached per (date, file-mtime). Announces a half-day ONCE per day if `log` is given."""
    today_str = today_str or _today_et()
    try:
        mtime = os.path.getmtime(PATH)
    except Exception:
        mtime = 0.0
    key = (today_str, mtime)
    if key not in _cache:
        _cache.clear()
        _cache[key] = _compute(today_str)
    res = _cache[key]
    if log is not None and res["is_half_day"] and today_str not in _announced:
        _announced.add(today_str)
        le, eo = res["last_entry"], res["eod"]
        log.warning(f"SESSION OVERRIDE (half-day): {res.get('note') or 'early close'} -> "
                    f"last-entry {le[0]:02d}:{le[1]:02d}, flatten {eo[0]:02d}:{eo[1]:02d} ET")
    return res


def last_entry(default, today_str: str | None = None, log=None):
    """Return (H, M) for today's last-entry cutoff: the half-day override, else `default`."""
    s = session_for_today(today_str, log)
    return s["last_entry"] if s["is_half_day"] else default


def eod(default, today_str: str | None = None, log=None):
    """Return (H, M) for today's flatten time: the half-day override, else `default`."""
    s = session_for_today(today_str, log)
    return s["eod"] if s["is_half_day"] else default


# --- shared trading-day / half-day classification (the single source every scheduled task uses) ---
# These delegate the calendar to preflight_check.classify (pandas_market_calendars + a rules fallback
# that includes Juneteenth), so "is today a trading day?" has ONE robust implementation. They are
# FAIL-OPEN: any error returns ('normal') -> a calendar glitch never skips a real session.
def classify_today(today_str: str | None = None):
    """('normal' | 'half_day' | 'closed', close_time_ET_or_None) for the given YYYYMMDD (default TODAY ET)."""
    try:
        if today_str:
            d = datetime.strptime(today_str, "%Y%m%d").date()
        else:
            d = datetime.now(timezone.utc).astimezone(ET).date()
        from preflight_check import classify
        return classify(d)
    except Exception:
        return ("normal", None)


def is_trading_day(today_str: str | None = None) -> bool:
    """True if the day is an OPEN NYSE session (weekends + full holidays -> False). FAIL-OPEN to True."""
    return classify_today(today_str)[0] != "closed"


def is_half_day(today_str: str | None = None) -> bool:
    """True if the day is an early-close half-day (a trading day, but a short one)."""
    return classify_today(today_str)[0] == "half_day"


if __name__ == "__main__":
    import sys as _sys
    _arg = _sys.argv[1] if len(_sys.argv) > 1 else ""
    _day = _sys.argv[2] if len(_sys.argv) > 2 else None      # optional YYYYMMDD to test a specific date
    if _arg in ("is-trading-today", "--is-trading-today"):
        print("TRADING" if is_trading_day(_day) else "CLOSED")
    elif _arg in ("status-today", "--status-today"):
        _st, _ct = classify_today(_day)
        print(("%s %s" % (_st, _ct.strftime("%H:%M") if _ct else "")).strip())
    else:
        print("classify:", classify_today(_day), "| trading:", is_trading_day(_day),
              "| half_day_override:", session_for_today())
