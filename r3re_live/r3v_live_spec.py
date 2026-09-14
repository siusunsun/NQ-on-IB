"""r3v_live_spec.py — signal spec for the LIVE R3 VWAP RE-ENTRY executor.

REUSE, not reimplement:
  * R3 sleeve      -> /root/v9/bt/bt_nq.py (build_daily_and_regime + build_5min + run_vwap_tlb)
                      which import the EXACT live VwapState from /root/v9/v9_strategy_lib.py.
  * overlay math   -> overlay_fires() below is the SAME arming/exit logic as the validated
                      backtest /root/v9/bt/_r3v_overlay.py (and /root/r3re_paper/r3re_spec.py).
  * LIVE R3 config -> the LONG VwapState is patched with post_halt_blackout_bars=24 to match
                      v9_config_live_user.VWAP_LONG. This is the ONE difference vs r3re_spec.py:
                      it drives off the LIVE R3 stop-out set, not the blackout-less one.

Two entry points:
  * overlay_fires(df, regime)  -> full simulated fires (for baseline seeding + reconciliation +
                                  parity self-test; IDENTICAL to _r3v_overlay.py).
  * arming_state(df, regime)   -> the CURRENTLY-ARMED re-entry at the last completed 5-min bar:
                                  the live RESTING BUY-STOP trigger (runmax), the prospective
                                  protective stop (20-bar swing low signal->now), and the tp
                                  context. This is what the executor turns into real orders.

READ-ONLY w.r.t. all live code/state. Pure pandas/numpy; imports nothing that can place an order.
"""
from __future__ import annotations
import sys, importlib.util
import math
import numpy as np, pandas as pd, pytz

ET = pytz.timezone("America/New_York")
sys.path.insert(0, "/root/v9/bt")
sys.path.insert(0, "/root/v9")

POINT_USD = 2.0            # 1 MNQ
K = 2.0                    # entry_band_k (matches VWAP_LONG sleeve)
FLAT = 15 * 60 + 55        # 15:55 ET flatten
# 2026-08-27 (5.6-yr audit): cap the re-entry ARMING window at 48 completed 5-min bars (4h)
# after the R3 stop bar. Uncapped (window ran to 15:55) = n173/$3,254/PF 1.60; 48-bar cap =
# n161/$4,170/PF 2.03, better in BOTH eras (2021-23 and 2024-26). A reclaim that takes longer
# than 4h is a different trade from the fast failure-recovery this sleeve is built on.
MAX_WAIT_BARS = 48
VWAP_MAX_STOP_PTS = 200.0  # live backstop (v9_config_live_user)
POST_HALT_BLACKOUT_BARS = 24  # LIVE VWAP_LONG config


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s); sys.modules[n] = m; s.loader.exec_module(m); return m


bt = _load("bt_nq", "/root/v9/bt/bt_nq.py")
vsl = _load("vsl_r3v", "/root/v9/v9_strategy_lib.py")
usv = vsl.update_session_vwap
sess_key = vsl.session_for_utc_bar

# ---- LIVE-CONFIG PATCH: inject post_halt_blackout_bars=24 on the LONG VwapState ----
_Orig = bt.VwapState
def _PatchedVwapState(*a, **k):
    if k.get("side") == "LONG":
        k.setdefault("post_halt_blackout_bars", POST_HALT_BLACKOUT_BARS)
    return _Orig(*a, **k)
bt.VwapState = _PatchedVwapState


def build_regime(df: pd.DataFrame):
    _, regime_by_date = bt.build_daily_and_regime(df)
    return regime_by_date


def _r3_stops(df, bars5, regime_by_date):
    """LIVE-config R3 sleeve, filtered by VWAP_MAX_STOP_PTS; return its trades."""
    vt, _ = bt.run_vwap_tlb(df, bars5, regime_by_date)
    out = []
    for t in vt["VWAP_LONG_R3"]:
        rr = (abs(t["pnl_points"]) / abs(t["R"])) if t.get("R") else None
        if rr is not None and rr > VWAP_MAX_STOP_PTS:
            continue
        out.append(t)
    return out


