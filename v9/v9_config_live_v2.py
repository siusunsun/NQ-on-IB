"""v9.2 LIVE config — Option 1: 4-sleeve composition (VWAP_R3, R3_REV, CCI_AM, TBS).

Replaces v9_config_live_user.py. All sleeves at 1 MNQ to start.
Realistic cost: $4.80/trade (1.88pt slippage * $2/pt + $1.04 commission).
Backtest: Net $20,575, Sharpe 1.90, MaxDD -$890, 72% positive months (69 months).
"""
from __future__ import annotations

# ===================== REAL-MONEY ARM CONTROLS =======================
LIVE_TRADING_CONFIRMED: bool = True
ENABLE_LONG:  bool = True     # ARMED 2026-09-25 — week 1, 1 MNQ, TWS tunnel
ENABLE_SHORT: bool = True

LIVE_ACCOUNT: str = "U6015126"
IB_PORT: int = 7497
# =====================================================================

IB_HOST: str = "127.0.0.1"
CLIENT_ID: int = 25
ACCOUNT_PREFIX: str = LIVE_ACCOUNT
PAPER_ONLY: bool = False

INSTRUMENT = dict(
    symbol="NQ", secType="FUT", exchange="CME", currency="USD",
    qty=1, point_value=20.0,
)
INSTRUMENT_EXEC = dict(
    symbol="MNQ", secType="FUT", exchange="CME", currency="USD",
    qty=1, point_value=2.0,
)

ROLL_DAYS_BEFORE_EXPIRY = 7

REGIME = dict(sma_window=200, momentum_window=20)
REGIME_CACHE_FILE = "/root/v9/v9_live_regime_cache.json"
REGIME_BOUNDARY_EPS = 0.003
REGIME_POLY_MAX_DEV = 0.003
REGIME_CLOSE_MAX_DEV = 0.015

# Per-sleeve MNQ quantity — all start at 1
SLEEVE_QTY = {
    "VWAP_LONG_R3": 1,
    "R3_REV":       1,
    "CCI_AM":       1,
    "TLB_BEAR_SHORT": 1,
}

# Regime -> active sleeves (CCI_AM in ALL regimes, TBS in BOTH_BEAR only,
# R3_REV fires conditionally after VWAP_R3 stop-out, not regime-gated here)
REGIME_SLEEVES = {
    "BOTH_BULL": ["VWAP_LONG_R3", "CCI_AM"],
    "ZONE_A":    ["VWAP_LONG_R3", "CCI_AM"],
    "ZONE_B":    ["CCI_AM"],
    "BOTH_BEAR": ["TLB_BEAR_SHORT", "CCI_AM"],
}

# VWAP wide-stop backstop
VWAP_MAX_STOP_PTS = 200

# VWAP LONG R3 config (unchanged from v9.1)
VWAP_LONG = dict(
    bar_min=5, entry_band_k=2.0, n_bars_outside=5, swing_lookback=20,
    r_multiple=3.0, anchor_hour_utc=0,
    active_windows_et=[(0, 360), (570, 720), (720, 870), (1140, 1440)],
    post_halt_blackout_bars=24,
)

# R3 re-entry config
R3_REV = dict(
    max_wait=48,
    wait_bars=2,
)

# CCI AM config
CCI_AM = dict(
    cci_n=20,
    ema_span=50,
    atr_n=14,
    norm_window=20,
    vwap_frac=0.0015,
    window_et=(570, 720),   # 09:30-12:00
    k_stop=1.0,
)

# TLB Bear Short config
TBS = dict(
    k=3, atr_len=14, line_min=15,
    sessions={"PRE": ((480, 570), 4.0, 4.0),
              "OPEN": ((570, 660), 3.0, 2.0),
              "MIDDAY": ((660, 840), 4.0, 3.0)},
    max_per_session=1,
    max_per_day=3,
)

SESSION_OPEN_H, SESSION_OPEN_M = 9, 30
EOD_FLATTEN_H, EOD_FLATTEN_M = 15, 20
GAP_FLATTEN_H, GAP_FLATTEN_M = 15, 55
DAILY_LOSS_LIMIT_USD = -2000

BAR_STALL_SEC = 180
IBC_COMMAND_PORT = 0

STATE_FILE = "/root/v9/v9_live_heartbeat.json"
TRADE_LOG_FILE = "/root/v9/v9_live_trade_log.json"
LOG_FILE = "/root/v9/v9_live_trading.log"

# Disabled sleeves (empty = all active per regime)
DISABLED_SLEEVES = set()


def safety_check() -> None:
    assert LIVE_TRADING_CONFIRMED is True, "LIVE_TRADING_CONFIRMED is False"
    assert LIVE_ACCOUNT and LIVE_ACCOUNT.upper().startswith("U"), (
        f"LIVE_ACCOUNT must be real. Got {LIVE_ACCOUNT!r}.")
    LIVE_PORTS = (4001, 7496, 7497)
    assert IB_PORT in LIVE_PORTS, f"IB_PORT must be LIVE. Got {IB_PORT}."
    assert CLIENT_ID not in (5, 21), "clientId collision with HSI/FX bot."
