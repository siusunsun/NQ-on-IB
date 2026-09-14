"""v9.1 LIVE config — sunsun139test setup on Hetzner VPS.

Derived from kristov18's v9_config_live.py. Adapted for:
- account U6015126 (sunsun139test)
- IB Gateway running LIVE on port 7497 (non-standard, but the bot's gateway)
- coexists with HSI bot (clientId 5) and FX engine (clientId 21)
- 1 MNQ to start

DEPLOY SEQUENCE:
  Day 1 (TODAY): ENABLE_LONG = ENABLE_SHORT = False   -> DRY RUN
    The bot connects to U6015126, computes the regime cell, logs signals.
    PLACES ZERO ORDERS. Watch for one full session.
  Day 2 (after clean dry session): flip both to True -> ARMED real orders.
"""
from __future__ import annotations

# ===================== REAL-MONEY ARM CONTROLS =======================
LIVE_TRADING_CONFIRMED: bool = True
ENABLE_LONG:  bool = True     # 2026-06-23: ARMED after clean dry-run
ENABLE_SHORT: bool = True

LIVE_ACCOUNT: str = "U6015126"
IB_PORT: int = 7497
# =====================================================================

IB_HOST: str = "127.0.0.1"
CLIENT_ID: int = 25  # avoid collision with HSI bot (5) and FX engine (21)
ACCOUNT_PREFIX: str = LIVE_ACCOUNT
PAPER_ONLY: bool = False

INSTRUMENT = dict(
    symbol="NQ", secType="FUT", exchange="CME", currency="USD",
    qty=1, point_value=20.0,
)
INSTRUMENT_EXEC = dict(
    symbol="MNQ", secType="FUT", exchange="CME", currency="USD",
    qty=1,             # 1 MNQ = $2/pt. START HERE.
    point_value=2.0,
)

ROLL_DAYS_BEFORE_EXPIRY = 7

REGIME = dict(sma_window=200, momentum_window=20)
REGIME_CACHE_FILE = "/root/v9/v9_live_regime_cache.json"
REGIME_BOUNDARY_EPS = 0.003
# Data-resilience cross-check tolerances (added 2026-07-01 after the 30/6 IB-disconnect
# regime corruption). Polygon is an INDEPENDENT provider (survives an IB outage); a tight
# 0.3% gate flags a corrupt/truncated IB 1-min pull and substitutes Polygon's close.
# IB settled-bar is the looser fallback (1.5%) used only when Polygon is unavailable.
REGIME_POLY_MAX_DEV = 0.003    # 0.3% — independent Polygon referee (primary)
REGIME_CLOSE_MAX_DEV = 0.015   # 1.5% — IB settled-bar gross-error gate (fallback)

TLB_MAX_TRADE_RISK_USD = 600   # halved with RISK_PER_TRADE so the single-trade cap still scales together
RISK_PER_TRADE_USD = 300   # halved to test multi-contract path safely; up-scale once smooth

# FIXED per-sleeve contract size (MNQ) — final 2026-07-01. Mean-reversion sleeves = 2 MNQ,
# TLB trend-break = 1 MNQ. Replaces dynamic risk-based sizing (RISK_PER_TRADE_USD now unused).

# DISABLED_SLEEVES: sleeves listed here are NEVER activated, regardless of regime cell.
# 2026-07-11: TLB_LONG runs as an MNQ futures sleeve again (A1 0DTE bot retired; options wrapper abandoned).
DISABLED_SLEEVES = set()  # 2026-07-11: TLB_LONG re-enabled as MNQ futures sleeve (A1 0DTE bot retired — options abandoned)

SLEEVE_QTY = {
    "TLB_LONG":      1,   # trend-line break long
    "VWAP_LONG_R3":  2,   # mean-reversion long (3R)
    # 2026-08-27 (5.6-yr audit): CUT 2 -> 1. This sleeve is a 2022 artifact (150/222 trades and
    # half its P&L from that one bear year), dies on the cost floor (PF 1.06 @3x cost), fails DSR,
    # and 4 yrs of walk-forward made $894/contract. KEPT because it is the only short exposure and
    # the regime gate IS its edge (ungated: -$3,666). Treat as REGIME INSURANCE, not a P&L sleeve.
    "VWAP_SHORT_R1": 1,   # mean-reversion short (1R) — insurance size
}

