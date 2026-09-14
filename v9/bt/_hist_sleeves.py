"""_hist_sleeves.py - FULL-HISTORY (2021-2026) re-run of all NQ book sleeves. READ-ONLY research.
Uses _hist_bt_nq.py (bt_nq harness repointed at /root/tlbrc_paper/ref/data_full/NQ)."""
import sys, importlib.util, warnings
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, pytz
ET = pytz.timezone("America/New_York")
sys.path.insert(0, "/root/v9/bt"); sys.path.insert(0, "/root/v9")

def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p); m = importlib.util.module_from_spec(s)
    sys.modules[n] = m; s.loader.exec_module(m); return m

bt  = _load("bt_nq", "/root/v9/bt/_hist_bt_nq.py")
vsl = _load("vsl", "/root/v9/v9_strategy_lib.py")
usv = vsl.update_session_vwap; sess_key = vsl.session_for_utc_bar

# ---- LIVE-CONFIG PATCH: post_halt_blackout_bars=24 on the LONG VwapState only ----
_Orig = bt.VwapState
def _Patched(*a, **k):
    if k.get("side") == "LONG":
        k.setdefault("post_halt_blackout_bars", 24)
    return _Orig(*a, **k)
bt.VwapState = _Patched

MNQ = 2.0
VWAP_MAX_STOP_PTS = 200.0
K = 2.0

print("[1] loading combined full-history data ...", flush=True)
df = bt.load_all()
print("    1min bars=%d  %s -> %s" % (len(df), df.index[0], df.index[-1]), flush=True)
daily, regime = bt.build_daily_and_regime(df)
rd = sorted(regime.keys())
print("    daily=%d regime days=%d  %s -> %s" % (len(daily), len(regime), rd[0], rd[-1]), flush=True)
bars5 = bt.build_5min(df)
print("    5min bars=%d" % len(bars5), flush=True)

print("[2] run_vwap_tlb (live cfg) ...", flush=True)
vt, sigs = bt.run_vwap_tlb(df, bars5, regime)

def apply_cap(trs):
    out = []; drop = 0
    for t in trs:
        rr = (abs(t["pnl_points"]) / abs(t["R"])) if t.get("R") else None
        if rr is not None and rr > VWAP_MAX_STOP_PTS:
            drop += 1; continue
        out.append(t)
    return out, drop

r3, d1 = apply_cap(vt["VWAP_LONG_R3"])
vs, d2 = apply_cap(vt["VWAP_SHORT_R1"])
print("    VWAP_LONG_R3  raw=%d -> %d (dropped %d wide-stop)" % (len(vt["VWAP_LONG_R3"]), len(r3), d1), flush=True)
print("    VWAP_SHORT_R1 raw=%d -> %d (dropped %d wide-stop)" % (len(vt["VWAP_SHORT_R1"]), len(vs), d2), flush=True)

# ---------------- TLB hybrid-40, INTRABAR 5m exit ----------------
print("[3] TLB hybrid-40 intrabar ...", flush=True)
TLBLive = bt.TLBLive; is_in_window = bt.is_in_window; no_trade = bt.no_trade
_mk_trade = bt._mk_trade; TLB_CFG = bt.TLB_CFG
TLB_PT_VALUE = bt.TLB_PT_VALUE; TLB_MAX_PREMIUM_USD = bt.TLB_MAX_PREMIUM_USD

def sim_tlb_intrabar(bars5, start_j, sig, entry_utc):
    entry = sig["entry_price"]; stop = sig["stop_price"]; target = sig["target_price"]
    risk = abs(entry - stop)
    for j in range(start_j + 1, len(bars5)):
        bts, bo, bh, bl, bc, bv = bars5[j]
        et_t = bts.tz_convert(ET).time(); hm = (et_t.hour, et_t.minute)
        x = bts + pd.Timedelta(minutes=5)
        if bl <= stop:   return _mk_trade("TLB_LONG", "long", entry_utc, entry, x, stop, "STOP", risk)
        if bh >= target: return _mk_trade("TLB_LONG", "long", entry_utc, entry, x, target, "TARGET", risk)
        if hm >= (15, 20): return _mk_trade("TLB_LONG", "long", entry_utc, entry, x, bc, "EOD", risk)
    bts, bo, bh, bl, bc, bv = bars5[-1]
    return _mk_trade("TLB_LONG", "long", entry_utc, entry, bts + pd.Timedelta(minutes=5), bc, "DATA_END", risk)

