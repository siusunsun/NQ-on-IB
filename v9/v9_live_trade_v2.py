"""v9.2 Live Trading Bot — 4-sleeve Option 1 composition.

Sleeves: VWAP_LONG_R3, R3_REV, CCI_AM, TLB_BEAR_SHORT
All at 1 MNQ to start. Signals from NQ data, orders on MNQ.

Key changes from v9.1:
  - 1-min bar aggregation (CCI + TBS need 1-min bars)
  - R3_REV fires after VWAP_R3 stop-out (inter-sleeve state dependency)
  - Opposite-position tracking: two sleeves can hold opposite sides simultaneously
    (e.g. CCI long + TBS short). Never use net position as proof of closure.
  - EOD: CCI flattens at 16:00 ET, TBS at session end, VWAP runs to bracket
"""
from __future__ import annotations

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

try:
    from ib_async import IB, Future, MarketOrder, StopOrder, LimitOrder, util
except ImportError:
    print("ERROR: ib_async not installed. Install with: pip install ib_async")
    sys.exit(1)

import v9_config_live_v2 as C
from v9_state import BotState, SleeveState
from v9_strategy_lib import (
    compute_regime, DailyRegime, VwapState, is_in_window, update_session_vwap,
    session_for_utc_bar,
)
from v9_execution import (
    FiveMinAggregator, place_market_entry, place_protective_stop,
    place_limit_target, cancel_order, market_flatten, record_v9_fill,
)
from v9_cci_live import CciAmLive, CCI_NAME, EOD_MIN as CCI_EOD_MIN
from v9_r3rev_live import R3RevLive, R3REV_NAME
from v9_tlb_bear_short import TlbBearShortLive, TBS_NAME, session_at as tbs_session_at, SESSIONS as TBS_SESSIONS
import session_override as so

DATA_INST = C.INSTRUMENT
EXEC_INST = getattr(C, "INSTRUMENT_EXEC", C.INSTRUMENT)
POINT_VALUE_TOTAL = EXEC_INST["point_value"] * EXEC_INST["qty"]

ET = pytz.timezone("America/New_York")

ENABLE_LONG = C.ENABLE_LONG
ENABLE_SHORT = C.ENABLE_SHORT

C.safety_check()

log = logging.getLogger("v9")
try:
    import v9_tg
    v9_tg.install_handler(log)
except Exception:
    pass
