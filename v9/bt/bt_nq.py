"""bt_nq.py — NQ futures backtest harness for the v9 VWAP sleeves + A1 TLB + B1 ORB.

LIVE/BACKTEST PARITY: this file does NOT reimplement any signal logic. It imports the
exact live objects:
  - VwapState, compute_regime, DailyRegime, is_in_window, session_for_utc_bar
        <- /root/v9/v9_strategy_lib.py            (driven live by v9_live_trade.py)
  - TLBLive, FiveMinAggregator
        <- /root/nq_options/v9_execution.py       (driven live by a1_tlb_0dte.py)
  - Bar, ORState, update_or, finalize_or, update_be, or_close_time
        <- /root/nq_options/orb_strategy_lib.py   (driven live by b1_orb_0dte.py)

Exit modelling mirrors each live bot:
  - VWAP_*  : server-side OCA stop/target on the FUTURE -> intrabar (1-min high/low),
              STOP wins ties; plus the 15:55-16:59 ET gap-flatten (v9_live_trade).
  - TLB_LONG: A1 exits the option on the NQ 5-min *close* (close<=stop / close>=target)
              or the 15:20 ET EOD flatten. Entry windows [(600,715),(895,920)] (A1).
  - ORB     : B1 intrabar low/high vs cur_sl with BE arming, EOD flatten 15:55, no target.

Writes trades_<sleeve>.csv + trades_all.csv under /root/v9/bt/.
"""
from __future__ import annotations
import sys
import importlib.util
from pathlib import Path
from datetime import time as dtime, timedelta

import numpy as np
import pandas as pd
import pytz

ET = pytz.timezone("America/New_York")
BT_DIR = Path("/root/v9/bt")
NQ_DIR = Path("/root/v9/data_1m/NQ")
POINT_USD = 2.0   # MNQ = $2/point (per task); stats reported per 1 contract


