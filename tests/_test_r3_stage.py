# -*- coding: utf-8 -*-
"""Offline tests for the 2026-09-14 R3 fixes. No IB connection, no real orders, no Telegram.
Event handlers are invoked INSIDE a running asyncio loop, exactly as ib_async dispatches them,
and the fake IB RAISES on any blocking call -- so a regression of Codex #1 fails loudly."""
import sys, os, asyncio, tempfile, types
sys.path[:0] = ["/root/_stage/r3re_live", "/root/r3re_live", "/root/v9"]
import r3re_live_config as CFG

TMP = tempfile.mkdtemp(prefix="r3test_")
CFG.LOG_FILE = os.path.join(TMP, "t.log")
CFG.STATE_FILE = os.path.join(TMP, "state.json")
CFG.LEDGER_FILE = os.path.join(TMP, "ledger.json")
CFG.KILL_FILE = os.path.join(TMP, "KILL")
CFG.ALERT_SH = "/bin/true"                       # never send a real Telegram
CFG.is_armed = lambda: True                      # exercise the armed code paths ...
import r3re_live_bot as B                        # ... against a FAKE router (no IB at all)
from eventkit import Event
import pandas as pd

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}  {detail}")


class FakeStatus:
    def __init__(self): self.avgFillPrice = 0.0; self.status = "Submitted"


class FakeExec:
    def __init__(self, px): self.avgPrice = px; self.price = px


class FakeFill:
    def __init__(self, px): self.execution = FakeExec(px)


class FakeTrade:
    _id = 100
    def __init__(self, kind, px=None, oca=""):
        FakeTrade._id += 1
        self.order = types.SimpleNamespace(orderId=FakeTrade._id, orderType=kind, ocaGroup=oca,
                                           clientId=CFG.CLIENT_ID)
        self.contract = types.SimpleNamespace(localSymbol="MNQZ6")
        self.px = px; self.fills = []; self.orderStatus = FakeStatus(); self._done = False
        self.filledEvent = Event("filled"); self.cancelledEvent = Event("cancelled")
    def isDone(self): return self._done
    def fill(self, px):
        self._done = True; self.orderStatus.avgFillPrice = px; self.fills = [FakeFill(px)]
        self.filledEvent.emit(self)
    def cancel_ack(self):
        self._done = True; self.orderStatus.status = "Cancelled"; self.cancelledEvent.emit(self)


class FakeRouter:
    def __init__(self): self.calls = []; self.made = []
    def resting_buy_stop(self, px, qty):
        t = FakeTrade("STP", px); t.order.auxPrice = px
        self.calls.append(("buystop", px)); self.made.append(t); return t
    def modify_buy_stop(self, t, px):
        self.calls.append(("modify", px)); t.order.auxPrice = px; return t
    def oca_bracket(self, d, q, stop, tgt, oca):
        st, tg = FakeTrade("STP", stop, oca), FakeTrade("LMT", tgt, oca)
        tg.order.lmtPrice = tgt; st.order.auxPrice = stop
        self.calls.append(("bracket", stop, tgt)); self.made += [st, tg]; return st, tg
    def modify_target(self, t, px):
        self.calls.append(("mtarget", px)); t.order.lmtPrice = px; return t
    def place_target(self, d, q, px, oca):
        t = FakeTrade("LMT", px, oca); t.order.lmtPrice = px; self.calls.append(("ptarget", px)); self.made.append(t); return t
    def replace_target(self, d, q, old, px, oca):
        self.calls.append(("replace", px)); t = FakeTrade("LMT", px, oca); self.made.append(t); return t
    def cancel(self, h):
        if h is not None: self.calls.append(("cancel", h.order.orderId))
    def flatten(self, d, q, reason):
        t = FakeTrade("MKT"); self.calls.append(("market", reason)); self.made.append(t); return t


class NoBlockIB:
    """Any blocking IB call inside a handler is exactly the Codex #1 bug -> raise."""
    def __getattr__(self, name):
        def boom(*a, **k): raise RuntimeError(f"BLOCKING IB CALL '{name}' made inside a handler")
        return boom


