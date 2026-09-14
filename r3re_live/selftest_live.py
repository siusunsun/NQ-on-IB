"""selftest_live.py — DISARMED validation for the R3 LIVE executor. Places NOTHING.

Checks:
 (A) ARM-GATE: default config is DISARMED; grep proves the gate; a RUNTIME INTERCEPT proves
     OrderRouter hard-blocks every order type while disarmed (blocked counter increments,
     the fake IB's placeOrder is never called).
 (B) DRY-RUN of a real recent re-entry: reconstruct the armed moment from the archive and
     print the EXACT resting BUY-STOP it would rest at runmax, plus the OCA stop+target it
     would attach on fill (prices / size / tp-type), and cross-check they reproduce the
     validated backtest fire. Evaluate risk-cap + daily-loss.
 (C) NO /root/v9 WRITE: sources never write under /root/v9 and never call record_v9_fill;
     all writes stay under /root/r3re_live.
 (D) LIVE connect: account U6015126 + port 7497 + clientId 65 connect, resolve MNQ front,
     read account — then disconnect. Places nothing (router stays disarmed throughout).
 (E) RECONCILE: with a fabricated 'phantom position' state and no live bracket, reconcile
     clears it (in a disarmed router, a flatten would be a WOULD-PLACE, never a real order).
"""
import sys, os, re, glob
import numpy as np, pandas as pd, pytz
sys.path.insert(0, "/root/r3re_live"); sys.path.insert(0, "/root/v9")
ET = pytz.timezone("America/New_York")
FAILS = []

import r3re_live_config as CFG
import r3v_live_spec as SPEC
import r3re_live_bot as BOT

# ================================================================= (A) ARM GATE
print("=" * 70, "\n(A) ARM-GATE (disarmed by default; grep + runtime intercept)")
print(f"  config: LIVE_TRADING_CONFIRMED={CFG.LIVE_TRADING_CONFIRMED}  DRY_RUN={CFG.DRY_RUN}  is_armed()={CFG.is_armed()}")
if CFG.is_armed():
    print("  !! SHIPPED ARMED — must be disarmed"); FAILS.append("shipped-armed")
else:
    print("  OK — shipped DISARMED")
# grep: is_armed() must gate every router method
src = open("/root/r3re_live/r3re_live_bot.py").read()
gate_hits = src.count("self._armed()")
print(f"  grep: OrderRouter methods guarded by self._armed(): {gate_hits} (expect >=4)")
if gate_hits < 4: FAILS.append("gate-grep")

class _FakeIB:
    def __init__(self): self.calls = 0
    def placeOrder(self, *a, **k): self.calls += 1; raise AssertionError("placeOrder called while DISARMED!")
    def cancelOrder(self, *a, **k): self.calls += 1; raise AssertionError("cancelOrder called while DISARMED!")
class _FakeC:  localSymbol = "MNQZ5"
fib = _FakeIB()
r = BOT.OrderRouter(fib, _FakeC())
r.resting_buy_stop(24000.0, 1)
r.oca_bracket("long", 1, 23950.0, 24010.0, "oca_test")
r.replace_target("long", 1, None, 24020.0, "oca_test")
r.cancel(object())
r.flatten("long", 1, "TEST")
print(f"  runtime intercept: router.blocked={r.blocked} (expect 5), fake IB order calls={fib.calls} (expect 0)")
if r.blocked != 5 or fib.calls != 0:
    print("  !! arm-gate leaked an order"); FAILS.append("gate-runtime")
else:
    print("  OK — all 5 order attempts hard-blocked, zero IB order calls")

# ================================================================= (C) NO v9 WRITE
print("=" * 70, "\n(C) NO write under /root/v9; no record_v9_fill")
srcs = {f: open(f).read() for f in
        ["/root/r3re_live/r3re_live_bot.py", "/root/r3re_live/r3v_live_spec.py",
         "/root/r3re_live/r3re_live_config.py"]}