def _per_bar_vwap(bars5):
    n = len(bars5)
    bts = [b[0] for b in bars5]
    h5 = np.array([b[2] for b in bars5]); l5 = np.array([b[3] for b in bars5])
    c5 = np.array([b[4] for b in bars5]); v5 = np.array([b[5] for b in bars5])
    vwap5 = np.full(n, np.nan); sd5 = np.full(n, np.nan)
    cur = None; cumv = cumvp = cumsq = 0.0
    for i in range(n):
        sk = sess_key(bts[i].to_pydatetime(), 0)
        if sk != cur:
            cur = sk; cumv = cumvp = cumsq = 0.0
        cumv, cumvp, cumsq, vw, sd = usv(cumv, cumvp, cumsq, h5[i], l5[i], c5[i], v5[i])
        vwap5[i] = vw; sd5[i] = sd
    return bts, vwap5, vwap5 + K * sd5


def _floor5(ts):
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.floor("5min")


def _et_min(ts):
    e = ts.tz_convert(ET); return e.hour * 60 + e.minute


# ============================================================ full simulator (parity)
def overlay_fires(df: pd.DataFrame, regime_by_date) -> list[dict]:
    """VERBATIM overlay (matches _r3v_overlay.py PURE-SPEC). Used for baseline seed + parity."""
    bars5 = bt.build_5min(df)
    if len(bars5) < 30:
        return []
    r3 = _r3_stops(df, bars5, regime_by_date)
    bts, vwap5, upper5 = _per_bar_vwap(bars5)
    n = len(bars5)
    o5 = np.array([b[1] for b in bars5]); h5 = np.array([b[2] for b in bars5]); l5 = np.array([b[3] for b in bars5])
    bucket_idx = {bts[i]: i for i in range(n)}
    idx1 = df.index
    o1 = df["open"].values; h1 = df["high"].values; l1 = df["low"].values; c1 = df["close"].values

    fires = []
    for tr in r3:
        if tr["exit_reason"] != "STOP":
            continue
        si = bucket_idx.get(_floor5(tr["entry_utc"] - pd.Timedelta(minutes=5)))
        ki = bucket_idx.get(_floor5(tr["exit_utc"]))
        if si is None or ki is None:
            continue
        sess = sess_key(bts[ki].to_pydatetime(), 0)
        entry = None
        j = ki + 2
        while j < n and sess_key(bts[j].to_pydatetime(), 0) == sess and _et_min(bts[j]) < FLAT:
            runmax = h5[ki:j].max()
            if h5[j] > runmax:
                fill = max(o5[j], runmax)
                stop_lvl = l5[si:j].min()
                lag_vw = vwap5[j - 1]
                if np.isnan(lag_vw):
                    j += 1; continue
                tp_kind = "VWAP" if fill < lag_vw else "UPPER"
                riskpt = fill - stop_lvl
                if riskpt <= 0:
                    entry = "BADRISK"; break
                entry = dict(j=j, fill=fill, stop=stop_lvl, tp_kind=tp_kind, riskpt=riskpt, sess=sess, r3=tr)
                break
            j += 1
        if entry in (None, "BADRISK"):
            continue
        j = entry["j"]; fill = entry["fill"]; stop_lvl = entry["stop"]; kind = entry["tp_kind"]
        start = bts[j] + pd.Timedelta(minutes=5)
        m0 = np.searchsorted(idx1.values, np.datetime64(start.tz_convert("UTC").tz_localize(None)))
        ex_px = ex_reason = ex_ts = None
        m = m0
        while m < len(idx1):
            t = idx1[m]
            if sess_key(t.to_pydatetime(), 0) != entry["sess"]:
                ex_px = c1[m - 1] if m > m0 else fill; ex_reason = "SESS_END"; ex_ts = idx1[m - 1]; break
            if _et_min(t) >= FLAT:
                ex_px = c1[m]; ex_reason = "FLATTEN"; ex_ts = t; break
            if l1[m] <= stop_lvl:
                ex_px = stop_lvl; ex_reason = "STOP"; ex_ts = t; break
            b = _floor5(t); bi = bucket_idx.get(b - pd.Timedelta(minutes=5))
            lvl = (vwap5[bi] if kind == "VWAP" else upper5[bi]) if bi is not None else np.nan
            if not np.isnan(lvl) and lvl > fill and h1[m] >= lvl:
                ex_px = lvl; ex_reason = "TARGET"; ex_ts = t; break
            m += 1
        if ex_px is None:
            ex_px = c1[-1]; ex_reason = "DATA_END"; ex_ts = idx1[-1]
        pts = ex_px - fill; R = pts / entry["riskpt"]
        reentry_utc = bts[j] + pd.Timedelta(minutes=5)
        fires.append(dict(
            r3_stop_time=pd.Timestamp(entry["r3"]["exit_utc"]).isoformat(),
            reentry_time=pd.Timestamp(reentry_utc).isoformat(),
            entry=round(float(fill), 2), stop=round(float(stop_lvl), 2),
            tp_type=("VWAP" if kind == "VWAP" else "band"),
            exit_time=pd.Timestamp(ex_ts).isoformat() if ex_ts is not None else None,
            exit=round(float(ex_px), 2), exit_reason=ex_reason,
            R=round(float(R), 3), net_usd=round(float(pts) * POINT_USD, 2),
            riskpt=round(float(entry["riskpt"]), 2), pts=round(float(pts), 2)))
    return fires