log.setLevel(logging.INFO)
fh = logging.FileHandler(C.LOG_FILE, encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s UTC [%(levelname)s] %(message)s"))
log.addHandler(fh)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(sh)

ALL_SLEEVE_NAMES = ["VWAP_LONG_R3", R3REV_NAME, CCI_NAME, TBS_NAME]


def now_et() -> datetime:
    return datetime.now(timezone.utc).astimezone(ET)


def load_daily_closes_for_regime() -> pd.Series:
    nq_dir = Path("/root/v9/data_1m/NQ")
    files = sorted(nq_dir.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"No NQ data in {nq_dir}")
    sys.path.insert(0, "/root/v9")
    from src.engine import load_csv
    df = pd.concat([load_csv(f) for f in files]).sort_index(kind="mergesort")
    if df.index.tz is None: df = df.tz_localize("UTC")
    df = df[~df.index.duplicated(keep="last")]
    df = df.copy()
    df["et_date"] = df.index.tz_convert(ET).date
    try:
        counts = df.groupby("et_date").size()
        if len(counts) >= 4:
            wd = [getattr(d, "weekday", lambda: 0)() < 5 for d in counts.index]
            full = counts[wd]
            med = float(full.iloc[:-1].median()) if len(full) >= 2 else float(counts.median())
            for d, n in counts.iloc[-4:-1].items():
                if getattr(d, "weekday", lambda: 0)() >= 5:
                    continue
                if med and n < 0.5 * med:
                    log.warning(f"DATA INTEGRITY: ET {d} has only {int(n)} bars "
                                f"(median {int(med)}) — likely truncated pull.")
    except Exception:
        pass
    daily = df.groupby("et_date").agg({"close": "last"})
    bar_counts = df.groupby("et_date").size()
    try:
        from us_market_holidays import is_day_complete
        while len(daily) > 0:
            last_date = daily.index[-1]
            n = int(bar_counts.get(last_date, 0))
            ok, expected = is_day_complete(last_date, n)
            if ok:
                break
            log.error(f"REGIME DATA GATE: ET {last_date} has only {n} bars "
                      f"(expected ~{expected}) — DROPPING.")
            daily = daily.iloc[:-1]
            bar_counts = bar_counts.iloc[:-1]
    except Exception as e:
        log.warning(f"regime data gate check failed ({e}) — proceeding without it")
    daily.index = pd.to_datetime(daily.index)
    bar_counts.index = pd.to_datetime(bar_counts.index)
    return daily["close"], bar_counts


class OneMinAggregator:
    """Accumulate 5-second realtime bars into completed 1-minute bars."""
    def __init__(self):
        self.bucket: Optional[pd.Timestamp] = None
        self.o = self.h = self.l = self.c = None
        self.v = 0.0
        self.emitted = False

    def add(self, ts: pd.Timestamp, o: float, h: float, l: float, c: float, v: float):
        floor = ts.floor("1min")
        completed = None
        if self.bucket is None:
            self.bucket, self.o, self.h, self.l, self.c, self.v = floor, o, h, l, c, v
            self.emitted = False
        elif floor == self.bucket:
            self.h = max(self.h, h); self.l = min(self.l, l); self.c = c; self.v += v
        else:
            if not self.emitted:
                completed = (self.bucket, self.o, self.h, self.l, self.c, self.v)
            self.bucket, self.o, self.h, self.l, self.c, self.v = floor, o, h, l, c, v
            self.emitted = False
        if completed is None and not self.emitted and floor == self.bucket:
            last_slot = self.bucket + pd.Timedelta(seconds=55)
            if ts >= last_slot:
                completed = (self.bucket, self.o, self.h, self.l, self.c, self.v)
                self.emitted = True
        return completed


class V9Bot:
    def __init__(self):
        self.ib: Optional[IB] = None
        self.state: Optional[BotState] = None
        self.regime: Optional[DailyRegime] = None
        self.contract: Optional[Future] = None
        self.data_contract: Optional[Future] = None

        # VWAP LONG R3 detector
        self.vwap_long_state = VwapState(
            side='LONG',
            entry_band_k=C.VWAP_LONG["entry_band_k"],
            n_bars_outside=C.VWAP_LONG["n_bars_outside"],
            swing_lookback=C.VWAP_LONG["swing_lookback"],
            post_halt_blackout_bars=C.VWAP_LONG.get("post_halt_blackout_bars", 0),
        )

        # R3 re-entry detector (fires after VWAP_R3 stop-out)
        self.r3rev = R3RevLive(
            max_wait=C.R3_REV["max_wait"],
            wait_bars=C.R3_REV["wait_bars"],
        )

        # CCI AM detector
        self.cci = CciAmLive(
            cci_n=C.CCI_AM["cci_n"],
            ema_span=C.CCI_AM["ema_span"],
            atr_n=C.CCI_AM["atr_n"],
            norm_window=C.CCI_AM["norm_window"],
            vwap_frac=C.CCI_AM["vwap_frac"],
        )

        # TLB Bear Short detector
        self.tbs = TlbBearShortLive(k=C.TBS["k"], atr_len=C.TBS["atr_len"],
                                     line_min=C.TBS["line_min"])
        self.tbs_session_fills: dict[str, int] = {}  # session_name -> count today
        self.tbs_day_fills: int = 0

        # Aggregators: 5-sec -> 5-min and 5-sec -> 1-min
        self.agg_5m = FiveMinAggregator(C.VWAP_LONG["bar_min"])
        self.agg_1m = OneMinAggregator()

        # VWAP session state for R3_REV target computation
        self._vwap_cumv = 0.0
        self._vwap_cumvp = 0.0
        self._vwap_cumsq = 0.0
        self._vwap_val = float("nan")
        self._vwap_sd = float("nan")
        self._vwap_session = ""

        # Per-sleeve order handles: name -> {entry, stop, target}
        self.live: dict[str, dict] = {}
        self._flattening: dict[str, dict] = {}
        self._quarantined: set[str] = set()
        self._flat_alert_t: float = 0.0
        self._last_close: Optional[float] = None
        self._last_bar_utc: Optional[datetime] = None
        self._stall_since: Optional[datetime] = None
        self._stall_escalated: bool = False

        # R3_REV: track VWAP_R3 5-min bar lows from entry for stop computation
        self._r3_entry_bar_lows: list[float] = []
        self._r3_in_position: bool = False
        self._r3_signal_bar_low: float = 0.0

    # ---- Connection (same as v9.1 — abbreviated, delegates to shared code) ----
    def refresh_data(self) -> None:
        sys.path.insert(0, "/root/v9")
        try:
            from tools.v9_pull_latest_data import run as pull_run
            log.info("Refreshing NQ data via IB...")
            result = pull_run()
            log.info(f"Data pull result: {result}")
        except Exception as e:
            log.warning(f"Data refresh FAILED: {e}. Continuing with existing data.")

    def connect_ib(self, first: bool = True) -> None:
        self.ib = IB()
        log.info(f"Connecting to IB at {C.IB_HOST}:{C.IB_PORT} clientId={C.CLIENT_ID}")
        SILENT_MIN = 60
        attempt = 0
        alerted = False
        while True:
            attempt += 1
            try:
                self.ib.connect(C.IB_HOST, C.IB_PORT, clientId=C.CLIENT_ID, timeout=15)
                if attempt > 1:
                    if alerted:
                        log.warning(f"IB RECONNECTED after {attempt} attempts.")
                    else:
                        log.info(f"IB connected on attempt {attempt}.")
                break
            except Exception as e:
                if attempt <= SILENT_MIN:
                    log.info(f"IB connect failed (attempt {attempt}): {e!r} — retrying in 60s...")
                elif attempt == SILENT_MIN + 1:
                    alerted = True
                    log.error(f"IB connect STILL failing after ~{SILENT_MIN} min: {e!r}.")
                elif attempt % 60 == 0:
                    alerted = True
                    log.warning(f"IB connect still failing (attempt {attempt}): {e!r}")
                else:
                    log.info(f"IB connect failed (attempt {attempt}): {e!r}; retrying...")
                try:
                    self.ib.disconnect()
                except Exception:
                    pass
                time.sleep(60)
        accts = self.ib.managedAccounts()
        for a in accts:
            if not a.startswith(C.ACCOUNT_PREFIX):
                log.error(f"REFUSING: account '{a}' not expected (prefix '{C.ACCOUNT_PREFIX}').")
                self.ib.disconnect()
                raise SystemExit(1)
        log.info(f"Connected. Accounts: {accts}")
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
                log.error(f"Could not resolve {label} contract. Aborting.")
                self.ib.disconnect(); raise SystemExit(1)
            if label == "DATA":
                try:
                    expiry_crosscheck(details, log)
                except Exception as e:
                    log.warning(f"expiry cross-check skipped: {e!r}")
            ch = pick_front_contract(details, _roll_days, _today)
            if ch is None:
                log.error(f"Every {inst['symbol']} contract expired. Aborting.")
                self.ib.disconnect(); raise SystemExit(1)
            log.info(f"Resolved {label}: {ch.contract.localSymbol}")
            return ch.contract

        self.data_contract = _resolve(DATA_INST, "DATA")
        self.contract = self.data_contract if EXEC_INST is DATA_INST else _resolve(EXEC_INST, "EXEC")
        if self.contract.lastTradeDateOrContractMonth != self.data_contract.lastTradeDateOrContractMonth:
            log.error("ROLL DESYNC between data and exec contracts. Aborting.")
            self.ib.disconnect(); raise SystemExit(1)
        self.ib.errorEvent += self._on_error

    def _on_error(self, reqId, errorCode, errorString, contract=None) -> None:
        if errorCode == 1100:
            self._ib_lost_at = datetime.now(timezone.utc)
            log.warning("IB 1100: connectivity LOST — awaiting restore...")
        elif errorCode == 1101:
            _lost = getattr(self, "_ib_lost_at", None)
            _sec = f" after {(datetime.now(timezone.utc) - _lost).total_seconds():.0f}s" if _lost else ""
            log.warning(f"IB 1101: connectivity RESTORED{_sec}, re-subscribing bars.")
            self._ib_lost_at = None
            try:
                self._subscribe_bars()
            except Exception as e:
                log.error(f"re-subscribe after 1101 failed: {e!r}")
        elif errorCode == 1102:
            _lost = getattr(self, "_ib_lost_at", None)
            if _lost is not None:
                _sec = (datetime.now(timezone.utc) - _lost).total_seconds()
                log.warning(f"IB 1102: RESTORED after {_sec:.0f}s.")
                self._ib_lost_at = None
            else:
                log.info("IB 1102: connectivity restored, data maintained.")

    def _subscribe_bars(self) -> None:
        prev = getattr(self, "bars", None)
        if prev is not None:
            try:
                self.ib.cancelRealTimeBars(prev)
            except Exception:
                pass
        log.info(f"Subscribing to {self.data_contract.localSymbol} realtime bars (5s)")
        self.bars = self.ib.reqRealTimeBars(self.data_contract, 5, "TRADES", False)
        self.bars.updateEvent += self._on_realtime_bar
        self._last_bar_utc = datetime.now(timezone.utc)

    def _nq_should_have_bars(self) -> bool:
        et = now_et()
        wd = et.weekday()
        mins = et.hour * 60 + et.minute
        if wd == 5: return False
        if wd == 6 and mins < 18 * 60: return False
        if wd == 4 and mins >= 17 * 60: return False
        if 17 * 60 <= mins < 18 * 60: return False
        return True

    def _bar_stall_check(self, now_u: datetime) -> bool:
        if self._last_bar_utc is None or not self._nq_should_have_bars():
            return False
        gap = (now_u - self._last_bar_utc).total_seconds()
        if gap <= C.BAR_STALL_SEC:
            return False
        if self._stall_since is None:
            self._stall_since = self._last_bar_utc
            log.warning(f"BAR STALL: no bar in {gap:.0f}s — re-subscribing.")
        else:
            dark = (now_u - self._stall_since).total_seconds()
            if dark > 600 and not self._stall_escalated:
                self._stall_escalated = True
                log.error(f"BAR STALL: feed dark {dark / 60:.0f} min — check Gateway.")
        return True

    # ---- Regime (same as v9.1) ----
    def _load_regime_cache(self):
        import json
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
            for _ in range(6):
                with urllib.request.urlopen(u, timeout=20) as r:
                    d = json.load(r)
                rows += d.get("results", [])
                nxt = d.get("next_url")
                if not nxt: break
                u = nxt + f"&apiKey={key}"
            if not rows: return None
            pdf = pd.DataFrame(rows)
            pdf["t"] = pd.to_datetime(pdf["window_start"], unit="ns", utc=True)
            pdf = pdf.set_index("t").sort_index()
            pdf["et_date"] = pdf.index.tz_convert(ET).date
            sel = pdf[pdf["et_date"] == pd.Timestamp(date_et_iso).date()]
            if len(sel) == 0: return None
            return float(sel.iloc[-1]["close"])
        except Exception as e:
            log.warning(f"Polygon cross-check skipped ({e!r})")
            return None

    @staticmethod
    def _with_close(closes, date_et, value):
        want = str(date_et)[:10]
        fixed = closes.copy()
        key = next((k for k in fixed.index if pd.Timestamp(k).date().isoformat() == want), None)
        if key is None:
            key = pd.Timestamp(want).date()
        fixed.loc[key] = value
        return fixed.sort_index()

    def _crosscheck_regime_close(self, closes, fresh):
        poly_dev_max = getattr(C, "REGIME_POLY_MAX_DEV", 0.003)
        max_dev = getattr(C, "REGIME_CLOSE_MAX_DEV", 0.015)
        try:
            from contract_roll import close_symbol_for_date
            ticker = close_symbol_for_date(fresh.date_et, DATA_INST["symbol"]) or getattr(self.data_contract, "localSymbol", None)
        except Exception:
            ticker = getattr(self.data_contract, "localSymbol", None)

        MIN_BARS_COMPLETE = 700
        prior_date = pd.Timestamp(fresh.date_et)
        bar_count = int(getattr(self, "_bar_counts", pd.Series(dtype=int)).get(prior_date, 9999))
        is_sunday = prior_date.weekday() == 6
        if bar_count < MIN_BARS_COMPLETE and not is_sunday:
            poly_close = self._polygon_daily_close(fresh.date_et, ticker)
            if poly_close and poly_close > 0:
                log.warning(f"INCOMPLETE SESSION {fresh.date_et}: {bar_count} bars, substituting Polygon.")
                fixed = self._with_close(closes, fresh.date_et, poly_close)
                return compute_regime(fixed, sma_window=C.REGIME["sma_window"],
                                      mom_window=C.REGIME["momentum_window"])
            try:
                bars = self.ib.reqHistoricalData(
                    self.data_contract, endDateTime="", durationStr="6 D",
                    barSizeSetting="1 day", whatToShow="TRADES", useRTH=False, formatDate=2)
                ib_close = None
                for b in (bars or []):
                    if pd.Timestamp(str(b.date)).date().isoformat() == fresh.date_et:
                        ib_close = float(b.close); break
                if ib_close and ib_close > 0:
                    log.warning(f"INCOMPLETE SESSION {fresh.date_et}: substituting IB ETH close.")
                    fixed = self._with_close(closes, fresh.date_et, ib_close)
                    return compute_regime(fixed, sma_window=C.REGIME["sma_window"],
                                          mom_window=C.REGIME["momentum_window"])
            except Exception:
                pass

        poly_close = self._polygon_daily_close(fresh.date_et, ticker)
        if poly_close and poly_close > 0:
            dev = abs(fresh.close - poly_close) / poly_close
            if dev <= poly_dev_max:
                log.info(f"regime cross-check OK (Polygon) {fresh.date_et}")
                return fresh
            log.warning(f"REGIME CLOSE CROSS-CHECK FAILED {fresh.date_et}: substituting Polygon.")
            fixed = self._with_close(closes, fresh.date_et, poly_close)
            return compute_regime(fixed, sma_window=C.REGIME["sma_window"],
                                  mom_window=C.REGIME["momentum_window"])

        try:
            bars = self.ib.reqHistoricalData(
                self.data_contract, endDateTime="", durationStr="6 D",
                barSizeSetting="1 day", whatToShow="TRADES", useRTH=False, formatDate=2)
            ib_close = None
            for b in (bars or []):
                if pd.Timestamp(str(b.date)).date().isoformat() == fresh.date_et:
                    ib_close = float(b.close); break
            if not ib_close or ib_close <= 0:
                return fresh
            dev = abs(fresh.close - ib_close) / ib_close
            if dev > max_dev:
                log.warning(f"REGIME CLOSE CROSS-CHECK FAILED {fresh.date_et}: substituting IB settle.")
                fixed = self._with_close(closes, fresh.date_et, ib_close)
                return compute_regime(fixed, sma_window=C.REGIME["sma_window"],
                                      mom_window=C.REGIME["momentum_window"])
            log.info(f"regime cross-check OK (IB settle) {fresh.date_et}")
        except Exception:
            pass
        return fresh

    # ---- Daily setup ----
    def setup_for_day(self) -> None:
        today_et = now_et().date().isoformat()
        self.state = BotState.load_or_new(C.STATE_FILE, today_et, ALL_SLEEVE_NAMES)
        closes, self._bar_counts = load_daily_closes_for_regime()

        _last_data_date = closes.index[-1].date() if hasattr(closes.index[-1], 'date') else pd.Timestamp(closes.index[-1]).date()
        _data_age_days = (now_et().date() - _last_data_date).days
        if _data_age_days > 4:
            log.error(f'DATA STALE ({_data_age_days}d) — aborting.')
            raise SystemExit(f'DATA STALE ({_data_age_days}d)')
        elif _data_age_days > 2:
            log.warning(f'DATA AGE WARNING: {_data_age_days} days old.')

        fresh = compute_regime(closes, sma_window=C.REGIME["sma_window"],
                               mom_window=C.REGIME["momentum_window"])
        fresh = self._crosscheck_regime_close(closes, fresh)

        cached = self._load_regime_cache()
        if cached is not None and cached.date_et == fresh.date_et:
            if cached.cell != fresh.cell:
                log.warning(f"REGIME FLIP BLOCKED: locked {cached.cell} vs recompute {fresh.cell}.")
            self.regime = cached
        else:
            self._save_regime_cache(fresh)
            self.regime = fresh

        reg = self.regime
        if abs(reg.ret20) < C.REGIME_BOUNDARY_EPS:
            log.warning(f"REGIME NEAR BOUNDARY: ret20 {reg.ret20*100:+.2f}%.")

        self.state.regime_cell = reg.cell
        self.state.regime_close = reg.close
        self.state.regime_sma200 = reg.sma200
        self.state.regime_ret20 = reg.ret20

        # Determine active sleeves from the new regime mapping
        active = list(C.REGIME_SLEEVES.get(reg.cell, []))
        # R3_REV is NOT in the regime map — it activates conditionally after VWAP_R3 stop-out.
        # But it can only fire when VWAP_R3 is active (it needs a stop-out), so mark it active
        # whenever VWAP_R3 is active.
        if "VWAP_LONG_R3" in active:
            active.append(R3REV_NAME)

        disabled = getattr(C, 'DISABLED_SLEEVES', set())
        if disabled:
            active = [s for s in active if s not in disabled]

        try:
            sys.path.insert(0, "/root/v9")
            import us_market_holidays as _ush
            _hol = _ush.no_trade_reason(now_et().date())
        except Exception:
            _hol = None
        if _hol:
            log.warning(f"US MARKET HOLIDAY — {today_et}: {_hol}. NO NEW ENTRIES today.")
            active = []

        for name, sleeve in self.state.sleeves.items():
            sleeve.active_today = (name in active)

        # Reset TBS daily counters
        self.tbs_session_fills = {}
        self.tbs_day_fills = 0
        self.r3rev.reset()
        self._r3_entry_bar_lows = []
        self._r3_in_position = False

        log.info("=" * 60)
        log.info(f"v9.2 DAILY SETUP — {today_et}")
        log.info(f"Regime cell: {reg.cell}")
        log.info(f"NQ close prev: {reg.close:.2f}  200-DMA: {reg.sma200:.2f}  20d ret: {reg.ret20*100:+.2f}%")
        log.info(f"Active sleeves: {active}")
        log.info("=" * 60)
        self.state.save(C.STATE_FILE)

    # ---- Roll sweep / reconcile ----
    def _roll_sweep(self) -> None:
        from contract_roll import flatten_foreign_positions
        held = {n: s for n, s in self.state.sleeves.items() if s.in_position} if self.state else {}
        own = sum((s.qty or EXEC_INST["qty"]) * (1 if s.side == "long" else -1) for s in held.values())
        closed = flatten_foreign_positions(self.ib, EXEC_INST["symbol"], self.contract.localSymbol, log,
                                           own_client_ids={C.CLIENT_ID}, max_close=own)
        if not closed or not held:
            return
        for name, sl in held.items():
            log.warning(f"[{name}] ROLL: closed on rolled-off month")
            self._reset_sleeve(name)
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
        from ib_async import ExecutionFilter
        ids = {sleeve.stop_order_id, sleeve.target_order_id} - {None}
        try:
            fills = self.ib.reqExecutions(ExecutionFilter(clientId=C.CLIENT_ID))
        except Exception:
            return None
        hit = [f for f in fills or [] if f.execution.orderId in ids]
        return float(hit[-1].execution.price) if hit else None

    def _reprotect(self, name: str, survivor) -> None:
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
        log.warning(f"[{name}] RE-PROTECTED: fresh OCA {oca}")

    def reconcile_open_positions(self) -> None:
        if not self.state:
            return
        held = [n for n, s in self.state.sleeves.items() if s.in_position]
        if not held:
            return
        try:
            self.ib.reqAllOpenOrders()
            self.ib.sleep(1)
            mine = [t for t in self.ib.openTrades()
                    if getattr(t.order, "clientId", C.CLIENT_ID) == C.CLIENT_ID and not t.isDone()]
            open_trades = {t.order.orderId: t for t in mine}
        except Exception as e:
            log.warning(f"reconcile: could not read open orders ({e}).")
            return
        for name in held:
            sleeve = self.state.sleeves[name]
            st = open_trades.get(sleeve.stop_order_id)
            tg = open_trades.get(sleeve.target_order_id)
            if st is None and tg is None:
                px = self._bracket_exit_px(sleeve)
                if px is not None:
                    log.warning(f"[{name}] bracket gone; exit FILLED @ {px:.2f} while down.")
                    self._book_exit(name, px, "RECONCILE")
                else:
                    # OPPOSITE-POSITION FIX: do NOT use _account_net() == 0 as proof.
                    # With opposite sleeves, net can be 0 while both are live.
                    # Quarantine: a human must verify.
                    self._quarantined.add(name)
                    log.error(f"[{name}] QUARANTINED: in_position but NO bracket, NO exit fill. "
                              f"Cannot attribute — CHECK IB MANUALLY.")
                continue
            if st is None or tg is None:
                log.warning(f"[{name}] bracket DAMAGED — replacing.")
                self._reprotect(name, st if st is not None else tg)
                continue
            self.live[name] = {"stop": st, "target": tg}
            st.filledEvent += lambda tr, n=name: self._on_exit_fill(n, tr, "STOP")
            tg.filledEvent += lambda tr, n=name: self._on_exit_fill(n, tr, "TARGET")
            log.info(f"[{name}] re-bound to working bracket.")

    # ---- 1-min bar handler (CCI + TBS) ----
    def on_1min(self, ts: pd.Timestamp, o: float, h: float, l: float, c: float,
                v: float, live: bool = True) -> None:
        """Process one completed 1-min bar. Feeds CCI and TBS detectors."""
        if not self.state or not self.regime:
            return

        et_ts = ts.tz_convert(ET) if ts.tzinfo else ts
        et_t = et_ts.time()

        # Feed CCI detector
        cci_sig = self.cci.on_1min(ts, o, h, l, c, live=live)

        # Feed TBS detector (returns touch events)
        tbs_ev = self.tbs.on_1min(ts, o, h, l, c, live=live)

        if not live or self.state.halted:
            return

        # CCI signal handling
        if cci_sig is not None:
            sleeve = self.state.sleeves.get(CCI_NAME)
            if sleeve and sleeve.active_today and not sleeve.in_position:
                enabled = ENABLE_LONG if cci_sig["side"] == "LONG" else ENABLE_SHORT
                if enabled and CCI_NAME not in self._quarantined:
                    self._enter_cci(cci_sig)

        # TBS signal handling
        if tbs_ev is not None and tbs_ev.get("session") is not None:
            sleeve = self.state.sleeves.get(TBS_NAME)
            if sleeve and sleeve.active_today and not sleeve.in_position:
                if ENABLE_SHORT and TBS_NAME not in self._quarantined:
                    sess_name = tbs_ev["session"]
                    if (self.tbs_session_fills.get(sess_name, 0) < C.TBS["max_per_session"]
                            and self.tbs_day_fills < C.TBS["max_per_day"]
                            and tbs_ev.get("stop_price") is not None
                            and tbs_ev.get("target_price") is not None):
                        self._enter_tbs(tbs_ev)

        # CCI EOD flatten at 16:00 ET
        et_min = et_t.hour * 60 + et_t.minute
        if et_min >= CCI_EOD_MIN:
            cci_sleeve = self.state.sleeves.get(CCI_NAME)
            if cci_sleeve and cci_sleeve.in_position:
                self._flatten_sleeve(CCI_NAME, "CCI_EOD", c)

        # TBS session-end flatten — only check the session this position entered in
        tbs_sleeve = self.state.sleeves.get(TBS_NAME)
        if tbs_sleeve and tbs_sleeve.in_position and tbs_sleeve.entry_session:
            sess_cfg = TBS_SESSIONS.get(tbs_sleeve.entry_session)
            if sess_cfg:
                (_, end_min), _, _ = sess_cfg
                if et_min >= end_min:
                    self._flatten_sleeve(TBS_NAME, f"TBS_{tbs_sleeve.entry_session}_END", c)

    def _enter_cci(self, sig: dict) -> None:
        direction = sig["side"].lower()
        qty = self._sleeve_qty(CCI_NAME)
        # CCI has no structural target — it exits EOD. Use a wide target as a placeholder
        # that effectively never fills (the EOD flatten gets it first).
        target = sig["entry_price"] + 500 if direction == "long" else sig["entry_price"] - 500
        log.info(f"[{CCI_NAME}] SIGNAL {direction.upper()} entry={sig['entry_price']:.2f} "
                 f"stop={sig['stop_price']:.2f} (EOD exit) — placing MKT qty={qty}")
        trade = place_market_entry(self.ib, self.contract, direction, qty)
        self.live.setdefault(CCI_NAME, {})["entry"] = trade
        full_sig = dict(side=sig["side"], entry_price=sig["entry_price"],
                        stop_price=sig["stop_price"], target_price=target)
        trade.filledEvent += lambda tr: self._on_entry_fill(CCI_NAME, full_sig, tr)
        trade.statusEvent += lambda tr: self._on_entry_status(CCI_NAME, full_sig, tr)

    def _enter_tbs(self, ev: dict) -> None:
        direction = "short"
        qty = self._sleeve_qty(TBS_NAME)
        log.info(f"[{TBS_NAME}] SIGNAL SHORT entry={ev['entry_price']:.2f} "
                 f"stop={ev['stop_price']:.2f} target={ev['target_price']:.2f} — placing MKT qty={qty}")
        trade = place_market_entry(self.ib, self.contract, direction, qty)
        self.live.setdefault(TBS_NAME, {})["entry"] = trade
        full_sig = dict(side="SHORT", entry_price=ev["entry_price"],
                        stop_price=ev["stop_price"], target_price=ev["target_price"],
                        tbs_session=ev["session"])
        trade.filledEvent += lambda tr: self._on_entry_fill(TBS_NAME, full_sig, tr)
        trade.statusEvent += lambda tr: self._on_entry_status(TBS_NAME, full_sig, tr)
        sess_name = ev["session"]
        self.tbs_session_fills[sess_name] = self.tbs_session_fills.get(sess_name, 0) + 1
        self.tbs_day_fills += 1

    # ---- 5-min bar handler (VWAP_R3, R3_REV, + CCI 5-min context) ----
    def on_5min(self, ts: pd.Timestamp, o: float, h: float, l: float, c: float,
                v: float, live: bool = True) -> None:
        if not self.state or not self.regime:
            return
        et_t = ts.tz_convert(ET).time() if ts.tzinfo else ts.time()

        _prev = getattr(self, "_last_5min_ts", None)
        if _prev is not None and ts <= _prev:
            log.warning(f"BAR REJECTED (non-advancing): {ts} <= {_prev}")
            return
        self._last_5min_ts = ts

        # Update session VWAP for R3_REV target computation
        ts_utc = ts.to_pydatetime() if ts.tzinfo else ts.tz_localize("UTC").to_pydatetime()
        sess = session_for_utc_bar(ts_utc, 0)
        if sess != self._vwap_session:
            self._vwap_session = sess
            self._vwap_cumv = self._vwap_cumvp = self._vwap_cumsq = 0.0
        self._vwap_cumv, self._vwap_cumvp, self._vwap_cumsq, self._vwap_val, self._vwap_sd = \
            update_session_vwap(self._vwap_cumv, self._vwap_cumvp, self._vwap_cumsq, h, l, c, v)

        # Feed CCI's 5-min context (EMA50, ATR, swing high/low)
        self.cci.on_5min(ts, o, h, l, c, v, vwap=self._vwap_val)

        # Feed VWAP_R3 detector
        sig_vwap_long = self.vwap_long_state.on_bar(ts_utc, h, l, c, v,
                                                     anchor_hour_utc=C.VWAP_LONG["anchor_hour_utc"])

        # Track R3 position bar lows for R3_REV stop computation
        if self._r3_in_position:
            self._r3_entry_bar_lows.append(l)

        # Feed R3_REV detector
        upper_band = self._vwap_val + 2.0 * self._vwap_sd if not (
            pd.isna(self._vwap_val) or pd.isna(self._vwap_sd)) else float("nan")
        r3rev_sig = self.r3rev.on_5min(ts, o, h, l, c, sess, self._vwap_val, upper_band, live=live)

        if not live or self.state.halted:
            return

        # Half-day / EOD handling
        _hd = so.session_for_today(log=log)
        _eod_h, _eod_m = _hd["eod"] if _hd["is_half_day"] else (C.EOD_FLATTEN_H, C.EOD_FLATTEN_M)
        _gap_h, _gap_m = _hd["eod"] if _hd["is_half_day"] else (C.GAP_FLATTEN_H, C.GAP_FLATTEN_M)
        _past_last_entry = _hd["is_half_day"] and (
            et_t.hour > _hd["last_entry"][0]
            or (et_t.hour == _hd["last_entry"][0] and et_t.minute >= _hd["last_entry"][1]))

        gap_flat = (et_t.hour == _gap_h and et_t.minute >= _gap_m) or (_gap_h < et_t.hour < 17)
        if gap_flat:
            self._flatten_all("GAP", c)

        # VWAP_R3 entry
        if sig_vwap_long is not None:
            sig_vwap_long["signal_bar_low"] = l
            sleeve = self.state.sleeves.get("VWAP_LONG_R3")
            if (sleeve and sleeve.active_today and not sleeve.in_position
                    and ENABLE_LONG and not gap_flat and not _past_last_entry
                    and "VWAP_LONG_R3" not in self._quarantined
                    and is_in_window(et_t, C.VWAP_LONG["active_windows_et"])
                    and self._vwap_risk_ok(sig_vwap_long, "VWAP_LONG_R3")):
                self._enter("VWAP_LONG_R3", sig_vwap_long)

        # R3_REV entry
        if r3rev_sig is not None:
            sleeve = self.state.sleeves.get(R3REV_NAME)
            if (sleeve and sleeve.active_today and not sleeve.in_position
                    and ENABLE_LONG and not gap_flat and not _past_last_entry
                    and R3REV_NAME not in self._quarantined):
                self._enter(R3REV_NAME, r3rev_sig)

    def _sleeve_qty(self, name: str) -> int:
        return int(C.SLEEVE_QTY.get(name, EXEC_INST["qty"]))

    def _vwap_risk_ok(self, sig: dict, name: str) -> bool:
        cap = getattr(C, "VWAP_MAX_STOP_PTS", 200)
        stop_dist = abs(sig["entry_price"] - sig["stop_price"])
        if stop_dist > cap:
            log.warning(f"[{name}] signal SKIPPED: stop {stop_dist:.1f}pt > {cap}pt cap.")
            return False
        return True

    def _enter(self, name: str, sig: dict) -> None:
        direction = sig["side"].lower()
        qty = self._sleeve_qty(name)
        target = sig.get("target_price", sig["entry_price"])
        log.info(f"[{name}] SIGNAL {direction.upper()} entry={sig['entry_price']:.2f} "
                 f"stop={sig['stop_price']:.2f} target={target:.2f} — MKT qty={qty}")
        trade = place_market_entry(self.ib, self.contract, direction, qty)
        self.live.setdefault(name, {})["entry"] = trade
        trade.filledEvent += lambda tr, n=name, s=sig: self._on_entry_fill(n, s, tr)
        trade.statusEvent += lambda tr, n=name, s=sig: self._on_entry_status(n, s, tr)

    def _on_entry_status(self, name: str, sig: dict, trade) -> None:
        os_ = trade.orderStatus
        if (os_.status in ("Cancelled", "ApiCancelled", "Inactive") and (os_.filled or 0) > 0
                and not self.state.sleeves[name].in_position):
            log.error(f"[{name}] entry ended {os_.status} after PARTIAL fill {os_.filled:g}")
            self._on_entry_fill(name, sig, trade, qty_override=int(os_.filled))

    def _on_entry_fill(self, name: str, sig: dict, trade, qty_override: int = 0) -> None:
        sleeve = self.state.sleeves[name]
        if sleeve.in_position:
            return
        direction = sig["side"].lower()
        qty = int(qty_override or trade.order.totalQuantity)
        fill_px = float(trade.fills[-1].execution.avgPrice) if trade.fills else sig["entry_price"]
        sleeve.in_position = True
        sleeve.side = direction
        sleeve.entry_price = fill_px
        sleeve.stop_price = sig["stop_price"]
        sleeve.target_price = sig.get("target_price", fill_px)
        sleeve.entry_time = now_et().isoformat()
        sleeve.entry_order_id = trade.order.orderId
        sleeve.qty = qty
        sleeve.trade_count_today += 1
        log.info(f"[{name}] {direction.upper()} ENTRY filled @ {fill_px:.2f}")
        record_v9_fill(name, "BUY" if direction == "long" else "SELL", qty, fill_px,
                       f"{name}_ENTRY",
                       signal_px=sig["entry_price"], stop=sig["stop_price"],
                       target=sig.get("target_price"))
        # Track R3 position for R3_REV dependency
        if name == "VWAP_LONG_R3":
            self._r3_in_position = True
            self._r3_signal_bar_low = sig.get("signal_bar_low", fill_px)
            self._r3_entry_bar_lows = []
        # Track TBS entry session for session-end flatten
        if name == TBS_NAME and "tbs_session" in sig:
            sleeve.entry_session = sig["tbs_session"]

        oca = f"v9_{name}_{trade.order.orderId}"
        st = place_protective_stop(self.ib, self.contract, direction, qty, sig["stop_price"], oca)
        tg = place_limit_target(self.ib, self.contract, direction, qty,
                                sig.get("target_price", fill_px), oca)
        self.live[name]["stop"] = st
        self.live[name]["target"] = tg
        sleeve.stop_order_id = st.order.orderId
        sleeve.target_order_id = tg.order.orderId
        st.filledEvent += lambda tr, n=name: self._on_exit_fill(n, tr, "STOP")
        tg.filledEvent += lambda tr, n=name: self._on_exit_fill(n, tr, "TARGET")

    def _on_exit_fill(self, name: str, trade, kind: str) -> None:
        sleeve = self.state.sleeves[name]
        if not sleeve.in_position:
            return
        self._flattening.pop(name, None)
        exit_px = float(trade.fills[-1].execution.price) if trade.fills else (
            sleeve.stop_price if kind == "STOP" else sleeve.target_price)

        # R3_REV inter-sleeve dependency: if VWAP_R3 stops out, arm the R3_REV detector
        if name == "VWAP_LONG_R3" and kind == "STOP":
            sess = self._vwap_session
            self.r3rev.on_r3_stopout(sess, self._r3_signal_bar_low, list(self._r3_entry_bar_lows))
            log.info(f"[{R3REV_NAME}] ARMED after VWAP_R3 stop-out @ {exit_px:.2f}")

        self._book_exit(name, exit_px, kind)

        # Clean up R3 tracking
        if name == "VWAP_LONG_R3":
            self._r3_in_position = False

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
                       label, realized=round(pnl_usd, 2), realized_R=round(R, 4),
                       signal_px=sleeve.entry_price, reason=label)
        handles = self.live.get(name, {})
        cancel_order(self.ib, handles.get("stop") if label != "STOP" else handles.get("target"))
        self._reset_sleeve(name)
        if self.state.cum_realized_usd <= C.DAILY_LOSS_LIMIT_USD:
            self.state.halted = True
            log.warning(f"DAILY LOSS LIMIT hit (${self.state.cum_realized_usd:+,.0f}) — halting.")
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
        sleeve = self.state.sleeves.get(name)
        if not sleeve or not sleeve.in_position or name in self._flattening:
            return
        if name in self._quarantined:
            log.error(f"[{name}] {reason} flatten SKIPPED — QUARANTINED.")
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
        log.info(f"[{name}] FLATTEN ({reason}) @ ~{last_px:.2f}")
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
            log.error(f"[{name}] flatten market order NOT placed — CHECK IB")
            return
        tr.filledEvent += lambda t, n=name: self._on_flatten_fill(n, t)

    def _on_flatten_fill(self, name: str, trade) -> None:
        fl = self._flattening.pop(name, None)
        if not self.state.sleeves[name].in_position:
            return
        px = self._fill_px(trade) or (fl["last_px"] if fl else self.state.sleeves[name].entry_price)

        # If VWAP_R3 is being flattened (not by stop), do NOT arm R3_REV
        self._book_exit(name, px, (fl["reason"] if fl else "FLATTEN").upper())
        if name == "VWAP_LONG_R3":
            self._r3_in_position = False

    def _flatten_watchdog(self) -> None:
        now = time.time()
        for name, fl in list(self._flattening.items()):
            if not fl["sent"] and now - fl["t0"] > 60 and now - self._flat_alert_t > 300:
                self._flat_alert_t = now
                log.error(f"[{name}] flatten STUCK {now - fl['t0']:.0f}s — re-sending cancels.")
                for h in self.live.get(name, {}).values():
                    if h is not None and not h.isDone():
                        cancel_order(self.ib, h)

    def _flatten_all(self, reason: str, last_px: float) -> None:
        for name in list(self.state.sleeves.keys()):
            self._flatten_sleeve(name, reason, last_px)

    # ---- Main loop ----
    def run(self) -> None:
        log.info(f"v9.2 bot starting — ENABLE_LONG={ENABLE_LONG} ENABLE_SHORT={ENABLE_SHORT}")
        self.refresh_data()
        self.connect_ib()
        self.setup_for_day()
        self._roll_sweep()
        self.reconcile_open_positions()
        if not (ENABLE_LONG or ENABLE_SHORT):
            log.warning("DRY RUN mode (no orders)")
            log.info(f"Regime: {self.regime.cell}, sleeves: "
                     f"{[n for n,s in self.state.sleeves.items() if s.active_today]}")
        self.warmup()
        self._subscribe_bars()
        try:
            while True:
                self.ib.sleep(60)
                if not self.ib.isConnected():
                    log.info("IB socket DOWN — reconnecting...")
                    try:
                        self.connect_ib(first=False)
                        self.reconcile_open_positions()
                        self._subscribe_bars()
                    except Exception as e:
                        log.error(f"reconnect failed: {e!r}; retrying next cycle.")
                if (self.ib and self.ib.isConnected()
                        and self._bar_stall_check(datetime.now(timezone.utc))):
                    try:
                        self._subscribe_bars()
                    except Exception as e:
                        log.error(f"bar-stall re-subscribe failed: {e!r}")
                self._flatten_watchdog()
                if self.state:
                    self.state.connected = bool(self.ib and self.ib.isConnected())
                    self.state.save(C.STATE_FILE)
                if now_et().date().isoformat() != self.state.date_et:
                    et_min = now_et().hour * 60 + now_et().minute
                    if et_min >= 10:
                        self.refresh_data()
                        self.setup_for_day()
        except KeyboardInterrupt:
            log.info("Shutdown — leaving server-side stops in place.")
        finally:
            if self.ib and self.ib.isConnected():
                self.ib.disconnect()
            log.info("v9.2 bot stopped.")

    def warmup(self) -> None:
        """Replay recent 5-min + 1-min bars to build detector state."""
        try:
            # 5-min warmup (VWAP, R3_REV)
            hist5 = self.ib.reqHistoricalData(
                self.data_contract, endDateTime="", durationStr="15 D",
                barSizeSetting="5 mins", whatToShow="TRADES", useRTH=False, formatDate=2)
            log.info(f"Warm-up: replaying {len(hist5)} 5-min bars")
            _now_utc = pd.Timestamp.now(tz="UTC")
            for b in hist5:
                ts = pd.Timestamp(b.date)
                ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
                if ts + pd.Timedelta(minutes=5) > _now_utc:
                    continue
                vol = float(b.volume) if (b.volume and b.volume > 0) else 0.0
                self.on_5min(ts, float(b.open), float(b.high), float(b.low),
                             float(b.close), vol, live=False)

            # 1-min warmup (CCI, TBS)
            hist1 = self.ib.reqHistoricalData(
                self.data_contract, endDateTime="", durationStr="2 D",
                barSizeSetting="1 min", whatToShow="TRADES", useRTH=False, formatDate=2)
            log.info(f"Warm-up: replaying {len(hist1)} 1-min bars")
            for b in hist1:
                ts = pd.Timestamp(b.date)
                ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
                if ts + pd.Timedelta(minutes=1) > _now_utc:
                    continue
                vol = float(b.volume) if (b.volume and b.volume > 0) else 0.0
                self.on_1min(ts, float(b.open), float(b.high), float(b.low),
                             float(b.close), vol, live=False)

            if hist5:
                self._last_close = float(hist5[-1].close)
            log.info("Warm-up done.")
        except Exception as e:
            log.warning(f"Warm-up failed ({e}); detectors will build from live bars.")

    def _on_realtime_bar(self, bars, hasNewBar):
        if not bars:
            return
        self._last_bar_utc = datetime.now(timezone.utc)
        self._stall_since = None
        self._stall_escalated = False
        bar = bars[-1]
        ts = pd.Timestamp(bar.time)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        o = float(bar.open_); h = float(bar.high); l = float(bar.low); c = float(bar.close)
        vol = float(bar.volume) if (bar.volume and bar.volume > 0) else 0.0

        # 5-sec -> 1-min aggregation
        done_1m = self.agg_1m.add(ts, o, h, l, c, vol)
        if done_1m is not None:
            bts, bo, bh, bl, bc, bv = done_1m
            try:
                self.on_1min(bts, bo, bh, bl, bc, bv, live=True)
            except Exception as e:
                log.exception(f"on_1min error: {e}")

        # 5-sec -> 5-min aggregation
        done_5m = self.agg_5m.add(ts, o, h, l, c, vol)
        if done_5m is not None:
            bts, bo, bh, bl, bc, bv = done_5m
            self._last_close = bc
            try:
                self.on_5min(bts, bo, bh, bl, bc, bv, live=True)
            except Exception as e:
                log.exception(f"on_5min error: {e}")

        # TBS also has a 5-sec touch path for live entries
        if self.state and not self.state.halted:
            tbs_sleeve = self.state.sleeves.get(TBS_NAME)
            if tbs_sleeve and tbs_sleeve.active_today and not tbs_sleeve.in_position:
                tbs_ev = self.tbs.on_5s(ts, o, h, l, c)
                if tbs_ev is not None and tbs_ev.get("session") is not None and ENABLE_SHORT:
                    sess_name = tbs_ev["session"]
                    if (self.tbs_session_fills.get(sess_name, 0) < C.TBS["max_per_session"]
                            and self.tbs_day_fills < C.TBS["max_per_day"]
                            and tbs_ev.get("stop_price") is not None
                            and tbs_ev.get("target_price") is not None
                            and TBS_NAME not in self._quarantined):
                        self._enter_tbs(tbs_ev)


def main():
    bot = V9Bot()
    bot.run()


if __name__ == "__main__":
    main()
