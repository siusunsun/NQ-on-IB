# -*- coding: utf-8 -*-
"""Offline tests for the STAGED 2026-09-14 V9 / contract_roll / puller / R3 fixes.
No IB, no real orders, no Telegram, no writes to any live file."""
import sys, os, types, asyncio, tempfile, importlib.util, datetime as dt
sys.path[:0] = ["/root/_stage/v9", "/root/_stage/r3re_live", "/root/v9", "/root/r3re_live"]
TMP = tempfile.mkdtemp(prefix="v9test_")

stub = types.ModuleType("v9_tg"); stub.install_handler = lambda *a, **k: None
sys.modules["v9_tg"] = stub                                       # never page Telegram
import v9_config_live_user as C                                   # EXACTLY what v9_run_user.py does:
sys.modules["v9_config"] = C                                       # the live config IS v9_config
assert C.__file__.startswith("/root/_stage/"), C.__file__
C.LOG_FILE = os.path.join(TMP, "v9.log"); C.STATE_FILE = os.path.join(TMP, "state.json")
import contract_roll as CR
import v9_live_trade as V
assert V.__file__.startswith("/root/_stage/"), V.__file__
assert CR.__file__.startswith("/root/_stage/"), CR.__file__
V.record_v9_fill = lambda *a, **k: FILLS.append((a, k))         # never touch the live trade log
FILLS = []
from eventkit import Event
import pandas as pd

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}  {detail}")


class Tr:
    n = 1000
    def __init__(self, kind, cid=25, oid=None, ls="MNQZ6", oca=""):
        Tr.n += 1
        self.order = types.SimpleNamespace(orderId=oid or Tr.n, orderType=kind, clientId=cid,
                                           ocaGroup=oca, totalQuantity=2)
        self.contract = types.SimpleNamespace(localSymbol=ls, symbol="MNQ")
        self.fills = []; self._done = False
        self.orderStatus = types.SimpleNamespace(avgFillPrice=0.0, status="Submitted", filled=0)
        self.filledEvent = Event("f"); self.cancelledEvent = Event("c"); self.statusEvent = Event("s")
    def isDone(self): return self._done
    def fill(self, px):
        self._done = True; self.orderStatus.avgFillPrice = px
        self.fills = [types.SimpleNamespace(execution=types.SimpleNamespace(avgPrice=px, price=px))]
        self.filledEvent.emit(self)
    def cancel_ack(self):
        self._done = True; self.orderStatus.status = "Cancelled"; self.cancelledEvent.emit(self)


CALLS = []
def fake_stop(ib, c, d, q, px, oca): t = Tr("STP", oca=oca); CALLS.append(("stop", px)); return t
def fake_tgt(ib, c, d, q, px, oca):  t = Tr("LMT", oca=oca); CALLS.append(("target", px)); return t
def fake_mkt(ib, c, d, q):           t = Tr("MKT"); CALLS.append(("market", d, q)); MKT.append(t); return t
def fake_cancel(ib, t):
    if t is not None: CALLS.append(("cancel", t.order.orderId))
MKT = []
V.place_protective_stop, V.place_limit_target = fake_stop, fake_tgt
V.market_flatten, V.cancel_order = fake_mkt, fake_cancel


class FakeState:
    def __init__(self, sleeves): self.sleeves = sleeves; self.cum_realized_usd = 0.0; self.halted = False
    def save(self, p): pass


def sleeve(side="long", qty=2, stop_id=None, tgt_id=None):
    return types.SimpleNamespace(in_position=True, side=side, entry_price=29200.0, stop_price=29170.0,
                                 target_price=29290.0, entry_time="2026-09-14T10:00:00-04:00",
                                 entry_order_id=1, stop_order_id=stop_id, target_order_id=tgt_id,
                                 qty=qty, trade_count_today=1, realized_R=0.0)


def make_bot(sleeves, ib=None):
    b = V.V9Bot.__new__(V.V9Bot)
    b.ib = ib or types.SimpleNamespace(sleep=lambda *a: None)
    b.state = FakeState(sleeves); b.live = {}; b._flattening = {}; b._quarantined = set()
    b._flat_alert_t = 0.0; b.contract = types.SimpleNamespace(localSymbol="MNQZ6")
    return b


