"""nq_sleeves_config.py — Configuration for CCI AM + SNAP + TBS deployment.

Three output targets:
  TWS   — via SSH tunnel on VPS (port 7497), temporary while Gateway is broken
  GW    — IB Gateway live (port 4001), long-term target
  FTMO  — MT5 bridge, $100 risk per sleeve
"""
from __future__ import annotations
import os

# ---- output target (set via env or override below) ----
# "TWS" | "GW" | "FTMO"
TARGET = os.environ.get("NQ_TARGET", "TWS")

# ---- IB connection (TWS / GW targets) ----
_IB = {
    "TWS":  dict(host="127.0.0.1", port=7497,  client_id=55, acct_pin=None),
    "GW":   dict(host="127.0.0.1", port=4001,  client_id=55, acct_pin=None),
}

def ib_conn():
    return _IB.get(TARGET, _IB["TWS"])

# ---- sizing ----
# IB: 1 MNQ per sleeve (minimum size, MNQ only until proven profitable)
# FTMO: $100 risk per sleeve (NAS100 CFD, $1/pt, so risk_pts = 100)
MNQ_QTY = 1                       # per sleeve on IB
FTMO_RISK_USD = 100.0              # per sleeve on FTMO
FTMO_USD_PER_PT = 1.0              # NAS100 CFD = $1/pt

# ---- contracts ----
TRADE_SYM = "MNQ"                  # always MNQ on IB
TRADE_EXCHANGE = "CME"
DATA_SYM = "NQ"                    # data feed: NQ continuous (MNQ is the same prices)
USD_PER_PT = 2.0                   # MNQ = $2/pt
NQ_TICK = 0.25

# ---- CCI AM slot ----
CCI_ENABLED = True
CCI_WIN_ET = (570, 720)            # 09:30 - 12:00 ET (minutes from midnight)
CCI_N = 20
CCI_THR = 0.0015                   # (SLOW - VWAP) / VWAP gate
CCI_VOL_GATE = 1.00
CCI_SLOPE_LB = 30
CCI_STOP_LB = 20                   # bars for swing stop
CCI_KATR = 1.0                     # stop = katr * ATR20
CCI_EOD_ET = 960                   # 16:00 ET flatten

# ---- SNAP ----
SNAP_ENABLED = True
SNAP_LOOKBACK = 20
SNAP_THR = -1.5                    # z-score threshold (long when z < this)
SNAP_HIST_DUR = "60 D"
SNAP_PRECLOSE_ET = (16, 57)        # Friday pre-close decision

# ---- TBS ----
TBS_ENABLED = True
# TBS only fires on BOTH_BEAR regime days
# Session params come from v9_tlb_bear_short.SESSIONS

# ---- kill / review lines (pre-registered, per sleeve, cumulative) ----
KILL_LINES = {
    "CCI":  -2213,     # 2x backtest maxDD ($1,107)
    "SNAP": -2969,     # 2x backtest maxDD ($1,484)
    "TBS":  -1646,     # 2x backtest maxDD ($823)
}
REVIEW_LINES = {
    "CCI":  -1660,     # 1.5x
    "SNAP": -2227,     # 1.5x
    "TBS":  -1235,     # 1.5x
}

# ---- FTMO bridge ----
FTMO_SIGNAL_FILE = "nq_sleeves_signal.json"

# ---- state / logging (per-target to avoid cross-contamination) ----
_sfx = f"_{TARGET.lower()}" if TARGET != "TWS" else ""
STATE_FILE = f"nq_sleeves_state{_sfx}.json"
LEDGER_FILE = f"nq_sleeves_ledger{_sfx}.jsonl"
HEARTBEAT_FILE = f"nq_sleeves_heartbeat{_sfx}.json"
DISABLED_FLAG = "NQ_SLEEVES_DISABLED.flag"

# ---- telegram (import from existing config, never hardcode tokens) ----
def tg_token():
    try:
        import telegram_config as tc
        return tc.TELEGRAM_BOT_TOKEN, tc.TELEGRAM_CHAT_ID
    except Exception:
        return None, None

# ---- regime (SMA/momentum windows, same as V9) ----
REGIME_SMA = 200
REGIME_MOM = 20

# ---- data ----
HIST_1M_DUR = "2 D"
HIST_5M_DUR = "45 D"
HIST_DAILY_DUR = "365 D"