def make_bot():
    b = B.R3LiveExecutor.__new__(B.R3LiveExecutor)
    b.ib = NoBlockIB(); b.router = FakeRouter()
    b.contract = types.SimpleNamespace(localSymbol="MNQZ6", conId=777)
    b.data_contract = types.SimpleNamespace(localSymbol="NQZ6")
    b.warm = pd.DataFrame(); b.regime = {}; b.baseline_done = True; b.handled = set()
    b.today = None; b.day_realized_usd = 0.0; b.halted = False; b.pos = None
    b.entry_trade = None; b.stop_handle = b.target_handle = None; b.armed_r3 = None
    b._win = None; b._full = None; b.flattening = None; b._flat_alert_t = 0.0
    return b


ARMED = dict(r3_stop_time="2026-09-14T13:05:00+00:00", r3_signal_time="2026-09-14T12:00:00+00:00",
             session="2026-09-14", buy_stop=29204.75, prospective_stop=29178.00,
             prospective_riskpt=26.75, lag_vwap=29250.37, lag_upper=29301.10)


def in_loop(fn):
    """Run fn from inside a running event loop -- how ib_async dispatches filledEvent."""
    async def main(): fn()
    asyncio.run(main())


print("\n#1  entry fill inside a RUNNING loop: no blocking call, stop placed, position recorded")
b = make_bot()
b._win = None                                    # worst case: no cached window -> armed fallback
entry = FakeTrade("STP", 29204.75)
entry.fills = [FakeFill(29204.75)]
err = []
def guarded():
    try: b._on_entry_fill(entry, ARMED)
    except Exception as e: err.append(e)
in_loop(guarded)
check("no exception from the handler", not err and b.pos is not None, f"err={err} pos={b.pos}")
check("a bracket was placed", any(c[0] == "bracket" for c in b.router.calls), b.router.calls)
check("stop = armed prospective stop, on tick", b.pos and b.pos["stop"] == 29178.0, b.pos)
check("target on 0.25 tick (VWAP 29250.37 -> 29250.25)", b.pos and b.pos["target"] == 29250.25, b.pos)
check("contract recorded for roll safety", b.pos and b.pos["contract"] == "MNQZ6" and b.pos["conId"] == 777)

print("\n#12 exit books the REAL fill (gap through the stop), not the planned stop")
st = b.stop_handle
in_loop(lambda: st.fill(29150.00))
led = B._load_json(CFG.LEDGER_FILE, [])
check("booked at 29150.00, not the 29178.00 stop", led and led[-1]["exit"] == 29150.0, led[-1:] )
check("daily P&L uses the real loss (-54.75pt x $2 = -$109.50)", abs(b.day_realized_usd + 109.5) < 1e-6, b.day_realized_usd)

print("\n#4  flatten: market sent only AFTER both cancels confirm; booked at the market fill")
b = make_bot(); entry = FakeTrade("STP"); entry.fills = [FakeFill(29204.75)]
in_loop(lambda: b._on_entry_fill(entry, ARMED))
st, tgt = b.stop_handle, b.target_handle
in_loop(lambda: b._flatten_position("FLATTEN"))
check("no market order before the cancels land", not any(c[0] == "market" for c in b.router.calls))
in_loop(lambda: st.cancel_ack())
check("still no market with one leg alive", not any(c[0] == "market" for c in b.router.calls))
in_loop(lambda: tgt.cancel_ack())
mk = [t for t in b.router.made if t.order.orderType == "MKT"]
check("market sent once both legs are cancelled", len(mk) == 1)
in_loop(lambda: mk[0].fill(29230.50))
led = B._load_json(CFG.LEDGER_FILE, [])
check("flatten booked at the market fill 29230.50 (not entry)", led and led[-1]["exit"] == 29230.5, led[-1:])
check("position cleared", b.pos is None and b.flattening is None)

print("\n#4  race: the stop fills while the flatten waits -> exactly ONE exit, no market order")
b = make_bot(); entry = FakeTrade("STP"); entry.fills = [FakeFill(29204.75)]
in_loop(lambda: b._on_entry_fill(entry, ARMED))
st, tgt = b.stop_handle, b.target_handle
in_loop(lambda: b._flatten_position("FLATTEN"))
in_loop(lambda: st.fill(29177.75))              # stop wins the race
in_loop(lambda: tgt.cancel_ack())
check("no market order was sent", not any(c[0] == "market" for c in b.router.calls), b.router.calls)
n_exits = sum(1 for r in B._load_json(CFG.LEDGER_FILE, []) if r["r3_stop_time"] == ARMED["r3_stop_time"])
check("exit booked once, at the stop fill", b.pos is None)