def in_loop(fn):
    async def main(): fn()
    asyncio.run(main())


print("\npandas: regime-close substitution REPLACES (was: appended a row + TypeError)")
from v9_strategy_lib import compute_regime
idx = [dt.date(2025, 11, 1) + dt.timedelta(days=i) for i in range(260)]
closes = pd.Series([27000.0 + i for i in range(259)] + [29355.50], index=idx)
fixed = V.V9Bot._with_close(closes, idx[-1].isoformat(), 29149.75)
check("row count unchanged (260)", len(fixed) == 260, len(fixed))
check("value replaced on the date key", fixed.loc[idx[-1]] == 29149.75, fixed.loc[idx[-1]])
try:
    r = compute_regime(fixed, sma_window=200, mom_window=20); ok = abs(r.close - 29149.75) < 1e-9
except Exception as e:
    ok = False; r = e
check("compute_regime runs and uses the substituted close", ok, r)

print("\n#13 quarterly_expiry is holiday-adjusted")
for (y, m), want in {(2026, 6): "2026-06-18", (2026, 9): "2026-09-18", (2026, 12): "2026-12-18",
                     (2027, 3): "2027-03-19"}.items():
    got = CR.quarterly_expiry(y, m).isoformat()
    check(f"{y}-{m:02d} -> {want}", got == want, got)
det = [types.SimpleNamespace(contract=types.SimpleNamespace(localSymbol=ls, lastTradeDateOrContractMonth=e))
       for ls, e in (("NQU6", "20260918"), ("NQZ6", "20261218"), ("NQM7", "20270617"), ("NQU7", "20270916"))]
warns = []; lg = types.SimpleNamespace(warning=lambda m: warns.append(m))
bad = CR.expiry_crosscheck(det, lg)
check("NQM7 Thu 2027-06-17 now AGREES (Saturday-Juneteenth observed on Friday)",
      not any(b[0] == "NQM7" for b in bad), bad)
check("cross-check still flags a genuinely wrong date (simulated NQU7 on 09-16)",
      len(bad) == 1 and bad[0][0] == "NQU7" and warns, bad)

print("\n#14 puller roll boundary = 18:00 ET on the roll date (DST-correct)")
spec = importlib.util.spec_from_file_location("puller", "/root/_stage/v9/tools/v9_pull_latest_data.py")
PL = importlib.util.module_from_spec(spec); spec.loader.exec_module(PL)
b1 = PL._roll_boundary_utc(dt.date(2026, 9, 14))
check("Sep roll -> 2026-09-11 22:00 UTC (18:00 EDT)", b1.isoformat() == "2026-09-11T22:00:00+00:00", b1)
b2 = PL._roll_boundary_utc(dt.date(2026, 12, 14))
check("Dec roll -> 2026-12-11 23:00 UTC (18:00 EST)", b2.isoformat() == "2026-12-11T23:00:00+00:00", b2)
check("the 2026-09-10 23:59 ET close bar (03:59 UTC 09-11) is now BEFORE the boundary (old month)",
      pd.Timestamp("2026-09-11T03:59:00Z") < pd.Timestamp(b1))

print("\n#3  roll sweep touches ONLY our orders and ONLY our quantity")
class SweepIB:
    def __init__(self):
        self.cancelled = []; self.placed = []
        self.trades = [Tr("STP", cid=25, ls="MNQU6"), Tr("STP", cid=65, ls="MNQU6"), Tr("LMT", cid=65, ls="MNQU6")]
        self.pos = [types.SimpleNamespace(contract=types.SimpleNamespace(localSymbol="MNQU6", symbol="MNQ"), position=2)]
    def reqPositions(self): pass
    def reqAllOpenOrders(self): pass
    def sleep(self, *a): pass
    def openTrades(self): return self.trades
    def positions(self): return self.pos
    def cancelOrder(self, o): self.cancelled.append(o.clientId)
    def qualifyContracts(self, c): pass
    def placeOrder(self, c, o): self.placed.append((o.action, o.totalQuantity)); return Tr("MKT")
