"""dash_lib.py - shared, MEMORY-SAFE helpers for the daily NQ dashboard push.

READ-ONLY with respect to every live bot / data file. Reuses the existing
validated backtest harness (_hist_bt_nq.py + the live strategy libs) - no
strategy logic is reimplemented here beyond the exact copies already validated
in _hist_sleeves.py / _ra_r3re_sweep.py.

MEMORY RULE: load_window() only ever materialises a trailing slice of the
1-min archive.  Never call bt.load_all() from the daily job.
"""
from __future__ import annotations
import os, sys, json, glob, importlib.util, warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
import pytz

ET = pytz.timezone("America/New_York")
BT = "/root/v9/bt/"
NQ_DIR = "/root/v9/data_1m/NQ"
FULL_DIR = "/root/tlbrc_paper/ref/data_full/NQ"   # full archive (one-time base build only)

BASE_JSON   = BT + "dash_monthly_base.json"
DAILY_CSV   = BT + "dash_daily_closes.csv"
INDEX_JSON  = BT + "dash_file_index.json"
LOG_PATH    = BT + "dash_push.log"

CAP        = 22500.0
MNQ        = 2.0
RT         = 5.40                 # $/round-turn/contract (2026-08-31: measured. 1.88 MNQ pts slippage = $3.76 + $1.22 commission. Was 2.04 = commission only, too optimistic.)
QTY        = {"VWAP_LONG_R3": 2, "VWAP_SHORT_R1": 1, "TLB_HYB40": 1, "R3_RE": 1}
VWAP_MAX_STOP_PTS = 200.0
K          = 2.0
R3RE_MAX_WAIT = 48
WINDOW_DAYS   = 120
MIN_FREE_MB = 500   # 2026-09-03: was 900 (5.5x). Job peak is a measured 163MB and it is
                    # memory-capped by design (trailing 120d window, chunked reads); 500MB is >3x
                    # peak and still leaves IB Gateway headroom. The 2026-08-27 OOM was two
                    # PARALLEL multi-GB backtests, not this job.
MAX_STALE_DAYS = 6

REPO   = "ericfongtrading/strategy-dashboard"
GHPATH = "data/nq.json"
BRANCH = "main"
TOKEN_FILE = "/root/.dash_gh_token"


# ------------------------------------------------------------------ logging
def log(msg):
    line = "%s  %s" % (pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M:%SZ"), msg)
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def rss_mb():
    """Peak RSS of this process (VmHWM) in MB."""
    try:
        with open("/proc/self/status") as fh:
            for ln in fh:
                if ln.startswith("VmHWM:"):
                    return int(ln.split()[1]) / 1024.0
    except Exception:
        pass
    return float("nan")


def mem_available_mb():
    with open("/proc/meminfo") as fh:
        for ln in fh:
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) / 1024.0
    return 0.0


# ------------------------------------------------------------------ harness
def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


_HARNESS = {}


def harness():
    """Import _hist_bt_nq + the live strategy lib, with the LIVE-CONFIG patch
    (post_halt_blackout_bars=24 on the LONG VwapState) exactly as _hist_sleeves.py."""
    if _HARNESS:
        return _HARNESS
    sys.path.insert(0, "/root/v9/bt")
    sys.path.insert(0, "/root/v9")
    bt = _load("bt_nq", "/root/v9/bt/_hist_bt_nq.py")
    vsl = _load("vsl", "/root/v9/v9_strategy_lib.py")
    _Orig = bt.VwapState

    def _Patched(*a, **k):
        if k.get("side") == "LONG":
            k.setdefault("post_halt_blackout_bars", 24)
        return _Orig(*a, **k)

    bt.VwapState = _Patched
    _HARNESS.update(bt=bt, vsl=vsl)
    return _HARNESS


# ------------------------------------------------------------------ file index
def _scan_range(path):
    """(min_ts, max_ts, rows) for one csv, read in chunks (time column only)."""
    mn = mx = None
    rows = 0
    for ch in pd.read_csv(path, usecols=lambda c: c.strip().lower() in
                          ("time", "timestamp", "date", "datetime"), chunksize=200000):
        ch.columns = [c.strip().lower() for c in ch.columns]
        tcol = ch.columns[0]
        t = pd.to_datetime(ch[tcol], utc=True, errors="coerce").dropna()
        if not len(t):
            continue
        rows += len(t)
        a, b = t.min(), t.max()
        mn = a if mn is None else min(mn, a)
        mx = b if mx is None else max(mx, b)
    return mn, mx, rows


