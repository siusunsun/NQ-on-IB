"""dash_push.py - DAILY, MEMORY-SAFE dashboard refresh + push.

Loads ONLY a trailing ~120-day window of /root/v9/data_1m/NQ (read-only), runs the
four deployed sleeves over it, splices the current month into the frozen monthly
table from dash_monthly_base.json, and pushes data/nq.json to GitHub.

Idempotent: re-running with unchanged inputs makes no commit.
Never touches a live bot, config, or running process.

  --dry-run    compute and print the JSON, do not push
  --no-roll    do not append newly-completed months to the frozen base
"""
import sys, os, json, base64, urllib.request, urllib.error
sys.path.insert(0, "/root/v9/bt")
import numpy as np
import pandas as pd
import dash_lib as L

DRY = "--dry-run" in sys.argv
NO_ROLL = "--no-roll" in sys.argv
MONTH_NAMES = 12


# ------------------------------------------------------------------ helpers
def money(v):
    return ("+$" if v >= 0 else "-$") + "{:,.0f}".format(abs(v))


def px(v):
    return "{:,.0f}".format(v)


def month_of(tr):
    return tr["entry_et"].strftime("%Y-%m")


def next_month(mstr):
    return str(pd.Period(mstr, freq="M") + 1)


# ------------------------------------------------------- recent-trades ledger
# 2026-09-01: the "last 5 closed trades" table shows REAL IB FILLS, not model
# trades. The 5 model trades that were already published are frozen in the
# ledger with sort_utc < cutover; each newly-closed LIVE round-trip is appended
# and pushes the oldest row out FIFO, so the table converts one trade at a time.
# Published trades are never retroactively rewritten.
LEDGER_PATH = "/root/v9/bt/dash_trades_ledger.json"
LIVE_LOG    = "/root/v9/v9_live_trade_log.json"
CUTOVER_UTC = "2026-09-01T03:00:00Z"
COMM_RT     = 1.22   # $/contract round-turn commission. Real fill prices already
                     # contain slippage, so the $5.40 model cost must NOT apply here.
PUB_KEYS = ("date", "direction", "entry", "exit", "pnl", "pnlNum", "rMultiple", "sleeve")


def _load_ledger():
    try:
        d = json.load(open(LEDGER_PATH))
        if isinstance(d, dict) and isinstance(d.get("trades"), list):
            d.setdefault("cutover_utc", CUTOVER_UTC)
            return d
        L.log("[trades] ledger malformed - starting empty")
    except IOError:
        L.log("[trades] ledger absent - starting empty")
    except Exception as e:
        L.log("[trades] ledger unreadable (%r) - starting empty" % (e,))
    return {"cutover_utc": CUTOVER_UTC, "trades": []}


def _save_ledger(led):
    tmp = LEDGER_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(led, fh, indent=1)
    os.replace(tmp, LEDGER_PATH)


def _live_round_trips(cutover):
    """Pair live IB fills into closed round-trips per sleeve.

    A fill whose label contains ENTRY opens; the next fill for the same
    instrument closes it. Returns ledger-shaped dicts for round-trips whose
    EXIT is strictly after `cutover`, or None if the log is unusable (caller
    then keeps and republishes the existing ledger unchanged).
    """
    try:
        fills = json.load(open(LIVE_LOG))["fills"]
        if not isinstance(fills, list):
            raise ValueError("fills is not a list")
    except Exception as e:
        L.log("[trades] live log unreadable (%r) - keeping ledger unchanged" % (e,))
        return None

    out, open_ = [], {}
    for f in sorted(fills, key=lambda x: str(x.get("time"))):
        ins = f.get("instrument")
        try:
            ts = pd.Timestamp(f["time"], tz="UTC")
        except Exception:
            continue
        if "ENTRY" in str(f.get("label") or ""):
            open_[ins] = (ts, f)          # a new entry supersedes any stale open
            continue
        ent = open_.pop(ins, None)
        if ent is None:
            continue                      # exit with no matching entry: ignore
        ets, ef = ent
        if ts <= cutover:
            continue                      # pre-cutover history: never re-enters
        # NB: use the EXIT fill qty; `realized` in the log is not guaranteed to
        # match the ENTRY qty (2026-06-30 TLB one-off 5-lot sizing bug).
        q = int(f.get("qty") or 0)
        n = round(float(f.get("realized") or 0.0) - COMM_RT * q, 2)
        tr = {
            "date": ets.tz_convert(L.ET).strftime("%Y-%m-%d"),
            "direction": "Long" if str(ef.get("side", "")).upper().startswith("B") else "Short",
            "entry": px(float(ef["price"])),
            "exit": px(float(f["price"])),
            "pnl": money(n),
            "pnlNum": n,
        }
        if f.get("realized_R") is not None:
            tr["rMultiple"] = "%+.1fR" % float(f["realized_R"])
        tr["sleeve"] = ins
        tr["src"] = "live"
        tr["sort_utc"] = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append(tr)
    return out