sib = SweepIB(); logs = []
lg = types.SimpleNamespace(warning=lambda m: logs.append(m), error=lambda m: logs.append(m))
closed = CR.flatten_foreign_positions(sib, "MNQ", "MNQZ6", lg, own_client_ids={25}, max_close=+1)
check("cancelled only clientId-25 orders (not R3's 65)", sib.cancelled == [25], sib.cancelled)
check("closed only OUR 1 of the account's 2 old-month contracts", sib.placed == [("SELL", 1)], sib.placed)
sib2 = SweepIB()
CR.flatten_foreign_positions(sib2, "MNQ", "MNQZ6", lg, own_client_ids={25}, max_close=0)
check("holding nothing -> old-month position left alone (warned)", sib2.placed == [], sib2.placed)

print("\n#4  V9 flatten: market only after BOTH cancels confirm; booked at the real fill")
CALLS.clear(); MKT.clear()
st, tg = Tr("STP"), Tr("LMT")
b = make_bot({"VWAP_LONG_R3": sleeve()}); b.live["VWAP_LONG_R3"] = {"stop": st, "target": tg}
in_loop(lambda: b._flatten_sleeve("VWAP_LONG_R3", "GAP", 29230.0))
check("no market order yet", not MKT)
in_loop(lambda: st.cancel_ack())
check("still none with one leg alive", not MKT)
in_loop(lambda: tg.cancel_ack())
check("market sent once both legs cancelled", len(MKT) == 1)
in_loop(lambda: MKT[0].fill(29228.25))
check("booked at the market FILL 29228.25", FILLS and FILLS[-1][0][3] == 29228.25, FILLS[-1:] )
check("sleeve reset", not b.state.sleeves["VWAP_LONG_R3"].in_position)

print("\n#4  V9 race: stop fills during the flatten -> ONE exit, no market order")
CALLS.clear(); MKT.clear(); FILLS.clear()
st, tg = Tr("STP"), Tr("LMT")
b = make_bot({"VWAP_LONG_R3": sleeve()}); b.live["VWAP_LONG_R3"] = {"stop": st, "target": tg}
st.filledEvent += lambda tr: b._on_exit_fill("VWAP_LONG_R3", tr, "STOP")
in_loop(lambda: b._flatten_sleeve("VWAP_LONG_R3", "GAP", 29230.0))
in_loop(lambda: st.fill(29169.75))
in_loop(lambda: tg.cancel_ack())
check("no market order sent", not MKT, CALLS)
check("exactly one exit booked, at the stop fill", len(FILLS) == 1 and FILLS[0][0][3] == 29169.75, FILLS)

print("\n#5/#7 V9 reconcile")
class RecIB:
    def __init__(self, trades, net=0, fills=()):
        self.t = trades; self.n = net; self.f = list(fills)
    def reqAllOpenOrders(self): pass
    def sleep(self, *a): pass
    def openTrades(self): return self.t
    def reqExecutions(self, flt): return self.f
    def positions(self):
        return [types.SimpleNamespace(contract=types.SimpleNamespace(localSymbol="MNQZ6"), position=self.n)]
r3_order = Tr("STP", cid=65, oid=501)                 # R3's order with the SAME id as V9's stop
b = make_bot({"VWAP_LONG_R3": sleeve(stop_id=501, tgt_id=502)}, RecIB([r3_order], net=3))
b.reconcile_open_positions()
check("#5 R3's colliding order id 501 is NOT bound to V9", "VWAP_LONG_R3" not in b.live or
      b.live["VWAP_LONG_R3"].get("stop") is not r3_order, b.live)