# ============================================================ LIVE arming detector
# ---- 2026-09-14 (Codex #11): every price sent to IB must sit on the 0.25 tick -----------------
TICK = 0.25


def _round_tick(x):
    return round(round(float(x) / TICK) * TICK, 2)


def _floor_tick(x):
    """Long SELL-LIMIT target: floor to the tick so it fills at-or-before the backtest's touch."""
    return round(math.floor(float(x) / TICK + 1e-9) * TICK, 2)


def arming_state(df: pd.DataFrame, regime_by_date) -> dict | None:
    """Return the CURRENTLY-ARMED re-entry at the last completed 5-min bar, or None.

    ARMED = an R3 STOP-out whose arming window is still open at the last completed bar
    (same 00:00-UTC session, at/after stop_bar+2, ET < 15:55) and which has NOT yet had a
    completed-bar breakout above its running resistance (i.e. no fill yet). For that state
    the LIVE executor rests a BUY-STOP at:
        buy_stop = max high of completed 5-min bars from the stop bar through 'now'  (= runmax)
    and, when it fills, will attach:
        protective stop = min low of completed 5-min bars from the R3 SIGNAL bar through 'now'
        target          = lag-1 band magnet (session VWAP if fill<VWAP else +2 sigma upper).
    Returns the most-recent armed candidate (R3 serialises via busy_until, so normally <=1).
    """
    bars5 = bt.build_5min(df)
    if len(bars5) < 30:
        return None
    r3 = _r3_stops(df, bars5, regime_by_date)
    bts, vwap5, upper5 = _per_bar_vwap(bars5)
    n = len(bars5)
    o5 = np.array([b[1] for b in bars5]); h5 = np.array([b[2] for b in bars5]); l5 = np.array([b[3] for b in bars5])
    bucket_idx = {bts[i]: i for i in range(n)}
    c = n - 1                                   # last completed 5-min bar
    sess_now = sess_key(bts[c].to_pydatetime(), 0)

    best = None
    for tr in r3:
        if tr["exit_reason"] != "STOP":
            continue
        si = bucket_idx.get(_floor5(tr["entry_utc"] - pd.Timedelta(minutes=5)))
        ki = bucket_idx.get(_floor5(tr["exit_utc"]))
        if si is None or ki is None:
            continue
        sess = sess_key(bts[ki].to_pydatetime(), 0)
        if sess != sess_now:                    # arming window belongs to a past session
            continue
        if c < ki + 1:                          # 2026-09-08 FIX: arm one bar EARLIER.
            continue                            # The spec fills at j = ki+2, so the BUY-STOP must
                                                # already be resting when bar ki+2 opens -- i.e. it
                                                # is placed at the close of ki+1. The old gate
                                                # (c < ki+2) only armed after ki+2 had CLOSED, so the
                                                # order first rested during ki+3 and every ki+2 fill
                                                # (32%% of all fires) was structurally unreachable.
                                                # runmax below = h5[ki:c+1] = max(h[ki],h[ki+1]),
                                                # exactly the spec resistance. Ref: kristov
                                                # v9_reentry.ReentryTracker.trigger_level().
        if c > ki + MAX_WAIT_BARS:              # 2026-08-27: arming window EXPIRED
            continue
        if _et_min(bts[c]) >= FLAT:             # past the 15:55 flatten -> window closed
            continue
        # already filled on a completed bar in (ki+2 .. c)? -> not currently arming (live handles pos)
        filled = False
        for j in range(ki + 2, min(c, ki + MAX_WAIT_BARS) + 1):
            if sess_key(bts[j].to_pydatetime(), 0) != sess or _et_min(bts[j]) >= FLAT:
                break
            if h5[j] > h5[ki:j].max() and not np.isnan(vwap5[j - 1]):
                filled = True; break
        if filled:
            continue
        runmax = float(h5[ki:c + 1].max())      # resting BUY-STOP trigger for the forming bar
        prospective_stop = float(l5[si:c + 1].min())
        lag_vw = float(vwap5[c]); lag_up = float(upper5[c])
        best = dict(
            r3_stop_time=pd.Timestamp(tr["exit_utc"]).isoformat(),
            r3_signal_time=pd.Timestamp(tr["entry_utc"] - pd.Timedelta(minutes=5)).isoformat(),
            session=sess, last_bar_time=pd.Timestamp(bts[c]).isoformat(),
            buy_stop=_round_tick(runmax), prospective_stop=_round_tick(prospective_stop),
            prospective_riskpt=round(runmax - prospective_stop, 2),
            lag_vwap=round(lag_vw, 2), lag_upper=round(lag_up, 2),
            si=si, ki=ki, c=c)
    return best