print("\n#8  KILL / halted / 15:55 cutoff cancel a resting buy-stop")
b = make_bot(); b.entry_trade = FakeTrade("STP", 29204.75); b.armed_r3 = "x"
b._cancel_entry("KILL")
check("resting entry cancelled + arming dropped", b.entry_trade is None and b.armed_r3 is None
      and any(c[0] == "cancel" for c in b.router.calls))

print("\n#17 target follows the ENTRY kind (force_kind), no longer freezes")
import inspect, r3v_live_spec as S
check("bracket_on_fill accepts force_kind", "force_kind" in inspect.signature(S.bracket_on_fill).parameters)
src = inspect.getsource(B.R3LiveExecutor.cycle)
check("cycle() passes force_kind=pos['tp_kind']", 'force_kind=self.pos["tp_kind"]' in src)

print("\n#9  live window fetches the NQ SIGNAL contract")
src = inspect.getsource(B.R3LiveExecutor.build_window)
check("build_window uses self.data_contract", "self.data_contract" in src)


print("\n#R  resting buy-stop maintenance (2026-09-14 churn fix)")
A1 = dict(ARMED); A2 = dict(ARMED, buy_stop=29210.00); B2 = dict(ARMED, r3_stop_time="2026-09-14T14:40:00+00:00", buy_stop=29150.25)
b = make_bot()
b._rest_entry(A1)
first = b.entry_trade
check("first arming places ONE buy-stop", b.router.calls == [("buystop", 29204.75)], b.router.calls)
b.router.calls.clear(); b._rest_entry(A1); b._rest_entry(A1)
check("unchanged trigger -> NOTHING sent (old code cancel+placed every minute)", b.router.calls == [], b.router.calls)
b._rest_entry(A2)
check("runmax rises -> one in-place MODIFY, no cancel, same order", b.router.calls == [("modify", 29210.00)]
      and b.entry_trade is first and first.order.auxPrice == 29210.00, b.router.calls)
b.router.calls.clear(); b._rest_entry(B2)
check("a DIFFERENT R3 stop -> cancel the old order, then place a new one",
      b.router.calls == [("cancel", first.order.orderId), ("buystop", 29150.25)] and b.armed_r3 == B2["r3_stop_time"], b.router.calls)
second = b.entry_trade; second.cancel_ack(); b.router.calls.clear(); b._rest_entry(B2)
check("IB cancelled/expired it -> a fresh one is placed", b.router.calls == [("buystop", 29150.25)], b.router.calls)
third = b.entry_trade; third.fills = [FakeFill(29150.25)]; third.orderStatus.status = "Filled"
b.router.calls.clear(); b._rest_entry(B2)
check("filled but no position recorded -> no second order", b.router.calls == [], b.router.calls)

b = make_bot(); b._rest_entry(A1); b._rest_entry(A2); t = b.entry_trade
in_loop(lambda: t.fill(29210.00))
check("fill after a modify -> position + bracket, using the latest levels", b.pos is not None
      and b.pos["entry"] == 29210.00 and any(c[0] == "bracket" for c in b.router.calls), b.router.calls)


class _IB:
    def placeOrder(self, c, o):
        if getattr(o, "_done", False): raise AssertionError("done")
        return types.SimpleNamespace(order=o, orderStatus=types.SimpleNamespace(status="PreSubmitted"))


r = B.OrderRouter(_IB(), types.SimpleNamespace(localSymbol="MNQZ6"))
tr = r.resting_buy_stop(29204.75, 1)
check("real router: entry order is DAY + outsideRth (was GTC)", tr.order.tif == "DAY" and tr.order.outsideRth is True, tr.order.tif)
tr.order._done = True; out = r.modify_buy_stop(tr, 29300.00)
check("modify on an order that just finished -> no crash, price restored", out is tr and tr.order.auxPrice == 29204.75)


print("\n#T  Telegram alerts for the resting buy-stop")
from datetime import datetime, timezone
SENT = []
B.tg = lambda msg, level="INFO": SENT.append(msg)
X = B.R3LiveExecutor
t1 = X._auto_cancel_utc(dict(ARMED, r3_stop_time="2026-09-14T08:19:00+00:00"))
check("today's case: stop 08:19 UTC -> auto-cancel 12:25 UTC (= 20:25 HKT)",
      t1 == datetime(2026, 9, 14, 12, 25, tzinfo=timezone.utc), t1)
