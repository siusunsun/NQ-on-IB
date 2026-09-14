# -*- coding: utf-8 -*-
"""Offline tests for the 2026-09-14 R3 fixes. No IB connection, no real orders, no Telegram.
Event handlers are invoked INSIDE a running asyncio loop, exactly as ib_async dispatches them,
and the fake IB RAISES on any blocking call -- so a regression of Codex #1 fails loudly."""
import sys, os, asyncio, tempfile, types
sys.path[:0] = ["/root/r3re_live", "/root/v9"]
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
        t = FakeTrade("STP", px); self.calls.append(("buystop", px)); self.made.append(t); return t
    def oca_bracket(self, d, q, stop, tgt, oca):
        st, tg = FakeTrade("STP", stop, oca), FakeTrade("LMT", tgt, oca)
        self.calls.append(("bracket", stop, tgt)); self.made += [st, tg]; return st, tg
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

print(f"\n{'=' * 60}\n  {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