# VWAP wide-stop backstop (2026-08-03): skip any VWAP entry whose stop is wider than this many
# points. 200pt = $800 total at 2 MNQ ($400/contract). Point-based so it is size-independent.
# Normal VWAP stops are ~25pt median / 159pt max (144 backtest trades) -> fires ~never; backstop.
VWAP_MAX_STOP_PTS = 200

TLB = dict(
    # 2026-08-26: HYBRID STOP (kristov18 upgrade, validated on our harness +29% full / all yrs >=0).
    # If pivot-stop risk < 40pt, widen to the 20-bar low (incl signal bar). Only widens tight stops.
    hybrid_stop_pts=40.0, hybrid_lookback=20,
    bar_min=5, pivot_k=3, r_multiple=1.0, use_24h=True, slope_filter=False,
    # 2026-09-14: (600,720) -> (600,715). is_in_window is half-open (lo <= t < hi), so 720
    # let a TLB entry fire on the bar LABELLED 11:55 -- one bar the validated 5.6-yr history
    # (_hist_sleeves.py / bt_nq.TLB_CFG / the dashboard) never had. Now identical to it.
    entry_windows_et=[(600, 715), (895, 920)],
)
VWAP_LONG = dict(
    bar_min=5, entry_band_k=2.0, n_bars_outside=5, swing_lookback=20,
    r_multiple=3.0, anchor_hour_utc=0,
    active_windows_et=[(0, 360), (570, 720), (720, 870), (1140, 1440)],
    post_halt_blackout_bars=24,   # 2026-08-03 (kristov18 716285d/1965953): long-only post-halt blackout,
    # 24 bars=2h; deployed-takeable post-halt longs are 0W/2L incl the live 08-03 loss, winners sit in
    # gated-off bear cells so blocking forfeits nothing. SHORT stays 0. >=17 needed to cover 08-03.
)
VWAP_SHORT = dict(
    bar_min=5, entry_band_k=2.0, n_bars_outside=3, swing_lookback=20,
    r_multiple=1.0, anchor_hour_utc=0,
    active_windows_et=[(0, 360), (570, 720), (720, 870), (1140, 1440)],
)

SESSION_OPEN_H, SESSION_OPEN_M = 9, 30
EOD_FLATTEN_H, EOD_FLATTEN_M = 15, 20
GAP_FLATTEN_H, GAP_FLATTEN_M = 15, 55
DAILY_LOSS_LIMIT_USD = -2000   # 1 MNQ on ~$150K account; per-trade cap 60% = $1200

BAR_STALL_SEC = 180
IBC_COMMAND_PORT = 0   # gateway is IBC-managed but watchdog should ALERT not restart

STATE_FILE = "/root/v9/v9_live_heartbeat.json"
TRADE_LOG_FILE = "/root/v9/v9_live_trade_log.json"
LOG_FILE = "/root/v9/v9_live_trading.log"


def safety_check() -> None:
    """FAIL-CLOSED real-money guard."""
    assert LIVE_TRADING_CONFIRMED is True, (
        "LIVE_TRADING_CONFIRMED is False — refusing to run."
    )
    assert LIVE_ACCOUNT and LIVE_ACCOUNT.upper().startswith("U"), (
        f"LIVE_ACCOUNT must be your real account. Got {LIVE_ACCOUNT!r}."
    )
    # User's gateway runs LIVE on port 7497 (non-standard but valid).
    LIVE_PORTS = (4001, 7496, 7497)
    assert IB_PORT in LIVE_PORTS, (
        f"IB_PORT must be a LIVE port {LIVE_PORTS}. Got {IB_PORT}."
    )
    assert CLIENT_ID not in (5, 21), (
        "clientId 5=HSI bot, 21=FX engine — use a distinct id (e.g. 25)."
    )