t2 = X._auto_cancel_utc(dict(ARMED, r3_stop_time="2026-09-14T18:02:00+00:00"))
check("afternoon stop 14:02 ET -> the 15:55 ET cutoff comes first (19:55 UTC)",
      t2 == datetime(2026, 9, 14, 19, 55, tzinfo=timezone.utc), t2)
t3 = X._auto_cancel_utc(dict(ARMED, r3_stop_time="2026-01-14T22:10:00+00:00"))
check("winter evening stop 17:10 ET (past cutoff) -> cutoff/now, never later",
      t3 <= datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc), t3)
check("HKT formatting", X._hkt(t1).endswith("HKT") and "20:25" in X._hkt(t1), X._hkt(t1))

SENT.clear(); b = make_bot(); b._rest_entry(dict(ARMED, r3_stop_time="2026-09-14T08:19:00+00:00"))
check("placing sends ONE 'ARMED' alert with price, stop, risk and cancel time",
      len(SENT) == 1 and "ARMED" in SENT[0] and "29204.75" in SENT[0] and "29178.00" in SENT[0]
      and "26.8pt" in SENT[0] and "20:25" in SENT[0] and "auto-cancels" in SENT[0], SENT)
SENT.clear(); b._rest_entry(dict(ARMED, r3_stop_time="2026-09-14T08:19:00+00:00"))
check("unchanged order -> no alert (no spam every minute)", SENT == [], SENT)
b._rest_entry(dict(ARMED, r3_stop_time="2026-09-14T08:19:00+00:00", buy_stop=29210.00))
check("moved up -> one 'moved' alert", len(SENT) == 1 and "moved 29204.75 -> 29210.00" in SENT[0], SENT)
SENT.clear(); b._cancel_entry("15:55 cutoff")
check("cancel path sends 'cancelled (reason)'", len(SENT) == 1 and "cancelled (15:55 cutoff)" in SENT[0], SENT)
SENT.clear(); b._cancel_entry("KILL")
check("nothing resting -> no duplicate cancel alert", SENT == [], SENT)


print("\n#G  target follows VWAP by in-place MODIFY (no cancel + new sell order)")
SENT.clear()
b = make_bot(); e = FakeTrade("STP"); e.fills = [FakeFill(29204.75)]
in_loop(lambda: b._on_entry_fill(e, ARMED))
tgt0, st0 = b.target_handle, b.stop_handle
b.router.calls.clear(); b._move_target(29262.50)
check("target moved in place: one modify, no cancel, same order", b.router.calls == [("mtarget", 29262.50)]
      and b.target_handle is tgt0 and b.pos["target"] == 29262.50, b.router.calls)
hits = []
b._on_exit_fill = lambda label, tr: hits.append(label)
in_loop(lambda: tgt0.fill(29262.50))
check("a fill after the move books the exit exactly ONCE (handler not bound twice)", hits == ["TARGET"], hits)
b.router.calls.clear(); b._move_target(29270.00)
check("target already filled -> nothing sent", b.router.calls == [], b.router.calls)

b = make_bot(); e = FakeTrade("STP"); e.fills = [FakeFill(29204.75)]
in_loop(lambda: b._on_entry_fill(e, ARMED))
b.target_handle.cancel_ack(); b.router.calls.clear(); SENT.clear(); b._move_target(29262.50)
check("target dropped at IB, stop live -> fresh target in the SAME OCA + a WARNING",
      b.router.calls == [("ptarget", 29262.50)] and b.target_handle.order.ocaGroup == b.pos["oca"]
      and len(SENT) == 1, (b.router.calls, SENT))
b.stop_handle.cancel_ack(); b.target_handle.cancel_ack(); b.router.calls.clear(); b._move_target(29265.00)
check("target AND stop gone -> nothing placed blind (reconcile's job)", b.router.calls == [], b.router.calls)

tr = types.SimpleNamespace(order=types.SimpleNamespace(orderId=9, lmtPrice=29250.0, _done=True),
                           orderStatus=types.SimpleNamespace(status="Filled"))
out = r.modify_target(tr, 29300.0)
check("real router: modify on a finished target -> no crash, price restored", out is tr and tr.order.lmtPrice == 29250.0)
check("cycle() no longer cancel+replaces the target", "replace_target" not in inspect.getsource(B.R3LiveExecutor.cycle))

print(f"\n{'=' * 60}\n  {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