def _live_open_positions():
    """Open positions from the REAL IB fill log, using the same ENTRY/exit pairing as
    _live_round_trips. Whatever is still open after replaying every fill is a real
    position. Returns [] when flat, or None if the log is unusable."""
    try:
        fills = json.load(open(LIVE_LOG))["fills"]
        if not isinstance(fills, list):
            raise ValueError("fills is not a list")
    except Exception as e:
        L.log("[positions] live log unreadable (%r) - showing none" % (e,))
        return None
    live_open = {}
    for f in sorted(fills, key=lambda x: str(x.get("time"))):
        ins = f.get("instrument")
        if "ENTRY" in str(f.get("label") or ""):
            live_open[ins] = f       # a new entry supersedes any stale open
        else:
            live_open.pop(ins, None) # any non-entry fill closes it
    return list(live_open.values())


def recent_trades(persist=True):
    """Merge new live round-trips into the ledger; return the published 5."""
    led = _load_ledger()
    cutover = pd.Timestamp(led.get("cutover_utc") or CUTOVER_UTC)
    before = json.dumps(led, sort_keys=True)

    live = _live_round_trips(cutover)
    if live is not None:
        seen = set((t.get("sort_utc"), t.get("sleeve")) for t in led["trades"])
        for t in live:
            k = (t["sort_utc"], t["sleeve"])
            if k in seen:
                continue                  # idempotent: already in the ledger
            seen.add(k)
            led["trades"].append(t)
            L.log("[trades] NEW live round-trip %s %s %s"
                  % (t["sort_utc"], t["sleeve"], t["pnl"]))

    led["trades"].sort(key=lambda t: (str(t.get("sort_utc") or ""),
                                      str(t.get("sleeve") or "")))
    led["trades"] = led["trades"][-5:]

    if persist and json.dumps(led, sort_keys=True) != before:
        _save_ledger(led)
    L.log("[trades] ledger %d rows (%d live) src=%s"
          % (len(led["trades"]),
             sum(1 for t in led["trades"] if t.get("src") == "live"),
             ",".join(str(t.get("src")) for t in led["trades"])))
    # 2026-09-03: publish NEWEST-FIRST, matching every other card (fx/gc/hsi/ml/...).
    # The LEDGER stays chronological on purpose - the FIFO trim above is [-5:].
    return [dict((k, t[k]) for k in PUB_KEYS if k in t) for t in reversed(led["trades"])]


# ------------------------------------------------------------------ github
def gh(method, url, token, body=None):
    req = urllib.request.Request(url, method=method,
                                 data=None if body is None else json.dumps(body).encode())
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "dash-push")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def push(payload):
    token = open(L.TOKEN_FILE).read().strip()
    base = "https://api.github.com/repos/%s/contents/%s" % (L.REPO, L.GHPATH)
    sha = None
    remote = None
    try:
        cur = gh("GET", base + "?ref=" + L.BRANCH, token)
        sha = cur.get("sha")
        remote = json.loads(base64.b64decode(cur["content"]).decode())
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        L.log("[push] remote file absent, creating")

    merged = dict(remote or {})
    merged.update(payload)

    if remote is not None:
        a = {k: v for k, v in remote.items() if k != "lastUpdated"}
        b = {k: v for k, v in merged.items() if k != "lastUpdated"}
        if json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True):
            # 2026-08-31 HEARTBEAT: refresh lastUpdated once per UTC day even when the
            # content is identical, so a stale timestamp on the dashboard means the
            # pipeline is DEAD rather than merely a quiet market. Repeat runs on the
            # same day still make no commit (idempotent).
            _rd = str(remote.get("lastUpdated", ""))[:10]
            _nd = str(merged.get("lastUpdated", ""))[:10]
            if _rd == _nd:
                L.log("[push] no content change, same UTC day - skipping commit (idempotent)")
                return None, merged
            L.log("[push] no content change but new UTC day (%s -> %s) - "
                  "pushing lastUpdated heartbeat" % (_rd or "none", _nd))

    txt = json.dumps(merged, indent=2) + "\n"
    body = dict(message="chore(nq): daily theoretical dashboard refresh %s"
                        % payload["lastUpdated"],
                content=base64.b64encode(txt.encode()).decode(),
                branch=L.BRANCH)
    if sha:
        body["sha"] = sha
    res = gh("PUT", base, token, body)
    return res["commit"]["sha"], merged