bad = []
for f, t in srcs.items():
    for ln in t.splitlines():
        if "record_v9_fill" in ln and "import" not in ln and "not call" not in ln.lower() and "#" not in ln.split("record_v9_fill")[0][-3:]:
            bad.append(f"{os.path.basename(f)}: {ln.strip()[:70]}")
        if "/root/v9" in ln and ("open(" in ln) and ("'w'" in ln or '"w"' in ln or "'a'" in ln or '"a"' in ln):
            bad.append(f"WRITE {os.path.basename(f)}: {ln.strip()[:70]}")
called = any(re.search(r"\brecord_v9_fill\s*\(", t) for t in srcs.values())
print(f"  record_v9_fill CALLED anywhere: {called} (expect False)")
print(f"  write-under-/root/v9 hits: {bad or 'none'}")
for p in (CFG.STATE_FILE, CFG.LEDGER_FILE, CFG.LOG_FILE):
    if not p.startswith("/root/r3re_live"):
        print(f"  !! write target outside sandbox: {p}"); FAILS.append("write-loc")
print(f"  write paths: STATE/LEDGER/LOG all under /root/r3re_live -> "
      f"{all(p.startswith('/root/r3re_live') for p in (CFG.STATE_FILE, CFG.LEDGER_FILE, CFG.LOG_FILE))}")
if called or bad: FAILS.append("v9-write")
else: print("  OK — v9 live-state never written")

# ================================================================= (B) DRY-RUN
print("=" * 70, "\n(B) DRY-RUN of a real recent re-entry (exact orders it WOULD place)")
full = BOT.load_csv_warmup(days=100000)
reg = SPEC.build_regime(full)
fires = SPEC.overlay_fires(full, reg)
booked = [f for f in fires if f["exit_reason"] != "DATA_END"]
fire = booked[-1]                                  # most recent completed fire
print(f"  target fire: reentry {fire['reentry_time'][:16]}  backtest entry={fire['entry']} "
      f"stop={fire['stop']} tp={fire['tp_type']} exit={fire['exit']} R={fire['R']}")
fill_bar_start = pd.Timestamp(fire["reentry_time"]) - pd.Timedelta(minutes=5)   # bts[j]
# Reconstruct the state as of the fill bar's j-1 CLOSE: include the fill bar's first 1-min bar
# so the j-1 5-min bucket rolls over and emits (FiveMinAggregator emits on rollover), but the
# fill bar j itself stays a forming (un-emitted) bucket. Last completed bar => j-1, exactly the
# live instant when the resting BUY-STOP is set + fires. Both entry-arming and on-fill bracket
# are evaluated from completed bars through j-1 (matches the backtest lag-1 math).
pre = full[full.index <= pd.Timestamp(fill_bar_start)]
reg_pre = SPEC.build_regime(pre)
armed = SPEC.arming_state(pre.tail(20000), reg_pre)
if armed is None:
    print("  !! could not reconstruct armed state"); FAILS.append("dryrun-arm")
else:
    print(f"  --- ARMED at last completed bar {armed['last_bar_time'][:16]} (R3 stop {armed['r3_stop_time'][:16]}) ---")
    print(f"  WOULD PLACE (entry): RESTING BUY-STOP  qty={CFG.EXEC_INST['qty']} MNQ  "
          f"trigger={armed['buy_stop']:.2f}  (runmax; GTC/outsideRTH)")
    print(f"      prospective protective stop={armed['prospective_stop']:.2f}  "
          f"prospective risk={armed['prospective_riskpt']:.1f}pt")
    # risk-cap eval
    ex016 = BOT.R3LiveExecutor.__new__(BOT.R3LiveExecutor)
    ok = ex016._risk_ok(armed)
    print(f"      RISK-CAP eval: risk {armed['prospective_riskpt']:.1f}pt vs cap {CFG.RISK_CAP_PTS}pt / "
          f"${armed['prospective_riskpt']*2:.0f} vs ${CFG.RISK_PER_TRADE_USD:.0f} -> {'PASS (would rest)' if ok else 'BLOCKED'}")
    print(f"      DAILY-LOSS eval: day P&L $0 vs halt {CFG.DAILY_LOSS_LIMIT_USD:.0f} -> "
          f"{'not halted' if 0 > CFG.DAILY_LOSS_LIMIT_USD else 'HALTED'}")
    # on-fill bracket: evaluated at the SAME j-1 close (last completed bar at the instant the
    # resting stop fills during bar j). Fill price = the actual execution (here: backtest entry).
    br = SPEC.bracket_on_fill(pre.tail(20000), reg_pre, fire["entry"], armed["r3_signal_time"], armed["session"])
    tp_map = "VWAP" if br["tp_kind"] == "VWAP" else "band"
    print(f"  --- ON FILL @ {fire['entry']:.2f} -> WOULD PLACE OCA bracket ---")
    print(f"      protective STOP (LMT? no, STP) = {br['stop']:.2f}   qty={CFG.EXEC_INST['qty']} MNQ")
    print(f"      LIMIT target = {br['target']:.2f}   tp-type={tp_map}   (lag-1 band magnet)")
    print(f"      reconstructed risk={br['riskpt']:.1f}pt")
    # cross-check vs backtest fire
    dstop = abs(br["stop"] - fire["stop"]); dtp = (tp_map == fire["tp_type"])
    print(f"  cross-check vs backtest fire: stop delta={dstop:.2f}  tp-type match={dtp}")
    if dstop > 0.5 or not dtp:
        print("  !! live bracket does not reproduce backtest fire"); FAILS.append("dryrun-parity")
    else:
        print("  OK — live orders reproduce the validated backtest fire")

