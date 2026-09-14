"""v9.1 Paper Trading Bot — Configuration

HARD-PINNED: PAPER_ONLY=True. Connects ONLY to a PAPER port (Gateway 4002/4003 or TWS 7497).
Refuses to start on a live port (Gateway 4001 / TWS 7496).

Instrument: native 1 NQ ($20/pt) — swapped off the 10-MNQ proxy 2026-06-05.
Account migration 2026-06-10: moved from the DU4234078 Gateway (4002) to the DUQ621725
Gateway (4003); account is resolved from managedAccounts() (must start with "DU").
Runs in parallel with the ORB bot (clientId 18) using clientId 19.
"""
from __future__ import annotations

# ---- Safety / IB connection ----
PAPER_ONLY: bool = True            # do not change; the bot refuses to start otherwise
IB_HOST: str = "127.0.0.1"
# Supported PAPER ports (we refuse to start on a LIVE port):
#   - 4002  = IB Gateway PAPER, DU4234078 (the old account)
#   - 4003  = IB Gateway PAPER, DUQ621725 (the new account — migrated here 2026-06-10)
#   - 7497  = TWS PAPER
# REFUSE: 4001 (Gateway LIVE), 7496 (TWS LIVE)
IB_PORT: int = 4003                # DUQ621725 Gateway (paper). 4002 = old DU4234078 Gateway.
CLIENT_ID: int = 19                # ORB bot is 18; v9 uses 19 so they coexist
ACCOUNT_PREFIX: str = "DU"         # IB paper accounts start with "DU"; reject anything else

# ---- Instrument ----
# 1 NQ = $20/pt.  Swapped MNQ->NQ 2026-06-05 so ORB can take MNQ for fixed-fractional
# sizing without netting against v9 on the shared paper account (different contracts).
INSTRUMENT = dict(
    symbol="NQ",
    secType="FUT",
    exchange="CME",
    currency="USD",
    qty=1,                         # 1 NQ = $20/pt (identical notional to the old 10 MNQ); fixed size
    point_value=20.0,              # NQ full multiplier $20/pt
)

# Quarterly roll: trade the next contract this many days before the front's last-trade
# date (CME equity-index volume rolls ~8 days before the 3rd-Friday expiry), and flatten
# any position stranded in the rolled-off contract on the next startup. See contract_roll.py.
ROLL_DAYS_BEFORE_EXPIRY = 7

# ---- v9.1 strategy parameters ----
# Daily regime gate (computed from prior session daily close)
REGIME = dict(
    sma_window=200,                # 200-DMA
    momentum_window=20,            # 20-day return
)
# Regime stability guard (added 2026-06-09): the prior-day close can wobble across data pulls,
# silently flipping the cell near the ret20=0 boundary (observed 2026-06-09: BOTH_BULL@05:00 ->
# ZONE_A@08:48 [bad outlier pull 29409] -> BOTH_BULL@14:11). LOCK the first cell computed per
# prior-day date (cache), WARN + keep-locked on a later restart that would flip it, flag near-boundary.
REGIME_CACHE_FILE = "v9_regime_cache.json"
REGIME_BOUNDARY_EPS = 0.003        # |ret20| < 0.3% => near the r20 boundary; cell is fragile

# TLB-LONG single-trade risk POLICY guard (NOT an edge filter — backtests showed every
# performance-motivated stop cap HURTS; the wide-stop trades are TLB's best bucket).
# Skip a TLB entry whose nominal stop risk on the configured size exceeds this. Rationale:
# one trade must never be able to burn more than ~60% of DAILY_LOSS_LIMIT_USD (-10k) by
# itself. Historical gated max was $5,945 (5y); the 2026-06-09 outlier was $16.8k nominal.
# At NQ~29k, $6k = ~1.0% of price -> expected cost ~$2k/yr of edge (user-accepted premium);
# $8-10k would be ~free. Fixed-$ guard tightens as NQ rises — revisit yearly. TLB-ONLY:
# never apply risk caps/sizing to the VWAP sleeves (tiny stops -> over-leverage trap).
TLB_MAX_TRADE_RISK_USD = 6000