def file_index(data_dir=NQ_DIR, index_path=INDEX_JSON):
    """Cached {name: [mtime, size, min_iso, max_iso]} so the daily job can skip
    files that lie entirely before the trailing window."""
    try:
        idx = json.load(open(index_path))
    except Exception:
        idx = {}
    changed = False
    for p in sorted(glob.glob(os.path.join(data_dir, "*.csv"))):
        name = os.path.basename(p)
        st = os.stat(p)
        cur = idx.get(name)
        if cur and abs(cur[0] - st.st_mtime) < 1e-6 and cur[1] == st.st_size:
            continue
        mn, mx, rows = _scan_range(p)
        idx[name] = [st.st_mtime, st.st_size,
                     None if mn is None else mn.isoformat(),
                     None if mx is None else mx.isoformat()]
        changed = True
    live = set(os.path.basename(p) for p in glob.glob(os.path.join(data_dir, "*.csv")))
    for k in [k for k in idx if k not in live]:
        idx.pop(k)
        changed = True
    if changed:
        tmp = index_path + ".tmp"
        json.dump(idx, open(tmp, "w"))
        os.replace(tmp, index_path)
    return idx


# ------------------------------------------------------------------ windowed loader
_COLS = ["open", "high", "low", "close", "volume"]


def _read_filtered(path, cutoff):
    """Same normalisation as bt.load_csv, but keeps only rows >= cutoff and
    reads big files in chunks so peak RSS stays flat."""
    out = []
    for ch in pd.read_csv(path, chunksize=200000):
        ch.columns = [c.strip().lower() for c in ch.columns]
        tcol = next((c for c in ("time", "timestamp", "date", "datetime") if c in ch.columns), None)
        if tcol is None:
            raise ValueError("no time column in %s" % path)
        if pd.api.types.is_numeric_dtype(ch[tcol]):
            ch[tcol] = pd.to_datetime(ch[tcol], utc=True, unit="s", errors="coerce")
        else:
            ch[tcol] = pd.to_datetime(ch[tcol], utc=True, errors="coerce")
        ch = ch.dropna(subset=[tcol])
        ch = ch[ch[tcol] >= cutoff]
        if not len(ch):
            continue
        ch = ch.set_index(tcol)
        if "volume" not in ch.columns:
            ch["volume"] = 0.0
        out.append(ch[_COLS].astype(float))
    if not out:
        return None
    return pd.concat(out)


def load_window(days=WINDOW_DAYS, data_dir=NQ_DIR, now=None):
    """Trailing-window 1-min frame. Concat/dedupe order is identical to
    bt.load_all() (sorted filename order, later file wins on duplicate ts)."""
    now = pd.Timestamp.utcnow() if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    cutoff = now - pd.Timedelta(days=days)
    idx = file_index(data_dir)
    frames = []
    used = 0
    for p in sorted(glob.glob(os.path.join(data_dir, "*.csv"))):
        meta = idx.get(os.path.basename(p))
        if meta and meta[3]:
            if pd.Timestamp(meta[3]) < cutoff:
                continue
        f = _read_filtered(p, cutoff)
        if f is not None and len(f):
            frames.append(f)
            used += 1
    if not frames:
        raise RuntimeError("no 1-min data in trailing %d days" % days)
    df = pd.concat(frames).sort_index(kind="mergesort")  # 2026-09-14: STABLE sort -- the default is not stable, so among duplicate timestamps keep="last" did NOT mean the newest file; an older seam could shadow a corrected re-pull
    del frames
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df, cutoff, used