# ================================================================= (E) RECONCILE (disarmed)
print("=" * 70, "\n(E) RECONCILE clears a phantom position (disarmed -> WOULD-PLACE only)")
ex = BOT.R3LiveExecutor.__new__(BOT.R3LiveExecutor)
ex.pos = dict(oca="r3re_phantom", direction="long", qty=1, entry=1.0, stop=0.5, target=2.0,
              tp_kind="VWAP", r3_stop_time="phantom", signal_time="x", session="x", riskpt=0.5)
ex.armed_r3 = None; ex.stop_handle = ex.target_handle = None
class _RIB:
    def reqAllOpenOrders(self): pass
    def sleep(self, s): pass
    def openTrades(self): return []
    def positions(self): return []
class _RC: localSymbol = "MNQZ5"
ex.ib = _RIB(); ex.contract = _RC(); ex.router = BOT.OrderRouter(ex.ib, ex.contract)
ex.reconcile()
print(f"  after reconcile: pos={'CLEARED' if ex.pos is None else 'STILL SET'} (expect CLEARED)")
if ex.pos is not None: FAILS.append("reconcile")
else: print("  OK — phantom position cleared, no real order placed")

# ================================================================= (D) LIVE CONNECT
print("=" * 70, "\n(D) LIVE connect (account/port/clientId 65) — reads only, places nothing")
try:
    from ib_async import IB, Future
    from contract_roll import pick_front_contract
    ib = IB(); ib.connect(CFG.IB_HOST, CFG.IB_PORT, clientId=CFG.CLIENT_ID, timeout=20)
    accts = ib.managedAccounts()
    det = ib.reqContractDetails(Future(symbol="MNQ", exchange="CME", currency="USD"))
    c = pick_front_contract(det, CFG.ROLL_DAYS_BEFORE_EXPIRY, BOT.now_et().strftime("%Y%m%d")).contract
    summ = ib.accountSummary(CFG.LIVE_ACCOUNT)
    nlv = next((v.value for v in summ if v.tag == "NetLiquidation"), "?")
    print(f"  connected clientId={CFG.CLIENT_ID}  accounts={accts}  MNQ front={c.localSymbol} "
          f"exp={c.lastTradeDateOrContractMonth}  NLV={nlv}")
    if CFG.LIVE_ACCOUNT not in accts:
        print(f"  !! {CFG.LIVE_ACCOUNT} not in managed accounts"); FAILS.append("connect-acct")
    else:
        print("  OK — live account resolved, MNQ front resolved, ZERO orders placed")
    ib.disconnect()
except Exception as e:
    print(f"  (D) connect FAIL: {e!r}"); FAILS.append("connect")

print("=" * 70)
print("SELFTEST-LIVE:", "ALL PASS" if not FAILS else f"FAIL {FAILS}")
sys.exit(0 if not FAILS else 1)
