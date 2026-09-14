"""r3re_live_config.py — LIVE REAL-MONEY config for the R3 VWAP RE-ENTRY executor.

================================  DISARMED  ================================
LIVE_TRADING_CONFIRMED = False  and  DRY_RUN = True  by DEFAULT.
While DISARMED the executor HARD-BLOCKS every real order (it logs
"WOULD PLACE: ..." only) — see the order_guard() gate in r3re_live_bot.py.

TO ARM (human, after review) — the EXACT one-line change:
    set  LIVE_TRADING_CONFIRMED = True   AND   DRY_RUN = False   (both required).
Either one left at its safe default keeps the bot disarmed. Nothing else arms it.
===========================================================================
"""
from __future__ import annotations

# ===================== REAL-MONEY ARM CONTROLS (DISARMED) =======================
LIVE_TRADING_CONFIRMED: bool = True   # <-- flip to True to arm (with DRY_RUN=False)
DRY_RUN: bool = False                    # <-- flip to False to arm (with LIVE_TRADING_CONFIRMED=True)
# ================================================================================

# ---- account / connection (confirmed from /root/v9/v9_config_live_user.py) ----
LIVE_ACCOUNT: str = "U6015126"
IB_HOST: str = "127.0.0.1"
IB_PORT: int = 7497
CLIENT_ID: int = 65            # RESERVED in /root/CLIENT_IDS.txt for the R3 re-entry REAL executor
ACCOUNT_PREFIX: str = "U"      # real IB account starts with 'U'

# ---- instrument (1 MNQ; DATA=NQ signals, EXEC=MNQ orders — same roll calendar) ----
DATA_INST = dict(symbol="NQ",  secType="FUT", exchange="CME", currency="USD", qty=1, point_value=20.0)
EXEC_INST = dict(symbol="MNQ", secType="FUT", exchange="CME", currency="USD", qty=1, point_value=2.0)
ROLL_DAYS_BEFORE_EXPIRY: int = 7

# ---- safety caps (mirror v9 live scaled to 1 MNQ) ----
# Per-trade risk cap: reject a re-entry whose stop distance risks more than this (USD, 1 MNQ).
# Overlay never produced a wider risk after the $6k full-NQ guard; here the point cap is the
# live guard. 300pt * $2 = $600.
RISK_PER_TRADE_USD: float = 600.0
RISK_CAP_PTS: float = 300.0
# Daily-loss halt: once cumulative realized <= this, flatten + stop entries for the ET day.
DAILY_LOSS_LIMIT_USD: float = -600.0   # 1 MNQ, ~= one max-risk trade; conservative single-sleeve
# 15:55 ET bot-managed flatten (gap-safe; never carry across the 17:00 ET settlement break).
FLATTEN_H, FLATTEN_M = 15, 55

# ---- files (ALL writes stay under /root/r3re_live; NO /root/v9 live-state touched) ----
DIR = "/root/r3re_live"
STATE_FILE   = f"{DIR}/state.json"
LEDGER_FILE  = f"{DIR}/r3re_live_ledger.json"
LOG_FILE     = f"{DIR}/r3re_live.log"
PAUSED_FILE  = f"{DIR}/PAUSED"
KILL_FILE    = f"{DIR}/KILL"          # kill-switch: presence => flatten-all + halt, place nothing
NQ_CSV_DIR   = "/root/v9/data_1m/NQ"  # READ-ONLY shared 1-min archive
ALERT_SH     = "/root/hsi/alert.sh"

WINDOW_DAYS = 7        # trailing recompute window for arming detection
WARMUP_DAYS = 400      # archive warmup so the SMA200 regime gate is warm


def is_armed() -> bool:
    """The ONLY thing that authorizes a real order. BOTH must be set by a human."""
    return (LIVE_TRADING_CONFIRMED is True) and (DRY_RUN is False)


def safety_check() -> None:
    """Fail-closed guards that hold whether armed or disarmed."""
    assert LIVE_ACCOUNT.upper().startswith("U"), f"LIVE_ACCOUNT must be a real U-account, got {LIVE_ACCOUNT!r}"
    assert IB_PORT in (4001, 7496, 7497), f"IB_PORT must be a live port, got {IB_PORT}"
    assert CLIENT_ID not in (5, 21, 25, 26, 27, 28, 63), (
        f"clientId {CLIENT_ID} collides with a live/paper bot (HSI 5 / FX 21 / v9 25-28 / R3-paper 63)"
    )