# ------------------------------------------------------------------ daily closes cache
def build_daily_closes(data_dir=FULL_DIR, out=DAILY_CSV):
    """One-time / refresh: ET-date -> last 1-min close over the FULL archive,
    computed file-by-file in chunks so peak RSS stays small.  Reproduces the
    `daily` series of bt.build_daily_and_regime exactly: for each ET date take
    the close at the greatest timestamp; on an exact timestamp tie the later
    file (sorted name order) wins, the same as dedupe(keep=last).
    """
    best = {}   # date -> (ts, close, rank)
    for rank, p in enumerate(sorted(glob.glob(os.path.join(data_dir, "*.csv")))):
        for ch in pd.read_csv(p, chunksize=200000):
            ch.columns = [c.strip().lower() for c in ch.columns]
            tcol = next((c for c in ("time", "timestamp", "date", "datetime") if c in ch.columns), None)
            if tcol is None or "close" not in ch.columns:
                continue
            if pd.api.types.is_numeric_dtype(ch[tcol]):
                t = pd.to_datetime(ch[tcol], utc=True, unit="s", errors="coerce")
            else:
                t = pd.to_datetime(ch[tcol], utc=True, errors="coerce")
            m = t.notna()
            if not m.any():
                continue
            t = t[m]
            c = ch.loc[m, "close"].astype(float)
            d = t.dt.tz_convert(ET).dt.date
            g = pd.DataFrame({"t": t.values, "c": c.values, "d": d.values})
            g = g.sort_values("t").groupby("d").last()
            for dd, row in g.iterrows():
                ts = row["t"]
                cl = float(row["c"])
                cur = best.get(dd)
                if cur is None or ts > cur[0] or (ts == cur[0] and rank >= cur[2]):
                    best[dd] = (ts, cl, rank)
            del g, ch
    s = pd.Series(dict((k, v[1]) for k, v in best.items())).sort_index()
    s.index = pd.to_datetime(list(s.index))
    s = s.sort_index()
    s.to_csv(out, header=["close"], index_label="date")
    return s


def load_daily_closes(path=DAILY_CSV):
    d = pd.read_csv(path)
    s = pd.Series(d["close"].astype(float).values, index=pd.to_datetime(d["date"]))
    return s.sort_index()


def daily_from_window(df):
    etd = df.index.tz_convert(ET)
    tmp = pd.DataFrame({"close": df["close"].values, "etd": etd.date})
    daily = tmp.groupby("etd")["close"].last()
    daily.index = pd.to_datetime(list(daily.index))
    return daily.sort_index()


def merge_daily(cached, fresh, out=DAILY_CSV):
    """Fresh window closes override / extend the cached full-history series."""
    s = cached.copy()
    for k, v in fresh.items():
        s.loc[k] = float(v)
    s = s.sort_index(kind="mergesort")
    s = s[~s.index.duplicated(keep="last")]
    tmp = out + ".tmp"
    s.to_csv(tmp, header=["close"], index_label="date")
    os.replace(tmp, out)
    return s


def regimes_for(daily_full, only_dates, sma_window=200, mom_window=20):
    """Same no-look-ahead construction as bt.build_daily_and_regime, but only
    evaluated for the dates we actually need (the trailing window)."""
    bt = harness()["bt"]
    compute_regime = bt.compute_regime
    dates = list(daily_full.index)
    want = set(only_dates)
    out = {}
    for i, d in enumerate(dates):
        if d.date() not in want:
            continue
        if i < sma_window + 5:
            continue
        try:
            out[d.date()] = compute_regime(daily_full.iloc[:i],
                                           sma_window=sma_window, mom_window=mom_window)
        except Exception:
            continue
    return out


# ================================================================== THE BOOK
# The four deployed sleeves.  Copied verbatim from _hist_sleeves.py (VWAP +
# TLB_HYB40 + R3_RE) and _ra_r3re_sweep.py (max_wait), only parameterised so
# they can be run over a trailing window instead of the full archive.

def _apply_cap(trs):
    out = []
    for t in trs:
        rr = (abs(t["pnl_points"]) / abs(t["R"])) if t.get("R") else None
        if rr is not None and rr > VWAP_MAX_STOP_PTS:
            continue
        out.append(t)
    return out


def _make_hybrid_cls():
    bt = harness()["bt"]

    class HybridTLBLive(bt.TLBLive):
        def __init__(self, k=3, r_multiple=1.0, hybrid_stop_pts=None, hybrid_lookback=20):
            super().__init__(k=k, r_multiple=r_multiple)
            self.hybrid_stop_pts = hybrid_stop_pts
            self.hybrid_lookback = hybrid_lookback

        def add_bar(self, high, low, close):
            sig = super().add_bar(high, low, close)
            if sig is None or self.hybrid_stop_pts is None:
                return sig
            entry = sig["entry_price"]
            stop = sig["stop_price"]
            risk = entry - stop
            if risk < self.hybrid_stop_pts:
                i_now = len(self.l) - 1
                lo0 = max(0, i_now - self.hybrid_lookback + 1)
                new_stop = min(self.l[lo0:i_now + 1])
                new_risk = entry - new_stop
                if new_risk > 0:
                    sig = dict(side="long", entry_price=entry, stop_price=new_stop,
                               target_price=entry + self.r_multiple * new_risk)
            return sig

    return HybridTLBLive


