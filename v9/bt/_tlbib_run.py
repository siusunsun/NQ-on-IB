"""_tlbib_ : INTRABAR-corrected TLB exit re-simulation (pivot + hybrid-40).
Read-only. Entries IDENTICAL to _tlbhyb_/live; only the exit model changes.
CLOSE-based sim_tlb (A1 option leftover) vs INTRABAR (matches live server-side STP/LMT).
"""
import sys, importlib.util
import numpy as np, pandas as pd

sys.path.insert(0, "/root/v9/bt")
spec = importlib.util.spec_from_file_location("bt_nq", "/root/v9/bt/bt_nq.py")
bt = importlib.util.module_from_spec(spec); sys.modules["bt_nq"] = bt; spec.loader.exec_module(bt)
ET = bt.ET
TLBLive = bt.TLBLive
is_in_window = bt.is_in_window
sim_tlb = bt.sim_tlb            # CLOSE-based (baseline we've been quoting)
no_trade = bt.no_trade
POINT_USD = bt.POINT_USD
_mk_trade = bt._mk_trade
TLB_CFG = bt.TLB_CFG
TLB_PT_VALUE = bt.TLB_PT_VALUE
TLB_MAX_PREMIUM_USD = bt.TLB_MAX_PREMIUM_USD

df = bt.load_all()                       # 1-min bars (UTC)
daily, regime = bt.build_daily_and_regime(df)
bars5 = bt.build_5min(df)
print("data 1min=%d 5min=%d  range %s -> %s" % (len(df), len(bars5), bars5[0][0], bars5[-1][0]), flush=True)


# ---- INTRABAR exit on 5-min bars (task spec: low<=stop STOP-wins, high>=target) ----
def sim_tlb_intrabar(bars5, start_j, sig, entry_utc):
    entry = sig["entry_price"]; stop = sig["stop_price"]; target = sig["target_price"]
    risk = abs(entry - stop)
    for j in range(start_j + 1, len(bars5)):
        bts, bo, bh, bl, bc, bv = bars5[j]
        et_t = bts.tz_convert(ET).time()
        hm = (et_t.hour, et_t.minute)
        x_utc = bts + pd.Timedelta(minutes=5)
        if bl <= stop:                                  # STOP wins same-bar ties (conservative)
            return _mk_trade("TLB_LONG", "long", entry_utc, entry, x_utc, stop, "STOP", risk)
        if bh >= target:
            return _mk_trade("TLB_LONG", "long", entry_utc, entry, x_utc, target, "TARGET", risk)
        if hm >= (15, 20):
            return _mk_trade("TLB_LONG", "long", entry_utc, entry, x_utc, bc, "EOD", risk)
    bts, bo, bh, bl, bc, bv = bars5[-1]
    return _mk_trade("TLB_LONG", "long", entry_utc, entry, bts + pd.Timedelta(minutes=5), bc, "DATA_END", risk)


# ---- INTRABAR exit on 1-MIN bars (most faithful to live tick stops; sensitivity) ----
_dfidx = df.index
_dh = df["high"].values; _dl = df["low"].values; _dc = df["close"].values
def sim_tlb_intrabar_1m(bars5, start_j, sig, entry_utc):
    entry = sig["entry_price"]; stop = sig["stop_price"]; target = sig["target_price"]
    risk = abs(entry - stop)
    pos = _dfidx.searchsorted(entry_utc, side="left")
    for k in range(pos, len(_dfidx)):
        ts = _dfidx[k]; et_t = ts.tz_convert(ET).time(); hm = (et_t.hour, et_t.minute)
        if _dl[k] <= stop:
            return _mk_trade("TLB_LONG", "long", entry_utc, entry, ts, stop, "STOP", risk)
        if _dh[k] >= target:
            return _mk_trade("TLB_LONG", "long", entry_utc, entry, ts, target, "TARGET", risk)
        if hm >= (15, 20):
            return _mk_trade("TLB_LONG", "long", entry_utc, entry, ts, _dc[k], "EOD", risk)
    return _mk_trade("TLB_LONG", "long", entry_utc, entry, _dfidx[-1], _dc[-1], "DATA_END", risk)