def bracket_on_fill(df: pd.DataFrame, regime_by_date, fill_px: float,
                    signal_time_iso: str, session: str, force_kind: str | None = None) -> dict:
    """Given an ACTUAL fill price + the armed R3 signal bar, compute the OCA bracket the
    executor should attach: protective stop, tp_kind ('VWAP'/'UPPER'), and the current
    lag-1 target level. Recomputed from completed bars only (no look-ahead).

    force_kind (2026-09-14, Codex #17): keep the KIND chosen at entry and return that kind's
    CURRENT level -- the magnet never switches mid-trade, but it must keep moving. The old caller
    re-derived the kind from the moving VWAP and FROZE the target whenever it differed.
    Prices are snapped to the 0.25 tick (Codex #11): the VWAP magnet is continuous (the paper
    ledger booked an exit at 29168.26) and IB rejects off-tick futures prices."""
    bars5 = bt.build_5min(df)
    bts, vwap5, upper5 = _per_bar_vwap(bars5)
    n = len(bars5); bucket_idx = {bts[i]: i for i in range(n)}
    l5 = np.array([b[3] for b in bars5])
    si = bucket_idx.get(_floor5(pd.Timestamp(signal_time_iso)))
    c = n - 1
    stop_lvl = float(l5[si:c + 1].min()) if si is not None else float("nan")
    lag_vw = float(vwap5[c]); lag_up = float(upper5[c])
    tp_kind = force_kind or ("VWAP" if fill_px < lag_vw else "UPPER")
    target = lag_vw if tp_kind == "VWAP" else lag_up
    stop_t = _round_tick(stop_lvl)
    return dict(stop=stop_t, tp_kind=tp_kind, target=_floor_tick(target),
                riskpt=round(fill_px - stop_t, 2), lag_vwap=round(lag_vw, 2),
                lag_upper=round(lag_up, 2))


def review_tally(fires: list[dict]) -> dict:
    booked = [f for f in fires if f.get("exit_reason") != "DATA_END"]
    n = len(booked)
    if not n:
        return dict(fires=0, expR=0.0, tp_hit_pct=0.0, net_usd=0.0)
    Rs = np.array([f["R"] for f in booked])
    tp = np.mean([f["exit_reason"] == "TARGET" for f in booked])
    return dict(fires=n, expR=round(float(Rs.mean()), 3), tp_hit_pct=round(float(tp) * 100, 1),
                net_usd=round(float(sum(f["net_usd"] for f in booked)), 2))
