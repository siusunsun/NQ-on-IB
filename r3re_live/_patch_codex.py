# -*- coding: utf-8 -*-
"""R3 re-entry executor: fixes for the 2026-09-14 Codex cross-check (verified findings).

  #1  CRITICAL  fill handler made a blocking IB call before placing the stop -> naked position
  #2  HIGH      restart kept armed_r3 but lost the order handle -> a second buy-stop beside the first
  #4  HIGH      flatten cancelled + fired market in one breath, booked before confirming the exit
  #7  HIGH      reconcile ran with a target but no stop; cleared state without confirming an exit
  #8  HIGH      KILL / halt / 15:55 cutoff returned with a resting buy-stop still working
  #9  HIGH      live window fetched MNQ bars (MNQ volume) instead of NQ -> VWAP drifted from backtest
  #10 HIGH      reconnect re-resolved contracts across a roll while a position sat on the old month
  #11 HIGH      VWAP target priced in cents, not the 0.25 tick -> IB rejects it     (spec file)
  #12 HIGH      exits + flatten booked at planned/entry prices -> daily-loss halt under-counted
  #17 MEDIUM    target froze whenever the re-derived tp kind differed from the entry kind (spec)
"""
import shutil, ast

BOT = "/root/r3re_live/r3re_live_bot.py"
SPEC = "/root/r3re_live/r3v_live_spec.py"
STAMP = ".bak_codex_20260914"


def between(s, start, end):
    i = s.index(start)
    j = s.index(end, i + len(start))
    return i, j


def replace_block(s, start, end, new):
    assert s.count(start) == 1, ("start anchor count", start[:60], s.count(start))
    i, j = between(s, start, end)
    return s[:i] + new + s[j:]


# =========================================================================== SPEC
sp = open(SPEC, encoding="utf-8").read()
shutil.copy(SPEC, SPEC + STAMP)
if "import math" not in sp:
    sp = sp.replace("import numpy as np", "import math\nimport numpy as np", 1)

TICK_HELPERS = '''
# ---- 2026-09-14 (Codex #11): every price sent to IB must sit on the 0.25 tick -----------------
TICK = 0.25


def _round_tick(x):
    return round(round(float(x) / TICK) * TICK, 2)


def _floor_tick(x):
    """Long SELL-LIMIT target: floor to the tick so it fills at-or-before the backtest's touch."""
    return round(math.floor(float(x) / TICK + 1e-9) * TICK, 2)


def arming_state('''
assert sp.count("\ndef arming_state(") == 1
sp = sp.replace("\ndef arming_state(", TICK_HELPERS, 1)

OLD_BR = sp[sp.index("def bracket_on_fill("):sp.index("def review_tally(")]
NEW_BR = '''def bracket_on_fill(df: pd.DataFrame, regime_by_date, fill_px: float,
                    signal_time_iso: str, session: str, force_kind: str | None = None) -> dict:
    """Given an ACTUAL fill price + the armed R3 signal bar, compute the OCA bracket the
    executor should attach: protective stop, tp_kind ('VWAP'/'UPPER'), and the current
    lag-1 target level. Recomputed from completed bars only (no look-ahead).

    force_kind (2026-09-14, Codex #17): keep the KIND chosen at entry and return that kind's
    CURRENT level -- the magnet never switches mid-trade, but it must keep moving. The old caller
    re-derived the kind from the moving VWAP and FROZE the target whenever it differed.
    Prices are snapped to the 0.25 tick (Codex #11): the VWAP magnet is continuous (the paper
    ledger booked an exit at 29168.26) and IB rejects off-tick futures prices."""
    bars5 = bt.build_5min(df)
    bts, vwap5, upper5 = _per_bar_vwap(bars5)
    n = len(bars5); bucket_idx = {bts[i]: i for i in range(n)}
    l5 = np.array([b[3] for b in bars5])
    si = bucket_idx.get(_floor5(pd.Timestamp(signal_time_iso)))
    c = n - 1
    stop_lvl = float(l5[si:c + 1].min()) if si is not None else float("nan")
    lag_vw = float(vwap5[c]); lag_up = float(upper5[c])
    tp_kind = force_kind or ("VWAP" if fill_px < lag_vw else "UPPER")
    target = lag_vw if tp_kind == "VWAP" else lag_up
    stop_t = _round_tick(stop_lvl)
    return dict(stop=stop_t, tp_kind=tp_kind, target=_floor_tick(target),
                riskpt=round(fill_px - stop_t, 2), lag_vwap=round(lag_vw, 2),
                lag_upper=round(lag_up, 2))


'''
sp = sp.replace(OLD_BR, NEW_BR, 1)
# the resting BUY-STOP and the prospective stop are sent to IB too -> snap them as well
sp = sp.replace("buy_stop=round(runmax, 2), prospective_stop=round(prospective_stop, 2),",
                "buy_stop=_round_tick(runmax), prospective_stop=_round_tick(prospective_stop),", 1)