check("#7 unverifiable (no bracket, no fill, account net != 0) -> QUARANTINED", "VWAP_LONG_R3" in b._quarantined)
CALLS.clear(); MKT.clear()
in_loop(lambda: b._flatten_sleeve("VWAP_LONG_R3", "GAP", 29230.0))
check("quarantined sleeve is never auto-flattened (no orders)", not MKT and not CALLS, CALLS)
b = make_bot({"VWAP_LONG_R3": sleeve(stop_id=601, tgt_id=602)}, RecIB([], net=0))
b.reconcile_open_positions()
check("#7 no bracket + account FLAT -> cleared", not b.state.sleeves["VWAP_LONG_R3"].in_position)
ex = types.SimpleNamespace(execution=types.SimpleNamespace(orderId=701, price=29169.75))
FILLS.clear()
b = make_bot({"VWAP_LONG_R3": sleeve(stop_id=701, tgt_id=702)}, RecIB([], net=0, fills=[ex]))
b.reconcile_open_positions()
check("#7 bracket gone but its fill found -> booked at the fill", FILLS and FILLS[-1][0][3] == 29169.75, FILLS)
CALLS.clear()
tgt_only = Tr("LMT", cid=25, oid=802)
b = make_bot({"VWAP_LONG_R3": sleeve(stop_id=801, tgt_id=802)}, RecIB([tgt_only], net=2))
b.reconcile_open_positions()
check("#7 target alive but STOP missing -> fresh stop+target placed",
      ("stop", 29170.0) in CALLS and ("target", 29290.0) in CALLS, CALLS)

print("\n#6  partial fill then cancel -> the filled quantity gets protected")
CALLS.clear()
sl = sleeve(); sl.in_position = False
b = make_bot({"VWAP_LONG_R3": sl}); b.live["VWAP_LONG_R3"] = {}
ent = Tr("MKT"); ent.orderStatus.status = "Cancelled"; ent.orderStatus.filled = 1
ent.fills = [types.SimpleNamespace(execution=types.SimpleNamespace(avgPrice=29205.0, price=29205.0))]
sig = dict(side="LONG", entry_price=29205.0, stop_price=29175.0, target_price=29295.0)
in_loop(lambda: b._on_entry_status("VWAP_LONG_R3", sig, ent))
check("position recorded with qty 1", sl.in_position and sl.qty == 1, (sl.in_position, sl.qty))
check("stop placed for it", any(c[0] == "stop" for c in CALLS), CALLS)

print("\n#15 reconnect keeps the PINNED contracts")
import inspect
src = inspect.getsource(V.V9Bot.connect_ib)
check("connect_ib(first=False) returns before re-resolving", "if not first and self.contract is not None" in src)
check("heartbeat reconnect passes first=False", "self.connect_ib(first=False)" in inspect.getsource(V.V9Bot.run))

print("\nTLB window = validated backtest")
check("TLB entry_windows_et == [(600,715),(895,920)]", C.TLB["entry_windows_et"] == [(600, 715), (895, 920)],
      C.TLB["entry_windows_et"])

print("\nR3 staged: missing bracket + no fill + account net != 0 -> QUARANTINE, no orders")
import r3re_live_config as RC
RC.LOG_FILE = os.path.join(TMP, "r3.log"); RC.STATE_FILE = os.path.join(TMP, "r3s.json")
RC.LEDGER_FILE = os.path.join(TMP, "r3l.json"); RC.ALERT_SH = "/bin/true"; RC.is_armed = lambda: True
import r3re_live_bot as R
assert R.__file__.startswith("/root/_stage/"), R.__file__
rb = R.R3LiveExecutor.__new__(R.R3LiveExecutor)
placed = []
rb.router = types.SimpleNamespace(oca_bracket=lambda *a: (placed.append(a), (None, None))[1],
                                  cancel=lambda h: None)
rb.ib = types.SimpleNamespace(reqOpenOrders=lambda: None, sleep=lambda *a: None, openTrades=lambda: [],
                              positions=lambda: [types.SimpleNamespace(contract=types.SimpleNamespace(localSymbol="MNQZ6"), position=1)],
                              reqExecutions=lambda f: [])
rb.contract = types.SimpleNamespace(localSymbol="MNQZ6", conId=1)
rb.pos = dict(oca="x", entry=29200.0, stop=29170.0, target=29290.0, direction="long", qty=1,
              entry_time="2026-09-14T14:00:00+00:00", contract="MNQZ6", conId=1, r3_stop_time="t",
              tp_kind="VWAP")
rb.armed_r3 = None; rb.entry_trade = None; rb.handled = set(); rb.quarantined = False
rb.baseline_done = True; rb.today = None; rb.day_realized_usd = 0.0; rb.halted = False
rb.reconcile()
check("R3 quarantined", rb.quarantined is True)
check("R3 placed NO bracket blind", not placed, placed)

print(f"\n{'=' * 60}\n  {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
