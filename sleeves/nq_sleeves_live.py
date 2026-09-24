"""nq_sleeves_live.py — Unified execution for CCI AM + SNAP + TBS on NQ/MNQ.

Single IB connection, three strategy sleeves, shared data feeds.
Connects via TWS tunnel (port 7497) or Gateway (port 4001).
FTMO: writes a signal file consumed by nq_ftmo_bridge.py.

Usage:
  NQ_TARGET=TWS python nq_sleeves_live.py        # TWS tunnel (default)
  NQ_TARGET=GW  python nq_sleeves_live.py        # Gateway
"""
from __future__ import annotations
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
from ib_async import IB, Future, ContFuture, MarketOrder, StopOrder, LimitOrder

import nq_sleeves_config as C
from v9_tlb_bear_short import TlbBearShortLive, TBS_NAME, SESSIONS as TBS_SESSIONS
from v9_tlb_bear_short import session_at as tbs_session_at, session_end_minutes as tbs_session_end

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
HERE = Path(__file__).resolve().parent

ib = IB()
_my_orders: set = set()


# ================================================================ utilities
def now_et() -> datetime:
    return datetime.now(ET)

def etm(d) -> int:
    return d.hour * 60 + d.minute

def log(msg, level="INFO"):
    print(f"{now_et():%Y-%m-%d %H:%M:%S} ET [{level}] {msg}", flush=True)

def notify(msg):
    tok, chat = C.tg_token()
    if tok is None:
        return
    try:
        tag = f"[{C.TARGET}] "
        data = urllib.parse.urlencode({"chat_id": chat, "text": f"NQ_SLEEVES {tag}{msg}"}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=10).read()
    except Exception as e:
        log(f"telegram failed ({e!r})", "WARN")

def _save(path, obj):
    try:
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:
        log(f"write {Path(path).name} failed ({e!r})", "WARN")

def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default

def _finite(x):
    try:
        return float(x) if x is not None and np.isfinite(x) else None
    except (TypeError, ValueError):
        return None

def tick_round(px: float, direction: str) -> float:
    t = C.NQ_TICK
    n = px / t
    k = math.floor(n + 1e-9) if direction == "long" else math.ceil(n - 1e-9)
    return round(k * t, 4)


# ================================================================ state
class SleeveState:
    def __init__(self, name: str):
        self.name = name
        self.in_position = False
        self.side: Optional[str] = None
        self.entry_price = 0.0
        self.stop_price = 0.0
        self.target_price = 0.0
        self.entry_time: Optional[str] = None
        self.entry_session: Optional[str] = None  # TBS: which session the entry is in
        self.qty = 0
        self.cum_pnl = 0.0
        self.trades_today = 0
        self.stop_order = None   # live Trade object, not persisted
        self.target_order = None # live Trade object, not persisted
        self.oca_group: Optional[str] = None  # persisted for stop reconciliation
        self.has_bracket = False  # True if stop+target were placed (False for SNAP)
        self.killed = False       # [H6] True after kill line hit — vetoes new entries

    def to_dict(self):
        return dict(name=self.name, in_position=self.in_position, side=self.side,
                    entry_price=self.entry_price, stop_price=self.stop_price,
                    target_price=self.target_price, entry_time=self.entry_time,
                    entry_session=self.entry_session,
                    qty=self.qty, cum_pnl=self.cum_pnl, trades_today=self.trades_today,
                    oca_group=self.oca_group, has_bracket=self.has_bracket,
                    killed=self.killed)

    @classmethod
    def from_dict(cls, d):
        s = cls(d["name"])
        for k in ("in_position", "side", "entry_price", "stop_price", "target_price",
                  "entry_time", "entry_session", "qty", "cum_pnl", "trades_today",
                  "oca_group", "has_bracket", "killed"):
            if k in d:
                setattr(s, k, d[k])
        return s


class BotState:
    def __init__(self):
        self.sleeves = {"CCI": SleeveState("CCI"), "SNAP": SleeveState("SNAP"), "TBS": SleeveState("TBS")}
        self.date_et = ""
        self.snap_last_decision_day: Optional[str] = None
        self.snap_preclose_day: Optional[str] = None
        self.snap_held_conid: Optional[int] = None
        self.tbs_done: set = set()

    def save(self):
        d = dict(date_et=self.date_et,
                 snap_last_decision_day=self.snap_last_decision_day,
                 snap_preclose_day=self.snap_preclose_day,
                 snap_held_conid=self.snap_held_conid,
                 tbs_done=list(self.tbs_done),
                 sleeves={k: v.to_dict() for k, v in self.sleeves.items()})
        _save(HERE / C.STATE_FILE, d)

    @classmethod
    def load(cls):
        d = _load(HERE / C.STATE_FILE, {})
        s = cls()
        if d:
            s.date_et = d.get("date_et", "")
            s.snap_last_decision_day = d.get("snap_last_decision_day")
            s.snap_preclose_day = d.get("snap_preclose_day")
            s.snap_held_conid = d.get("snap_held_conid")
            s.tbs_done = set(d.get("tbs_done", []))
            for k, v in d.get("sleeves", {}).items():
                if k in s.sleeves:
                    s.sleeves[k] = SleeveState.from_dict(v)
        return s