class HybridTLBLive(TLBLive):
    def __init__(self, k=3, r_multiple=1.0, hybrid_stop_pts=None, hybrid_lookback=20):
        super().__init__(k=k, r_multiple=r_multiple)
        self.hybrid_stop_pts = hybrid_stop_pts
        self.hybrid_lookback = hybrid_lookback
    def add_bar(self, high, low, close):
        sig = super().add_bar(high, low, close)
        if sig is None:
            return sig
        entry = sig["entry_price"]; stop = sig["stop_price"]; risk = entry - stop
        if self.hybrid_stop_pts is None:
            return sig
        if risk < self.hybrid_stop_pts:
            i_now = len(self.l) - 1
            lo0 = max(0, i_now - self.hybrid_lookback + 1)
            new_stop = min(self.l[lo0:i_now + 1]); new_risk = entry - new_stop
            if new_risk > 0:
                sig = dict(side="long", entry_price=entry, stop_price=new_stop,
                           target_price=entry + self.r_multiple * new_risk)
        return sig


def run_tlb(hybrid_stop_pts=None, exitfn=sim_tlb, hybrid_lookback=20):
    tlb = HybridTLBLive(k=TLB_CFG["pivot_k"], r_multiple=TLB_CFG["r_multiple"],
                        hybrid_stop_pts=hybrid_stop_pts, hybrid_lookback=hybrid_lookback)
    trades = []; busy_until = None
    for j, (bts, bo, bh, bl, bc, bv) in enumerate(bars5):
        et_t = bts.tz_convert(ET).time(); et_date = bts.tz_convert(ET).date()
        reg = regime.get(et_date)
        s_tlb = tlb.add_bar(bh, bl, bc)
        entry_utc = bts + pd.Timedelta(minutes=5)
        holiday = no_trade(et_date)
        if s_tlb:
            premium = (s_tlb["entry_price"] - s_tlb["stop_price"]) * TLB_PT_VALUE
            gate = (reg is not None and reg.cell in ("BOTH_BULL", "ZONE_B"))
            ok = (gate and not holiday and is_in_window(et_t, TLB_CFG["entry_windows_et"])
                  and premium <= TLB_MAX_PREMIUM_USD
                  and (busy_until is None or entry_utc >= busy_until))
            if ok:
                tr = exitfn(bars5, j, s_tlb, entry_utc)
                trades.append(tr); busy_until = tr["exit_utc"]
    return trades


def stat(trs):
    if not trs: return None
    pts = np.array([t["pnl_points"] for t in trs]); usd = np.array([t["pnl_usd"] for t in trs])
    R = np.array([t["R"] for t in trs])
    gp = usd[usd > 0].sum(); gl = -usd[usd < 0].sum(); pf = gp / gl if gl > 0 else float("inf")
    trs_s = sorted(trs, key=lambda t: t["entry_utc"]); cum = np.cumsum([t["pnl_usd"] for t in trs_s])
    peak = np.maximum.accumulate(cum); mdd = (cum - peak).min() if len(cum) else 0.0
    net = usd.sum()
    return dict(n=len(trs), win=100 * (pts > 0).mean(), pf=pf, net=net, sumR=R.sum(),
                mdd=mdd, exp=net / len(trs))

def line(tag, s):
    if s is None: print(f"  {tag:<22} none"); return
    pf = "inf" if s['pf'] == float('inf') else f"{s['pf']:.3f}"
    print(f"  {tag:<22} n={s['n']:>3} win%={s['win']:>5.1f} PF={pf:>6} net=${s['net']:>8,.0f} "
          f"exp/tr=${s['exp']:>6.1f} sumR={s['sumR']:>7.2f} maxDD=${s['mdd']:>8,.0f}")

# ---- build all runs ----
piv_close = run_tlb(None, sim_tlb)
piv_intra = run_tlb(None, sim_tlb_intrabar)
h40_close = run_tlb(40.0, sim_tlb)
h40_intra = run_tlb(40.0, sim_tlb_intrabar)
piv_1m    = run_tlb(None, sim_tlb_intrabar_1m)
h40_1m    = run_tlb(40.0, sim_tlb_intrabar_1m)

print("\n" + "="*95); print("REPRODUCE CHECK (pivot CLOSE should match n=285 PF~1.10 net~$1,782)"); print("="*95)
sb = stat(piv_close)
print(f"  pivot-close n={sb['n']} PF={sb['pf']:.3f} net=${sb['net']:,.0f} maxDD=${sb['mdd']:,.0f} win={sb['win']:.1f}")