class HybridTLBLive(TLBLive):
    def __init__(self, k=3, r_multiple=1.0, hybrid_stop_pts=None, hybrid_lookback=20):
        super().__init__(k=k, r_multiple=r_multiple)
        self.hybrid_stop_pts = hybrid_stop_pts; self.hybrid_lookback = hybrid_lookback
    def add_bar(self, high, low, close):
        sig = super().add_bar(high, low, close)
        if sig is None or self.hybrid_stop_pts is None: return sig
        entry = sig["entry_price"]; stop = sig["stop_price"]; risk = entry - stop
        if risk < self.hybrid_stop_pts:
            i_now = len(self.l) - 1
            lo0 = max(0, i_now - self.hybrid_lookback + 1)
            new_stop = min(self.l[lo0:i_now + 1]); new_risk = entry - new_stop
            if new_risk > 0:
                sig = dict(side="long", entry_price=entry, stop_price=new_stop,
                           target_price=entry + self.r_multiple * new_risk)
        return sig

def run_tlb(hybrid_stop_pts, exitfn):
    tlb = HybridTLBLive(k=TLB_CFG["pivot_k"], r_multiple=TLB_CFG["r_multiple"],
                        hybrid_stop_pts=hybrid_stop_pts, hybrid_lookback=20)
    trades = []; busy = None
    for j, (bts, bo, bh, bl, bc, bv) in enumerate(bars5):
        et_t = bts.tz_convert(ET).time(); et_date = bts.tz_convert(ET).date()
        reg = regime.get(et_date); s = tlb.add_bar(bh, bl, bc)
        eu = bts + pd.Timedelta(minutes=5)
        if s:
            prem = (s["entry_price"] - s["stop_price"]) * TLB_PT_VALUE
            gate = (reg is not None and reg.cell in ("BOTH_BULL", "ZONE_B"))
            if (gate and not no_trade(et_date) and is_in_window(et_t, TLB_CFG["entry_windows_et"])
                    and prem <= TLB_MAX_PREMIUM_USD and (busy is None or eu >= busy)):
                tr = exitfn(bars5, j, s, eu); trades.append(tr); busy = tr["exit_utc"]
    return trades

tlb_h40 = run_tlb(40.0, sim_tlb_intrabar)
tlb_piv = run_tlb(None, sim_tlb_intrabar)
print("    TLB hyb40 intrabar n=%d   pivot intrabar n=%d" % (len(tlb_h40), len(tlb_piv)), flush=True)

# ---------------- R3 RE-ENTRY overlay ----------------
print("[4] R3 re-entry overlay ...", flush=True)
n = len(bars5); btsA = [b[0] for b in bars5]
o5 = np.array([b[1] for b in bars5]); h5 = np.array([b[2] for b in bars5])
l5 = np.array([b[3] for b in bars5]); c5 = np.array([b[4] for b in bars5]); v5 = np.array([b[5] for b in bars5])
vwap5 = np.full(n, np.nan); sd5 = np.full(n, np.nan)
cur = None; cumv = cumvp = cumsq = 0.0
skA = []
for i in range(n):
    sk = sess_key(btsA[i].to_pydatetime(), 0)
    skA.append(sk)
    if sk != cur: cur = sk; cumv = cumvp = cumsq = 0.0
    cumv, cumvp, cumsq, vw, sd = usv(cumv, cumvp, cumsq, h5[i], l5[i], c5[i], v5[i])
    vwap5[i] = vw; sd5[i] = sd
upper5 = vwap5 + K * sd5
bucket_idx = {btsA[i]: i for i in range(n)}

def floor5(ts):
    t = pd.Timestamp(ts); t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.floor("5min")

def et_min(ts):
    e = ts.tz_convert(ET); return e.hour * 60 + e.minute

FLAT = 15 * 60 + 55
idx1 = df.index; h1 = df["high"].values; l1 = df["low"].values; c1 = df["close"].values