ast.parse(sp)
open(SPEC, "w", encoding="utf-8").write(sp)
print("spec patched")

# =========================================================================== BOT
s = open(BOT, encoding="utf-8").read()
shutil.copy(BOT, BOT + STAMP)

s = s.replace("from ib_async import IB, Future, StopOrder",
              "from ib_async import IB, Future, StopOrder, Contract, ExecutionFilter", 1)

# ---- __init__ ------------------------------------------------------------------------------
A = '        self.armed_r3 = st.get("armed_r3")             # r3_stop_time currently resting a buy-stop for\n'
assert s.count(A) == 1
s = s.replace(A, A + '''        self.data_contract = None                      # NQ -- the SIGNAL contract (Codex #9)
        self._win = None; self._full = None            # window cached by the last cycle (Codex #1)
        self.flattening = None                         # event-driven flatten in progress (Codex #4)
        self._flat_alert_t = 0.0
''', 1)

# ---- connect(): pin contracts for the life of the process (Codex #10) ------------------------
NEW_CONNECT = '''        # 2026-09-14 FIX (Codex #10): PIN the contracts for the life of the process. A reconnect
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
'''
s = replace_block(s, '        today = now_et().strftime("%Y%m%d")\n',
                  "\n    def _on_error(", NEW_CONNECT)

# ---- reconcile ------------------------------------------------------------------------------
NEW_RECONCILE = '''    # ---------------------------------------------------------------- reconcile
    def _our_open_trades(self):
        # reqOpenOrders() (NOT reqAllOpenOrders) -- this client's orders ONLY (2026-08-27 fix).
        self.ib.reqOpenOrders(); self.ib.sleep(1)
        return [t for t in self.ib.openTrades()
                if getattr(t.order, 'clientId', CFG.CLIENT_ID) == CFG.CLIENT_ID]

    def _bind_bracket(self, st, tgt):
        self.stop_handle, self.target_handle = st, tgt
        if st is not None: st.filledEvent += lambda tr: self._on_exit_fill("STOP", tr)
        if tgt is not None: tgt.filledEvent += lambda tr: self._on_exit_fill("TARGET", tr)

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
        tg(f"STRANDED roll leg: R3 long {self.pos['qty']} {old} -- flattening it on {old}.")
        try:
            oc = Contract(conId=int(self.pos["conId"]), exchange="CME"); self.ib.qualifyContracts(oc)
        except Exception as e:
            log.error(f"cannot qualify stranded contract {old}: {e!r}")
            tg(f"CANNOT resolve stranded {old} -- CLOSE IT MANUALLY."); return
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
                    tg(f"RECONCILE: R3 exit filled while down @ {px:.2f} -- booked.")
                    self._book_exit(px, "RECONCILE")
                else:
                    log.error("reconcile: bracket gone and NO exit fill found -- position may be OPEN and UNPROTECTED")
                    tg("RECONCILE: R3 bracket GONE with no exit fill -- re-protecting. CHECK IB.")
                    self._reprotect(None)
            else:
                # 2026-09-14 FIX (Codex #7): never run with a target but no stop (or vice versa).
                log.warning(f"reconcile: bracket DAMAGED (stop={'Y' if st else 'MISSING'}, "
                            f"target={'Y' if tgt else 'MISSING'}) -- replacing it")
                tg("RECONCILE: R3 bracket damaged -- replaced with a fresh stop+target.")
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

'''
s = replace_block(s, "    # ---------------------------------------------------------------- reconcile\n",
                  "    # ---------------------------------------------------------------- daily roll\n",
                  NEW_RECONCILE)

# ---- window ----------------------------------------------------------------------------------
NEW_WINDOW = '''    # ---------------------------------------------------------------- window
    def build_window(self):
        # 2026-09-14 FIX (Codex #9): fetch the SIGNAL contract (NQ), not the execution contract
        # (MNQ). Same prices, different volume -- and VWAP is volume-weighted, so MNQ bars spliced
        # onto the NQ archive moved the bands the arming logic and the target read.
        live = fetch_live_1m(self.ib, self.data_contract or self.contract, "3 D")
        if len(self.warm) and len(live): df = pd.concat([self.warm, live])
        elif len(live): df = live
        else: df = self.warm.copy()
        df = df[~df.index.duplicated(keep='last')].sort_index()
        if not len(df): return df, df
        cut = df.index[-1] - pd.Timedelta(days=CFG.WINDOW_DAYS)
        win = df[df.index >= cut]
        self._win, self._full = win, df        # cached for the fill handler (Codex #1)
        return win, df

'''
s = replace_block(s, "    # ---------------------------------------------------------------- window\n",
                  "    # ---------------------------------------------------------------- fills\n",
                  NEW_WINDOW)

