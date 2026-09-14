"""r3re_live_bot.py — LIVE REAL-MONEY executor for the R3 VWAP RE-ENTRY overlay.

============================  DISARMED BY DEFAULT  ============================
r3re_live_config.LIVE_TRADING_CONFIRMED=False + DRY_RUN=True  => every real order is
HARD-BLOCKED by order_guard(): the bot logs "WOULD PLACE: ..." and touches nothing at IB
beyond read-only account/market-data. A human arms it (see r3re_live_config header).
=============================================================================

WHAT IT DOES (once armed)
  Recompute-and-reconcile executor for the kristov18 R3 VWAP re-entry, driven off the LIVE
  R3 config (post_halt_blackout_bars=24) via r3v_live_spec (which reuses the read-only bt_nq
  harness + the exact live VwapState). It is SELF-CONTAINED: it independently recomputes R3
  and the overlay from the READ-ONLY 1-min archive; it never reads or writes any /root/v9
  live-state file and never interferes with the live v9 process.

  ENTRY  : a RESTING BUY-STOP (DAY) at the current runmax (post-stop high), MODIFIED IN PLACE
           only when runmax rises, so a live fill == the backtest max(open, runmax).
  ON FILL: a server-side OCA bracket = protective STOP (20-bar swing low) + LIMIT target
           (lag-1 band magnet: session VWAP if fill<VWAP else +2sigma upper), the target leg
           replaced each 5-min bar to track the moving magnet. Plus a bot-managed 15:55 ET flatten.
  SAFETY : per-trade risk cap, daily-loss halt, reconciliation on startup/reconnect
           (rebind/flatten orphans + Telegram-warn), 15:55 flatten, PAUSED file + KILL switch,
           err-326/clientId + connect-retry handling. SIZE 1 MNQ.

  clientId 65 (REAL). Account U6015126. Port 7497. Restart-safe (state.json).
"""
from __future__ import annotations
import os, sys, json, glob, subprocess, logging, time
from datetime import datetime, timezone, timedelta

import numpy as np, pandas as pd, pytz
from ib_async import IB, Future, StopOrder, Contract, ExecutionFilter

sys.path.insert(0, "/root/r3re_live")
sys.path.insert(0, "/root/v9")

import r3re_live_config as CFG
import r3v_live_spec as SPEC
from contract_roll import pick_front_contract, flatten_foreign_positions
# REUSE the proven v9 OCA/bracket helpers (read-only import; we do NOT call record_v9_fill,
# which would write under /root/v9 — this bot keeps its own ledger under /root/r3re_live).
from v9_execution import place_protective_stop, place_limit_target, cancel_order, market_flatten

ET = pytz.timezone("America/New_York")
HKT = pytz.timezone("Asia/Hong_Kong")                 # the owner reads Telegram in HKT

os.makedirs(CFG.DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout),
                              logging.FileHandler(CFG.LOG_FILE)])
log = logging.getLogger("r3re_live")
logging.getLogger("ib_async").setLevel(logging.WARNING)


def now_utc(): return datetime.now(timezone.utc)
def now_et():  return now_utc().astimezone(ET)


TG_TAG = "R3_REENTRY"   # trade type, same bracket style as V9's [VWAP_LONG_R3] / [TLB_LONG]


