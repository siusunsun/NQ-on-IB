"""v9.1 Paper Trading Bot — live IB main loop.

Hard-pinned to TWS paper port 7497 with clientId 19 (parallel with the ORB bot on 18).
Trades 10 MNQ contracts (= 1 NQ notional equivalent).

PRE-FLIGHT:
  - Start TWS paper account (DU prefix), enable API on port 7497
  - Run `python tools/v9_today_signal.py` to see the regime gate state
  - Start this bot AT OR BEFORE 09:30 ET so VWAP+pivot warm-up has full RTH data

Per-day flow:
  1. Determine the regime cell from prior day's daily close
  2. Mark sleeves active/inactive based on cell
  3. Subscribe to live MNQ 5-min bars
  4. On each closed bar, check each ACTIVE sleeve for entry signals
  5. If signal: place MKT entry, then STP-protective + LMT-target server-side
  6. EOD flatten at 15:20 ET — TLB only; VWAP sleeves run to their server-side
     bracket (3R target / swing stop) and may hold into the evening / overnight
  7. Daily-loss-limit halt: if cum P&L <= -10,000 USD, flatten ALL sleeves for the day

This bot is OBSERVE-AND-LOG only when first deployed — set ENABLE_LONG/ENABLE_SHORT
to False to dry-run the regime+signal logic without placing any orders.
"""
from __future__ import annotations

# Master arm flags — set to True ONLY after you've watched the bot dry-run a full session
# ARMED 2026-06-04 10:30 BST — paper account DU4234078, 10 MNQ = 1 NQ equiv
ENABLE_LONG  = True
ENABLE_SHORT = True

import asyncio
import logging
import sys
import time
from datetime import datetime, time as dtime, timezone
from pathlib import Path
import pathlib
from typing import Optional
import pandas as pd
import pytz

# These imports require ib_async to be installed (already in the user's env per ORB bot)
try:
    from ib_async import IB, Future, MarketOrder, StopOrder, LimitOrder, util
except ImportError:
    print("ERROR: ib_async not installed. Install with: pip install ib_async")
    sys.exit(1)

import v9_config as C
from v9_state import BotState, SleeveState
from v9_strategy_lib import (
    compute_regime, DailyRegime, VwapState, is_in_window
)
from v9_execution import (
    FiveMinAggregator, TLBLive, place_market_entry, place_protective_stop,
    place_limit_target, cancel_order, market_flatten, record_v9_fill,
)
import session_override as so          # half-day flatten/last-entry override (preflight_check.py)

# NQ-data -> MNQ-exec split (backward-compatible, 2026-06-12): if the config defines INSTRUMENT_EXEC,
# signals come from INSTRUMENT (NQ data) and ALL orders route to INSTRUMENT_EXEC (MNQ). If it does NOT
# (paper single-contract config), EXEC_INST IS INSTRUMENT so DATA == EXEC and behaviour is identical.
DATA_INST = C.INSTRUMENT
EXEC_INST = getattr(C, "INSTRUMENT_EXEC", C.INSTRUMENT)
# $ per point is driven by the EXEC contract (what actually fills), not the data contract.
POINT_VALUE_TOTAL = EXEC_INST["point_value"] * EXEC_INST["qty"]

ET = pytz.timezone("America/New_York")

# ---- Safety check FIRST ----
C.safety_check()

# ---- Logging ----
log = logging.getLogger("v9")

# 2026-06-22: pipe WARNING+ and important INFO events to Telegram via HSI alert.sh
try:
    import v9_tg
    v9_tg.install_handler(log)
except Exception as _e:
    pass