# ---- fills + flatten -----------------------------------------------------------------------
NEW_FILLS = '''    # ---------------------------------------------------------------- fills
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
            tg("R3: bracket placement FAILED on fill -- position may be UNPROTECTED. CHECK IB NOW.")
        log.info(f"ENTRY FILLED @ {fill_px:.2f} -> OCA stop {br['stop']:.2f} / target {br['target']:.2f} "
                 f"({br['tp_kind']}) risk {br['riskpt']:.1f}pt")
        tg(f"R3 RE-ENTRY LONG {fill_px:.2f} | stop {br['stop']:.2f} | tp {br['tp_kind']} {br['target']:.2f} | risk {br['riskpt']:.1f}pt")

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
        tg(f"R3 RE-ENTRY EXIT {label} {exit_px:.2f} | {pts:+.1f}pt (${pnl:+,.0f})")
        if self.day_realized_usd <= CFG.DAILY_LOSS_LIMIT_USD:
            self.halted = True
            log.warning(f"DAILY LOSS LIMIT (${self.day_realized_usd:+,.0f}) -- halting for the day.")
            tg(f"DAILY-LOSS HALT ${self.day_realized_usd:+,.0f} -- no more entries today.")
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
            log.error("flatten: market order NOT placed"); tg("R3: flatten market order NOT placed -- CHECK IB.")
            return
        tr.filledEvent += lambda t, r=fl["reason"]: self._on_flatten_fill(t, r)

    def _on_flatten_fill(self, trade, reason):
        px = self._fill_px(trade) or (self.pos["entry"] if self.pos else 0.0)
        self.flattening = None
        self._book_exit(px, reason)

'''
s = replace_block(s, "    # ---------------------------------------------------------------- fills\n",
                  "    # ---------------------------------------------------------------- risk gate\n",
                  NEW_FILLS)

# ---- cycle -----------------------------------------------------------------------------------
NEW_CYCLE = '''    # ---------------------------------------------------------------- cycle
    def _cancel_entry(self, why):
        """Codex #8: never leave a resting BUY-STOP working once entries are off."""
        if self.entry_trade is not None or self.armed_r3 is not None:
            log.info(f"cancelling resting buy-stop ({why})")
            self.router.cancel(self.entry_trade)
            self.entry_trade = None; self.armed_r3 = None; self._persist()

    def cycle(self):
        # KILL switch
        if os.path.exists(CFG.KILL_FILE):
            self._cancel_entry("KILL")                    # Codex #8
            if self.pos is not None: self._flatten_position("KILL")
            if not self.halted:
                self.halted = True; log.warning("KILL switch present -- halted, flattened."); tg("KILL switch -- halted + flattened.")
            self._persist(); return
        self._roll_day()
        # an event-driven flatten is still waiting for cancel confirmations -> do nothing else
        if self.flattening is not None:
            age = time.time() - self.flattening["t0"]
            if age > 60 and not self.flattening.get("sent") and time.time() - self._flat_alert_t > 300:
                self._flat_alert_t = time.time()
                log.error(f"flatten stuck {age:.0f}s waiting for cancels {self.flattening['waiting']} -- re-sending cancels")
                tg(f"R3 flatten stuck {age:.0f}s waiting for bracket cancels -- CHECK IB.")
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
                    th = self.router.replace_target(
                        self.pos["direction"], self.pos["qty"], self.target_handle, new_target, self.pos["oca"])
                    self.target_handle = th
                    if th is not None:
                        th.filledEvent += lambda tr: self._on_exit_fill("TARGET", tr)
                    self.pos["target"] = new_target; self._persist()
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
        # place or cancel/replace the resting BUY-STOP at the current runmax
        if self.armed_r3 == armed["r3_stop_time"] and self.entry_trade is not None:
            self.router.cancel(self.entry_trade)          # replace as runmax rises
        self.armed_r3 = armed["r3_stop_time"]
        self.entry_trade = self.router.resting_buy_stop(armed["buy_stop"], CFG.EXEC_INST["qty"])
        if self.entry_trade is not None:
            self.entry_trade.filledEvent += lambda tr, a=armed: self._on_entry_fill(tr, a)
        else:
            # DISARMED: nothing rests at IB; log what WOULD rest so the dry-run is auditable
            log.info(f"[DISARMED] armed on R3 stop {armed['r3_stop_time']}: buy-stop {armed['buy_stop']:.2f} "
                     f"prospective stop {armed['prospective_stop']:.2f} risk {armed['prospective_riskpt']:.1f}pt")
        self._persist()

'''
s = replace_block(s, "    # ---------------------------------------------------------------- cycle\n",
                  "    # ---------------------------------------------------------------- run\n",
                  NEW_CYCLE)

ast.parse(s)
open(BOT, "w", encoding="utf-8").write(s)
print("bot patched  (backups *" + STAMP + ")")