def _sim_tlb_intrabar(bars5, start_j, sig, entry_utc):
    bt = harness()["bt"]
    _mk = bt._mk_trade
    entry = sig["entry_price"]
    stop = sig["stop_price"]
    target = sig["target_price"]
    risk = abs(entry - stop)
    for j in range(start_j + 1, len(bars5)):
        bts, bo, bh, bl, bc, bv = bars5[j]
        et_t = bts.tz_convert(ET).time()
        hm = (et_t.hour, et_t.minute)
        x = bts + pd.Timedelta(minutes=5)
        if bl <= stop:
            return _mk("TLB_LONG", "long", entry_utc, entry, x, stop, "STOP", risk)
        if bh >= target:
            return _mk("TLB_LONG", "long", entry_utc, entry, x, target, "TARGET", risk)
        if hm >= (15, 20):
            return _mk("TLB_LONG", "long", entry_utc, entry, x, bc, "EOD", risk)
    bts, bo, bh, bl, bc, bv = bars5[-1]
    return _mk("TLB_LONG", "long", entry_utc, entry, bts + pd.Timedelta(minutes=5), bc,
               "DATA_END", risk)


def run_tlb_hyb40(bars5, regime, hybrid_stop_pts=40.0):
    bt = harness()["bt"]
    Hyb = _make_hybrid_cls()
    tlb = Hyb(k=bt.TLB_CFG["pivot_k"], r_multiple=bt.TLB_CFG["r_multiple"],
              hybrid_stop_pts=hybrid_stop_pts, hybrid_lookback=20)
    trades = []
    busy = None
    for j, (bts, bo, bh, bl, bc, bv) in enumerate(bars5):
        et_t = bts.tz_convert(ET).time()
        et_date = bts.tz_convert(ET).date()
        reg = regime.get(et_date)
        s = tlb.add_bar(bh, bl, bc)
        eu = bts + pd.Timedelta(minutes=5)
        if s:
            prem = (s["entry_price"] - s["stop_price"]) * bt.TLB_PT_VALUE
            gate = (reg is not None and reg.cell in ("BOTH_BULL", "ZONE_B"))
            if (gate and not bt.no_trade(et_date)
                    and bt.is_in_window(et_t, bt.TLB_CFG["entry_windows_et"])
                    and prem <= bt.TLB_MAX_PREMIUM_USD and (busy is None or eu >= busy)):
                tr = _sim_tlb_intrabar(bars5, j, s, eu)
                tr["sleeve"] = "TLB_HYB40"
                trades.append(tr)
                busy = tr["exit_utc"]
    return trades