# ------------------------------------------------------------------ main
def main():
    L.log("=" * 70)
    avail = L.mem_available_mb()
    L.log("[daily] START memAvail=%.0fMB dry=%s" % (avail, DRY))
    if avail < L.MIN_FREE_MB:
        L.log("[daily] ABORT: MemAvailable %.0fMB < %dMB guard - not pushing"
              % (avail, L.MIN_FREE_MB))
        return 3

    if not os.path.exists(L.BASE_JSON):
        L.log("[daily] ABORT: missing frozen base %s (run dash_base_build.py)" % L.BASE_JSON)
        return 4
    base = json.load(open(L.BASE_JSON))

    # ---- data window ---------------------------------------------------
    df, cutoff, nfiles = L.load_window()
    edge = df.index[-1]
    edge_et = edge.tz_convert(L.ET)
    now = pd.Timestamp.utcnow()
    stale_days = (now - edge).total_seconds() / 86400.0
    L.log("[daily] window %s .. %s  bars=%d files=%d  edgeAge=%.2fd  rss=%.0fMB"
          % (df.index[0], edge, len(df), nfiles, stale_days, L.rss_mb()))
    if stale_days > L.MAX_STALE_DAYS:
        L.log("[daily] ABORT: archive stale by %.2f days (> %d) - not pushing"
              % (stale_days, L.MAX_STALE_DAYS))
        return 5

    # ---- coverage guard: window must fully cover every unfrozen month ---
    lastf = base["lastFrozenMonth"]
    need_from = pd.Timestamp(next_month(lastf) + "-01")
    win_start_et = df.index[0].tz_convert(L.ET).tz_localize(None).normalize()
    if win_start_et > need_from:
        L.log("[daily] ABORT: window starts %s but must cover from %s "
              "(frozen base too old - re-run dash_base_build.py)"
              % (win_start_et.date(), need_from.date()))
        return 6

    # ---- regime: cached full-history daily closes + fresh window closes -
    cached = L.load_daily_closes()
    fresh = L.daily_from_window(df)
    daily = L.merge_daily(cached, fresh)
    win_dates = set(d.date() for d in fresh.index)
    regime = L.regimes_for(daily, win_dates)
    L.log("[daily] daily closes=%d  regime days in window=%d" % (len(daily), len(regime)))

    # ---- run the book ---------------------------------------------------
    bt = L.harness()["bt"]
    bars5 = bt.build_5min(df)
    book = L.run_book(df, bars5, regime)
    L.log("[daily] trades in window: " + "  ".join(
        "%s=%d" % (k, len(v)) for k, v in book.items()) + "  rss=%.0fMB" % L.rss_mb())

    allt = [t for v in book.values() for t in v]
    closed = [t for t in allt if t["exit_reason"] != "DATA_END"]
    open_ = [t for t in allt if t["exit_reason"] == "DATA_END"]

    # ---- last 5 closed trades: REAL FILLS via the ledger ----------------
    trades = recent_trades(persist=not DRY)

    # ---- open positions: REAL IB fills (2026-09-03) ----------------------
    # WAS: the modelled book at the data edge, which lags ~1 ET day and therefore
    # showed positions the account had already closed. Now derived from the same
    # fill log that feeds the trades table, so the card cannot show a phantom.
    last_px = float(df["close"].iloc[-1])
    positions = []
    for t in (_live_open_positions() or []):
        q = int(t.get("qty") or L.QTY.get(t.get("instrument"), 1))
        sgn = 1.0 if str(t.get("side", "")).upper().startswith("B") else -1.0
        upl = sgn * (last_px - float(t["price"])) * L.MNQ * q
        positions.append({
            "symbol": "MNQ",
            "direction": "Long" if sgn > 0 else "Short",
            "entry": px(float(t["price"])),
            "current": px(last_px),
            "size": q,
            "pnl": money(upl),
            "pnlNum": round(upl, 2),
        })

    # ---- monthly: roll completed months, splice current -----------------
    cur_month = edge_et.strftime("%Y-%m")
    by_month = {}
    for t in closed + open_:
        if t["exit_reason"] == "DATA_END":
            continue                       # unrealised: not booked to a month
        by_month.setdefault(month_of(t), 0.0)
        by_month[month_of(t)] += L.net_usd(t)

    months = dict(base["months"])
    eq = float(base["equityAfterFrozen"])
    rolled = []
    m = next_month(lastf)
    while m < cur_month:
        v = round(by_month.get(m, 0.0), 2)
        months[m] = dict(pnl=v, pct=round(100.0 * v / eq, 6),
                         equityStart=round(eq, 2), equityEnd=round(eq + v, 2))
        eq += v
        rolled.append(m)
        lastf = m
        m = next_month(m)

    if rolled and not NO_ROLL:
        base["months"] = months
        base["lastFrozenMonth"] = lastf
        base["equityAfterFrozen"] = round(eq, 2)
        base["generated"] = pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        tmp = L.BASE_JSON + ".tmp"
        json.dump(base, open(tmp, "w"), indent=1)
        os.replace(tmp, L.BASE_JSON)
        L.log("[daily] ROLLED into frozen base: %s  equityAfterFrozen=$%.2f"
              % (",".join(rolled), eq))
    elif rolled:
        L.log("[daily] would roll %s (--no-roll set)" % ",".join(rolled))

    cur_pnl = round(by_month.get(cur_month, 0.0), 2)
    cur_pct = 100.0 * cur_pnl / eq
    L.log("[daily] current month %s  net=$%.2f  base equity=$%.2f  -> %+.2f%%"
          % (cur_month, cur_pnl, eq, cur_pct))

    table = dict(months)
    table[cur_month] = dict(pnl=cur_pnl, pct=cur_pct,
                            equityStart=round(eq, 2), equityEnd=round(eq + cur_pnl, 2))

    monthly = []
    for y in sorted(set(int(k[:4]) for k in table)):
        arr = [None] * MONTH_NAMES
        ytd = 1.0
        for i in range(1, 13):
            k = "%04d-%02d" % (y, i)
            if k in table:
                arr[i - 1] = round(table[k]["pct"], 1)
                # compound the UNROUNDED month returns (matches _cagr.py)
                ytd *= (1 + table[k]["pct"] / 100.0)
        # 2026-09-03: mark the CURRENT year live so the table renders the live dot and the
        # "MTD" superscript on the in-progress month (same convention as the GC card).
        # Earlier years stay False, so the "not all rows live" legend is unaffected.
        monthly.append({"year": y, "months": arr,
                        "ytd": round(100.0 * (ytd - 1), 1),
                        "isLive": (y == int(cur_month[:4]))})

    payload = {
        "lastUpdated": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "basis": "Monthly figures are strategy returns - signal-level at deployed size, MNQ $2/pt, net of modelled cost $5.40/contract round-turn (incl. 1.88pt measured slippage). Recent trades are actual IB fills, net of commission. Live execution since Jul 2026.",
        "recentTrades": trades,
        "positions": positions,
        "monthlyReturns": monthly,
    }

    if DRY:
        print(json.dumps(payload, indent=2))
        L.log("[daily] DRY-RUN done  peakRSS=%.0fMB" % L.rss_mb())
        return 0

    sha, merged = push(payload)
    print(json.dumps(merged, indent=2))
    L.log("[daily] pushed commit=%s  peakRSS=%.0fMB" % (sha or "(no change)", L.rss_mb()))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as e:
        import traceback
        L.log("[daily] FAILED: %r" % (e,))
        L.log(traceback.format_exc())
        rc = 1
    sys.exit(rc)