# TLB-LONG sleeve (active in BOTH_BULL and ZONE_B)
TLB = dict(
    bar_min=5,
    pivot_k=3,
    r_multiple=1.0,
    use_24h=True,
    slope_filter=False,
    entry_windows_et=[(600, 720),  # 10:00-12:00 ET
                      (895, 920)],  # 14:55-15:20 ET
)

# VWAP-LONG-R3 sleeve (active in BOTH_BULL and ZONE_A)
VWAP_LONG = dict(
    bar_min=5,
    entry_band_k=2.0,
    n_bars_outside=5,
    swing_lookback=20,
    r_multiple=3.0,
    anchor_hour_utc=0,             # 00:00 UTC daily VWAP anchor
    active_windows_et=[(0,   360), # 00:00-06:00 ET
                       (570, 720), # 09:30-12:00 ET
                       (720, 870), # 12:00-14:30 ET
                       (1140, 1440)],  # 19:00-24:00 ET
)

# VWAP-SHORT-R1 sleeve (active in BOTH_BEAR ONLY — v9.1 uses n=3, not 5)
VWAP_SHORT = dict(
    bar_min=5,
    entry_band_k=2.0,
    n_bars_outside=3,              # v9.1 bear refinement (asymmetric vs LONG's n=5)
    swing_lookback=20,
    r_multiple=1.0,
    anchor_hour_utc=0,
    active_windows_et=[(0,   360),
                       (570, 720),
                       (720, 870),
                       (1140, 1440)],
)

# ---- Risk + session ----
SESSION_OPEN_H, SESSION_OPEN_M = 9, 30      # NQ RTH open ET
EOD_FLATTEN_H, EOD_FLATTEN_M   = 15, 20     # TLB flattens at 15:20 ET (matches its backtest EOD)
GAP_FLATTEN_H, GAP_FLATTEN_M   = 15, 55     # ALL sleeves flatten at the cash close — gap-safe:
                                            # never carry across the 17:00 ET settlement / weekend / roll
DAILY_LOSS_LIMIT_USD = -10000               # halt for the day if cum P&L hits this

# ---- Feed health (bar-stall watchdog) ----
# If no realtime 5-sec bar arrives for this many seconds while CME NQ is open, the bot
# re-subscribes to self-heal a silent feed stall (e.g. a data-farm 2108 that leaves the
# API socket UP but starves the realtime subscription — the 2026-06-09 blind-feed incident
# where the bot stayed "connected"+heartbeating but missed a signal for hours). 180s is a
# safe trigger: NQ is liquid ~23h, so a >3-min zero-bar gap during active hours almost
# always means a real stall. A benign trip is possible in thin overnight/holiday minutes
# (IB only emits a TRADES bar when a trade prints), but the re-subscribe is harmless (no
# orders, no strategy-state reset), and the loop WARNs once / re-subscribes silently while
# it persists / escalates to a single ERROR if the feed stays dark — so a benign trip costs
# ~one WARN and a long real outage gets one loud ERROR, never a flood that masks a stall.
BAR_STALL_SEC = 180
IBC_COMMAND_PORT = 7463   # paper GW IBC command port; bar-stall watchdog self-heals via RECONNECTDATA/RESTART (no 2FA)

# ---- Logging / state files ----
STATE_FILE = "v9_live_heartbeat.json"
TRADE_LOG_FILE = "v9_trade_log.json"
LOG_FILE = "v9_live_trading.log"


def safety_check() -> None:
    """Hard-stop the bot if any guard is violated."""
    assert PAPER_ONLY is True, "PAPER_ONLY guard must be True — refuse to run."
    PAPER_PORTS = (4002, 4003, 7497)  # 4002=DU4234078 GW, 4003=DUQ621725 GW, 7497=TWS — all paper
    LIVE_PORTS  = (4001, 7496)        # Gateway live,  TWS live  ← we refuse
    assert IB_PORT in PAPER_PORTS, (
        f"IB_PORT must be in {PAPER_PORTS} (paper). Got {IB_PORT}. "
        f"Refusing to connect — LIVE ports {LIVE_PORTS} are explicitly rejected."
    )
    assert IB_PORT not in LIVE_PORTS, (
        f"REFUSED: IB_PORT {IB_PORT} is a LIVE port. PAPER ONLY."
    )
    assert CLIENT_ID != 18, (
        "clientId 18 is the ORB bot. Use a different clientId for v9."
    )