def tg(msg: str, level: str = "INFO"):
    try:
        subprocess.Popen([CFG.ALERT_SH, f"[NQ-{level}] [{TG_TAG}] {msg}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log.warning(f"telegram send failed: {e!r}")


# ======================================================================= order guard
class OrderRouter:
    """The SINGLE choke-point for every real order. When disarmed, hard-blocks and logs
    'WOULD PLACE: ...' — it NEVER touches IB with an order in that state. `blocked` counts
    how many real orders were suppressed (used by the self-test runtime intercept)."""
    def __init__(self, ib, contract):
        self.ib = ib; self.contract = contract
        self.blocked = 0

    def _armed(self) -> bool:
        return CFG.is_armed()

    def resting_buy_stop(self, trigger_px: float, qty: int):
        # 2026-09-14: DAY, not GTC. The bot cancels it itself (window end / 15:55 / KILL), but if the
        # process died while armed a GTC order would rest at IB indefinitely. A DAY futures order
        # expires at the 17:00 ET session end, which still covers every hour R3 re-entry may trade.
        desc = f"RESTING BUY-STOP {qty} {self.contract.localSymbol} @ trigger {trigger_px:.2f} (DAY,outsideRTH)"
        if not self._armed():
            self.blocked += 1
            log.warning(f"WOULD PLACE: {desc}  [DISARMED — no order sent]")
            return None
        o = StopOrder("BUY", qty, trigger_px)
        o.tif = "DAY"; o.outsideRth = True
        log.info(f"PLACING: {desc}")
        return self.ib.placeOrder(self.contract, o)

    def modify_buy_stop(self, trade, trigger_px: float):
        """Move a resting buy-stop's trigger IN PLACE (same orderId -> no moment with no order
        and no moment with two). ib_async returns the same Trade, so its filledEvent survives."""
        desc = f"MODIFY BUY-STOP #{trade.order.orderId} {trade.order.auxPrice:.2f} -> {trigger_px:.2f}"
        if not self._armed():
            self.blocked += 1
            log.warning(f"WOULD PLACE: {desc}  [DISARMED — no order sent]")
            return trade
        old = trade.order.auxPrice
        trade.order.auxPrice = trigger_px
        log.info(f"PLACING: {desc}")
        try:
            return self.ib.placeOrder(self.contract, trade.order)
        except AssertionError:
            # ib_async refuses to modify an order that finished (filled/cancelled) in the meantime;
            # leave the handle as-is -- the fill handler or the next cycle deals with it
            trade.order.auxPrice = old
            log.warning(f"modify skipped: order #{trade.order.orderId} already {trade.orderStatus.status}")
            return trade

    def oca_bracket(self, direction: str, qty: int, stop_px: float, target_px: float, oca: str):
        desc = (f"OCA[{oca}] STOP {stop_px:.2f} + LIMIT {target_px:.2f} "
                f"({qty} {self.contract.localSymbol}, dir={direction})")
        if not self._armed():
            self.blocked += 1
            log.warning(f"WOULD PLACE: {desc}  [DISARMED — no order sent]")
            return None, None
        st = place_protective_stop(self.ib, self.contract, direction, qty, stop_px, oca)
        tgt = place_limit_target(self.ib, self.contract, direction, qty, target_px, oca)
        log.info(f"PLACING: {desc}")
        return st, tgt

    def modify_target(self, trade, target_px: float):
        """Move the bracket's LIMIT target IN PLACE (same orderId, stays in its OCA group).
        2026-09-14: replaces the old cancel + place-new. If the old target filled in that gap, a
        NEW sell limit was still sent -- after the position had already closed. ib_async returns
        the same Trade, so the filledEvent bound in _bind_bracket keeps working (do NOT re-bind)."""
        desc = f"MODIFY LIMIT target #{trade.order.orderId} {trade.order.lmtPrice:.2f} -> {target_px:.2f}"
        if not self._armed():
            self.blocked += 1
            log.warning(f"WOULD PLACE: {desc}  [DISARMED — no order sent]")
            return trade
        old = trade.order.lmtPrice
        trade.order.lmtPrice = target_px
        log.info(f"PLACING: {desc}")
        try:
            return self.ib.placeOrder(self.contract, trade.order)
        except AssertionError:
            # it filled/cancelled in the meantime -> the exit handler / next cycle owns it
            trade.order.lmtPrice = old
            log.warning(f"target modify skipped: order #{trade.order.orderId} already {trade.orderStatus.status}")
            return trade

    def place_target(self, direction: str, qty: int, target_px: float, oca: str):
        """A fresh LIMIT target in the position's EXISTING OCA group (only used when IB has dropped
        the target while the protective stop is still working)."""
        desc = f"LIMIT target {target_px:.2f} into OCA {oca}"
        if not self._armed():
            self.blocked += 1
            log.warning(f"WOULD PLACE: {desc}  [DISARMED — no order sent]")
            return None
        log.info(f"PLACING: {desc}")
        return place_limit_target(self.ib, self.contract, direction, qty, target_px, oca)

    def cancel(self, handle):
        if handle is None:
            return
        if not self._armed():
            self.blocked += 1
            log.warning("WOULD PLACE: CANCEL working order  [DISARMED — no order sent]")
            return
        cancel_order(self.ib, handle)

    def flatten(self, direction: str, qty: int, reason: str):
        desc = f"MARKET FLATTEN {qty} {self.contract.localSymbol} (dir={direction}, {reason})"
        if not self._armed():
            self.blocked += 1
            log.warning(f"WOULD PLACE: {desc}  [DISARMED — no order sent]")
            return None
        log.info(f"PLACING: {desc}")
        return market_flatten(self.ib, self.contract, direction, qty)


# ======================================================================= state
def _load_json(path, default):
    if os.path.exists(path):
        try: return json.load(open(path))
        except Exception: return default
    return default


def _save_json(path, obj):
    tmp = path + ".tmp"; json.dump(obj, open(tmp, "w"), indent=2); os.replace(tmp, path)


# ======================================================================= data
def load_csv_warmup(days=400) -> pd.DataFrame:
    cutoff = now_utc() - timedelta(days=days)
    dfs = []
    for f in sorted(glob.glob(CFG.NQ_CSV_DIR + "/*.csv")):
        try: d = pd.read_csv(f)
        except Exception: continue
        d.columns = [c.strip().lower() for c in d.columns]
        tc = next((c for c in ('time', 'timestamp', 'date', 'datetime') if c in d.columns), None)
        if tc is None: continue
        if pd.api.types.is_numeric_dtype(d[tc]):
            d[tc] = pd.to_datetime(d[tc], utc=True, unit='s', errors='coerce')
        else:
            d[tc] = pd.to_datetime(d[tc], utc=True, errors='coerce')
        d = d.dropna(subset=[tc]).set_index(tc)
        if 'volume' not in d.columns: d['volume'] = 0.0
        d = d[['open', 'high', 'low', 'close', 'volume']].astype(float)
        d = d[d.index >= cutoff]
        if len(d): dfs.append(d)
    if not dfs:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
    df = pd.concat(dfs).sort_index(kind="mergesort")  # 2026-09-14: STABLE sort -- the default is not stable, so among duplicate timestamps keep="last" did NOT mean the newest file; an older seam could shadow a corrected re-pull
    return df[~df.index.duplicated(keep='last')].sort_index()


def fetch_live_1m(ib, contract, duration="3 D") -> pd.DataFrame:
    bars = ib.reqHistoricalData(contract, endDateTime="", durationStr=duration,
                                barSizeSetting="1 min", whatToShow="TRADES",
                                useRTH=False, formatDate=2)
    rows = []
    for b in (bars or []):
        t = b.date
        ts = pd.to_datetime(int(t), unit='s', utc=True) if isinstance(t, (int, float)) \
            else pd.to_datetime(str(t), utc=True)
        rows.append((ts, float(b.open), float(b.high), float(b.low), float(b.close),
                     float(getattr(b, 'volume', 0) or 0)))
    if not rows:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
    df = pd.DataFrame(rows, columns=['time', 'open', 'high', 'low', 'close', 'volume']).set_index('time')
    df = df[~df.index.duplicated(keep='last')].sort_index()
    minute_floor = now_utc().replace(second=0, microsecond=0)
    return df[df.index < pd.Timestamp(minute_floor)]


# ======================================================================= bot
class R3LiveExecutor:
    def __init__(self):
        CFG.safety_check()
        self.ib = IB()
        self.contract = None
        self.router = None
        self.warm = pd.DataFrame()
        self.regime = {}
        st = _load_json(CFG.STATE_FILE, {})
        self.baseline_done = st.get("baseline_done", False)
        self.handled = set(st.get("handled", []))     # r3_stop_time we've already acted on (forward only)
        self.today = st.get("today")
        self.day_realized_usd = st.get("day_realized_usd", 0.0)
        self.halted = st.get("halted", False)
        # position state (single position at a time)
        self.pos = st.get("pos")                       # None or dict(entry,stop,target,tp_kind,qty,direction,oca,r3_stop_time,signal_time,session)
        self.entry_trade = None                        # working resting buy-stop handle (armed only)
        self._armed_latest = None                      # arming levels behind the resting buy-stop
        self.stop_handle = None
        self.target_handle = None
        self.armed_r3 = st.get("armed_r3")             # r3_stop_time currently resting a buy-stop for
        self.data_contract = None                      # NQ -- the SIGNAL contract (Codex #9)
        self._win = None; self._full = None            # window cached by the last cycle (Codex #1)
        self.flattening = None                         # event-driven flatten in progress (Codex #4)
        self.quarantined = False                       # unverifiable position -> hands off
        self._flat_alert_t = 0.0

    def _persist(self):
        _save_json(CFG.STATE_FILE, dict(
            baseline_done=self.baseline_done, handled=sorted(self.handled), today=self.today,
            day_realized_usd=self.day_realized_usd, halted=self.halted,
            pos=self.pos, armed_r3=self.armed_r3))

    # ---------------------------------------------------------------- connect
    def connect(self):
        log.info(f"Connecting IB {CFG.IB_HOST}:{CFG.IB_PORT} clientId={CFG.CLIENT_ID} "
                 f"(armed={CFG.is_armed()})")
        attempt = 0
        while True:
            attempt += 1
            try:
                self.ib.connect(CFG.IB_HOST, CFG.IB_PORT, clientId=CFG.CLIENT_ID, timeout=15)
                break
            except Exception as e:
                # err 326 = clientId already in use; every other connect error also retried.
                lvl = log.info if attempt <= 60 else log.warning
                lvl(f"IB connect failed (attempt {attempt}): {e!r} — retrying in 60s")
                if attempt == 61:
                    tg(f"connect still failing after ~60 min: {e!r} — check Gateway/clientId {CFG.CLIENT_ID}", level="ERROR")
                try: self.ib.disconnect()
                except Exception: pass
                time.sleep(60)
        accts = self.ib.managedAccounts()
        for a in accts:
            if not a.startswith(CFG.ACCOUNT_PREFIX):
                log.error(f"REFUSING: account {a!r} lacks prefix {CFG.ACCOUNT_PREFIX!r}"); self.ib.disconnect(); raise SystemExit(1)
        if CFG.LIVE_ACCOUNT not in accts:
            log.error(f"REFUSING: expected account {CFG.LIVE_ACCOUNT} not in {accts}"); self.ib.disconnect(); raise SystemExit(1)
        log.info(f"Connected. Accounts: {accts}")
        # 2026-09-14 FIX (Codex #10): PIN the contracts for the life of the process. A reconnect
        # used to re-resolve them, so across a roll the router switched to the new month while a
        # position and its bracket still sat on the old one (a flatten would then SELL the new month).
        # The roll now happens only at the nightly restart, when R3 is flat by design.
        if self.contract is None:
            today = now_et().strftime("%Y%m%d")
            det_data = self.ib.reqContractDetails(Future(symbol=CFG.DATA_INST["symbol"], exchange="CME", currency="USD"))
            det_exec = self.ib.reqContractDetails(Future(symbol=CFG.EXEC_INST["symbol"], exchange="CME", currency="USD"))
            if not det_data or not det_exec:
                raise SystemExit("Could not resolve NQ/MNQ contract details.")
            data_c = pick_front_contract(det_data, CFG.ROLL_DAYS_BEFORE_EXPIRY, today).contract
            exec_c = pick_front_contract(det_exec, CFG.ROLL_DAYS_BEFORE_EXPIRY, today).contract
            if data_c.lastTradeDateOrContractMonth != exec_c.lastTradeDateOrContractMonth:
                log.error(f"ROLL DESYNC: NQ {data_c.lastTradeDateOrContractMonth} != MNQ {exec_c.lastTradeDateOrContractMonth}. Aborting.")
                self.ib.disconnect(); raise SystemExit(1)
            self.data_contract = data_c
            self.contract = exec_c
            self.router = OrderRouter(self.ib, self.contract)
            log.info(f"EXEC contract {exec_c.localSymbol} expiry={exec_c.lastTradeDateOrContractMonth} "
                     f"(signals off NQ {data_c.localSymbol})")
        else:
            log.info(f"reconnect: keeping PINNED contracts exec={self.contract.localSymbol} "
                     f"data={getattr(self.data_contract, 'localSymbol', '?')}")
        try: self.ib.errorEvent -= self._on_error
        except Exception: pass
        self.ib.errorEvent += self._on_error

    def _on_error(self, reqId, errorCode, errorString, contract=None):
        if errorCode in (1100,):
            log.warning("IB 1100: connectivity LOST — awaiting restore")
        elif errorCode in (1101, 1102):
            log.warning(f"IB {errorCode}: connectivity RESTORED")
        elif errorCode == 326:
            log.error("IB 326: clientId 65 already in use — another process holds it. Will retry connect.")

    # ---------------------------------------------------------------- reconcile
    def _our_open_trades(self):
        # reqOpenOrders() (NOT reqAllOpenOrders) -- this client's orders ONLY (2026-08-27 fix).
        self.ib.reqOpenOrders(); self.ib.sleep(1)
        return [t for t in self.ib.openTrades()
                if getattr(t.order, 'clientId', CFG.CLIENT_ID) == CFG.CLIENT_ID]

    def _bind_bracket(self, st, tgt):
        self.stop_handle, self.target_handle = st, tgt
        if st is not None: st.filledEvent += lambda tr: self._on_exit_fill("STOP", tr)
        if tgt is not None: tgt.filledEvent += lambda tr: self._on_exit_fill("TARGET", tr)

    def _move_target(self, new_target):
        """Follow the moving VWAP/band magnet with the SAME target order (see modify_target)."""
        th = self.target_handle
        if th is not None and (th.fills or getattr(th.orderStatus, "status", "") == "Filled"):
            return                                        # target filled: _on_exit_fill owns the exit
        if th is not None and not th.isDone():
            moved = self.router.modify_target(th, new_target)
            if float(moved.order.lmtPrice) == new_target:
                self.pos["target"] = new_target; self._persist()
            return
        # the target is gone (cancelled/rejected at IB) but we still hold the position
        st = self.stop_handle
        if st is None or st.isDone():
            return                                        # no live stop either -> reconcile's job, never place blind
        log.warning(f"target order missing at IB while the stop is live -- placing a fresh target {new_target:.2f}")
        tg(f"R3 target order was missing at IB -- placed a fresh one @ {new_target:.2f} (stop still live).",
           level="WARNING")
        th = self.router.place_target(self.pos["direction"], self.pos["qty"], new_target, self.pos["oca"])
        if th is not None:
            self.target_handle = th
            th.filledEvent += lambda tr: self._on_exit_fill("TARGET", tr)
            self.pos["target"] = new_target; self._persist()

    def _reprotect(self, survivor):
        """Replace a damaged bracket with a fresh OCA at the recorded levels (main loop only)."""
        if survivor is not None:
            self.router.cancel(survivor); self.ib.sleep(1)
        oca = f"r3re_{int(time.time())}"
        st, tgt = self.router.oca_bracket(self.pos["direction"], self.pos["qty"],
                                          self.pos["stop"], self.pos["target"], oca)
        self.pos["oca"] = oca
        self._bind_bracket(st, tgt); self._persist()
        log.warning(f"reconcile: RE-PROTECTED with fresh OCA {oca} stop {self.pos['stop']:.2f} "
                    f"target {self.pos['target']:.2f}")

    def _exit_fills_since(self, since_iso):
        """OUR (clientId 65) SELL executions on the position's contract since entry -- the only
        reliable proof that a missing bracket really closed the position (Codex #7)."""
        try:
            fills = self.ib.reqExecutions(ExecutionFilter(clientId=CFG.CLIENT_ID))
        except Exception as e:
            log.warning(f"reconcile: reqExecutions failed ({e!r})"); return None
        since = pd.Timestamp(since_iso) if since_iso else None
        if since is not None and since.tzinfo is None:
            since = since.tz_localize("UTC")
        want = self.pos.get("contract", self.contract.localSymbol)
        out = []
        for f in fills or []:
            ex = f.execution
            if f.contract.localSymbol != want or ex.side not in ("SLD", "SELL"):
                continue
            t = pd.Timestamp(ex.time)
            t = t.tz_localize("UTC") if t.tzinfo is None else t
            if since is None or t >= since:
                out.append(f)
        return out

    def _flatten_stranded(self, open_trades):
        """Crash-restart across a roll: our position is on the OLD month. Close exactly that leg,
        on that contract -- never touch account positions we cannot attribute to R3."""
        old = self.pos["contract"]
        log.error(f"STRANDED: R3 position on {old}, front is now {self.contract.localSymbol} -- flattening the old leg")
        tg(f"STRANDED roll leg: R3 long {self.pos['qty']} {old} -- flattening it on {old}.", level="WARNING")
        try:
            oc = Contract(conId=int(self.pos["conId"]), exchange="CME"); self.ib.qualifyContracts(oc)
        except Exception as e:
            log.error(f"cannot qualify stranded contract {old}: {e!r}")
            tg(f"CANNOT resolve stranded {old} -- CLOSE IT MANUALLY.", level="ERROR"); return
        for t in open_trades:
            if t.contract.localSymbol == old and not t.isDone():
                self.router.cancel(t)
        self.ib.sleep(2)                                   # main loop: let the cancels land first
        tr = OrderRouter(self.ib, oc).flatten(self.pos["direction"], self.pos["qty"], "STRANDED_ROLL")
        if tr is not None:
            self.flattening = dict(reason="STRANDED_ROLL", waiting=set(), t0=time.time(), sent=True)
            tr.filledEvent += lambda t: self._on_flatten_fill(t, "STRANDED_ROLL")

    def reconcile(self):
        """On startup/reconnect: rebind to our working bracket, repair a damaged one, and cancel
        any working order of ours that no tracked position/arming owns. Telegram-warn on surprises."""
        try:
            open_trades = self._our_open_trades()
            positions = [p for p in self.ib.positions() if p.contract.localSymbol == self.contract.localSymbol]
        except Exception as e:
            log.warning(f"reconcile: could not read orders/positions ({e}); leaving state as-is."); return
        net = sum(int(p.position) for p in positions)
        if self.pos and self.pos.get("contract") and self.pos["contract"] != self.contract.localSymbol:
            self._flatten_stranded(open_trades); return
        if self.pos:
            oca = self.pos.get("oca")
            st = next((t for t in open_trades if getattr(t.order, "ocaGroup", "") == oca
                       and t.order.orderType in ("STP", "STP LMT") and not t.isDone()), None)
            tgt = next((t for t in open_trades if getattr(t.order, "ocaGroup", "") == oca
                        and t.order.orderType == "LMT" and not t.isDone()), None)
            if st is not None and tgt is not None:
                self._bind_bracket(st, tgt)
                log.info("reconcile: re-bound bracket (stop=Y, target=Y)")
            elif st is None and tgt is None:
                # 2026-09-14 FIX (Codex #7): a missing bracket does NOT prove the trade closed.
                ex = self._exit_fills_since(self.pos.get("entry_time"))
                if ex:
                    px = float(ex[-1].execution.avgPrice or ex[-1].execution.price)
                    log.warning(f"reconcile: bracket gone, exit fill found @ {px:.2f} -- booking it")
                    tg(f"RECONCILE: R3 exit filled while down @ {px:.2f} -- booked.", level="WARNING")
                    self._book_exit(px, "RECONCILE")
                elif net == 0:
                    log.warning("reconcile: bracket gone, no exit fill, account FLAT -- closed outside "
                                "the bracket. Clearing. VERIFY P&L in IB.")
                    tg("RECONCILE: R3 position closed outside its bracket -- cleared. Verify P&L in IB.", level="WARNING")
                    self.pos = None; self._persist()
                else:
                    # Unverifiable (account shared with V9): never place orders blind -- a stop on a
                    # position that is already closed OPENS a new one. Quarantine + page a human.
                    self.quarantined = True
                    log.error(f"reconcile: QUARANTINED -- bracket gone, no exit fill, account net {net:+d} "
                              f"(shared with V9). NO orders will be sent. CHECK IB.")
                    tg(f"R3 QUARANTINED: bracket gone, no exit fill, account net {net:+d}. "
                       f"No orders sent -- close or protect it MANUALLY.", level="ERROR")
            else:
                # 2026-09-14 FIX (Codex #7): never run with a target but no stop (or vice versa).
                log.warning(f"reconcile: bracket DAMAGED (stop={'Y' if st else 'MISSING'}, "
                            f"target={'Y' if tgt else 'MISSING'}) -- replacing it")
                tg("RECONCILE: R3 bracket damaged -- replaced with a fresh stop+target.", level="WARNING")
                self._reprotect(st if st is not None else tgt)
        elif net != 0:
            # 2026-08-27 FIX: DO NOT FLATTEN. ib.positions() is ACCOUNT-level and cannot be
            # attributed to a bot -- V9 trades the same MNQ contract. Warn only; a human decides.
            log.warning(f"reconcile: {self.contract.localSymbol} account net={net} but R3 holds "
                        f"no state. NOT flattening (this is very likely V9's position).")
            tg(f"RECONCILE (info): account net {net} on {self.contract.localSymbol} with no R3 "
               f"state -- left alone (likely V9). No action taken.")
        # 2026-09-14 FIX (Codex #2): with no position, cancel EVERY working order of ours on the
        # contract -- including a resting entry left by the previous process -- and drop the stale
        # arming. The old guard skipped this whenever armed_r3 was set, so the next cycle placed a
        # SECOND buy-stop beside the first; both could fill while only one got a stop.
        if not self.pos:
            for t in open_trades:
                if t.contract.localSymbol == self.contract.localSymbol and not t.isDone():
                    log.warning(f"reconcile: cancelling working order {t.order.orderId} ({t.order.orderType})")
                    self.router.cancel(t)
            if self.armed_r3 is not None:
                log.info(f"reconcile: dropping stale arming {self.armed_r3} -- next cycle re-arms fresh")
            self.armed_r3 = None; self.entry_trade = None; self._persist()

    # ---------------------------------------------------------------- daily roll
    def _roll_day(self):
        d = now_et().date().isoformat()
        if self.today != d:
            self.today = d; self.day_realized_usd = 0.0; self.halted = False
            log.info(f"new ET day {d} — daily P&L + halt reset")
            self._persist()

    # ---------------------------------------------------------------- baseline
    def seed_baseline(self):
        if self.baseline_done:
            log.info(f"baseline already seeded ({len(self.handled)} historical R3 stops handled)."); return
        log.info("seeding historical baseline (full-archive overlay pass) ...")
        full = load_csv_warmup(days=100000)
        reg = SPEC.build_regime(full)
        for f in SPEC.overlay_fires(full, reg):
            self.handled.add(f["r3_stop_time"])       # never act on a pre-deployment re-entry
        self.baseline_done = True; self._persist()
        log.info(f"baseline seeded: {len(self.handled)} historical R3 stop-outs marked handled.")

    # ---------------------------------------------------------------- window
    def build_window(self):
        # 2026-09-14 FIX (Codex #9): fetch the SIGNAL contract (NQ), not the execution contract
        # (MNQ). Same prices, different volume -- and VWAP is volume-weighted, so MNQ bars spliced
        # onto the NQ archive moved the bands the arming logic and the target read.
        live = fetch_live_1m(self.ib, self.data_contract or self.contract, "3 D")
        # 2026-09-14 FIX: live bars only EXTEND the archive, never overwrite it. The 3-day IB fetch
        # is on the CURRENT front month, so across a roll it re-priced archived pre-roll days with
        # the new month (Fri 09-11 close 29691.00 Z6 over the archive's correct 29391.75 U6) and R3
        # re-entry's regime drifted from V9's -- it could then 'see' a parent VWAP_LONG_R3 trade V9
        # never took. Completed days now come from the corrected archive (as in V9 and the
        # backtest); the live contract supplies only bars the archive does not have yet.
        if len(self.warm) and len(live):
            live = live[live.index > self.warm.index.max()]
        if len(self.warm) and len(live): df = pd.concat([self.warm, live])
        elif len(live): df = live
        else: df = self.warm.copy()
        df = df[~df.index.duplicated(keep='last')].sort_index()
        if not len(df): return df, df
        cut = df.index[-1] - pd.Timedelta(days=CFG.WINDOW_DAYS)
        win = df[df.index >= cut]
        self._win, self._full = win, df        # cached for the fill handler (Codex #1)
        return win, df

    # ---------------------------------------------------------------- fills
    def _on_entry_fill(self, trade, armed):
        """Resting BUY-STOP filled -> record the position FIRST, then attach the OCA bracket.

        2026-09-14 FIX (Codex #1, CRITICAL): this runs INSIDE an ib_async event handler, where any
        blocking IB call raises 'This event loop is already running'. The old code called
        build_window() -> reqHistoricalData() here, so the handler died BEFORE placing the stop:
        a real long with no protection that the bot did not know it held. It now uses the window
        cached by the last cycle() (all completed 5-min bars, <= ~1 min old) and makes NO blocking
        call. If that still fails, it falls back to the levels computed at arming time, so the
        position can never be left without a stop."""
        if self.pos is not None:
            return
        fill_px = float(trade.fills[-1].execution.avgPrice) if trade.fills else armed["buy_stop"]
        qty = CFG.EXEC_INST["qty"]; direction = "long"
        oca = f"r3re_{int(time.time())}"
        try:
            if self._win is None or not len(self._win):
                raise RuntimeError("no cached window")
            br = SPEC.bracket_on_fill(self._win, self.regime, fill_px,
                                      armed["r3_signal_time"], armed["session"])
        except Exception as e:
            log.error(f"bracket from cached window failed ({e!r}) -- using the armed levels")
            kind = "VWAP" if fill_px < armed["lag_vwap"] else "UPPER"
            stop_t = SPEC._round_tick(armed["prospective_stop"])
            br = dict(stop=stop_t, tp_kind=kind,
                      target=SPEC._floor_tick(armed["lag_vwap"] if kind == "VWAP" else armed["lag_upper"]),
                      riskpt=round(fill_px - stop_t, 2))
        # the position is recorded BEFORE any order call, so the bot knows it holds it even if
        # bracket placement throws
        self.pos = dict(entry=round(fill_px, 2), stop=br["stop"], target=br["target"],
                        tp_kind=br["tp_kind"], qty=qty, direction=direction, oca=oca,
                        r3_stop_time=armed["r3_stop_time"], signal_time=armed["r3_signal_time"],
                        session=armed["session"], riskpt=br["riskpt"],
                        entry_time=now_utc().isoformat(),
                        contract=self.contract.localSymbol, conId=int(self.contract.conId or 0))
        self.armed_r3 = None; self.entry_trade = None
        self._persist()
        try:
            st, tgt = self.router.oca_bracket(direction, qty, br["stop"], br["target"], oca)
            self._bind_bracket(st, tgt)
        except Exception as e:
            log.exception(f"BRACKET PLACEMENT FAILED on fill: {e!r}")
            tg("R3: bracket placement FAILED on fill -- position may be UNPROTECTED. CHECK IB NOW.", level="ERROR")
        log.info(f"ENTRY FILLED @ {fill_px:.2f} -> OCA stop {br['stop']:.2f} / target {br['target']:.2f} "
                 f"({br['tp_kind']}) risk {br['riskpt']:.1f}pt")
        tg(f"LONG ENTRY filled @ {fill_px:.2f} | stop {br['stop']:.2f} | tp {br['tp_kind']} {br['target']:.2f} | risk {br['riskpt']:.1f}pt")

    @staticmethod
    def _fill_px(trade):
        try:
            px = float(trade.orderStatus.avgFillPrice or 0)
            if not px and trade.fills:
                px = float(trade.fills[-1].execution.avgPrice)
            return px or None
        except Exception:
            return None

    def _on_exit_fill(self, kind, trade=None):
        """2026-09-14 FIX (Codex #12): book the REAL execution price, not the planned level. A gap
        through the stop was booked at the stop, so the daily-loss halt under-counted."""
        if self.pos is None: return
        px = self._fill_px(trade) if trade is not None else None
        if not px:
            px = self.pos["stop"] if kind == "STOP" else self.pos["target"]
        self.flattening = None           # a bracket leg beat the flatten: this IS the exit
        self._book_exit(px, kind)

    def _book_exit(self, exit_px, label):
        if self.pos is None: return
        p = self.pos
        pts = exit_px - p["entry"]
        pnl = pts * CFG.EXEC_INST["point_value"] * p["qty"]
        self.day_realized_usd += pnl
        rec = dict(r3_stop_time=p["r3_stop_time"], entry=p["entry"], stop=p["stop"],
                   target=p["target"], tp_kind=p["tp_kind"], exit=round(exit_px, 2),
                   exit_reason=label, pts=round(pts, 2), net_usd=round(pnl, 2),
                   booked_at=now_utc().isoformat())
        led = _load_json(CFG.LEDGER_FILE, []); led.append(rec); _save_json(CFG.LEDGER_FILE, led)
        # cancel the sibling (defensive; OCA should already have)
        sib = self.target_handle if label == "STOP" else self.stop_handle
        if sib is not None and not sib.isDone():
            self.router.cancel(sib)
        self.handled.add(p["r3_stop_time"])
        self.pos = None; self.stop_handle = self.target_handle = None
        log.info(f"EXIT {label} @ {exit_px:.2f}  pts={pts:+.2f}  ${pnl:+,.0f}  (day ${self.day_realized_usd:+,.0f})")
        tg(f"{label} exit @ {exit_px:.2f} | {pts:+.1f}pt (${pnl:+,.0f})")
        if self.day_realized_usd <= CFG.DAILY_LOSS_LIMIT_USD:
            self.halted = True
            log.warning(f"DAILY LOSS LIMIT (${self.day_realized_usd:+,.0f}) -- halting for the day.")
            tg(f"DAILY-LOSS HALT ${self.day_realized_usd:+,.0f} -- no more entries today.", level="WARNING")
        self._persist()

    # ---------------------------------------------------------------- flatten (event-driven)
    def _flatten_position(self, reason):
        """2026-09-14 FIX (Codex #4/#12): cancel the bracket, send the market exit ONLY once both
        legs confirm cancelled, and book at the market order's REAL fill. The old code cancelled
        and fired the market order in the same breath, then booked at ENTRY (=$0): a stop filling
        in the cancel gap plus the market order could double-exit into a short, and a flatten was
        never counted by the daily-loss halt. This can run inside event handlers, so it makes no
        blocking calls -- it is driven entirely by cancelledEvent / filledEvent."""
        if self.pos is None or self.flattening is not None:
            return
        if not CFG.is_armed():
            px = float(self._full["close"].iloc[-1]) if self._full is not None and len(self._full) else self.pos["entry"]
            log.warning(f"WOULD FLATTEN ({reason}) @ ~{px:.2f}  [DISARMED]")
            self._book_exit(px, reason); return
        fl = dict(reason=reason, waiting=set(), t0=time.time(), sent=False)
        self.flattening = fl
        live_legs = [(k, h) for k, h in (("stop", self.stop_handle), ("target", self.target_handle))
                     if h is not None and not h.isDone()]
        for key, h in live_legs:
            fl["waiting"].add(key)
            h.cancelledEvent += (lambda tr, k=key: self._flatten_leg_gone(k))
        for _, h in live_legs:
            self.router.cancel(h)
        if not fl["waiting"]:
            self._flatten_send_market()

    def _flatten_leg_gone(self, key):
        fl = self.flattening
        if fl is None or self.pos is None: return
        fl["waiting"].discard(key)
        if not fl["waiting"]:
            self._flatten_send_market()

    def _flatten_send_market(self):
        fl = self.flattening
        if fl is None or self.pos is None or fl.get("sent"): return
        fl["sent"] = True
        tr = self.router.flatten(self.pos["direction"], self.pos["qty"], fl["reason"])
        if tr is None:
            log.error("flatten: market order NOT placed"); tg("R3: flatten market order NOT placed -- CHECK IB.", level="ERROR")
            return
        tr.filledEvent += lambda t, r=fl["reason"]: self._on_flatten_fill(t, r)

    def _on_flatten_fill(self, trade, reason):
        px = self._fill_px(trade) or (self.pos["entry"] if self.pos else 0.0)
        self.flattening = None
        self._book_exit(px, reason)

    # ---------------------------------------------------------------- risk gate
    def _risk_ok(self, armed) -> bool:
        riskpt = armed["prospective_riskpt"]
        if riskpt <= 0:
            log.warning(f"skip: non-positive prospective risk {riskpt}"); return False
        if riskpt > CFG.RISK_CAP_PTS:
            log.warning(f"skip: prospective risk {riskpt:.1f}pt > cap {CFG.RISK_CAP_PTS}pt"); return False
        risk_usd = riskpt * CFG.EXEC_INST["point_value"] * CFG.EXEC_INST["qty"]
        if risk_usd > CFG.RISK_PER_TRADE_USD:
            log.warning(f"skip: prospective risk ${risk_usd:,.0f} > cap ${CFG.RISK_PER_TRADE_USD:,.0f}"); return False
        return True

    # ---------------------------------------------------------------- cycle
    def _cancel_entry(self, why):
        """Codex #8: never leave a resting BUY-STOP working once entries are off."""
        if self.entry_trade is not None or self.armed_r3 is not None:
            log.info(f"cancelling resting buy-stop ({why})")
            if self.entry_trade is not None:
                tg(f"BUY-STOP cancelled ({why}) -- no order resting now.")
            self.router.cancel(self.entry_trade)
            self.entry_trade = None; self.armed_r3 = None; self._persist()

    # ---------------------------------------------------------------- resting-order alerts
    @staticmethod
    def _auto_cancel_utc(armed) -> datetime:
        """When the bot itself cancels an unfilled buy-stop: the EARLIEST of
          - the 48-bar window: bar ki+49 (ki = the stop's 5-min bar) completes = ki start + 250 min,
            the same last bar the backtest may fill on (j = ki+2 .. ki+49);
          - the 15:55 ET no-new-entries cutoff;
          - the end of the UTC session the stop happened in (arming requires the same session)."""
        stop = datetime.fromisoformat(armed["r3_stop_time"]).astimezone(timezone.utc)
        ki = stop.replace(minute=stop.minute - stop.minute % 5, second=0, microsecond=0)
        window = ki + timedelta(minutes=5 * (SPEC.MAX_WAIT_BARS + 2))
        stop_et = stop.astimezone(ET)
        cut = ET.localize(datetime(stop_et.year, stop_et.month, stop_et.day,
                                   CFG.FLATTEN_H, CFG.FLATTEN_M)).astimezone(timezone.utc)
        if cut <= stop:
            cut = stop                                    # already past today's cutoff
        sess_end = ki.replace(hour=0, minute=0) + timedelta(days=1)
        return min(window, cut, sess_end)

    @staticmethod
    def _hkt(t: datetime) -> str:
        h = t.astimezone(HKT)
        today = now_utc().astimezone(HKT).date()
        return h.strftime("%H:%M HKT") if h.date() == today else h.strftime("%a %d %b %H:%M HKT")

    def _entry_alert(self, armed, head):
        qty = CFG.EXEC_INST["qty"]
        risk_pt = float(armed["buy_stop"]) - float(armed["prospective_stop"])
        tg(f"{head} 1 {self.contract.localSymbol} BUY-STOP @ {armed['buy_stop']:.2f} (resting at IB, waiting to fill) | "
           f"if filled: stop {armed['prospective_stop']:.2f} = {risk_pt:.1f}pt (~${risk_pt * 2 * qty:,.0f}), "
           f"target = session VWAP / upper band | auto-cancels {self._hkt(self._auto_cancel_utc(armed))} if not filled")

    def cycle(self):
        # KILL switch
        if os.path.exists(CFG.KILL_FILE):
            self._cancel_entry("KILL")                    # Codex #8
            if self.pos is not None: self._flatten_position("KILL")
            if not self.halted:
                self.halted = True; log.warning("KILL switch present -- halted, flattened."); tg("KILL switch -- halted + flattened.", level="WARNING")
            self._persist(); return
        self._roll_day()
        if self.quarantined:
            return                                        # a human must resolve it first
        # an event-driven flatten is still waiting for cancel confirmations -> do nothing else
        if self.flattening is not None:
            age = time.time() - self.flattening["t0"]
            if age > 60 and not self.flattening.get("sent") and time.time() - self._flat_alert_t > 300:
                self._flat_alert_t = time.time()
                log.error(f"flatten stuck {age:.0f}s waiting for cancels {self.flattening['waiting']} -- re-sending cancels")
                tg(f"R3 flatten stuck {age:.0f}s waiting for bracket cancels -- CHECK IB.", level="ERROR")
                for h in (self.stop_handle, self.target_handle):
                    if h is not None and not h.isDone(): self.router.cancel(h)
            return
        win, full = self.build_window()
        if not len(win):
            log.warning("no data this cycle"); return
        try:
            self.regime = SPEC.build_regime(full)
        except Exception as e:
            log.warning(f"regime build skipped: {e!r}")

        # ---- manage an OPEN position: track the moving lag-1 target + 15:55 flatten ----
        et = now_et(); mins = et.hour * 60 + et.minute
        if self.pos is not None:
            if mins >= CFG.FLATTEN_H * 60 + CFG.FLATTEN_M:
                log.info("15:55 ET flatten"); self._flatten_position("FLATTEN"); return
            try:
                # Codex #17: keep the ENTRY kind and follow THAT kind's current level
                br = SPEC.bracket_on_fill(win, self.regime, self.pos["entry"], self.pos["signal_time"],
                                          self.pos["session"], force_kind=self.pos["tp_kind"])
                new_target = br["target"]
                if abs(new_target - self.pos["target"]) >= 0.25 and new_target > self.pos["entry"]:
                    self._move_target(new_target)
            except Exception as e:
                log.warning(f"target-update skipped: {e!r}")
            return

        if self.halted:
            self._cancel_entry("halted")                  # Codex #8
            return

        # ---- no position: evaluate ARMING and rest / update a BUY-STOP ----
        armed = SPEC.arming_state(win, self.regime)
        if armed is None:
            if self.armed_r3 is not None:                 # armed window closed with no fill -> cancel resting stop
                log.info("arming window closed with no fill -- cancelling resting buy-stop")
                if self.entry_trade is not None:
                    tg("BUY-STOP cancelled -- not filled before its window closed. No order resting now.")
                self.router.cancel(self.entry_trade); self.entry_trade = None; self.armed_r3 = None; self._persist()
            return
        if armed["r3_stop_time"] in self.handled and self.armed_r3 != armed["r3_stop_time"]:
            return                                        # historical / already-acted re-entry
        if mins >= CFG.FLATTEN_H * 60 + CFG.FLATTEN_M:
            self._cancel_entry("15:55 cutoff")            # Codex #8: no new entries AND nothing left resting
            return
        if not self._risk_ok(armed):
            self._cancel_entry("risk gate")               # risk grew past the cap while resting
            return
        self._rest_entry(armed)

    def _rest_entry(self, armed):
        """Rest / update the BUY-STOP at the current runmax.

        2026-09-14 FIX: the old code cancelled and re-placed the order EVERY cycle (once a minute)
        even at an unchanged price. In the gap between the cancel and the new order, either both
        could fill (_on_entry_fill ignores the second -> an extra lot with NO stop) or none rested.
        It also never cancelled the old order when a DIFFERENT R3 stop took over the arming, so two
        buy-stops could rest side by side. Now:
          - trigger unchanged              -> send nothing
          - runmax rose                    -> MODIFY the same order in place
          - a different R3 stop now armed  -> cancel the old order, place a new one
          - IB cancelled / expired it      -> place a new one
          - it FILLED but no position yet  -> send nothing (the fill handler owns it)"""
        self._armed_latest = armed
        tr = self.entry_trade
        if tr is not None and self.armed_r3 != armed["r3_stop_time"]:
            log.info(f"new R3 stop {armed['r3_stop_time']} replaces arming {self.armed_r3} -- cancelling old buy-stop")
            self.router.cancel(tr)
            tr = self.entry_trade = None
        if tr is not None:
            status = getattr(tr.orderStatus, "status", "")
            if status == "Filled" or tr.fills:
                log.error("resting buy-stop FILLED but no position recorded -- not placing another. CHECK IB.")
                return
            if not tr.isDone():
                if abs(float(tr.order.auxPrice) - armed["buy_stop"]) < SPEC.TICK / 2:
                    return                                # unchanged -> nothing to send
                old_px = float(tr.order.auxPrice)
                self.entry_trade = self.router.modify_buy_stop(tr, armed["buy_stop"])
                if float(self.entry_trade.order.auxPrice) == armed["buy_stop"]:
                    self._entry_alert(armed, f"BUY-STOP moved {old_px:.2f} -> {armed['buy_stop']:.2f}:")
                self._persist()
                return
            log.warning(f"resting buy-stop #{tr.order.orderId} is {status} at IB -- placing a fresh one")
        self.armed_r3 = armed["r3_stop_time"]
        self.entry_trade = self.router.resting_buy_stop(armed["buy_stop"], CFG.EXEC_INST["qty"])
        if self.entry_trade is not None:
            # use the LATEST arming levels at fill time (the trigger may have been modified since)
            self.entry_trade.filledEvent += lambda t: self._on_entry_fill(t, self._armed_latest)
            self._entry_alert(armed, "R3 re-entry ARMED:")
        else:
            # DISARMED: nothing rests at IB; log what WOULD rest so the dry-run is auditable
            log.info(f"[DISARMED] armed on R3 stop {armed['r3_stop_time']}: buy-stop {armed['buy_stop']:.2f} "
                     f"prospective stop {armed['prospective_stop']:.2f} risk {armed['prospective_riskpt']:.1f}pt")
        self._persist()

    # ---------------------------------------------------------------- run
    def run(self):
        armed = CFG.is_armed()
        log.info(f"R3 LIVE executor starting — ARMED={armed} "
                 f"(LIVE_TRADING_CONFIRMED={CFG.LIVE_TRADING_CONFIRMED} DRY_RUN={CFG.DRY_RUN})")
        if not armed:
            log.warning("DISARMED: every order is hard-blocked (logs 'WOULD PLACE'). No real order will be sent.")
        self.connect()
        self.warm = load_csv_warmup(days=CFG.WARMUP_DAYS)
        log.info(f"warmup {len(self.warm):,} archive 1-min bars")
        self.seed_baseline()
        self.reconcile()
        if not armed:
            tg(f"STARTUP — R3 executor on {self.contract.localSymbol}, clientId {CFG.CLIENT_ID}, "
               f"DISARMED (dry-run, no orders will be sent).", level="WARNING")   # only page on the ABNORMAL case
        try: self.cycle()
        except Exception as e: log.exception(f"first cycle error: {e!r}")
        while True:
            nowu = now_utc()
            nxt = nowu.replace(second=0, microsecond=0) + timedelta(minutes=1, seconds=8)
            self.ib.sleep(max(1.0, (nxt - nowu).total_seconds()))
            if os.path.exists(CFG.PAUSED_FILE):
                log.info("PAUSED file present — idling (no cycle)."); continue
            if not self.ib.isConnected():
                log.warning("IB disconnected — reconnecting.")
                try: self.ib.disconnect()
                except Exception: pass
                try: self.connect(); self.reconcile(); tg("reconnected to IB.")
                except Exception as e: log.error(f"reconnect failed: {e!r}"); self.ib.sleep(15); continue
            try: self.cycle()
            except Exception as e: log.exception(f"cycle error: {e!r}")


if __name__ == "__main__":
    R3LiveExecutor().run()