def run_r3_reentry(df, bars5, r3_trades, wait=2, stop_src="si", magnet="adaptive",
                   max_wait=R3RE_MAX_WAIT):
    """R3 re-entry overlay, identical to _ra_r3re_sweep.run() at the deployed
    setting (wait=2, stop_src=si, magnet=adaptive, max_wait=48)."""
    H = harness()
    vsl = H["vsl"]
    usv = vsl.update_session_vwap
    sess_key = vsl.session_for_utc_bar

    n = len(bars5)
    btsA = [b[0] for b in bars5]
    o5 = np.array([b[1] for b in bars5])
    h5 = np.array([b[2] for b in bars5])
    l5 = np.array([b[3] for b in bars5])
    c5 = np.array([b[4] for b in bars5])
    v5 = np.array([b[5] for b in bars5])
    vwap5 = np.full(n, np.nan)
    sd5 = np.full(n, np.nan)
    skA = []
    cur = None
    cumv = cumvp = cumsq = 0.0
    for i in range(n):
        sk = sess_key(btsA[i].to_pydatetime(), 0)
        skA.append(sk)
        if sk != cur:
            cur = sk
            cumv = cumvp = cumsq = 0.0
        cumv, cumvp, cumsq, vw, sd = usv(cumv, cumvp, cumsq, h5[i], l5[i], c5[i], v5[i])
        vwap5[i] = vw
        sd5[i] = sd
    upper5 = vwap5 + K * sd5
    bucket_idx = dict((btsA[i], i) for i in range(n))

    def floor5(ts):
        t = pd.Timestamp(ts)
        t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
        return t.floor("5min")

    def et_min(ts):
        e = ts.tz_convert(ET)
        return e.hour * 60 + e.minute

    FLAT = 15 * 60 + 55
    idx1 = df.index
    h1 = df["high"].values
    l1 = df["low"].values
    c1 = df["close"].values

    fires = []
    for tr in r3_trades:
        if tr["exit_reason"] != "STOP":
            continue
        si = bucket_idx.get(floor5(tr["entry_utc"] - pd.Timedelta(minutes=5)))
        ki = bucket_idx.get(floor5(tr["exit_utc"]))
        if si is None or ki is None:
            continue
        sess = skA[ki]
        entry = None
        j = ki + wait
        scanned = 0
        while j < n and skA[j] == sess and et_min(btsA[j]) < FLAT:
            if max_wait is not None and scanned >= max_wait:
                break
            runmax = h5[ki:j].max() if j > ki else h5[ki]
            if h5[j] > runmax:
                fill = max(o5[j], runmax)
                if stop_src == "si":
                    stop_lvl = l5[si:j].min()
                elif stop_src == "ki":
                    stop_lvl = l5[ki:j].min() if j > ki else l5[ki]
                else:
                    stop_lvl = l5[max(0, j - 20):j].min()
                lag_vw = vwap5[j - 1]
                if np.isnan(lag_vw):
                    j += 1
                    scanned += 1
                    continue
                if magnet == "adaptive":
                    kind = "VWAP" if fill < lag_vw else "UPPER"
                elif magnet == "vwap":
                    kind = "VWAP"
                elif magnet == "upper":
                    kind = "UPPER"
                else:
                    kind = "UPPER" if fill < lag_vw else "VWAP"
                riskpt = fill - stop_lvl
                if riskpt <= 0:
                    entry = "BAD"
                    break
                entry = dict(j=j, fill=fill, stop=stop_lvl, kind=kind, riskpt=riskpt, sess=sess)
                break
            j += 1
            scanned += 1
        if entry in (None, "BAD"):
            continue
        j = entry["j"]
        fill = entry["fill"]
        stop_lvl = entry["stop"]
        kind = entry["kind"]
        start = btsA[j] + pd.Timedelta(minutes=5)
        m0 = idx1.searchsorted(start, side="left")
        m = m0
        ex_px = ex_reason = ex_ts = None
        while m < len(idx1):
            t = idx1[m]
            if sess_key(t.to_pydatetime(), 0) != entry["sess"]:
                ex_px = c1[m - 1] if m > m0 else fill
                ex_reason = "SESS_END"
                ex_ts = idx1[m - 1]
                break
            if et_min(t) >= FLAT:
                ex_px = c1[m]
                ex_reason = "FLATTEN"
                ex_ts = t
                break
            if l1[m] <= stop_lvl:
                ex_px = stop_lvl
                ex_reason = "STOP"
                ex_ts = t
                break
            bi = bucket_idx.get(floor5(t) - pd.Timedelta(minutes=5))
            lvl = (vwap5[bi] if kind == "VWAP" else upper5[bi]) if bi is not None else np.nan
            if not np.isnan(lvl) and lvl > fill and h1[m] >= lvl:
                ex_px = lvl
                ex_reason = "TARGET"
                ex_ts = t
                break
            m += 1
        if ex_px is None:
            ex_px = c1[-1]
            ex_reason = "DATA_END"
            ex_ts = idx1[-1]
        pts = ex_px - fill
        eu = btsA[j] + pd.Timedelta(minutes=5)
        fires.append(dict(sleeve="R3_RE", direction="long", entry_utc=eu,
                          entry_et=eu.tz_convert(ET), entry_px=round(fill, 2),
                          exit_utc=ex_ts, exit_et=ex_ts.tz_convert(ET),
                          exit_px=round(ex_px, 2), exit_reason=ex_reason,
                          pnl_points=round(pts, 2), pnl_usd=round(pts * MNQ, 2),
                          R=round(pts / entry["riskpt"], 3)))
    return fires


def run_book(df, bars5, regime):
    """All four deployed sleeves over the supplied bars.  Returns
    {sleeve: [trade dicts]} with the harness trade schema."""
    bt = harness()["bt"]
    vt, _sigs = bt.run_vwap_tlb(df, bars5, regime)
    r3 = _apply_cap(vt["VWAP_LONG_R3"])
    vs = _apply_cap(vt["VWAP_SHORT_R1"])
    for t in r3:
        t["sleeve"] = "VWAP_LONG_R3"
    for t in vs:
        t["sleeve"] = "VWAP_SHORT_R1"
    tlb = run_tlb_hyb40(bars5, regime)
    r3re = run_r3_reentry(df, bars5, r3)
    return {"VWAP_LONG_R3": r3, "VWAP_SHORT_R1": vs, "TLB_HYB40": tlb, "R3_RE": r3re}


def net_usd(tr):
    """Net $ for one trade at the deployed contract size, after cost."""
    q = QTY[tr["sleeve"]]
    return float(tr["pnl_usd"]) * q - RT * q