log.setLevel(logging.INFO)
fh = logging.FileHandler(C.LOG_FILE, encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s UTC [%(levelname)s] %(message)s"))
log.addHandler(fh)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(sh)


def now_et() -> datetime:
    return datetime.now(timezone.utc).astimezone(ET)


def load_daily_closes_for_regime() -> pd.Series:
    """Load daily closes from the smc-backtest NQ data for regime computation.

    Reads the NQ 1-min files, aggregates to daily by ET date, returns the close series.
    """
    nq_dir = Path("/root/v9/data_1m/NQ")
    files = sorted(nq_dir.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"No NQ data in {nq_dir}")
    # Lazy import to avoid forcing smc-backtest into the bot's dependency tree
    sys.path.insert(0, "/root/v9")
    from src.engine import load_csv
    df = pd.concat([load_csv(f) for f in files]).sort_index(kind="mergesort")  # 2026-09-14: STABLE sort -- the default is not stable, so among duplicate timestamps keep="last" did NOT mean the newest file; an older seam could shadow a corrected re-pull
    if df.index.tz is None: df = df.tz_localize("UTC")
    # keep="last": a newer (corrected) pull overrides an older bad/truncated one.
    # keep="first" (the old behaviour) let stale/corrupt bars permanently shadow good
    # re-pulls, turning a transient IB disconnect into persistent regime corruption.
    df = df[~df.index.duplicated(keep="last")]
    df = df.copy()
    df["et_date"] = df.index.tz_convert(ET).date
    # DATA-INTEGRITY WARNING: a truncated pull (IB gap/disconnect) leaves a recent ET day
    # with far fewer than the ~1380 one-min bars of a full CME session; its "last close"
    # then resolves to a wrong/earlier bar. Warn so the Polygon cross-check can override.
    try:
        counts = df.groupby("et_date").size()
        if len(counts) >= 4:
            # Baseline from FULL weekday sessions only. Sunday CME opens 18:00 ET (~360 bars)
            # and holidays/half-days are legitimately short — never flag those as truncated.
            wd = [getattr(d, "weekday", lambda: 0)() < 5 for d in counts.index]
            full = counts[wd]
            med = float(full.iloc[:-1].median()) if len(full) >= 2 else float(counts.median())
            for d, n in counts.iloc[-4:-1].items():         # last 3 COMPLETE days
                if getattr(d, "weekday", lambda: 0)() >= 5:
                    continue                                 # Sat/Sun: short by design, skip
                if med and n < 0.5 * med:
                    log.warning(f"DATA INTEGRITY: ET {d} has only {int(n)} bars "
                                f"(median {int(med)}) — likely a truncated pull "
                                f"(IB gap/disconnect). Regime close for this day may be "
                                f"unreliable; Polygon cross-check will verify/override.")
    except Exception:
        pass
    daily = df.groupby("et_date").agg({"close": "last"})
    bar_counts = df.groupby("et_date").size()

    # HARD CALENDAR GATE (2026-07-08): the median heuristic above only WARNS. The most
    # recent date is about to become reg.close (feeds SMA200/ret20 directly) — verify it
    # against the calendar-aware expected bar count (holidays/half-days included) and
    # DROP it (fall back to the prior complete day) rather than lock a truncated close.
    # See us_market_holidays.expected_minute_bars / [[feedback-data-completeness]].
    try:
        from us_market_holidays import is_day_complete
        while len(daily) > 0:
            last_date = daily.index[-1]
            n = int(bar_counts.get(last_date, 0))
            ok, expected = is_day_complete(last_date, n)
            if ok:
                break
            log.error(f"REGIME DATA GATE: ET {last_date} has only {n} bars "
                      f"(expected ~{expected}) — truncated pull, DROPPING this day. "
                      f"Falling back to the prior complete day for regime close.")
            daily = daily.iloc[:-1]
            bar_counts = bar_counts.iloc[:-1]
    except Exception as e:
        log.warning(f"regime data gate check failed ({e}) — proceeding without it")

    daily.index = pd.to_datetime(daily.index)
    bar_counts.index = pd.to_datetime(bar_counts.index)
    return daily["close"], bar_counts


class V9Bot:
    """The main bot orchestrator."""

    def __init__(self):
        self.ib: Optional[IB] = None
        self.state: Optional[BotState] = None
        self.regime: Optional[DailyRegime] = None
        self.contract: Optional[Future] = None        # EXEC contract (MNQ live / NQ paper) — all orders
        self.data_contract: Optional[Future] = None    # DATA contract (NQ) — realtime bars + warm-up (signals)
        # VWAP sleeve states (separate states for LONG and SHORT sleeves)
        self.vwap_long_state = VwapState(
            side='LONG',
            entry_band_k=C.VWAP_LONG["entry_band_k"],
            n_bars_outside=C.VWAP_LONG["n_bars_outside"],
            swing_lookback=C.VWAP_LONG["swing_lookback"],
            post_halt_blackout_bars=C.VWAP_LONG.get("post_halt_blackout_bars", 0),
        )
        self.vwap_short_state = VwapState(
            side='SHORT',
            entry_band_k=C.VWAP_SHORT["entry_band_k"],
            n_bars_outside=C.VWAP_SHORT["n_bars_outside"],
            swing_lookback=C.VWAP_SHORT["swing_lookback"],
        )
        # TLB-LONG live detector + 5-sec -> 5-min aggregator
        self.tlb_state = TLBLive(k=C.TLB["pivot_k"], r_multiple=C.TLB["r_multiple"],
                                 hybrid_stop_pts=C.TLB.get("hybrid_stop_pts", 0.0),
                                 hybrid_lookback=C.TLB.get("hybrid_lookback", 20))
        self.agg = FiveMinAggregator(C.VWAP_LONG["bar_min"])
        # Live IB order handles per sleeve: name -> {entry, stop, target}
        self.live: dict[str, dict] = {}
        self._flattening: dict[str, dict] = {}   # event-driven flatten per sleeve (Codex #4)
        self._quarantined: set[str] = set()      # unverifiable position: no auto-orders (Codex #7)
        self._flat_alert_t: float = 0.0
        self._last_close: Optional[float] = None
        # Wall-clock (UTC) of the last realtime bar received — drives the bar-stall watchdog
        self._last_bar_utc: Optional[datetime] = None
        # Bar-stall throttle: when continuous darkness began (None = feed alive), and whether
        # we've already escalated a long stall to ERROR (so repeats stay silent, no log flood).
        self._stall_since: Optional[datetime] = None
        self._stall_escalated: bool = False

    # ---- Pre-flight: refresh data ----
    def refresh_data(self) -> None:
        """Call the auto-pull tool to bring data up to date before regime computation.

        Failure to refresh is logged as a WARNING but does not abort startup —
        the bot will use whatever data is available. Regime accuracy depends on
        the data file being current.
        """
        sys.path.insert(0, "/root/v9")
        try:
            from tools.v9_pull_latest_data import run as pull_run
            log.info("Refreshing NQ data via IB...")
            result = pull_run()
            log.info(f"Data pull result: {result}")
        except Exception as e:
            log.warning(f"Data refresh FAILED: {e}. Continuing with existing data — regime may be stale.")

    # ---- Connection ----
    def connect_ib(self, first: bool = True) -> None:
        self.ib = IB()
        log.info(f"Connecting to IB at {C.IB_HOST}:{C.IB_PORT} clientId={C.CLIENT_ID}")
        # Connect WITH RETRY. After a reboot or IB's Sunday maintenance the Gateway can be UP
        # (listening on the port) yet not reconnected to IB's servers for ~10-20 min. A single
        # connect would fail and the bot would EXIT, staying down the whole session -- this bit
        # us 2026-06-08 (v9 woke at 04:56, Gateway didn't finish reconnecting until ~05:15).
        # Retry every 60s for ~30 min so the bot self-heals once the Gateway is API-ready.
        # Retry FOREVER (2026-06-10): the old 30-attempt (~30 min) budget was outlived when an
        # unscheduled reboot + IB's nightly reset left the Gateway re-authenticating for ~2h —
        # v9 exited and stayed DEAD until manually relaunched (missing its overnight window).
        # A waiting bot is strictly better than a dead one: it cannot manage anything while
        # disconnected anyway, and any open position is protected by its server-side GTC
        # bracket. Log shape: WARN each of the first 30 attempts, ONE ERROR at attempt 31,
        # then WARN every 30th attempt — a multi-hour outage can't flood the log.
        # Telegram-noise policy (2026-07-29): the Gateway's nightly restart/re-auth routinely
        # takes up to ~SILENT_MIN minutes, during which every connect times out. Stay SILENT on
        # Telegram for that window (INFO -> file only) so a routine nightly reconnect produces
        # ZERO alerts. Escalate to Telegram ONCE (ERROR) only if the outage outlasts the routine
        # window (a genuinely stuck Gateway), then a WARNING hourly, plus a single all-clear on
        # reconnect — but only if we had actually alerted.
        SILENT_MIN = 60
        attempt = 0
        alerted = False
        while True:
            attempt += 1
            try:
                self.ib.connect(C.IB_HOST, C.IB_PORT, clientId=C.CLIENT_ID, timeout=15)
                if attempt > 1:
                    if alerted:
                        log.warning(f"IB RECONNECTED after {attempt} attempts (~{attempt} min) — Gateway back online.")
                    else:
                        log.info(f"IB connected on attempt {attempt}.")
                break
            except Exception as e:
                if attempt <= SILENT_MIN:
                    log.info(f"IB connect failed (attempt {attempt}): {e!r} — Gateway re-authenticating "
                             f"(routine nightly restart); retrying in 60s (Telegram-silent < {SILENT_MIN}m)...")
                elif attempt == SILENT_MIN + 1:
                    alerted = True
                    log.error(f"IB connect STILL failing after ~{SILENT_MIN} min ({attempt} attempts): {e!r}. "
                              f"Beyond the usual nightly re-auth — check the Gateway / IBC. "
                              f"Retrying every 60s; next Telegram ping in ~60 min.")
                elif attempt % 60 == 0:
                    alerted = True
                    log.warning(f"IB connect still failing (attempt {attempt}, ~{attempt} min): {e!r}")
                else:
                    log.info(f"IB connect failed (attempt {attempt}): {e!r}; retrying in 60s...")
                try:
                    self.ib.disconnect()
                except Exception:
                    pass
                time.sleep(60)
        accts = self.ib.managedAccounts()
        for a in accts:
            if not a.startswith(C.ACCOUNT_PREFIX):
                log.error(f"REFUSING: account '{a}' is not a paper account (prefix '{C.ACCOUNT_PREFIX}').")
                self.ib.disconnect()
                raise SystemExit(1)
        log.info(f"Connected. Accounts: {accts}")
        # 2026-09-14 FIX (Codex #15): contracts are PINNED for the life of the process. A reconnect
        # used to re-resolve them, so across a roll the bot subscribed to the new month while its
        # VWAP sums and pivot history were still built on the old one. The roll happens only at the
        # nightly restart (a fresh process, fresh warm-up).
        if not first and self.contract is not None:
            log.info(f"reconnect: keeping PINNED contracts data={self.data_contract.localSymbol} "
                     f"exec={self.contract.localSymbol}")
            self.ib.errorEvent += self._on_error
            return
        from contract_roll import pick_front_contract, expiry_crosscheck
        _roll_days = getattr(C, "ROLL_DAYS_BEFORE_EXPIRY", 7)
        _today = now_et().strftime("%Y%m%d")

        def _resolve(inst, label):
            base = Future(symbol=inst["symbol"], exchange=inst["exchange"], currency=inst["currency"])
            details = self.ib.reqContractDetails(base)
            if not details:
                log.error(f"Could not resolve {label} ({inst['symbol']}) contract details. Aborting.")
                self.ib.disconnect(); raise SystemExit(1)
            if label == "DATA":
                try:
                    expiry_crosscheck(details, log)       # Codex #13: calendar vs IB's truth
                except Exception as e:
                    log.warning(f"expiry cross-check skipped: {e!r}")
            front_exp = sorted(details, key=lambda d: d.contract.lastTradeDateOrContractMonth)[0].contract.lastTradeDateOrContractMonth
            ch = pick_front_contract(details, _roll_days, _today)
            if ch is None:
                log.error(f"Every {inst['symbol']} contract is expired?! Aborting.")
                self.ib.disconnect(); raise SystemExit(1)
            rolled = "  [ROLLED EARLY past " + front_exp + "]" if ch.contract.lastTradeDateOrContractMonth != front_exp else ""
            log.info(f"Resolved {label} contract: {ch.contract.localSymbol} expiry={ch.contract.lastTradeDateOrContractMonth}{rolled}")
            return ch.contract

        # DATA (NQ) feeds the signals; EXEC (MNQ live / same as DATA on paper) takes ALL orders.
        self.data_contract = _resolve(DATA_INST, "DATA")
        self.contract = self.data_contract if EXEC_INST is DATA_INST else _resolve(EXEC_INST, "EXEC")
        # If data & exec are different symbols they MUST share the expiry (CME equity-index roll
        # calendar) — else signals read one quarter while orders hit another. Refuse to run on desync.
        if self.contract.lastTradeDateOrContractMonth != self.data_contract.lastTradeDateOrContractMonth:
            log.error(f"ROLL DESYNC: data {self.data_contract.localSymbol} "
                      f"({self.data_contract.lastTradeDateOrContractMonth}) != exec {self.contract.localSymbol} "
                      f"({self.contract.lastTradeDateOrContractMonth}). Aborting.")
            self.ib.disconnect(); raise SystemExit(1)
        # The stranded-leg sweep now runs from run() via _roll_sweep(), AFTER state is loaded
        # (Codex #3) -- it needs to know how much of any old-month position is actually ours.
        self.ib.errorEvent += self._on_error          # re-subscribe data / log connectivity events

    def _on_error(self, reqId, errorCode, errorString, contract=None) -> None:
        """IB connectivity events. The daily ~12:15 BST IB server reconnect lands here:
          1100 = connectivity to IB LOST;  1101 = RESTORED but market data LOST (must re-subscribe);
          1102 = RESTORED, data maintained (no action). A true API-socket drop (not just an IB
        server blip) is handled separately by the isConnected() check in run()'s loop."""
        # 2026-08-03 (FX-session UX request): PAIR every 1100 "LOST" alert with a restore alert.
        # 1100 is WARNING (-> Telegram) but 1102 was INFO (silent), so the user saw "LOST" and never
        # the all-clear and assumed IB was still down. Now 1100 stamps _ib_lost_at; the matching
        # restore (1101 or 1102) reaches Telegram WITH the self-heal duration. Unpaired 1102s (no
        # preceding 1100) stay INFO/silent so routine blips don't add noise.
        if errorCode == 1100:
            self._ib_lost_at = datetime.now(timezone.utc)
            log.warning("IB 1100: connectivity to IB LOST (server reset?) — awaiting restore...")
        elif errorCode == 1101:
            _lost = getattr(self, "_ib_lost_at", None)
            _sec = f" after {(datetime.now(timezone.utc) - _lost).total_seconds():.0f}s" if _lost else ""
            log.warning(f"IB 1101: connectivity RESTORED{_sec}, market data LOST — re-subscribing bars.")
            self._ib_lost_at = None
            try:
                self._subscribe_bars()
            except Exception as e:
                log.error(f"re-subscribe after 1101 failed: {e!r}")
        elif errorCode == 1102:
            _lost = getattr(self, "_ib_lost_at", None)
            if _lost is not None:
                _sec = (datetime.now(timezone.utc) - _lost).total_seconds()
                log.warning(f"IB 1102: connectivity RESTORED — self-healed after {_sec:.0f}s, nothing lost.")
                self._ib_lost_at = None
            else:
                log.info("IB 1102: connectivity restored, data maintained (no action).")

    def _subscribe_bars(self) -> None:
        """(Re)subscribe to live 5-sec realtime bars; cancel any prior subscription first so a
        re-subscribe after a drop / 1101 doesn't stack duplicate handlers."""
        prev = getattr(self, "bars", None)
        if prev is not None:
            try:
                self.ib.cancelRealTimeBars(prev)
            except Exception:
                pass
        log.info(f"Subscribing to {self.data_contract.localSymbol} (signal data) realtime bars (5s -> 5min)")
        self.bars = self.ib.reqRealTimeBars(self.data_contract, 5, "TRADES", False)
        self.bars.updateEvent += self._on_realtime_bar
        # Start the stall clock at subscribe time so the fresh subscription gets a full
        # BAR_STALL_SEC window to deliver its first bar before the watchdog can fire.
        self._last_bar_utc = datetime.now(timezone.utc)

    def _nq_should_have_bars(self) -> bool:
        """True when CME NQ is in open hours (realtime bars should be flowing). Gates the
        bar-stall watchdog so the daily 17:00-18:00 ET settlement halt and the weekend
        close don't trigger a spurious re-subscribe. CME NQ (GLOBEX): Sun 18:00 ET ->
        Fri 17:00 ET, with a daily 17:00-18:00 ET halt; closed all day Saturday."""
        et = now_et()
        wd = et.weekday()                 # Mon=0 .. Sat=5, Sun=6
        mins = et.hour * 60 + et.minute
        if wd == 5:                                   # Saturday — closed all day
            return False
        if wd == 6 and mins < 18 * 60:                # Sunday before 18:00 ET — closed
            return False
        if wd == 4 and mins >= 17 * 60:               # Friday from 17:00 ET — weekend close
            return False
        if 17 * 60 <= mins < 18 * 60:                 # daily settlement halt 17:00-18:00 ET
            return False
        return True

    def _bar_stall_check(self, now_u: datetime) -> bool:
        """Bar-stall watchdog decision for one heartbeat. Returns True if the caller should
        re-subscribe to self-heal a silent feed stall. Logs WARN once on entering a stall,
        then stays silent on the per-cycle re-subscribes, and emits ONE ERROR if the feed
        stays dark > ~10 min during open hours — so a long outage (e.g. an unflagged CME
        holiday the weekly-calendar gate can't see) gets one loud alert instead of a WARN
        flood that could mask a real stall. _stall_since persists across the re-subscribe
        (which resets _last_bar_utc) and is cleared only by a real bar in _on_realtime_bar.
        Caller guards isConnected(); this owns the None-guard, the open-hours gate, the gap,
        and the throttle, so it is unit-testable with a fake now_u and no IB loop."""
        if self._last_bar_utc is None or not self._nq_should_have_bars():
            return False
        gap = (now_u - self._last_bar_utc).total_seconds()
        if gap <= C.BAR_STALL_SEC:
            return False
        if self._stall_since is None:                 # entering a stall
            self._stall_since = self._last_bar_utc    # darkness began at the last real bar
            log.warning(f"BAR STALL: no realtime bar in {gap:.0f}s during CME open hours "
                        f"— re-subscribing (silent feed stall?).")
        else:
            dark = (now_u - self._stall_since).total_seconds()
            _port = getattr(C, "IBC_COMMAND_PORT", 0)   # 0 on the LIVE config -> never self-restarts
            if dark > 600 and not getattr(self, "_stall_escalated", False):   # 10 min: reconnect farms
                self._stall_escalated = True
                if _port:
                    log.error(f"BAR STALL: feed dark {dark / 60:.0f} min during CME open hours "
                              f"— requesting IBC RECONNECTDATA (farm reconnect, no restart).")
                    try:
                        from ibc_recover import recover_gateway
                        log.warning(f"gateway self-heal: {recover_gateway('reconnect', _port)}")
                    except Exception as e:
                        log.error(f"RECONNECTDATA request FAILED: {e!r}")
                else:
                    log.error(f"BAR STALL: feed dark {dark / 60:.0f} min during CME open hours "
                              f"— re-subscribing each cycle; check the data farm / Gateway.")
            elif dark > 900 and not getattr(self, "_stall_restarted", False):   # 15 min: restart GW
                self._stall_restarted = True
                if _port:
                    log.error(f"BAR STALL: STILL dark {dark / 60:.0f} min after reconnect "
                              f"— requesting IBC RESTART (full session-preserving Gateway restart).")
                    try:
                        from ibc_recover import recover_gateway
                        log.warning(f"gateway self-heal: {recover_gateway('restart', _port)}")
                    except Exception as e:
                        log.error(f"RESTART request FAILED: {e!r}")
        return True

    # ---- Regime stability cache (lock the day's cell; no silent flips on data-pull wobble) ----
    def _load_regime_cache(self):
        """Return the cached DailyRegime for the locked prior-day, or None if absent/unreadable."""
        import json
        from v9_strategy_lib import DailyRegime
        try:
            with open(C.REGIME_CACHE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return DailyRegime(date_et=d["date_et"], close=float(d["close"]),
                               sma200=float(d["sma200"]), ret20=float(d["ret20"]),
                               s200=bool(d["s200"]), r20=bool(d["r20"]))
        except Exception:
            return None

    def _save_regime_cache(self, reg) -> None:
        import json
        try:
            with open(C.REGIME_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(dict(date_et=reg.date_et, close=reg.close, sma200=reg.sma200,
                               ret20=reg.ret20, s200=reg.s200, r20=reg.r20,
                               computed_at_utc=datetime.now(timezone.utc).isoformat()), f, indent=2)
        except Exception as e:
            log.warning(f"could not write regime cache: {e!r}")

    def _polygon_daily_close(self, date_et_iso: str, ticker):
        """Independent prior-day close from Polygon futures — same aggregation as the IB
        1-min path (last 1-min close of the ET day). Survives an IB disconnect entirely
        (different provider), so it can referee even when IB itself is the failed source.
        Fail-safe: any error/missing-key/no-data -> None (caller falls back to IB settle)."""
        if not ticker:
            return None
        import os, urllib.request, json
        key = os.environ.get("POLYGON_API_KEY")
        if not key:
            try:
                key = pathlib.Path("/root/v9/.polygon_key").read_text().strip()
            except Exception:
                key = None
        if not key:
            return None
        try:
            gte = int(pd.Timestamp(date_et_iso, tz="UTC").value)
            lt  = int((pd.Timestamp(date_et_iso, tz="UTC") + pd.Timedelta(days=2)).value)
            u = (f"https://api.polygon.io/futures/v1/aggs/{ticker}"
                 f"?resolution=1minute&window_start.gte={gte}&window_start.lt={lt}"
                 f"&order=asc&limit=50000&apiKey={key}")
            rows = []
            for _ in range(6):                       # paginate cap
                with urllib.request.urlopen(u, timeout=20) as r:
                    d = json.load(r)
                rows += d.get("results", [])
                nxt = d.get("next_url")
                if not nxt:
                    break
                u = nxt + f"&apiKey={key}"
            if not rows:
                return None
            pdf = pd.DataFrame(rows)
            pdf["t"] = pd.to_datetime(pdf["window_start"], unit="ns", utc=True)
            pdf = pdf.set_index("t").sort_index()
            pdf["et_date"] = pdf.index.tz_convert(ET).date
            sel = pdf[pdf["et_date"] == pd.Timestamp(date_et_iso).date()]
            if len(sel) == 0:
                return None
            return float(sel.iloc[-1]["close"])
        except Exception as e:
            log.warning(f"Polygon cross-check skipped ({e!r})")
            return None

    @staticmethod
    def _with_close(closes, date_et, value):
        """Return `closes` with the close for ET date `date_et` REPLACED by `value`.
        2026-09-14 FIX: the closes index holds datetime.date (from groupby(et_date)) while the old
        code keyed the assignment with a pd.Timestamp. Under pandas 3 those do not match, so .loc
        APPENDED a second row and left the bad close in place, then compute_regime raised
        TypeError on the mixed index and the bot silently kept the bad value. Every regime-close
        safety net in _crosscheck_regime_close was affected (all four substitution sites)."""
        want = str(date_et)[:10]
        fixed = closes.copy()
        key = next((k for k in fixed.index if pd.Timestamp(k).date().isoformat() == want), None)
        if key is None:
            key = pd.Timestamp(want).date()
        fixed.loc[key] = value
        return fixed.sort_index()

    def _crosscheck_regime_close(self, closes, fresh):
        """Verify the prior-day regime close against INDEPENDENT sources so an IB
        disconnect/gap (which can truncate the 1-min pull and yield a wrong daily close —
        the 30/6 incident) cannot silently arm the wrong sleeves.

        Referee order:
          1. POLYGON futures (independent provider; survives an IB outage). Agree within
             REGIME_POLY_MAX_DEV (0.3%) -> keep 1-min close. Disagree -> substitute
             Polygon's close + recompute (it is the trusted independent value).
          2. Fallback: IB settled daily bar (only used if Polygon is unavailable), with
             the looser REGIME_CLOSE_MAX_DEV (1.5%) gross-error gate.
          3. Neither available -> keep 1-min close (fail-open on price, but the bar-count
             integrity warning + DATA STALE guard still apply).
        Fail-safe throughout: any lookup error degrades to the next referee, never blocks."""
        poly_dev_max = getattr(C, "REGIME_POLY_MAX_DEV", 0.003)
        max_dev = getattr(C, "REGIME_CLOSE_MAX_DEV", 0.015)
        # 2026-09-14: query the month that PRINTED this date's close, not today's contract. After a
        # roll the archive's pre-roll closes are the OLD month; comparing them with the NEW month's
        # Polygon close was a 0.7% false alarm every quarter -- and with the substitution now actually
        # working, it would have spliced the wrong month's price into the regime.
        try:
            from contract_roll import close_symbol_for_date
            ticker = close_symbol_for_date(fresh.date_et, DATA_INST["symbol"])                 or getattr(self.data_contract, "localSymbol", None)
        except Exception:
            ticker = getattr(self.data_contract, "localSymbol", None)

        # --- 0. Incomplete-session override (Sunday/partial day) ---
        # Friend's spec: Sunday Globex sessions ARE included but use IB-settle for the
        # close value from a truncated weekday pull is unreliable. Sundays (~360 bars) are
        # exempt — their short session is normal, not a data gap.
        MIN_BARS_COMPLETE = 700
        prior_date = pd.Timestamp(fresh.date_et)
        bar_count = int(getattr(self, "_bar_counts", pd.Series(dtype=int)).get(prior_date, 9999))
        is_sunday = prior_date.weekday() == 6
        if bar_count < MIN_BARS_COMPLETE and not is_sunday:
            # Try Polygon re-pull first (gives the correct overnight close).
            poly_close = self._polygon_daily_close(fresh.date_et, ticker)
            if poly_close and poly_close > 0:
                log.warning(f"INCOMPLETE SESSION {fresh.date_et}: only {bar_count} bars "
                            f"(< {MIN_BARS_COMPLETE}). 1-min close={fresh.close:.2f}, "
                            f"Polygon overnight close={poly_close:.2f}. Substituting Polygon.")
                fixed = self._with_close(closes, fresh.date_et, poly_close)
                return compute_regime(fixed, sma_window=C.REGIME["sma_window"],
                                      mom_window=C.REGIME["momentum_window"])
            # Polygon unavailable — fall back to IB ETH bar (useRTH=False gives the
            # full-session close at ~17:00 ET, much closer to midnight than RTH 16:00 ET).
            try:
                bars = self.ib.reqHistoricalData(
                    self.data_contract, endDateTime="", durationStr="6 D",
                    barSizeSetting="1 day", whatToShow="TRADES", useRTH=False, formatDate=2)
                ib_close = None
                for b in (bars or []):
                    bd = pd.Timestamp(str(b.date)).date().isoformat()
                    if bd == fresh.date_et:
                        ib_close = float(b.close)
                        break
                if ib_close and ib_close > 0:
                    log.warning(f"INCOMPLETE SESSION {fresh.date_et}: only {bar_count} bars "
                                f"(< {MIN_BARS_COMPLETE}). Polygon unavailable. "
                                f"1-min close={fresh.close:.2f}, IB ETH close={ib_close:.2f}. "
                                f"Substituting IB ETH (useRTH=False).")
                    fixed = self._with_close(closes, fresh.date_et, ib_close)
                    return compute_regime(fixed, sma_window=C.REGIME["sma_window"],
                                          mom_window=C.REGIME["momentum_window"])
                log.warning(f"INCOMPLETE SESSION {fresh.date_et}: {bar_count} bars, no "
                            f"substitute available -- keeping 1-min close {fresh.close:.2f}")
            except Exception as e:
                log.warning(f"INCOMPLETE SESSION {fresh.date_et}: {bar_count} bars, IB ETH "
                            f"lookup failed ({e!r}) -- keeping 1-min close {fresh.close:.2f}")


        # --- 1. Independent Polygon referee ---
        poly_close = self._polygon_daily_close(fresh.date_et, ticker)
        if poly_close and poly_close > 0:
            dev = abs(fresh.close - poly_close) / poly_close
            if dev <= poly_dev_max:
                log.info(f"regime cross-check OK (Polygon) {fresh.date_et}: 1-min "
                         f"{fresh.close:.2f} vs Polygon {poly_close:.2f} ({dev*100:+.3f}%)")
                return fresh
            log.warning("=" * 60)
            log.warning(f"REGIME CLOSE CROSS-CHECK FAILED {fresh.date_et}: 1-min {fresh.close:.2f} "
                        f"vs Polygon {poly_close:.2f} = {dev*100:.2f}% (> {poly_dev_max*100:.1f}%) — "
                        f"BAD 1-min PULL (likely IB disconnect/gap). Substituting INDEPENDENT "
                        f"Polygon close + recomputing regime.")
            log.warning("=" * 60)
            fixed = self._with_close(closes, fresh.date_et, poly_close)
            return compute_regime(fixed, sma_window=C.REGIME["sma_window"],
                                  mom_window=C.REGIME["momentum_window"])

        # --- 2. Fallback: IB settled daily bar ---
        try:
            bars = self.ib.reqHistoricalData(
                self.data_contract, endDateTime="", durationStr="6 D",
                barSizeSetting="1 day", whatToShow="TRADES", useRTH=False, formatDate=2)
            ib_close = None
            for b in (bars or []):
                if pd.Timestamp(str(b.date)).date().isoformat() == fresh.date_et:
                    ib_close = float(b.close)
                    break
            if not ib_close or ib_close <= 0:
                log.warning(f"regime cross-check: no independent source (Polygon unavailable, "
                            f"no IB settled bar for {fresh.date_et}) — keeping 1-min close "
                            f"{fresh.close:.2f}")
                return fresh
            dev = abs(fresh.close - ib_close) / ib_close
            if dev > max_dev:
                log.warning("=" * 60)
                log.warning(f"REGIME CLOSE CROSS-CHECK FAILED {fresh.date_et}: 1-min {fresh.close:.2f} vs "
                            f"IB settle {ib_close:.2f} = {dev*100:.2f}% (> {max_dev*100:.1f}%) — BAD PULL "
                            f"(Polygon unavailable); substituting IB's settled close + recomputing.")
                log.warning("=" * 60)
                fixed = self._with_close(closes, fresh.date_et, ib_close)
                return compute_regime(fixed, sma_window=C.REGIME["sma_window"],
                                      mom_window=C.REGIME["momentum_window"])
            log.info(f"regime cross-check OK (IB settle, Polygon unavailable) {fresh.date_et}: "
                     f"1-min {fresh.close:.2f} vs IB settle {ib_close:.2f} ({dev*100:+.2f}%)")
        except Exception as e:
            log.warning(f"regime cross-check skipped (Polygon unavailable + IB hist error {e!r}) "
                        f"— keeping 1-min close")
        return fresh

    # ---- Daily setup ----
    def setup_for_day(self) -> None:
        today_et = now_et().date().isoformat()
        sleeve_names = ["TLB_LONG", "VWAP_LONG_R3", "VWAP_SHORT_R1"]
        # load_or_new carries any open overnight position across the date boundary
        # (VWAP holds to its server-side bracket), so there is no hard reset here.
        self.state = BotState.load_or_new(C.STATE_FILE, today_et, sleeve_names)
        # Compute the fresh regime from the latest data
        closes, self._bar_counts = load_daily_closes_for_regime()
        # DATA STALENESS GUARD (2026-06-25): if the last data date is > 4 calendar days
        # before today, the data pipeline is broken. Block all trading.
        _last_data_date = closes.index[-1].date() if hasattr(closes.index[-1], 'date') else pd.Timestamp(closes.index[-1]).date()
        _data_age_days = (now_et().date() - _last_data_date).days
        if _data_age_days > 4:
            log.error('=' * 60)
            log.error(f'DATA STALE: last NQ data date is {_last_data_date} ({_data_age_days} days old). '
                      f'Data pipeline is broken — REFUSING TO TRADE on stale regime. '
                      f'Fix tools/v9_pull_latest_data.py and restart.')
            log.error('=' * 60)
            raise SystemExit(f'DATA STALE ({_data_age_days}d) — aborting to prevent wrong-regime trades')
        elif _data_age_days > 2:
            log.warning(f'DATA AGE WARNING: last NQ data date is {_last_data_date} ({_data_age_days} days ago). '
                        f'Expected <= 2 days (accounting for weekends). Data pipeline may be failing.')
        fresh = compute_regime(closes,
                               sma_window=C.REGIME["sma_window"],
                               mom_window=C.REGIME["momentum_window"])
        # SETTLED-CLOSE CROSS-CHECK (2026-06-12): reject a GROSS bad 1-min pull of the prior-day close
        # (06-12: 28816 vs the real ~29446, 2.1% off -> wrong cell) by sanity-checking vs IB's settled
        # daily bar. Keeps the validated ~midnight-ET overnight close on clean days; substitutes IB's
        # settle ONLY on a gross miss. (NDX/RTH/settle-as-primary all degraded the backtest -15%..-34%.)
        fresh = self._crosscheck_regime_close(closes, fresh)
        # REGIME STABILITY GUARD (2026-06-09): the prior-day close can wobble across data pulls;
        # near the ret20=0 boundary that silently flips the cell (seen 2026-06-09: BOTH_BULL@05:00
        # -> ZONE_A@08:48 [bad outlier pull] -> BOTH_BULL@14:11). Policy: LOCK the first cell computed
        # for each prior-day date; on a later restart whose recompute DIFFERS, WARN and KEEP the
        # locked cell (no silent mid-day flip). New prior-day date -> recompute + re-lock.
        cached = self._load_regime_cache()
        if cached is not None and cached.date_et == fresh.date_et:
            if cached.cell != fresh.cell:
                log.warning("=" * 60)
                log.warning(f"REGIME FLIP BLOCKED for prior-day {fresh.date_et}: locked {cached.cell} "
                            f"(close {cached.close:.2f}, ret20 {cached.ret20*100:+.2f}%) vs recompute "
                            f"{fresh.cell} (close {fresh.close:.2f}, ret20 {fresh.ret20*100:+.2f}%). Likely a "
                            f"data-pull wobble near the ret20 boundary — KEEPING locked {cached.cell}. "
                            f"Verify the prior-day close manually if this persists.")
                log.warning("=" * 60)
            self.regime = cached
        else:
            self._save_regime_cache(fresh)        # new prior-day date (or no cache) -> lock it
            self.regime = fresh
        reg = self.regime
        if abs(reg.ret20) < C.REGIME_BOUNDARY_EPS:   # near-boundary fragility flag
            log.warning(f"REGIME NEAR BOUNDARY: ret20 {reg.ret20*100:+.2f}% within +/-"
                        f"{C.REGIME_BOUNDARY_EPS*100:.1f}% of zero — cell {reg.cell} is fragile to a small "
                        f"close change (r20 could flip). Locked for the day.")
        self.state.regime_cell    = reg.cell
        self.state.regime_close   = reg.close
        self.state.regime_sma200  = reg.sma200
        self.state.regime_ret20   = reg.ret20
        active = reg.active_sleeves
        disabled = getattr(C, 'DISABLED_SLEEVES', set())
        if disabled:
            active = [s for s in active if s not in disabled]
        # US MARKET HOLIDAY GUARD (2026-07-03): stand ALL sleeves down on a US full-closure
        # or early-close day. Thin/half sessions are unsafe for the sleeves and the regime gate
        # assumes a normal prior RTH close. Existing positions are untouched (they run to their
        # server-side OCA brackets). Self-resumes automatically the next normal trading day.
        try:
            sys.path.insert(0, "/root/v9")
            import us_market_holidays as _ush
            _hol = _ush.no_trade_reason(now_et().date())
        except Exception as _e:
            _hol = None
            log.warning(f"US holiday check skipped ({_e!r}) — proceeding with normal sleeves")
        if _hol:
            log.warning("=" * 60)
            log.warning(f"US MARKET HOLIDAY — {today_et}: {_hol}. NO NEW ENTRIES today; all "
                        f"sleeves stood down. Open positions run to their OCA brackets.")
            log.warning("=" * 60)
            active = []
        for name, sleeve in self.state.sleeves.items():
            sleeve.active_today = (name in active)
        log.info("="*60)
        log.info(f"v9.1 DAILY SETUP — {today_et}")
        log.info(f"Regime cell: {reg.cell}")
        log.info(f"NQ close prev: {reg.close:.2f}  200-DMA: {reg.sma200:.2f}  20d return: {reg.ret20*100:+.2f}%")
        log.info(f"Active sleeves: {active}")
        log.info("="*60)
        self.state.save(C.STATE_FILE)

    # ---- Roll sweep / reconcile helpers (2026-09-14 Codex fixes) ----
    def _roll_sweep(self) -> None:
        """Close a V9 position stranded on a rolled-off month -- and ONLY V9's (Codex #3)."""
        from contract_roll import flatten_foreign_positions
        held = {n: s for n, s in self.state.sleeves.items() if s.in_position} if self.state else {}
        own = sum((s.qty or EXEC_INST["qty"]) * (1 if s.side == "long" else -1) for s in held.values())
        closed = flatten_foreign_positions(self.ib, EXEC_INST["symbol"], self.contract.localSymbol, log,
                                           own_client_ids={C.CLIENT_ID}, max_close=own)
        if not closed or not held:
            return
        # Everything V9 holds after a roll was opened by the previous process on the OLD month, and
        # is being closed right now. Reset those sleeves BEFORE reconcile runs -- otherwise it would
        # see "no bracket" and act on the NEW month for a position closing on the old one.
        snaps = {}
        for name, sl in held.items():
            snaps[name] = dict(side=sl.side, entry=sl.entry_price, stop=sl.stop_price,
                               qty=sl.qty or EXEC_INST["qty"])
            log.warning(f"[{name}] ROLL: position was on the rolled-off month -- closed by the sweep")
            self._reset_sleeve(name)
        self.state.save(C.STATE_FILE)
        for (_ls, _side, _q, tr) in closed:
            if tr is not None:
                tr.filledEvent += lambda t, sn=snaps: self._book_roll_exits(t, sn)

    def _book_roll_exits(self, trade, snaps) -> None:
        px = self._fill_px(trade)
        for name, sn in snaps.items():
            sgn = 1.0 if sn["side"] == "long" else -1.0
            risk = abs((sn["entry"] or 0) - (sn["stop"] or 0)) or 1.0
            pnl = sgn * (px - sn["entry"]) * EXEC_INST["point_value"] * sn["qty"]
            self.state.cum_realized_usd += pnl
            record_v9_fill(name, "SELL" if sn["side"] == "long" else "BUY", sn["qty"], px, "ROLL",
                           realized=round(pnl, 2), realized_R=round(sgn * (px - sn["entry"]) / risk, 4))
            log.info(f"[{name}] ROLL exit booked @ {px:.2f}  ${pnl:+,.0f}")
        self.state.save(C.STATE_FILE)

    @staticmethod
    def _fill_px(trade) -> float:
        try:
            px = float(trade.orderStatus.avgFillPrice or 0)
            if not px and trade.fills:
                px = float(trade.fills[-1].execution.avgPrice or trade.fills[-1].execution.price)
            return px
        except Exception:
            return 0.0

    def _bracket_exit_px(self, sleeve):
        """Price of an EXECUTION of this sleeve's own stop/target orders (our clientId only)."""
        from ib_async import ExecutionFilter
        ids = {sleeve.stop_order_id, sleeve.target_order_id} - {None}
        try:
            fills = self.ib.reqExecutions(ExecutionFilter(clientId=C.CLIENT_ID))
        except Exception as e:
            log.warning(f"reconcile: reqExecutions failed ({e!r})"); return None
        hit = [f for f in fills or [] if f.execution.orderId in ids]
        return float(hit[-1].execution.price) if hit else None

    def _account_net(self) -> int:
        try:
            return sum(int(p.position) for p in self.ib.positions()
                       if p.contract.localSymbol == self.contract.localSymbol)
        except Exception:
            return 999999                                  # unknown -> treated as non-zero

    def _reprotect(self, name: str, survivor) -> None:
        """Replace a DAMAGED bracket (one leg survives, proving the position is ours)."""
        sleeve = self.state.sleeves[name]
        if survivor is not None:
            cancel_order(self.ib, survivor); self.ib.sleep(1)
        qty = sleeve.qty or EXEC_INST["qty"]
        oca = f"v9_{name}_{int(time.time())}"
        st = place_protective_stop(self.ib, self.contract, sleeve.side, qty, sleeve.stop_price, oca)
        tg = place_limit_target(self.ib, self.contract, sleeve.side, qty, sleeve.target_price, oca)
        self.live[name] = {"stop": st, "target": tg}
        sleeve.stop_order_id = st.order.orderId
        sleeve.target_order_id = tg.order.orderId
        st.filledEvent += lambda tr, n=name: self._on_exit_fill(n, tr, "STOP")
        tg.filledEvent += lambda tr, n=name: self._on_exit_fill(n, tr, "TARGET")
        self.state.save(C.STATE_FILE)
        log.warning(f"[{name}] RE-PROTECTED: fresh OCA {oca} stop {sleeve.stop_price} target {sleeve.target_price}")

    def reconcile_open_positions(self) -> None:
        """After a (re)connect, re-bind fill handlers to any working bracket orders for sleeves the
        persisted state says are still in_position (a VWAP hold can span a process restart)."""
        if not self.state:
            return
        held = [n for n, s in self.state.sleeves.items() if s.in_position]
        if not held:
            return
        try:
            self.ib.reqAllOpenOrders()
            self.ib.sleep(1)  # let ib_async receive the open-order snapshot
            # 2026-09-14 FIX (Codex #5): ONLY our own (clientId 25) orders. V9 and R3 share the
            # account and both trade MNQ; order ids are per-client, so indexing EVERY client's orders
            # by orderId let an R3 order replace the right handle (last one wins) and bind V9's exit
            # handlers to another bot's bracket. Same bug class fixed in R3 on 2026-08-27.
            mine = [t for t in self.ib.openTrades()
                    if getattr(t.order, "clientId", C.CLIENT_ID) == C.CLIENT_ID and not t.isDone()]
            open_trades = {t.order.orderId: t for t in mine}
        except Exception as e:
            log.warning(f"reconcile: could not read open orders ({e}); leaving state as-is.")
            return
        for name in held:
            sleeve = self.state.sleeves[name]
            st = open_trades.get(sleeve.stop_order_id)
            tg = open_trades.get(sleeve.target_order_id)
            if st is None and tg is None:
                # 2026-09-14 FIX (Codex #7): a missing bracket does NOT prove the trade closed.
                px = self._bracket_exit_px(sleeve)
                if px is not None:
                    log.warning(f"[{name}] bracket gone; its exit FILLED @ {px:.2f} while down -- booking it")
                    self._book_exit(name, px, "RECONCILE")
                elif self._account_net() == 0:
                    log.warning(f"[{name}] bracket gone, no exit fill found, account flat on "
                                f"{self.contract.localSymbol} -- position closed outside the bracket. "
                                f"Clearing. VERIFY P&L in IB.")
                    self._reset_sleeve(name); self.state.save(C.STATE_FILE)
                else:
                    # Unverifiable: never place orders blind (a stop on a closed position opens a new
                    # one), never forget it either. No auto-orders, no auto-flatten -- a human decides.
                    self._quarantined.add(name)
                    log.error(f"[{name}] QUARANTINED: state says in_position, NO bracket, NO exit fill, "
                              f"account net {self._account_net():+d} on {self.contract.localSymbol} "
                              f"(shared with R3) -- cannot attribute. NO orders will be sent for this "
                              f"sleeve. CHECK IB and close or protect it MANUALLY.")
                continue
            if st is None or tg is None:
                # Codex #7: never run with a target but no stop. The surviving leg is OUR order in
                # OUR OCA group, so the position is real and ours -> safe to repair.
                log.warning(f"[{name}] bracket DAMAGED on reconnect (stop={'Y' if st else 'MISSING'}, "
                            f"target={'Y' if tg else 'MISSING'}) -- replacing it")
                self._reprotect(name, st if st is not None else tg)
                continue
            self.live[name] = {"stop": st, "target": tg}
            st.filledEvent += lambda tr, n=name: self._on_exit_fill(n, tr, "STOP")
            tg.filledEvent += lambda tr, n=name: self._on_exit_fill(n, tr, "TARGET")
            log.info(f"[{name}] re-bound to working bracket on reconnect (stop=Y, target=Y); "
                     f"{sleeve.side} entry={sleeve.entry_price} since {sleeve.entry_time}")

    # ---- Bar handler ----
    def on_5min(self, ts: pd.Timestamp, o: float, h: float, l: float, c: float,
                v: float, live: bool = True) -> None:
        """Process one completed 5-min bar.

        Feeds every sleeve's detector (state must see ALL bars to stay correct),
        then ACTS on a returned signal only when that sleeve is active, flat, in its
        entry window, and the matching ENABLE flag is on. `live=False` during warm-up
        (replay history to build state without placing any orders).
        """
        if not self.state or not self.regime:
            return
        et_t = ts.tz_convert(ET).time() if ts.tzinfo else ts.time()

        # ★SEAM/DUPLICATE GUARD (kristov18 4340135, 2026-08-03): reject any bar whose label does not
        # ADVANCE. Catches the restart-seam duplicate (warm-up partial + aggregator first-bucket sharing
        # one label) and IB resends. Warm-up bars set the cursor too. Cursor advances only after gates.
        _prev = getattr(self, "_last_5min_ts", None)
        if _prev is not None and ts <= _prev:
            log.warning(f"BAR REJECTED (non-advancing stamp): {ts} <= last processed {_prev} — "
                        f"restart-seam duplicate/resend; detectors not fed.")
            return
        self._last_5min_ts = ts

        # Feed all three detectors EVERY bar (VWAP accumulators + pivot history).
        sig_vwap_long  = self.vwap_long_state.on_bar(ts.to_pydatetime(), h, l, c, v,
                                                     anchor_hour_utc=C.VWAP_LONG["anchor_hour_utc"])
        sig_vwap_short = self.vwap_short_state.on_bar(ts.to_pydatetime(), h, l, c, v,
                                                      anchor_hour_utc=C.VWAP_SHORT["anchor_hour_utc"])
        sig_tlb        = self.tlb_state.add_bar(h, l, c)

        if not live or self.state.halted:
            return

        # EOD flatten at 15:20 ET applies to TLB ONLY — its backtest flattens at 15:20.
        # The VWAP sleeves are NOT force-flattened: they run to their server-side OCA
        # bracket (3R target / swing stop), which is how they were validated and which
        # lets the 19:00-24:00 ET evening window trade and afternoon holds mature.
        # (Deliberately NO `return` here so VWAP keeps processing after 15:20.)
        # Half-day session override (preflight_check.py writes session_override.json nightly):
        # on an early-close day, flatten ALL sleeves by the early close and stop new entries at
        # the early cutoff. Fail-safe -> normal config times unless TODAY is a fresh half-day.
        _hd = so.session_for_today(log=log)
        _eod_h, _eod_m = _hd["eod"] if _hd["is_half_day"] else (C.EOD_FLATTEN_H, C.EOD_FLATTEN_M)
        _gap_h, _gap_m = _hd["eod"] if _hd["is_half_day"] else (C.GAP_FLATTEN_H, C.GAP_FLATTEN_M)
        _past_last_entry = _hd["is_half_day"] and (
            et_t.hour > _hd["last_entry"][0]
            or (et_t.hour == _hd["last_entry"][0] and et_t.minute >= _hd["last_entry"][1]))

        eod = et_t.hour > _eod_h or (et_t.hour == _eod_h and et_t.minute >= _eod_m)
        if eod:
            self._flatten_sleeve("TLB_LONG", "EOD", c)

        # Gap-safe flatten: never carry ANY sleeve across the 17:00 ET settlement break
        # (daily halt / weekend / contract roll). Flatten everything at the cash close
        # (15:55 ET) — the backtest sweet spot (best of the EOD cutoffs). NARROW window
        # (15:55-16:59) so the 19:00-24:00 ET evening + 00:00-06:00 overnight VWAP windows
        # still fire and run WITHIN a session; they just can't be carried across the gap.
        gap_flat = (et_t.hour == _gap_h and et_t.minute >= _gap_m) or (_gap_h < et_t.hour < 17)
        if gap_flat:
            self._flatten_all("GAP", c)

        plan = [
            ("VWAP_LONG_R3",  sig_vwap_long,  C.VWAP_LONG["active_windows_et"], ENABLE_LONG),
            ("VWAP_SHORT_R1", sig_vwap_short, C.VWAP_SHORT["active_windows_et"], ENABLE_SHORT),
            ("TLB_LONG",      sig_tlb,        C.TLB["entry_windows_et"],         ENABLE_LONG),
        ]
        for name, sig, windows, enabled in plan:
            sleeve = self.state.sleeves.get(name)
            if not sleeve or not sleeve.active_today or sleeve.in_position or not enabled:
                continue
            if gap_flat or _past_last_entry:
                continue  # no entries during the gap-flatten window or after a half-day cutoff
            if name == "TLB_LONG" and eod:
                continue  # TLB must not re-enter after its EOD flatten
            if not sig or not is_in_window(et_t, windows):
                continue
            if name == "TLB_LONG" and not self._tlb_risk_ok(sig):
                continue  # single-trade risk policy guard (see _tlb_risk_ok)
            if name in ("VWAP_LONG_R3", "VWAP_SHORT_R1") and not self._vwap_risk_ok(sig, name):
                continue  # VWAP wide-stop backstop (see _vwap_risk_ok)
            self._enter(name, sig)

    def _sleeve_qty(self, name: str) -> int:
        """FIXED per-sleeve contract size (MNQ). Explicit C.SLEEVE_QTY map — NO risk-based
        sizing (VWAP sleeves have tiny stops -> risk sizing over-leverages them). Falls back
        to the base EXEC qty for any unmapped sleeve."""
        return int(getattr(C, "SLEEVE_QTY", {}).get(name, EXEC_INST["qty"]))

    def _vwap_risk_ok(self, sig: dict, name: str) -> bool:
        """Backstop (2026-08-03): skip a VWAP entry whose stop is pathologically wide -- a broken
        setup or an un-foreseen cause the gap guard didn't catch. POINT-based so it's qty-independent
        (a raw $-cap would wrongly clip normal trades if size changed). Cap C.VWAP_MAX_STOP_PTS=200pt
        = $800 total at 2 MNQ / $400 per contract. Normal VWAP stops: ~25pt median, 159pt max over
        144 backtest trades -> fires ~never; it is the 'buy time to find the next cause' layer."""
        cap = getattr(C, "VWAP_MAX_STOP_PTS", 200)
        stop_dist = abs(sig["entry_price"] - sig["stop_price"])
        if stop_dist > cap:
            qty = self._sleeve_qty(name)
            log.warning(f"[{name}] signal SKIPPED by VWAP risk cap: stop {stop_dist:.1f}pt > {cap}pt "
                        f"(entry={sig['entry_price']:.2f} stop={sig['stop_price']:.2f}; "
                        f"~${stop_dist * qty * EXEC_INST['point_value']:,.0f} at {qty} MNQ) -- wide/broken stop.")
            return False
        return True

    def _tlb_risk_ok(self, sig: dict) -> bool:
        """Single-trade risk guard for TLB: skip if even 1 contract exceeds TLB_MAX_TRADE_RISK_USD."""
        qty = self._sleeve_qty("TLB_LONG")
        stop_dist = abs(sig["entry_price"] - sig["stop_price"])
        risk_usd = stop_dist * qty * EXEC_INST["point_value"]
        if risk_usd > C.TLB_MAX_TRADE_RISK_USD:
            log.warning(f"[TLB_LONG] signal SKIPPED by risk guard: nominal stop risk "
                        f"${risk_usd:,.0f} > cap ${C.TLB_MAX_TRADE_RISK_USD:,.0f} "
                        f"(entry={sig['entry_price']:.2f} stop={sig['stop_price']:.2f} qty={qty}; "
                        f"policy: one trade must not risk more than ~60% of the daily kill).")
            return False
        return True

    def _enter(self, name: str, sig: dict) -> None:
        """Place a MKT entry for a sleeve; the protective STP + LMT target go on fill."""
        direction = sig["side"].lower()
        qty = self._sleeve_qty(name)
        log.info(f"[{name}] SIGNAL {direction.upper()} entry={sig['entry_price']:.2f} "
                 f"stop={sig['stop_price']:.2f} target={sig['target_price']:.2f} — placing MKT qty={qty}")
        trade = place_market_entry(self.ib, self.contract, direction, qty)
        self.live.setdefault(name, {})["entry"] = trade
        trade.filledEvent += lambda tr, n=name, s=sig: self._on_entry_fill(n, s, tr)
        trade.statusEvent += lambda tr, n=name, s=sig: self._on_entry_status(n, s, tr)

    def _on_entry_status(self, name: str, sig: dict, trade) -> None:
        """Codex #6: filledEvent fires only on a COMPLETE fill. If the entry ends cancelled/inactive
        after a PARTIAL fill, those contracts would sit with no stop. Protect the filled quantity."""
        os_ = trade.orderStatus
        if (os_.status in ("Cancelled", "ApiCancelled", "Inactive") and (os_.filled or 0) > 0
                and not self.state.sleeves[name].in_position):
            log.error(f"[{name}] entry ended {os_.status} after a PARTIAL fill of {os_.filled:g} "
                      f"-- protecting the filled quantity")
            self._on_entry_fill(name, sig, trade, qty_override=int(os_.filled))

    def _on_entry_fill(self, name: str, sig: dict, trade, qty_override: int = 0) -> None:
        sleeve = self.state.sleeves[name]
        if sleeve.in_position:
            return  # idempotent
        direction = sig["side"].lower()
        qty = int(qty_override or trade.order.totalQuantity)
        fill_px = float(trade.fills[-1].execution.avgPrice) if trade.fills else sig["entry_price"]
        sleeve.in_position = True
        sleeve.side = direction
        sleeve.entry_price = fill_px
        sleeve.stop_price = sig["stop_price"]
        sleeve.target_price = sig["target_price"]
        sleeve.entry_time = now_et().isoformat()
        sleeve.entry_order_id = trade.order.orderId
        sleeve.qty = qty                          # remember actual size for flatten/book_exit
        sleeve.trade_count_today += 1
        log.info(f"[{name}] {direction.upper()} ENTRY filled @ {fill_px:.2f}")
        record_v9_fill(name, "BUY" if direction == "long" else "SELL", qty, fill_px,
                       f"{name}_ENTRY")
        # Protective stop + R-target as an OCA pair (one fills -> the other cancels)
        oca = f"v9_{name}_{trade.order.orderId}"
        st = place_protective_stop(self.ib, self.contract, direction, qty, sig["stop_price"], oca)
        tg = place_limit_target(self.ib, self.contract, direction, qty, sig["target_price"], oca)
        self.live[name]["stop"] = st
        self.live[name]["target"] = tg
        sleeve.stop_order_id = st.order.orderId
        sleeve.target_order_id = tg.order.orderId
        st.filledEvent += lambda tr, n=name: self._on_exit_fill(n, tr, "STOP")
        tg.filledEvent += lambda tr, n=name: self._on_exit_fill(n, tr, "TARGET")

    def _on_exit_fill(self, name: str, trade, kind: str) -> None:
        sleeve = self.state.sleeves[name]
        if not sleeve.in_position:
            return  # already closed (OCA sibling or EOD beat us here)
        self._flattening.pop(name, None)   # a bracket leg beat the flatten: this IS the exit
        exit_px = float(trade.fills[-1].execution.price) if trade.fills else (
            sleeve.stop_price if kind == "STOP" else sleeve.target_price)
        self._book_exit(name, exit_px, kind)

    def _book_exit(self, name: str, exit_px: float, label: str) -> None:
        sleeve = self.state.sleeves[name]
        sgn = 1.0 if sleeve.side == "long" else -1.0
        risk = abs(sleeve.entry_price - sleeve.stop_price) or 1.0
        R = sgn * (exit_px - sleeve.entry_price) / risk
        qty = sleeve.qty or EXEC_INST["qty"]
        pnl_usd = sgn * (exit_px - sleeve.entry_price) * EXEC_INST["point_value"] * qty
        log.info(f"[{name}] {label} exit @ {exit_px:.2f}  R={R:+.3f}  ${pnl_usd:+,.0f}")
        sleeve.realized_R += R
        self.state.cum_realized_usd += pnl_usd
        record_v9_fill(name, "SELL" if sleeve.side == "long" else "BUY", qty, exit_px,
                       label, realized=round(pnl_usd, 2), realized_R=round(R, 4))
        # Cancel the sibling order (defensive; OCA should already handle it) and reset.
        handles = self.live.get(name, {})
        cancel_order(self.ib, handles.get("stop") if label != "STOP" else handles.get("target"))
        self._reset_sleeve(name)
        # Daily-loss-limit halt
        if self.state.cum_realized_usd <= C.DAILY_LOSS_LIMIT_USD:
            self.state.halted = True
            log.warning(f"DAILY LOSS LIMIT hit (${self.state.cum_realized_usd:+,.0f}) — halting, flattening.")
            self._flatten_all("DAILY_LOSS_LIMIT", exit_px)
        self.state.save(C.STATE_FILE)

    def _reset_sleeve(self, name: str) -> None:
        s = self.state.sleeves[name]
        s.in_position = False; s.side = None
        s.entry_price = s.stop_price = s.target_price = None
        s.entry_time = None
        s.entry_order_id = s.stop_order_id = s.target_order_id = None
        s.qty = 0
        self.live[name] = {}

    def _flatten_sleeve(self, name: str, reason: str, last_px: float) -> None:
        """Flatten ONE sleeve. 2026-09-14 FIX (Codex #4): cancel the bracket, send the market exit
        ONLY once both legs confirm cancelled, and book at the market order's REAL fill. The old code
        cancelled and fired the market order in the same breath and booked immediately: a stop
        filling in the cancel gap plus the market order could double-exit into the opposite
        position, and a rejected market order left state cleared on a live, unprotected position.
        Runs inside the bar handler, so it makes NO blocking calls -- it is event-driven."""
        sleeve = self.state.sleeves.get(name)
        if not sleeve or not sleeve.in_position or name in self._flattening:
            return
        if name in self._quarantined:
            log.error(f"[{name}] {reason} flatten SKIPPED -- sleeve is QUARANTINED (unattributed). CHECK IB.")
            return
        handles = self.live.get(name, {})
        legs = [(k, h) for k, h in (("stop", handles.get("stop")), ("target", handles.get("target")))
                if h is not None and not h.isDone()]
        fl = dict(reason=reason, last_px=last_px, waiting={k for k, _ in legs}, sent=False, t0=time.time())
        self._flattening[name] = fl
        for key, h in legs:
            h.cancelledEvent += (lambda tr, n=name, k=key: self._flatten_leg_gone(n, k))
        for _, h in legs:
            cancel_order(self.ib, h)
        log.info(f"[{name}] FLATTEN ({reason}) @ ~{last_px:.2f} -- cancelling bracket first")
        if not legs:
            self._flatten_send_market(name)

    def _flatten_leg_gone(self, name: str, key: str) -> None:
        fl = self._flattening.get(name)
        if fl is None or not self.state.sleeves[name].in_position:
            return
        fl["waiting"].discard(key)
        if not fl["waiting"]:
            self._flatten_send_market(name)

    def _flatten_send_market(self, name: str) -> None:
        fl = self._flattening.get(name)
        sleeve = self.state.sleeves[name]
        if fl is None or fl["sent"] or not sleeve.in_position:
            return
        fl["sent"] = True
        tr = market_flatten(self.ib, self.contract, sleeve.side, sleeve.qty or EXEC_INST["qty"])
        if tr is None:
            log.error(f"[{name}] flatten market order NOT placed -- CHECK IB")
            return
        tr.filledEvent += lambda t, n=name: self._on_flatten_fill(n, t)

    def _on_flatten_fill(self, name: str, trade) -> None:
        fl = self._flattening.pop(name, None)
        if not self.state.sleeves[name].in_position:
            return
        px = self._fill_px(trade) or (fl["last_px"] if fl else self.state.sleeves[name].entry_price)
        self._book_exit(name, px, (fl["reason"] if fl else "FLATTEN").upper())

    def _flatten_watchdog(self) -> None:
        """Main-loop check: a flatten stuck waiting for cancel confirmations is paged + re-cancelled."""
        now = time.time()
        for name, fl in list(self._flattening.items()):
            if not fl["sent"] and now - fl["t0"] > 60 and now - self._flat_alert_t > 300:
                self._flat_alert_t = now
                log.error(f"[{name}] flatten STUCK {now - fl['t0']:.0f}s waiting for {fl['waiting']} "
                          f"to cancel -- re-sending cancels. CHECK IB.")
                for h in self.live.get(name, {}).values():
                    if h is not None and not h.isDone():
                        cancel_order(self.ib, h)

    def _flatten_all(self, reason: str, last_px: float) -> None:
        """Flatten ALL open sleeves (used by the daily-loss-limit halt)."""
        for name in list(self.state.sleeves.keys()):
            self._flatten_sleeve(name, reason, last_px)

    # ---- Main loop ----
    def run(self) -> None:
        log.info(f"v9.1 bot starting — PAPER_ONLY={C.PAPER_ONLY} ENABLE_LONG={ENABLE_LONG} ENABLE_SHORT={ENABLE_SHORT}")
        # Pre-flight: refresh historical data from IB before computing regime
        self.refresh_data()
        self.connect_ib()
        self.setup_for_day()
        self._roll_sweep()               # Codex #3: scoped; AFTER state loads, BEFORE reconcile
        self.reconcile_open_positions()  # re-bind to brackets surviving a restart
        if not (ENABLE_LONG or ENABLE_SHORT):
            log.warning("ENABLE_LONG=False and ENABLE_SHORT=False — DRY RUN mode (no orders placed)")
            log.info(f"Today's regime: {self.regime.cell}, active sleeves: {self.regime.active_sleeves}")
            log.info("Bot will idle, log heartbeat, and not place any orders.")
        # Warm up the detectors with recent history so VWAP + pivots are correct now
        self.warmup()
        # Subscribe to live 5-second bars (aggregated to 5-min locally)
        self._subscribe_bars()
        # Heartbeat loop
        try:
            while True:
                self.ib.sleep(60)  # heartbeat every minute
                # Auto-reconnect if the API socket actually dropped. (The 12:15 BST IB server
                # reconnect usually keeps the socket UP and is handled by _on_error/1101; this
                # covers a genuine socket drop or full Gateway restart so v9 self-heals.)
                if not self.ib.isConnected():
                    log.info("IB API socket is DOWN — reconnecting + re-subscribing (routine if nightly restart)...")
                    try:
                        self.connect_ib(first=False)      # contracts PINNED (Codex #15)
                        self.reconcile_open_positions()   # re-bind to surviving server brackets
                        self._subscribe_bars()
                        log.info("Reconnected and re-subscribed.")
                    except Exception as e:
                        log.error(f"reconnect attempt failed: {e!r}; will retry next cycle.")
                # --- Bar-stall watchdog -------------------------------------------------
                # A silent realtime-feed stall (e.g. the usfuture data farm goes inactive on
                # a 2108 while the API socket stays UP) leaves the bot "connected" + heart-
                # beating but BLIND to bars — it misses entries AND never fires the EOD
                # flatten. (2026-06-09: blind for hours after a Gateway restart, missed a
                # VWAP-LONG-R3 signal.) The isConnected() check above can't catch this — the
                # socket is fine, only the subscription is starved. So if no realtime bar has
                # arrived for BAR_STALL_SEC while CME NQ is open, re-subscribe to self-heal.
                # _subscribe_bars() cancels-then-resubscribes (idempotent, no orders, no
                # strategy-state reset) and resets _last_bar_utc; the throttle/escalation
                # lives in _bar_stall_check so a long outage gets one ERROR, not a WARN flood.
                if (self.ib and self.ib.isConnected()
                        and self._bar_stall_check(datetime.now(timezone.utc))):
                    try:
                        self._subscribe_bars()   # resets _last_bar_utc; _stall_since persists
                    except Exception as e:
                        log.error(f"bar-stall re-subscribe failed: {e!r}; retry next cycle.")
                self._flatten_watchdog()
                if self.state:
                    self.state.connected = bool(self.ib and self.ib.isConnected())
                    self.state.save(C.STATE_FILE)
                # Reset if past midnight ET — delay 10 min so Polygon ingests the last bars
                if now_et().date().isoformat() != self.state.date_et:
                    et_min = now_et().hour * 60 + now_et().minute
                    if et_min >= 10:
                        self.refresh_data()
                        self.setup_for_day()
        except KeyboardInterrupt:
            # Leave the server-side OCA stop+target working so any open position stays
            # protected while the bot is down (do NOT market-flatten on a restart).
            log.info("Shutdown requested — leaving server-side stops in place.")
        finally:
            if self.ib and self.ib.isConnected():
                self.ib.disconnect()
            log.info("v9 bot stopped.")

    def warmup(self) -> None:
        """Replay recent historical 5-min bars into the detectors (live=False, no orders)
        so the VWAP accumulators + TLB pivot history are correct from the first live bar."""
        try:
            hist = self.ib.reqHistoricalData(
                self.data_contract, endDateTime="", durationStr="2 D",
                barSizeSetting="5 mins", whatToShow="TRADES", useRTH=False, formatDate=2)
            log.info(f"Warm-up: replaying {len(hist)} historical 5-min bars (no orders)")
            _now_utc = pd.Timestamp.now(tz="UTC")
            for b in hist:
                ts = pd.Timestamp(b.date)
                ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
                # ★SEAM FIX (kristov18 4340135): endDateTime="" includes the FORMING bar. Feeding that
                # partial here AND letting the realtime aggregator rebuild the same window double-counts
                # the streak (a real 4 fired n_out=5). Skip any bar whose bucket hasn't closed.
                if ts + pd.Timedelta(minutes=5) > _now_utc:
                    log.info(f"Warm-up: skipping forming bar {ts} (bucket not closed)")
                    continue
                vol = float(b.volume) if (b.volume and b.volume > 0) else 0.0
                self.on_5min(ts, float(b.open), float(b.high), float(b.low),
                             float(b.close), vol, live=False)
            if hist:
                self._last_close = float(hist[-1].close)
            log.info(f"Warm-up done. TLB pivots: {len(self.tlb_state.last_ph)} highs / "
                     f"{len(self.tlb_state.last_pl)} lows.")
        except Exception as e:
            log.warning(f"Warm-up failed ({e}); live VWAP/pivots will build from now.")

    def _on_realtime_bar(self, bars, hasNewBar):
        if not bars:
            return
        self._last_bar_utc = datetime.now(timezone.utc)  # feed liveness for the stall watchdog
        self._stall_since = None          # a bar arrived -> feed alive; clear stall throttle
        self._stall_escalated = False
        self._stall_restarted = False
        bar = bars[-1]  # latest 5-second bar
        ts = pd.Timestamp(bar.time)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        o = float(bar.open_); h = float(bar.high); l = float(bar.low); c = float(bar.close)
        vol = float(bar.volume) if (bar.volume and bar.volume > 0) else 0.0
        done = self.agg.add(ts, o, h, l, c, vol)
        if done is not None:
            bts, bo, bh, bl, bc, bv = done
            self._last_close = bc
            try:
                self.on_5min(bts, bo, bh, bl, bc, bv, live=True)
            except Exception as e:
                log.exception(f"on_5min error: {e}")


def main():
    bot = V9Bot()
    bot.run()


if __name__ == "__main__":
    main()
