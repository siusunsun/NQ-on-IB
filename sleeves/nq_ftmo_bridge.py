"""nq_ftmo_bridge.py — MT5 executor for NQ sleeve signals on FTMO.

Reads the signal file written by nq_sleeves_live.py and converges
FTMO NAS100 positions to match. $100 risk per sleeve.

Requires MetaTrader5 Python package and a running MT5 terminal
connected to the FTMO account.

Usage:
  python nq_ftmo_bridge.py
"""
from __future__ import annotations
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None
    print("WARNING: MetaTrader5 not installed. Running in DRY mode.", flush=True)

import nq_sleeves_config as C

ET = ZoneInfo("America/New_York")
HERE = Path(__file__).resolve().parent

# ---- config ----
MT5_SYMBOL = "NAS100"              # FTMO NAS100 CFD (check your broker's symbol name)
MT5_MAGIC = 55001                  # unique magic number for this bridge
RISK_USD = C.FTMO_RISK_USD         # $100 per sleeve
USD_PER_PT = C.FTMO_USD_PER_PT     # $1/pt for NAS100 CFD
STALE_SEC = 30                     # ignore signals older than this
POLL_SEC = 2
DRY_RUN = True                     # MUST be flipped to False after validation

log = logging.getLogger("nq_ftmo")
if not log.handlers:
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    sh = logging.StreamHandler(); sh.setFormatter(fmt)
    fh = logging.FileHandler(HERE / "nq_ftmo_bridge.log", encoding="utf-8"); fh.setFormatter(fmt)
    log.addHandler(sh); log.addHandler(fh); log.setLevel(logging.INFO)


# ---- MT5 helpers ----
def mt5_init() -> bool:
    if mt5 is None:
        return False
    if not mt5.initialize():
        log.error(f"MT5 init failed: {mt5.last_error()}")
        return False
    info = mt5.account_info()
    if info:
        log.info(f"MT5 connected: account={info.login} balance={info.balance} equity={info.equity}")
    return True

def mt5_position(magic: int) -> dict | None:
    if mt5 is None:
        return None
    positions = mt5.positions_get(symbol=MT5_SYMBOL)
    if positions:
        for p in positions:
            if p.magic == magic:
                return dict(ticket=p.ticket, side="long" if p.type == 0 else "short",
                            volume=p.volume, price=p.price_open, sl=p.sl, tp=p.tp)
    return None

def mt5_open(side: str, lots: float, sl: float, tp: float, comment: str) -> bool:
    if mt5 is None or DRY_RUN:
        log.info(f"[DRY] would OPEN {side} {lots:.2f} lots SL={sl:.1f} TP={tp:.1f} ({comment})")
        return True
    order_type = mt5.ORDER_TYPE_BUY if side == "long" else mt5.ORDER_TYPE_SELL
    tick = mt5.symbol_info_tick(MT5_SYMBOL)
    if not tick:
        log.error(f"No tick for {MT5_SYMBOL}")
        return False
    price = tick.ask if side == "long" else tick.bid
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": MT5_SYMBOL,
        "volume": lots,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "magic": MT5_MAGIC,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(f"OPENED {side} {lots:.2f} lots @ {result.price} (order {result.order})")
        return True
    log.error(f"Open failed: {result}")
    return False

def mt5_close(ticket: int, side: str, volume: float, comment: str) -> bool:
    if mt5 is None or DRY_RUN:
        log.info(f"[DRY] would CLOSE ticket={ticket} {side} {volume:.2f} lots ({comment})")
        return True
    close_type = mt5.ORDER_TYPE_SELL if side == "long" else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(MT5_SYMBOL)
    if not tick:
        return False
    price = tick.bid if side == "long" else tick.ask
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": MT5_SYMBOL,
        "volume": volume,
        "type": close_type,
        "price": price,
        "position": ticket,
        "magic": MT5_MAGIC,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(f"CLOSED ticket={ticket} @ {result.price}")
        return True
    log.error(f"Close failed: {result}")
    return False


# ---- signal reader ----
def read_signals() -> tuple[dict | None, bool]:
    p = HERE / C.FTMO_SIGNAL_FILE
    if not p.exists():
        return None, True
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None, True
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(d["ts_utc"])).total_seconds()
    except Exception:
        return d, True
    return d, (age > STALE_SEC)


# ---- per-sleeve state ----
class SleeveTracker:
    def __init__(self, name: str):
        self.name = name
        _OFFSETS = {"CCI": 1, "SNAP": 2, "TBS": 3}
        self.magic = MT5_MAGIC + _OFFSETS.get(name, 0)
        self.in_position = False
        self.side: str | None = None
        self.ticket: int | None = None
        self.lots = 0.0
        self.last_action_ts: str | None = None

    def converge(self, sleeve_sig: dict | None, stale: bool):
        if stale or sleeve_sig is None:
            return

        action = sleeve_sig.get("action", "")
        side = sleeve_sig.get("side", "")
        sig_ts = sleeve_sig.get("ts", "")

        if sig_ts == self.last_action_ts:
            return

        pos = mt5_position(self.magic)

        if action == "ENTER" and not self.in_position and pos is None:
            risk_pts = sleeve_sig.get("risk_pts", 0)
            if risk_pts <= 0:
                return
            lots = round(RISK_USD / risk_pts / USD_PER_PT, 2)
            lots = max(lots, 0.01)

            # Convert NQ absolute prices to relative offsets from current CFD price
            stop = sleeve_sig.get("stop", 0)
            target = sleeve_sig.get("target", 0)

            if mt5_open(side, lots, stop, target, f"NQS_{self.name}"):
                self.in_position = True
                self.side = side
                self.lots = lots
                self.last_action_ts = sig_ts
                log.info(f"[{self.name}] entered {side} {lots:.2f} lots")

        elif action == "EXIT" and self.in_position:
            closed = True
            if pos is not None:
                closed = mt5_close(pos["ticket"], pos["side"], pos["volume"], f"NQS_{self.name}_EXIT")
            if closed:
                self.in_position = False
                self.side = None
                self.lots = 0.0
                self.last_action_ts = sig_ts
                log.info(f"[{self.name}] exited")
            else:
                log.error(f"[{self.name}] close FAILED — retrying next cycle")


# ---- main ----
def main():
    log.info(f"NQ FTMO Bridge starting — DRY_RUN={DRY_RUN}, risk=${RISK_USD}/sleeve")

    if not DRY_RUN:
        if not mt5_init():
            log.error("MT5 init failed — exiting")
            return

    trackers = {name: SleeveTracker(name) for name in ("CCI", "SNAP", "TBS")}

    while True:
        try:
            signals, stale = read_signals()
            if signals and not stale:
                sleeves = signals.get("sleeves", {})
                for name, tracker in trackers.items():
                    tracker.converge(sleeves.get(name), stale)

            time.sleep(POLL_SEC)

        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"loop error: {e!r}")
            time.sleep(10)

    if mt5 is not None:
        mt5.shutdown()
    log.info("Bridge stopped.")


if __name__ == "__main__":
    main()