# ---------------------------------------------------------------------------
# Import the EXACT live modules from their file paths (avoid name collisions)
# ---------------------------------------------------------------------------
def _load(mod_name, path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = m   # required for dataclass introspection on py3.14
    spec.loader.exec_module(m)
    return m

vsl = _load("v9_strategy_lib_live", "/root/v9/v9_strategy_lib.py")
vex = _load("v9_execution_a1", "/root/nq_options/v9_execution.py")
orb = _load("orb_strategy_lib_live", "/root/nq_options/orb_strategy_lib.py")

VwapState = vsl.VwapState
compute_regime = vsl.compute_regime
is_in_window = vsl.is_in_window
Bar = orb.Bar
ORState = orb.ORState
update_or = orb.update_or
finalize_or = orb.finalize_or
update_be = orb.update_be
or_close_time = orb.or_close_time
TLBLive = vex.TLBLive
FiveMinAggregator = vex.FiveMinAggregator

# holiday gate (best-effort; falls back to no gating if import fails)
try:
    sys.path.insert(0, "/root/v9")
    import us_market_holidays as USH
    def no_trade(d):
        try:
            return bool(USH.no_trade_reason(d))
        except Exception:
            return False
except Exception:
    def no_trade(d):
        return False


# ---------------------------------------------------------------------------
# STEP 2 — Robust loader (mirrors src.engine.load_csv + live concat/dedupe)
# ---------------------------------------------------------------------------
def load_csv(path):
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next((c for c in ("time", "timestamp", "date", "datetime") if c in df.columns), None)
    if tcol is None:
        raise ValueError(f"No time column in {path}")
    if pd.api.types.is_numeric_dtype(df[tcol]):
        df[tcol] = pd.to_datetime(df[tcol], utc=True, unit="s", errors="coerce")
    else:
        df[tcol] = pd.to_datetime(df[tcol], utc=True, errors="coerce")
    df = df.dropna(subset=[tcol]).set_index(tcol).sort_index()
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return df[["open", "high", "low", "close", "volume"]].astype(
        {"open": float, "high": float, "low": float, "close": float, "volume": float})


def load_all():
    files = sorted(NQ_DIR.glob("*.csv"))
    if not files:
        raise FileNotFoundError(NQ_DIR)
    df = pd.concat([load_csv(f) for f in files]).sort_index(kind="mergesort")  # 2026-09-14: STABLE sort -- the default is not stable, so among duplicate timestamps keep="last" did NOT mean the newest file; an older seam could shadow a corrected re-pull
    if df.index.tz is None:
        df = df.tz_localize("UTC")
    df = df[~df.index.duplicated(keep="last")]   # newer seams override
    df = df.sort_index()
    return df


# ---------------------------------------------------------------------------
# STEP 3a — daily closes + per-ET-date regime (lagged, no look-ahead)
# ---------------------------------------------------------------------------
def build_daily_and_regime(df, sma_window=200, mom_window=20):
    etd = df.index.tz_convert(ET)
    tmp = pd.DataFrame({"close": df["close"].values, "etd": etd.date})
    daily = tmp.groupby("etd")["close"].last()
    daily.index = pd.to_datetime(list(daily.index))
    daily = daily.sort_index()

    regime_by_date = {}   # ET date (datetime.date) -> DailyRegime
    dates = list(daily.index)
    for i, d in enumerate(dates):
        prior = daily.iloc[:i]                       # strictly before d -> no look-ahead
        if len(prior) < sma_window + 5:
            continue
        try:
            reg = compute_regime(prior, sma_window=sma_window, mom_window=mom_window)
            regime_by_date[d.date()] = reg
        except Exception:
            continue
    return daily, regime_by_date


# ---------------------------------------------------------------------------
# STEP 3b — generate 5-min bars once (single FiveMinAggregator over 1-min bars)
# ---------------------------------------------------------------------------
def build_5min(df):
    agg = FiveMinAggregator(5)
    out = []
    idx = df.index
    o = df["open"].values; h = df["high"].values
    l = df["low"].values; c = df["close"].values; v = df["volume"].values
    for i in range(len(df)):
        done = agg.add(idx[i], o[i], h[i], l[i], c[i], v[i])
        if done is not None:
            out.append(done)   # (bucket_ts, o, h, l, c, v)
    return out   # list of tuples, bucket_ts is tz-aware UTC


# ---------------------------------------------------------------------------
# Trade simulators
# ---------------------------------------------------------------------------
def _mk_trade(sleeve, direction, e_utc, e_px, x_utc, x_px, reason, risk):
    sgn = 1.0 if direction == "long" else -1.0
    pts = sgn * (x_px - e_px)
    R = pts / risk if risk else 0.0
    return dict(
        sleeve=sleeve, direction=direction,
        entry_utc=e_utc, entry_et=e_utc.tz_convert(ET),
        entry_px=round(e_px, 2),
        exit_utc=x_utc, exit_et=(x_utc.tz_convert(ET) if x_utc is not None else None),
        exit_px=round(x_px, 2), exit_reason=reason,
        pnl_points=round(pts, 2), pnl_usd=round(pts * POINT_USD, 2),
        R=round(R, 3),
    )


def sim_vwap(df, sig, entry_utc):
    """Intrabar (1-min) stop/target on the future, STOP wins ties, + 15:55 gap flatten."""
    direction = sig["side"].lower()
    entry = sig["entry_price"]; stop = sig["stop_price"]; target = sig["target_price"]
    risk = abs(entry - stop)
    sub = df.loc[df.index >= entry_utc]
    hi = sub["high"].values; lo = sub["low"].values; cl = sub["close"].values
    ts = sub.index
    for k in range(len(sub)):
        et_t = ts[k].tz_convert(ET).time()
        et_min = et_t.hour * 60 + et_t.minute
        if direction == "long":
            hit_stop = lo[k] <= stop
            hit_tgt = hi[k] >= target
        else:
            hit_stop = hi[k] >= stop
            hit_tgt = lo[k] <= target
        if hit_stop:           # STOP wins ties (conservative)
            return _mk_trade("VWAP", direction, entry_utc, entry, ts[k], stop, "STOP", risk)
        if hit_tgt:
            return _mk_trade("VWAP", direction, entry_utc, entry, ts[k], target, "TARGET", risk)
        # gap flatten window 15:55-16:59 ET -> flatten at bar close
        if (et_min >= 15 * 60 + 55) and (et_t.hour < 17):
            return _mk_trade("VWAP", direction, entry_utc, entry, ts[k], cl[k], "GAP_FLATTEN", risk)
    # ran out of data
    return _mk_trade("VWAP", direction, entry_utc, entry, ts[-1], cl[-1], "DATA_END", risk)


def sim_tlb(bars5, start_j, sig, entry_utc):
    """A1 semantics: 5-min CLOSE stop/target, or 15:20 ET EOD flatten."""
    entry = sig["entry_price"]; stop = sig["stop_price"]; target = sig["target_price"]
    risk = abs(entry - stop)
    for j in range(start_j + 1, len(bars5)):
        bts, bo, bh, bl, bc, bv = bars5[j]
        et_t = bts.tz_convert(ET).time()
        hm = (et_t.hour, et_t.minute)
        x_utc = bts + pd.Timedelta(minutes=5)
        if bc <= stop:
            return _mk_trade("TLB_LONG", "long", entry_utc, entry, x_utc, bc, "STOP", risk)
        if bc >= target:
            return _mk_trade("TLB_LONG", "long", entry_utc, entry, x_utc, bc, "TARGET", risk)
        if hm >= (15, 20):
            return _mk_trade("TLB_LONG", "long", entry_utc, entry, x_utc, bc, "EOD", risk)
    bts, bo, bh, bl, bc, bv = bars5[-1]
    return _mk_trade("TLB_LONG", "long", entry_utc, entry, bts + pd.Timedelta(minutes=5), bc, "DATA_END", risk)


# ---------------------------------------------------------------------------
# STEP 3 driver
# ---------------------------------------------------------------------------
VWAP_LONG = dict(entry_band_k=2.0, n_bars_outside=5, swing_lookback=20, anchor_hour_utc=0,
                 active_windows_et=[(0, 360), (570, 720), (720, 870), (1140, 1440)])
VWAP_SHORT = dict(entry_band_k=2.0, n_bars_outside=3, swing_lookback=20, anchor_hour_utc=0,
                  active_windows_et=[(0, 360), (570, 720), (720, 870), (1140, 1440)])
TLB_CFG = dict(pivot_k=3, r_multiple=1.0, entry_windows_et=[(600, 715), (895, 920)])
TLB_PT_VALUE = 2.0
TLB_MAX_PREMIUM_USD = 3000.0
ORB_CFG = dict(or_minutes=30, narrow_threshold_pct=0.40, be_narrow=0.7, be_wide=0.5,
               last_entry=(15, 30), eod=(15, 55))


def run_vwap_tlb(df, bars5, regime_by_date):
    vlong = VwapState(side="LONG", entry_band_k=VWAP_LONG["entry_band_k"],
                      n_bars_outside=VWAP_LONG["n_bars_outside"],
                      swing_lookback=VWAP_LONG["swing_lookback"])
    vshort = VwapState(side="SHORT", entry_band_k=VWAP_SHORT["entry_band_k"],
                       n_bars_outside=VWAP_SHORT["n_bars_outside"],
                       swing_lookback=VWAP_SHORT["swing_lookback"])
    tlb = TLBLive(k=TLB_CFG["pivot_k"], r_multiple=TLB_CFG["r_multiple"])

    trades = {"VWAP_LONG_R3": [], "VWAP_SHORT_R1": [], "TLB_LONG": []}
    busy_until = {"VWAP_LONG_R3": None, "VWAP_SHORT_R1": None, "TLB_LONG": None}
    signals = []   # raw signal log for parity window

    for j, (bts, bo, bh, bl, bc, bv) in enumerate(bars5):
        et_t = bts.tz_convert(ET).time()
        et_date = bts.tz_convert(ET).date()
        reg = regime_by_date.get(et_date)
        active = reg.active_sleeves if reg else []

        s_long = vlong.on_bar(bts.to_pydatetime(), bh, bl, bc, bv,
                              anchor_hour_utc=VWAP_LONG["anchor_hour_utc"])
        s_short = vshort.on_bar(bts.to_pydatetime(), bh, bl, bc, bv,
                                anchor_hour_utc=VWAP_SHORT["anchor_hour_utc"])
        s_tlb = tlb.add_bar(bh, bl, bc)

        entry_utc = bts + pd.Timedelta(minutes=5)
        holiday = no_trade(et_date)

        # ---- VWAP_LONG_R3 ----
        if s_long:
            ok = ("VWAP_LONG_R3" in active and not holiday
                  and is_in_window(et_t, VWAP_LONG["active_windows_et"])
                  and (busy_until["VWAP_LONG_R3"] is None or entry_utc >= busy_until["VWAP_LONG_R3"]))
            signals.append((entry_utc, "VWAP_LONG_R3", "long", s_long["entry_price"],
                            s_long["target_price"], reg.cell if reg else "NONE", ok))
            if ok:
                tr = sim_vwap(df, s_long, entry_utc)
                tr["sleeve"] = "VWAP_LONG_R3"
                trades["VWAP_LONG_R3"].append(tr)
                busy_until["VWAP_LONG_R3"] = tr["exit_utc"]

        # ---- VWAP_SHORT_R1 ----
        if s_short:
            ok = ("VWAP_SHORT_R1" in active and not holiday
                  and is_in_window(et_t, VWAP_SHORT["active_windows_et"])
                  and (busy_until["VWAP_SHORT_R1"] is None or entry_utc >= busy_until["VWAP_SHORT_R1"]))
            signals.append((entry_utc, "VWAP_SHORT_R1", "short", s_short["entry_price"],
                            s_short["target_price"], reg.cell if reg else "NONE", ok))
            if ok:
                tr = sim_vwap(df, s_short, entry_utc)
                tr["sleeve"] = "VWAP_SHORT_R1"
                trades["VWAP_SHORT_R1"].append(tr)
                busy_until["VWAP_SHORT_R1"] = tr["exit_utc"]

        # ---- TLB_LONG (A1) ----
        if s_tlb:
            premium = (s_tlb["entry_price"] - s_tlb["stop_price"]) * TLB_PT_VALUE
            gate = (reg is not None and reg.cell in ("BOTH_BULL", "ZONE_B"))
            ok = (gate and not holiday
                  and is_in_window(et_t, TLB_CFG["entry_windows_et"])
                  and premium <= TLB_MAX_PREMIUM_USD
                  and (busy_until["TLB_LONG"] is None or entry_utc >= busy_until["TLB_LONG"]))
            signals.append((entry_utc, "TLB_LONG", "long", s_tlb["entry_price"],
                            s_tlb["target_price"], reg.cell if reg else "NONE", ok))
            if ok:
                tr = sim_tlb(bars5, j, s_tlb, entry_utc)
                trades["TLB_LONG"].append(tr)
                busy_until["TLB_LONG"] = tr["exit_utc"]

    return trades, signals


def run_orb(df):
    """B1 ORB over RTH (09:30-16:00 ET) 1-min bars. No regime gate. No target."""
    et_idx = df.index.tz_convert(ET)
    mask = np.array([(9 * 60 + 30) <= (t.hour * 60 + t.minute) < (16 * 60) and t.weekday() < 5
                     for t in et_idx])
    rth = df[mask]
    rth_et = rth.index.tz_convert(ET)

    or_open = dtime(9, 30)
    or_close = or_close_time(9, 30, ORB_CFG["or_minutes"])
    last_entry = ORB_CFG["last_entry"]
    eod = ORB_CFG["eod"]

    trades = []
    # group by ET date
    dates = pd.Index(rth_et.date)
    o = rth["open"].values; h = rth["high"].values; l = rth["low"].values; c = rth["close"].values
    ts_utc = rth.index

    cur = None
    day_rows = []
    def flush(day_rows):
        if not day_rows:
            return
        idxs = day_rows
        d0 = rth_et[idxs[0]].date()
        if no_trade(d0):
            return
        s = ORState()
        # build OR
        for k in idxs:
            et_t = rth_et[k].time()
            if or_open <= et_t < or_close:
                update_or(s, Bar(ts_utc[k], o[k], h[k], l[k], c[k]))
        if s.or_hi is None:
            return
        finalize_or(s, ORB_CFG["narrow_threshold_pct"])
        # find entry
        entry_k = None
        for k in idxs:
            et_t = rth_et[k].time()
            if not (or_close <= et_t):
                continue
            if (et_t.hour, et_t.minute) >= last_entry:
                break
            if h[k] >= s.or_hi:
                s.side, s.entry, s.init_sl, s.cur_sl = "long", s.or_hi, s.or_lo, s.or_lo
                entry_k = k; break
            if l[k] <= s.or_lo:
                s.side, s.entry, s.init_sl, s.cur_sl = "short", s.or_lo, s.or_hi, s.or_hi
                entry_k = k; break
        if entry_k is None:
            return
        risk = abs(s.entry - s.init_sl)
        # walk forward from next bar
        for k in idxs:
            if k <= entry_k:
                continue
            et_t = rth_et[k].time()
            bar = Bar(ts_utc[k], o[k], h[k], l[k], c[k])
            update_be(s, bar, ORB_CFG["be_narrow"], ORB_CFG["be_wide"])
            hit = (l[k] <= s.cur_sl) if s.side == "long" else (h[k] >= s.cur_sl)
            is_eod = (et_t.hour, et_t.minute) >= eod
            if hit:
                px = s.cur_sl
                trades.append(_mk_trade("ORB", s.side, ts_utc[entry_k], s.entry, ts_utc[k], px,
                                        "STOP/BE", risk))
                return
            if is_eod:
                trades.append(_mk_trade("ORB", s.side, ts_utc[entry_k], s.entry, ts_utc[k], c[k],
                                        "EOD", risk))
                return
        # never exited within data
        lastk = idxs[-1]
        trades.append(_mk_trade("ORB", s.side, ts_utc[entry_k], s.entry, ts_utc[lastk], c[lastk],
                                "DATA_END", risk))

    for k in range(len(rth)):
        d = dates[k]
        if cur is None:
            cur = d
        if d != cur:
            flush(day_rows); day_rows = []; cur = d
        day_rows.append(k)
    flush(day_rows)
    return trades


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def write_csv(trades, path):
    if not trades:
        pd.DataFrame().to_csv(path, index=False)
        return
    rows = []
    for t in trades:
        rows.append({
            "sleeve": t["sleeve"], "direction": t["direction"],
            "entry_time_et": t["entry_et"].strftime("%Y-%m-%d %H:%M"),
            "entry_time_utc": t["entry_utc"].strftime("%Y-%m-%d %H:%M"),
            "entry_px": t["entry_px"],
            "exit_time_et": t["exit_et"].strftime("%Y-%m-%d %H:%M") if t["exit_et"] is not None else "",
            "exit_px": t["exit_px"], "exit_reason": t["exit_reason"],
            "pnl_points": t["pnl_points"], "pnl_usd": t["pnl_usd"], "R": t["R"],
        })
    pd.DataFrame(rows).to_csv(path, index=False)


def stats(trades):
    if not trades:
        return None
    pts = np.array([t["pnl_points"] for t in trades])
    usd = np.array([t["pnl_usd"] for t in trades])
    wins = pts[pts > 0]; losses = pts[pts <= 0]
    gp = usd[usd > 0].sum(); gl = -usd[usd < 0].sum()
    # max drawdown on cumulative $
    cum = np.cumsum(usd); peak = np.maximum.accumulate(cum)
    mdd = (cum - peak).min() if len(cum) else 0.0
    return dict(
        n=len(trades),
        win_rate=len(wins) / len(trades) * 100,
        avg_win_pts=wins.mean() if len(wins) else 0.0,
        avg_loss_pts=losses.mean() if len(losses) else 0.0,
        pf=(gp / gl) if gl > 0 else float("inf"),
        exp_pts=pts.mean(), exp_usd=usd.mean(),
        total_usd=usd.sum(), total_pts=pts.sum(),
        max_dd_usd=mdd,
    )


def main():
    print("Loading data...")
    df = load_all()
    print(f"  bars={len(df):,}  range={df.index[0]} -> {df.index[-1]}")
    # gap check
    deltas = df.index.to_series().diff().dropna()
    big = deltas[deltas > pd.Timedelta(hours=6)]
    print(f"  gaps >6h: {len(big)}")
    for t, d in list(big.items())[:12]:
        print(f"    gap of {d} ending at {t}")

    daily, regime_by_date = build_daily_and_regime(df)
    print(f"  daily closes={len(daily)}  regime-covered days={len(regime_by_date)}")
    if regime_by_date:
        rd = sorted(regime_by_date.keys())
        print(f"  regime available {rd[0]} -> {rd[-1]}")

    print("Building 5-min bars...")
    bars5 = build_5min(df)
    print(f"  5-min bars={len(bars5):,}")

    print("Running VWAP + TLB...")
    vt_trades, signals = run_vwap_tlb(df, bars5, regime_by_date)
    print("Running ORB...")
    orb_trades = run_orb(df)

    all_trades = {
        "VWAP_LONG_R3": vt_trades["VWAP_LONG_R3"],
        "VWAP_SHORT_R1": vt_trades["VWAP_SHORT_R1"],
        "TLB_LONG": vt_trades["TLB_LONG"],
        "ORB": orb_trades,
    }
    combined = []
    for name, trs in all_trades.items():
        write_csv(trs, BT_DIR / f"trades_{name}.csv")
        combined.extend(trs)
    combined.sort(key=lambda t: t["entry_utc"])
    write_csv(combined, BT_DIR / "trades_all.csv")

    # ---------------- PARITY WINDOW ----------------
    print("\n" + "=" * 78)
    print("STEP 4 — PARITY: generated signals 2026-06-22 -> 2026-07-10 (UTC)")
    print("=" * 78)
    lo = pd.Timestamp("2026-06-22", tz="UTC"); hi = pd.Timestamp("2026-07-11", tz="UTC")
    print(f"{'entry_utc':16} {'entry_et':16} {'sleeve':14} {'dir':5} {'entry':>9} {'target':>9} {'cell':9} taken")
    for (eutc, sleeve, d, epx, tpx, cell, ok) in signals:
        if lo <= eutc < hi:
            print(f"{eutc.strftime('%m-%d %H:%M'):16} "
                  f"{eutc.tz_convert(ET).strftime('%m-%d %H:%M'):16} "
                  f"{sleeve:14} {d:5} {epx:9.2f} {tpx:9.2f} {cell:9} {'YES' if ok else 'no'}")
    # ORB signals in window
    print("\nORB trades in parity window:")
    for t in orb_trades:
        if lo <= t["entry_utc"] < hi:
            print(f"  {t['entry_utc'].strftime('%m-%d %H:%M')} UTC / "
                  f"{t['entry_et'].strftime('%m-%d %H:%M')} ET  {t['direction']:5} "
                  f"entry={t['entry_px']} exit={t['exit_px']} ({t['exit_reason']}) "
                  f"pts={t['pnl_points']}")

    # ---------------- STATS ----------------
    print("\n" + "=" * 78)
    print("STEP 5 — PER-SLEEVE STATS (per 1 contract, $2/pt)")
    print("=" * 78)
    for name, trs in all_trades.items():
        st = stats(trs)
        print(f"\n### {name}  (n={0 if st is None else st['n']})")
        if st is None:
            print("  no trades"); continue
        print(f"  win_rate={st['win_rate']:.1f}%  avg_win={st['avg_win_pts']:.1f}pt  "
              f"avg_loss={st['avg_loss_pts']:.1f}pt  PF={st['pf']:.2f}")
        print(f"  expectancy={st['exp_pts']:.2f}pt / ${st['exp_usd']:.2f}  "
              f"total=${st['total_usd']:,.0f} ({st['total_pts']:.0f}pt)  maxDD=${st['max_dd_usd']:,.0f}")
        # by half-year
        by = {}
        for t in trs:
            y = t["entry_et"].year
            hh = "H1" if t["entry_et"].month <= 6 else "H2"
            key = f"{y}{hh}"
            by.setdefault(key, [0, 0.0])
            by[key][0] += 1; by[key][1] += t["pnl_usd"]
        print("  by half-year:  " + "  ".join(
            f"{k}: n={v[0]} ${v[1]:,.0f}" for k, v in sorted(by.items())))
        # last 10
        print("  last 10 trades:")
        for t in trs[-10:]:
            print(f"    {t['entry_et'].strftime('%Y-%m-%d %H:%M')} ET {t['direction']:5} "
                  f"entry={t['entry_px']:.2f} -> {t['exit_et'].strftime('%m-%d %H:%M')} "
                  f"exit={t['exit_px']:.2f} {t['exit_reason']:11} "
                  f"pts={t['pnl_points']:+.1f} R={t['R']:+.2f}")

    print("\nDONE. CSVs in /root/v9/bt/")


if __name__ == "__main__":
    main()