print("\n" + "="*95); print("CLOSE-BASED (optimistic, quoted)  vs  INTRABAR (honest, live-faithful)"); print("="*95)
line("PIVOT  close", stat(piv_close))
line("PIVOT  intrabar-5m", stat(piv_intra))
line("PIVOT  intrabar-1m", stat(piv_1m))
print()
line("HYB40  close", stat(h40_close))
line("HYB40  intrabar-5m", stat(h40_intra))
line("HYB40  intrabar-1m", stat(h40_1m))

def d(a,b,key):
    sa,sb=stat(a),stat(b); return sb[key]-sa[key]
print("\n  --- OPTIMISM (intrabar-5m minus close) ---")
print(f"  PIVOT: dnet=${d(piv_close,piv_intra,'net'):+,.0f}  dPF={stat(piv_intra)['pf']-stat(piv_close)['pf']:+.3f}  dexp/tr=${d(piv_close,piv_intra,'exp'):+.1f}")
print(f"  HYB40: dnet=${d(h40_close,h40_intra,'net'):+,.0f}  dPF={stat(h40_intra)['pf']-stat(h40_close)['pf']:+.3f}  dexp/tr=${d(h40_close,h40_intra,'exp'):+.1f}")

print("\n" + "="*95); print("HYBRID vs PIVOT  (does hybrid still beat pivot?)"); print("="*95)
for tag, pv, hv in [("CLOSE-based", piv_close, h40_close),
                    ("INTRABAR-5m", piv_intra, h40_intra),
                    ("INTRABAR-1m", piv_1m, h40_1m)]:
    sp, sh = stat(pv), stat(hv)
    dn = sh['net']-sp['net']; pct = 100*dn/abs(sp['net']) if sp['net'] else float('nan')
    print(f"  {tag:<12} pivot net=${sp['net']:>8,.0f}  hyb40 net=${sh['net']:>8,.0f}  "
          f"delta=${dn:>+8,.0f} ({pct:+.1f}%)  PF {sp['pf']:.3f}->{sh['pf']:.3f}  sumR {sp['sumR']:.2f}->{sh['sumR']:.2f}")

print("\n" + "="*95); print("PER-YEAR net$  (ET year)"); print("="*95)
def yr(t): return t["entry_utc"].tz_convert(ET).year
allyears = sorted(set(yr(t) for t in piv_close))
print(f"  {'year':<6} {'pivC':>9} {'pivI5':>9} {'hybC':>9} {'hybI5':>9} {'hybI5-pivI5':>12}")
for y in allyears:
    def yn(trs): s=stat([t for t in trs if yr(t)==y]); return s['net'] if s else 0.0
    pc,pi,hc,hi = yn(piv_close),yn(piv_intra),yn(h40_close),yn(h40_intra)
    print(f"  {y:<6} {pc:>9,.0f} {pi:>9,.0f} {hc:>9,.0f} {hi:>9,.0f} {hi-pi:>+12,.0f}")

# ---- dump corrected INTRABAR HYBRID-40 per-trade rows ----
rows = []
for t in sorted(h40_intra, key=lambda x: x["entry_utc"]):
    et = t["entry_et"]
    rows.append(dict(entry_date=et.strftime("%Y-%m-%d"), entry_time_et=et.strftime("%H:%M"),
                     entry_utc=t["entry_utc"].strftime("%Y-%m-%d %H:%M"),
                     entry_px=t["entry_px"], exit_px=t["exit_px"], exit_reason=t["exit_reason"],
                     pnl_points=t["pnl_points"], pnl_usd=t["pnl_usd"], R=t["R"]))
pd.DataFrame(rows).to_csv("/root/v9/bt/_tlbib_hybrid_trades.csv", index=False)
print(f"\nwrote /root/v9/bt/_tlbib_hybrid_trades.csv  rows={len(rows)}  net=${sum(r['pnl_usd'] for r in rows):,.0f}")

from collections import Counter
for tag, trs in [("pivot-intra5m", piv_intra), ("hyb40-intra5m", h40_intra)]:
    print(f"  {tag} exit mix:", dict(Counter(t['exit_reason'] for t in trs)))