fires = []
for tr in r3:
    if tr["exit_reason"] != "STOP": continue
    si = bucket_idx.get(floor5(tr["entry_utc"] - pd.Timedelta(minutes=5)))
    ki = bucket_idx.get(floor5(tr["exit_utc"]))
    if si is None or ki is None: continue
    sess = skA[ki]; entry = None; j = ki + 2
    while j < n and skA[j] == sess and et_min(btsA[j]) < FLAT:
        runmax = h5[ki:j].max()
        if h5[j] > runmax:
            fill = max(o5[j], runmax); stop_lvl = l5[si:j].min()
            lag_vw = vwap5[j - 1]
            if np.isnan(lag_vw): j += 1; continue
            kind = "VWAP" if fill < lag_vw else "UPPER"
            riskpt = fill - stop_lvl
            if riskpt <= 0: entry = "BAD"; break
            entry = dict(j=j, fill=fill, stop=stop_lvl, kind=kind, riskpt=riskpt, sess=sess); break
        j += 1
    if entry in (None, "BAD"): continue
    j = entry["j"]; fill = entry["fill"]; stop_lvl = entry["stop"]; kind = entry["kind"]
    start = btsA[j] + pd.Timedelta(minutes=5)
    m0 = idx1.searchsorted(start, side="left"); m = m0
    ex_px = ex_reason = ex_ts = None
    while m < len(idx1):
        t = idx1[m]
        if sess_key(t.to_pydatetime(), 0) != entry["sess"]:
            ex_px = c1[m - 1] if m > m0 else fill; ex_reason = "SESS_END"; ex_ts = idx1[m - 1]; break
        if et_min(t) >= FLAT: ex_px = c1[m]; ex_reason = "FLATTEN"; ex_ts = t; break
        if l1[m] <= stop_lvl: ex_px = stop_lvl; ex_reason = "STOP"; ex_ts = t; break
        bi = bucket_idx.get(floor5(t) - pd.Timedelta(minutes=5))
        lvl = (vwap5[bi] if kind == "VWAP" else upper5[bi]) if bi is not None else np.nan
        if not np.isnan(lvl) and lvl > fill and h1[m] >= lvl:
            ex_px = lvl; ex_reason = "TARGET"; ex_ts = t; break
        m += 1
    if ex_px is None: ex_px = c1[-1]; ex_reason = "DATA_END"; ex_ts = idx1[-1]
    pts = ex_px - fill
    eu = btsA[j] + pd.Timedelta(minutes=5)
    fires.append(dict(sleeve="R3_RE", direction="long", entry_utc=eu, entry_et=eu.tz_convert(ET),
                      entry_px=round(fill, 2), exit_utc=ex_ts, exit_et=ex_ts.tz_convert(ET),
                      exit_px=round(ex_px, 2), exit_reason=ex_reason,
                      pnl_points=round(pts, 2), pnl_usd=round(pts * MNQ, 2),
                      R=round(pts / entry["riskpt"], 3)))
print("    R3_RE fires n=%d" % len(fires), flush=True)

def dump(trs, name):
    rows = []
    for t in sorted(trs, key=lambda x: x["entry_utc"]):
        rows.append(dict(sleeve=name, entry_utc=t["entry_utc"], entry_et=t["entry_et"],
                         exit_utc=t["exit_utc"], exit_et=t["exit_et"],
                         direction=t["direction"], entry_px=t["entry_px"], exit_px=t["exit_px"],
                         exit_reason=t["exit_reason"], pnl_points=t["pnl_points"],
                         pnl_usd=t["pnl_usd"], R=t["R"]))
    d = pd.DataFrame(rows); d.to_csv("/root/v9/bt/_hist_tr_%s.csv" % name, index=False)
    tot = d["pnl_usd"].sum() if len(d) else 0
    print("    wrote _hist_tr_{}.csv n={} net(1MNQ)=${:,.0f}".format(name, len(d), tot), flush=True)

dump(r3, "VWAP_LONG_R3"); dump(vs, "VWAP_SHORT_R1")
dump(tlb_h40, "TLB_HYB40"); dump(tlb_piv, "TLB_PIVOT"); dump(fires, "R3_RE")
print("DONE", flush=True)