def ledger_append(row):
    try:
        row["ts_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
        with open(HERE / C.LEDGER_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except Exception as e:
        log(f"ledger write failed: {e!r}", "ERROR")

def heartbeat(phase, extra=None):
    hb = dict(ts_utc=datetime.now(UTC).isoformat(timespec="seconds"), phase=phase,
              target=C.TARGET, sleeves={k: v.to_dict() for k, v in state.sleeves.items()})
    if extra:
        hb.update(extra)
    _save(HERE / C.HEARTBEAT_FILE, hb)


# ================================================================ IB connection
def connect():
    cfg = C.ib_conn()
    while True:
        try:
            ib.connect(cfg["host"], cfg["port"], clientId=cfg["client_id"], timeout=20)
            if cfg.get("acct_pin"):
                accts = ib.managedAccounts()
                if cfg["acct_pin"] not in accts:
                    log(f"ACCOUNT PIN FAILED: {accts}", "ERROR")
                    ib.disconnect()
                    time.sleep(300)
                    continue
            log(f"Connected to {C.TARGET} ({cfg['host']}:{cfg['port']} cid={cfg['client_id']})")
            return
        except Exception as e:
            log(f"connect failed ({e!r}); retry in 60s", "WARN")
            time.sleep(60)

def ensure_connected():
    if not ib.isConnected():
        log("IB disconnected, reconnecting...", "WARN")
        try:
            ib.disconnect()
        except Exception:
            pass
        _contracts.clear()  # [H3] invalidate contract cache on reconnect
        connect()


# ================================================================ contracts
_contracts = {}

def get_contract():
    if "trade" not in _contracts:
        cf = ContFuture(C.TRADE_SYM, C.TRADE_EXCHANGE)
        ib.qualifyContracts(cf)
        ib.sleep(0.5)
        fut = Future(C.TRADE_SYM, cf.lastTradeDateOrContractMonth, C.TRADE_EXCHANGE)
        ib.qualifyContracts(fut)
        _contracts["trade"] = fut
        _contracts["data"] = cf
        log(f"Contract: {fut.localSymbol} (expiry {fut.lastTradeDateOrContractMonth})")
    return _contracts["trade"], _contracts["data"]


# ================================================================ data feeds
bars_1m: pd.DataFrame = pd.DataFrame()
bars_5m: pd.DataFrame = pd.DataFrame()
daily_closes: pd.Series = pd.Series(dtype=float)
_last_fed_tbs_ts: Optional[pd.Timestamp] = None  # [C6] track last bar fed to TBS detector
_full_loaded = False  # True after the initial full history load

def _ib_bars_to_df(bars_list):
    df = pd.DataFrame([(b.date, b.open, b.high, b.low, b.close, b.volume) for b in bars_list],
                      columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.set_index("time").sort_index()

def fetch_full_history():
    """Full history load — called once at startup and at session boundaries."""
    global bars_1m, bars_5m, daily_closes, _full_loaded
    _, cf = get_contract()

    b1 = ib.reqHistoricalData(cf, "", C.HIST_1M_DUR, "1 min",
                               "TRADES", useRTH=False, formatDate=2, timeout=120)
    if b1:
        df1 = _ib_bars_to_df(b1)
        if len(df1) > 1:
            df1 = df1.iloc[:-1]
        bars_1m = df1
        log(f"1m bars (full): {len(df1)} ({df1.index[0]} .. {df1.index[-1]})")

    b5 = ib.reqHistoricalData(cf, "", C.HIST_5M_DUR, "5 mins",
                               "TRADES", useRTH=False, formatDate=2, timeout=300)
    if b5:
        df5 = _ib_bars_to_df(b5)
        if len(df5) > 1:
            df5 = df5.iloc[:-1]
        bars_5m = df5
        log(f"5m bars (full): {len(df5)} ({df5.index[0]} .. {df5.index[-1]})")

    bd = ib.reqHistoricalData(cf, "", C.HIST_DAILY_DUR, "1 day",
                               "TRADES", useRTH=True, formatDate=2, timeout=120)
    if bd:
        dc = pd.Series({pd.Timestamp(b.date).date() if hasattr(pd.Timestamp(b.date), 'date')
                        else pd.Timestamp(b.date): float(b.close) for b in bd})
        dc = dc.sort_index().dropna()
        today = now_et().date()
        dc = dc[dc.index < today]
        daily_closes = dc
        log(f"Daily closes: {len(dc)} ({dc.index[0]} .. {dc.index[-1]})")

    _full_loaded = True

def refresh_bars():
    """[B1 FIX] Lightweight refresh — pull only recent bars and merge."""
    global bars_1m, bars_5m
    _, cf = get_contract()

    # 1-min: pull last 15 minutes and merge
    b1 = ib.reqHistoricalData(cf, "", "900 S", "1 min",
                               "TRADES", useRTH=False, formatDate=2, timeout=30)
    if b1:
        fresh = _ib_bars_to_df(b1)
        if len(fresh) > 1:
            fresh = fresh.iloc[:-1]
        if not bars_1m.empty and not fresh.empty:
            cutoff = fresh.index[0]
            bars_1m = pd.concat([bars_1m[bars_1m.index < cutoff], fresh]).sort_index()
        elif not fresh.empty:
            bars_1m = fresh

    # 5-min: pull last 2 hours and merge
    b5 = ib.reqHistoricalData(cf, "", "7200 S", "5 mins",
                               "TRADES", useRTH=False, formatDate=2, timeout=30)
    if b5:
        fresh5 = _ib_bars_to_df(b5)
        if len(fresh5) > 1:
            fresh5 = fresh5.iloc[:-1]
        if not bars_5m.empty and not fresh5.empty:
            cutoff5 = fresh5.index[0]
            bars_5m = pd.concat([bars_5m[bars_5m.index < cutoff5], fresh5]).sort_index()
        elif not fresh5.empty:
            bars_5m = fresh5


# ================================================================ CCI indicators
def session_vwap(df_1m):
    """Running VWAP anchored at 18:00 ET (NQ session open)."""
    et_idx = df_1m.index.tz_convert(ET)
    anchor = np.array((et_idx.hour * 60 + et_idx.minute) == (18 * 60))
    hlc3 = ((df_1m["high"] + df_1m["low"] + df_1m["close"]) / 3.0).values
    vol = df_1m["volume"].replace(0, 1).values.copy()
    cum_pv = (hlc3 * vol).copy()
    cum_v = vol.copy()
    for i in range(len(cum_pv)):
        if anchor[i]:
            cum_pv[i] = hlc3[i] * vol[i]
            cum_v[i] = vol[i]
        elif i > 0:
            cum_pv[i] += cum_pv[i - 1]
            cum_v[i] += cum_v[i - 1]
    vwap = cum_pv / np.where(cum_v > 0, cum_v, 1)
    return pd.Series(vwap, index=df_1m.index)

def ema50_5m_on_1m(df_1m, df_5m):
    """5-min EMA(50) stamped at the 5-min CLOSE, forward-filled to 1-min.
    [C2 FIX] Shift 5-min index forward by 5 minutes so the EMA value is only
    available after the 5-min bar closes (no look-ahead)."""
    ema = df_5m["close"].ewm(span=50, adjust=False).mean()
    avail = pd.Series(ema.values, index=ema.index + pd.Timedelta(minutes=5))
    slow = avail.reindex(df_1m.index, method="ffill")
    return slow

def cci_signal(df_1m, df_5m, bar_idx: int) -> Optional[dict]:
    """Check CCI entry at bar_idx. Returns signal dict or None."""
    if bar_idx < max(C.CCI_N + 1, C.CCI_SLOPE_LB + 1, C.CCI_STOP_LB + 1):
        return None

    et_t = df_1m.index[bar_idx].tz_convert(ET)
    et_min = et_t.hour * 60 + et_t.minute
    if not (C.CCI_WIN_ET[0] <= et_min < C.CCI_WIN_ET[1]):
        return None

    # [C1 FIX] CCI(20) on TYPICAL PRICE (h+l+c)/3, not close
    highs = df_1m["high"].iloc[max(0, bar_idx - C.CCI_N):bar_idx + 1].values
    lows = df_1m["low"].iloc[max(0, bar_idx - C.CCI_N):bar_idx + 1].values
    closes_w = df_1m["close"].iloc[max(0, bar_idx - C.CCI_N):bar_idx + 1].values
    if len(closes_w) < C.CCI_N + 1:
        return None
    tp_all = (highs + lows + closes_w) / 3.0

    win = tp_all[-C.CCI_N:]
    tp_now = tp_all[-1]
    mean_tp = win.mean()
    mad = np.mean(np.abs(win - mean_tp))
    if mad <= 0:
        return None
    cci_now = (tp_now - mean_tp) / (0.015 * mad)

    win_prev = tp_all[-(C.CCI_N + 1):-1]
    tp_prev = tp_all[-2]
    mean_prev = win_prev.mean()
    mad_prev = np.mean(np.abs(win_prev - mean_prev))
    cci_prev = (tp_prev - mean_prev) / (0.015 * mad_prev) if mad_prev > 0 else 0

    # CCI cross: prev <= -100 and now > -100 => long
    if cci_prev <= -100 and cci_now > -100:
        side = "long"
    elif cci_prev >= 100 and cci_now < 100:
        side = "short"
    else:
        return None

    # VWAP and EMA50(5m)
    vw = session_vwap(df_1m)
    slow = ema50_5m_on_1m(df_1m, df_5m)

    vw_val = _finite(vw.iloc[bar_idx])
    slow_val = _finite(slow.iloc[bar_idx])
    close_val = _finite(df_1m["close"].iloc[bar_idx])

    if vw_val is None or slow_val is None or close_val is None:
        return None

    # Separation gate
    sep = (slow_val - vw_val) / vw_val if vw_val != 0 else 0
    if side == "long" and sep < C.CCI_THR:
        return None
    if side == "short" and sep > -C.CCI_THR:
        return None

    # Close beyond SLOW
    if side == "long" and close_val <= slow_val:
        return None
    if side == "short" and close_val >= slow_val:
        return None

    # Slope check
    if bar_idx >= C.CCI_SLOPE_LB:
        s_now = _finite(slow.iloc[bar_idx])
        s_prev = _finite(slow.iloc[bar_idx - C.CCI_SLOPE_LB])
        if s_now is None or s_prev is None:
            return None
        slope = s_now - s_prev
        if side == "long" and slope <= 0:
            return None
        if side == "short" and slope >= 0:
            return None

    # [B2 FIX] Stop = swing low/high WITH katr * ATR(14,5m) cushion — matches reference
    window = df_1m.iloc[max(0, bar_idx - C.CCI_STOP_LB):bar_idx]

    # ATR from 5-min bars (EWM span=14 like reference)
    if len(df_5m) >= 14:
        h5 = df_5m["high"].iloc[-14:]
        l5 = df_5m["low"].iloc[-14:]
        c5 = df_5m["close"].iloc[-14:]
        c5_prev = df_5m["close"].iloc[-15:-1]
        tr5 = pd.concat([h5 - l5, (h5 - c5_prev).abs(), (l5 - c5_prev).abs()], axis=1).max(axis=1)
        atr5 = float(tr5.ewm(span=14, adjust=False).mean().iloc[-1])
    else:
        atr5 = 10.0  # fallback

    if side == "long":
        stop = float(window["low"].min()) - C.CCI_KATR * atr5
    else:
        stop = float(window["high"].max()) + C.CCI_KATR * atr5

    risk = abs(close_val - stop)
    if not (np.isfinite(risk) and risk > 0):
        return None

    # [B4 FIX] No target — CCI exits by stop or EOD only (matches backtest)
    return dict(side=side, entry_price=close_val, stop_price=stop, target_price=0,
                cci=cci_now, sep=sep, atr=atr5, risk=risk)


# ================================================================ SNAP logic
def snap_z_score(closes_series) -> Optional[float]:
    if len(closes_series) < C.SNAP_LOOKBACK:
        return None
    win = closes_series.iloc[-C.SNAP_LOOKBACK:].values.astype(float)
    mean = win.mean()
    sd = np.std(win, ddof=0)  # population std — matches the research formula
    return None if sd <= 0 else (win[-1] - mean) / sd

def snap_decision() -> Optional[str]:
    if daily_closes.empty or len(daily_closes) < C.SNAP_LOOKBACK:
        return None
    z = snap_z_score(daily_closes)
    if z is None:
        return None
    return "long" if z < C.SNAP_THR else "flat"


# ================================================================ regime
def compute_regime() -> Optional[str]:
    if daily_closes.empty or len(daily_closes) < C.REGIME_SMA + 5:
        return None
    today = now_et().date()
    dc = daily_closes[daily_closes.index < today] if hasattr(daily_closes.index[0], 'date') else daily_closes
    if len(dc) < C.REGIME_SMA + 5:
        return None
    prev_close = _finite(dc.iloc[-1])
    if prev_close is None:
        return None
    sma = dc.iloc[-C.REGIME_SMA:].mean()
    ret = (dc.iloc[-1] / dc.iloc[-(C.REGIME_MOM + 1)]) - 1.0
    if not (np.isfinite(sma) and np.isfinite(ret)):
        return None
    s200 = prev_close > sma
    r20 = ret > 0
    if s200 and r20:     return "BOTH_BULL"
    if s200 and not r20: return "ZONE_A"
    if not s200 and r20: return "ZONE_B"
    return "BOTH_BEAR"


# ================================================================ order management
def place_entry(contract, side: str, qty: int) -> Optional[object]:
    action = "BUY" if side == "long" else "SELL"
    o = MarketOrder(action, qty)
    o.tif = "DAY"
    o.outsideRth = True
    trade = ib.placeOrder(contract, o)
    _my_orders.add(trade.order.orderId)
    return trade

def place_stop(contract, side: str, qty: int, stop_px: float, oca: str) -> Optional[object]:
    action = "SELL" if side == "long" else "BUY"
    stop_px = tick_round(stop_px, side)
    o = StopOrder(action, qty, stop_px)
    o.tif = "GTC"; o.outsideRth = True
    o.ocaGroup = oca; o.ocaType = 1
    trade = ib.placeOrder(contract, o)
    _my_orders.add(trade.order.orderId)
    return trade

def place_target(contract, side: str, qty: int, target_px: float, oca: str) -> Optional[object]:
    action = "SELL" if side == "long" else "BUY"
    target_px = tick_round(target_px, side)
    o = LimitOrder(action, qty, target_px)
    o.tif = "GTC"; o.outsideRth = True
    o.ocaGroup = oca; o.ocaType = 1
    trade = ib.placeOrder(contract, o)
    _my_orders.add(trade.order.orderId)
    return trade

def cancel_order(trade):
    try:
        if trade is not None:
            ib.cancelOrder(trade.order)
    except Exception:
        pass

def wait_fill(trade, timeout_s=60) -> Optional[float]:
    for _ in range(timeout_s * 2):
        ib.sleep(0.5)
        st = trade.orderStatus.status
        if st == "Filled":
            px = trade.orderStatus.avgFillPrice
            if px is not None and np.isfinite(px) and px > 0:
                return float(px)
            break
    return None

def market_flatten(contract, side: str, qty: int):
    action = "SELL" if side == "long" else "BUY"
    o = MarketOrder(action, qty)
    o.tif = "DAY"; o.outsideRth = True
    trade = ib.placeOrder(contract, o)
    _my_orders.add(trade.order.orderId)  # [H5] track flatten orders
    return trade


# ================================================================ FTMO signal output
def emit_ftmo_signal(sleeve: str, action: str, side: str, price: float, stop: float,
                     target: float, risk_pts: float):
    sig = _load(HERE / C.FTMO_SIGNAL_FILE, {"sleeves": {}})
    sig["ts_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    sig["sleeves"][sleeve] = dict(
        action=action, side=side, price=price, stop=stop, target=target,
        risk_pts=risk_pts, lots=round(C.FTMO_RISK_USD / max(risk_pts, 0.01), 2),
        ts=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    _save(HERE / C.FTMO_SIGNAL_FILE, sig)


# ================================================================ sleeve entry/exit
def enter_sleeve(sleeve_name: str, sig: dict, entry_session: Optional[str] = None):
    sl = state.sleeves[sleeve_name]
    if sl.in_position:
        return
    if sl.killed:
        log(f"[{sleeve_name}] KILLED — entry vetoed", "WARN")
        return

    fut, _ = get_contract()
    qty = C.MNQ_QTY
    side = sig["side"]
    has_stop = sig["stop_price"] != 0
    has_target = sig["target_price"] != 0
    has_bracket = has_stop and has_target  # TBS: stop+target OCA pair
    # CCI: stop only, no target (EOD exit). SNAP: neither (signal exit).

    log(f"[{sleeve_name}] SIGNAL {side.upper()} entry={sig['entry_price']:.2f} "
        f"stop={sig['stop_price']:.2f} target={sig['target_price']:.2f}")

    risk_pts = abs(sig["entry_price"] - sig["stop_price"]) if has_stop else 0
    emit_ftmo_signal(sleeve_name, "ENTER", side, sig["entry_price"],
                     sig["stop_price"], sig["target_price"], risk_pts)

    if C.TARGET == "FTMO":
        sl.in_position = True
        sl.side = side
        sl.entry_price = sig["entry_price"]
        sl.stop_price = sig["stop_price"]
        sl.target_price = sig["target_price"]
        sl.entry_time = now_et().isoformat()
        sl.entry_session = entry_session
        sl.qty = 0
        sl.has_bracket = has_bracket
        notify(f"{sleeve_name} {side.upper()} signal @ {sig['entry_price']:.2f} (FTMO)")
        state.save()
        return

    # IB entry
    trade = place_entry(fut, side, qty)
    fill = wait_fill(trade, timeout_s=90)

    if fill is None:
        log(f"[{sleeve_name}] entry not filled ({trade.orderStatus.status}) — abandoning", "ERROR")
        notify(f"{sleeve_name} entry FAILED: {trade.orderStatus.status}")
        cancel_order(trade)
        return

    sl.in_position = True
    sl.side = side
    sl.entry_price = fill
    sl.stop_price = sig["stop_price"]
    sl.target_price = sig["target_price"]
    sl.entry_time = now_et().isoformat()
    sl.entry_session = entry_session
    sl.qty = qty
    sl.has_bracket = has_bracket

    # Order placement: TBS=bracket (OCA stop+target), CCI=stop only, SNAP=none
    if has_bracket:
        oca = f"nqs_{sleeve_name}_{trade.order.orderId}"
        st = place_stop(fut, side, qty, sig["stop_price"], oca)
        tg = place_target(fut, side, qty, sig["target_price"], oca)
        sl.stop_order = st
        sl.target_order = tg
        sl.oca_group = oca
    elif has_stop:
        # CCI: stop only, no target — exits by stop or EOD
        st = place_stop(fut, side, qty, sig["stop_price"], "")
        sl.stop_order = st
        sl.target_order = None
        sl.oca_group = None
    else:
        # SNAP: no orders — signal exit
        sl.stop_order = None
        sl.target_order = None
        sl.oca_group = None

    sl.trades_today += 1
    state.save()  # [C5] persist state immediately after orders placed

    risk_usd = abs(fill - sig["stop_price"]) * C.USD_PER_PT * qty if has_bracket else 0
    ledger_append(dict(sleeve=sleeve_name, ev="ENTRY", side=side, qty=qty, px=fill,
                       stop=sig["stop_price"], target=sig["target_price"], risk_usd=round(risk_usd, 2)))
    notify(f"{sleeve_name} {side.upper()} ENTRY @ {fill:.2f}" +
           (f" (stop {sig['stop_price']:.2f}, target {sig['target_price']:.2f}, risk ${risk_usd:.0f})"
            if has_bracket else " (no bracket — signal exit)"))
    log(f"[{sleeve_name}] {side.upper()} ENTRY filled @ {fill:.2f}")


def exit_sleeve(sleeve_name: str, reason: str, exit_px: Optional[float] = None,
                already_flat: bool = False):
    """Exit a sleeve position.
    already_flat=True means IB already closed it (stop/target fill) — skip market_flatten."""
    sl = state.sleeves[sleeve_name]
    if not sl.in_position:
        return

    fut, _ = get_contract()

    # Cancel outstanding orders BEFORE nulling the references
    cancel_order(sl.stop_order)
    cancel_order(sl.target_order)
    sl.stop_order = None
    sl.target_order = None

    if not already_flat and C.TARGET != "FTMO" and sl.qty > 0:
        trade = market_flatten(fut, sl.side, sl.qty)
        fill = wait_fill(trade, timeout_s=60)
        if fill is not None:
            exit_px = fill
        elif exit_px is None:
            log(f"[{sleeve_name}] flatten not confirmed — keeping position tracked", "ERROR")
            return  # [H1 FIX] Don't clear state if flatten failed

    emit_ftmo_signal(sleeve_name, "EXIT", sl.side or "flat", exit_px or 0, 0, 0, 0)

    if exit_px is not None and sl.entry_price > 0:
        if sl.side == "long":
            pnl_pts = exit_px - sl.entry_price
        else:
            pnl_pts = sl.entry_price - exit_px
        pnl_usd = pnl_pts * C.USD_PER_PT * max(sl.qty, 1)
        sl.cum_pnl += pnl_usd

        ledger_append(dict(sleeve=sleeve_name, ev="EXIT", reason=reason, side=sl.side,
                           qty=sl.qty, entry=sl.entry_price, exit=exit_px,
                           pnl_pts=round(pnl_pts, 2), pnl_usd=round(pnl_usd, 2),
                           cum_pnl=round(sl.cum_pnl, 2)))
        notify(f"{sleeve_name} EXIT ({reason}) @ {exit_px:.2f} P&L ${pnl_usd:+.0f} "
               f"(cum ${sl.cum_pnl:+.0f})")
        log(f"[{sleeve_name}] EXIT ({reason}) @ {exit_px:.2f} P&L ${pnl_usd:+.0f}")

        # [H6 FIX] Kill/review line — killed state vetoes future entries
        if sl.cum_pnl <= C.KILL_LINES.get(sleeve_name, -999999):
            sl.killed = True
            msg = f"KILL LINE HIT: {sleeve_name} cum P&L ${sl.cum_pnl:,.0f} <= ${C.KILL_LINES[sleeve_name]:,.0f} — SLEEVE KILLED"
            log(msg, "ERROR")
            notify(msg)
        elif sl.cum_pnl <= C.REVIEW_LINES.get(sleeve_name, -999999):
            msg = f"REVIEW LINE: {sleeve_name} cum P&L ${sl.cum_pnl:,.0f} <= ${C.REVIEW_LINES[sleeve_name]:,.0f}"
            log(msg, "WARN")
            notify(msg)

    sl.in_position = False
    sl.side = None
    sl.entry_price = 0.0
    sl.stop_price = 0.0
    sl.target_price = 0.0
    sl.entry_time = None
    sl.entry_session = None
    sl.qty = 0
    sl.has_bracket = False
    sl.oca_group = None
    state.save()


# ================================================================ fill event handlers
def check_stop_target_fills():
    for name, sl in state.sleeves.items():
        if not sl.in_position:
            continue
        # Check stop fill (works for both bracket and stop-only)
        if sl.stop_order and sl.stop_order.orderStatus.status == "Filled":
            px = _finite(sl.stop_order.orderStatus.avgFillPrice)
            if px is not None and px > 0:
                cancel_order(sl.target_order)  # no-op if None
                sl.stop_order = None
                sl.target_order = None
                exit_sleeve(name, "STOP", px, already_flat=True)
        # Check target fill (bracket only — TBS)
        elif sl.target_order and sl.target_order.orderStatus.status == "Filled":
            px = _finite(sl.target_order.orderStatus.avgFillPrice)
            if px is not None and px > 0:
                cancel_order(sl.stop_order)
                sl.target_order = None
                sl.stop_order = None
                exit_sleeve(name, "TARGET", px, already_flat=True)


# ================================================================ CCI cycle
def cci_cycle():
    if not C.CCI_ENABLED:
        return
    sl = state.sleeves["CCI"]

    ne = now_et()
    et_min = etm(ne)

    # EOD flatten
    if sl.in_position and et_min >= C.CCI_EOD_ET:
        exit_sleeve("CCI", "EOD")
        return

    if sl.in_position or sl.trades_today >= 1:
        return

    if not (C.CCI_WIN_ET[0] <= et_min < C.CCI_WIN_ET[1]):
        return

    if bars_1m.empty or bars_5m.empty:
        return

    sig = cci_signal(bars_1m, bars_5m, len(bars_1m) - 1)
    if sig is not None:
        enter_sleeve("CCI", sig)


# ================================================================ SNAP cycle
def snap_cycle():
    if not C.SNAP_ENABLED:
        return
    sl = state.sleeves["SNAP"]
    ne = now_et()
    today_str = ne.strftime("%Y-%m-%d")

    # [H4 FIX] Skip weekends (Sat=5, Sun=6) — CME equity futures are closed
    if ne.weekday() >= 5:
        return

    is_decision_time = False
    is_preclose = False
    # Daily decision at 00:01 ET (weekdays only)
    if ne.hour == 0 and ne.minute <= 5 and state.snap_last_decision_day != today_str:
        is_decision_time = True
    # Friday pre-close: separate check (daily decision may already have run today)
    if (ne.weekday() == 4 and ne.hour == C.SNAP_PRECLOSE_ET[0]
            and ne.minute >= C.SNAP_PRECLOSE_ET[1]
            and getattr(state, "snap_preclose_day", None) != today_str):
        is_decision_time = True
        is_preclose = True

    if not is_decision_time:
        return

    decision = snap_decision()
    if decision is None:
        return

    if is_preclose:
        state.snap_preclose_day = today_str
    else:
        state.snap_last_decision_day = today_str

    if decision == "long" and not sl.in_position:
        # [C4 FIX] SNAP has NO stop/target — signal exit only
        sig = dict(side="long", entry_price=float(daily_closes.iloc[-1]),
                   stop_price=0, target_price=0)
        log(f"[SNAP] z < {C.SNAP_THR} -> LONG signal")
        enter_sleeve("SNAP", sig)
    elif decision == "flat" and sl.in_position:
        log(f"[SNAP] z >= {C.SNAP_THR} -> FLAT signal")
        exit_sleeve("SNAP", "SIGNAL_FLAT")

    state.save()


# ================================================================ TBS cycle
tbs_detector = TlbBearShortLive(k=3, atr_len=14, line_min=15)
tbs_warmed = False

def tbs_warmup():
    global tbs_warmed, _last_fed_tbs_ts
    if tbs_warmed or bars_1m.empty:
        return
    n = 0
    for ts, row in bars_1m.iterrows():
        tbs_detector.on_1min(ts, row["open"], row["high"], row["low"], row["close"])
        _last_fed_tbs_ts = ts
        n += 1
    tbs_warmed = True
    log(f"[TBS] warm-up: {n} 1-min bars, {len(tbs_detector.l15)} 15-min buckets, "
        f"{len(tbs_detector.pivots)} pivots, {tbs_detector.n_lines} lines")

def tbs_feed_new_bars():
    """[C6 FIX] Feed only NEW bars to the detector, never re-feed or skip."""
    global _last_fed_tbs_ts
    if bars_1m.empty:
        return None
    last_ev = None
    for ts, row in bars_1m.iterrows():
        if _last_fed_tbs_ts is not None and ts <= _last_fed_tbs_ts:
            continue
        ev = tbs_detector.on_1min(ts, row["open"], row["high"], row["low"], row["close"])
        _last_fed_tbs_ts = ts
        if ev is not None:
            last_ev = ev
    return last_ev

def tbs_cycle():
    if not C.TBS_ENABLED:
        return
    sl = state.sleeves["TBS"]

    # [H2 FIX] Session-end flatten BEFORE regime gate — regime can change while in position
    if sl.in_position and sl.entry_session:
        ne = now_et()
        end_min = tbs_session_end(sl.entry_session)
        if etm(ne) >= end_min:
            exit_sleeve("TBS", "SESSION_END")
            return

    regime = compute_regime()
    if regime != "BOTH_BEAR":
        return

    if sl.in_position or sl.trades_today >= 3:
        return

    # [C6 FIX] Feed new bars and get the latest touch event
    ev = tbs_feed_new_bars()

    if ev is None or ev.get("session") is None or ev.get("atr") is None:
        return

    sess = ev["session"]
    if sess in state.tbs_done:
        return
    if ev["stop_price"] is None or ev["target_price"] is None:
        return

    state.tbs_done.add(sess)
    log(f"[TBS] TOUCH {sess} {ev['et']:%H:%M:%S} ET: line {ev.get('line', 0):.2f}, "
        f"entry {ev['entry_price']:.2f}, stop {ev['stop_price']:.2f}, target {ev['target_price']:.2f}")
    enter_sleeve("TBS", dict(side="short", entry_price=ev["entry_price"],
                              stop_price=ev["stop_price"], target_price=ev["target_price"]),
                 entry_session=sess)  # [H1] pass entry session for flatten tracking


# ================================================================ main loop
def new_day_reset():
    today_str = now_et().strftime("%Y-%m-%d")
    if state.date_et != today_str:
        log(f"New day: {today_str}")
        state.date_et = today_str
        for sl in state.sleeves.values():
            sl.trades_today = 0
        state.tbs_done = set()
        state.save()

def disabled():
    return (HERE / C.DISABLED_FLAG).exists()

state = BotState()

def main():
    global state

    state = BotState.load()
    log(f"NQ Sleeves Bot starting — target={C.TARGET}, sleeves: "
        f"CCI={'ON' if C.CCI_ENABLED else 'OFF'} "
        f"SNAP={'ON' if C.SNAP_ENABLED else 'OFF'} "
        f"TBS={'ON' if C.TBS_ENABLED else 'OFF'}")

    for name, sl in state.sleeves.items():
        if sl.in_position:
            log(f"[{name}] RESUMING {sl.side} from {sl.entry_price:.2f} (cum P&L ${sl.cum_pnl:+.0f})")

    if C.TARGET != "FTMO":
        connect()

    if C.TARGET != "FTMO":
        fetch_full_history()
        tbs_warmup()

    notify("Bot started" + (" (FTMO signal-only)" if C.TARGET == "FTMO" else ""))

    last_refresh = time.monotonic()
    last_daily = time.monotonic()
    REFRESH_SEC = 60       # lightweight 1m+5m refresh
    DAILY_SEC = 3600       # full daily reload (once per hour)

    while True:
        try:
            is_disabled = disabled()

            if C.TARGET != "FTMO":
                ensure_connected()
                ib.sleep(1)
            else:
                time.sleep(5)

            # [H10 FIX] Always check fills even when disabled — protect open positions
            if C.TARGET != "FTMO":
                check_stop_target_fills()

            if is_disabled:
                heartbeat("disabled")
                ib.sleep(55) if ib.isConnected() else time.sleep(55)
                continue

            new_day_reset()

            # [B1 FIX] Lightweight refresh every 60s, full daily reload hourly
            now_mono = time.monotonic()
            if C.TARGET != "FTMO":
                if now_mono - last_refresh >= REFRESH_SEC:
                    try:
                        refresh_bars()
                        if not tbs_warmed:
                            tbs_warmup()
                    except Exception as e:
                        log(f"bar refresh failed: {e!r}", "WARN")
                    last_refresh = now_mono
                if now_mono - last_daily >= DAILY_SEC:
                    try:
                        fetch_full_history()
                        tbs_warmup()
                    except Exception as e:
                        log(f"daily reload failed: {e!r}", "WARN")
                    last_daily = now_mono

            cci_cycle()
            snap_cycle()
            tbs_cycle()

            heartbeat("running")

        except KeyboardInterrupt:
            log("Shutting down (KeyboardInterrupt)")
            break
        except Exception as e:
            log(f"main loop error: {e!r}", "ERROR")
            heartbeat("error", {"error": str(e)})
            time.sleep(10)

    if ib.isConnected():
        ib.disconnect()
    state.save()
    log("Bot stopped.")


if __name__ == "__main__":
    main()
