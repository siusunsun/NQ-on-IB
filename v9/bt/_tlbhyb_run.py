"""_tlbhyb_ : Backtest hybrid-stop vs pivot-stop on OUR deployed TLB sleeve.
Read-only research. Replicates the TLB branch of bt_nq.run_vwap_tlb end-to-end.
"""
import sys, importlib.util
from pathlib import Path
import numpy as np, pandas as pd

sys.path.insert(0, "/root/v9/bt")
spec = importlib.util.spec_from_file_location("bt_nq", "/root/v9/bt/bt_nq.py")
bt = importlib.util.module_from_spec(spec); sys.modules["bt_nq"] = bt; spec.loader.exec_module(bt)
ET = bt.ET
TLBLive = bt.TLBLive
is_in_window = bt.is_in_window
sim_tlb = bt.sim_tlb
no_trade = bt.no_trade
POINT_USD = bt.POINT_USD

TLB_CFG = bt.TLB_CFG
TLB_PT_VALUE = bt.TLB_PT_VALUE
TLB_MAX_PREMIUM_USD = bt.TLB_MAX_PREMIUM_USD
print("TLB_CFG:", TLB_CFG, flush=True)

df = bt.load_all()
daily, regime = bt.build_daily_and_regime(df)
bars5 = bt.build_5min(df)
print("data 1min=%d 5min=%d  range %s -> %s" % (len(df), len(bars5), bars5[0][0], bars5[-1][0]), flush=True)


class HybridTLBLive(TLBLive):
    def __init__(self, k=3, r_multiple=1.0, hybrid_stop_pts=None, hybrid_lookback=20):
        super().__init__(k=k, r_multiple=r_multiple)
        self.hybrid_stop_pts = hybrid_stop_pts
        self.hybrid_lookback = hybrid_lookback
        self.last_modified = False
        self.last_pivot_risk = None

    def add_bar(self, high, low, close):
        sig = super().add_bar(high, low, close)
        self.last_modified = False; self.last_pivot_risk = None
        self.last_hybrid_stop = None
        if sig is None:
            return sig
        entry = sig["entry_price"]; stop = sig["stop_price"]; risk = entry - stop
        self.last_pivot_risk = risk   # always recorded, even for pure-pivot runs
        # compute the hybrid-would-be stop for the <thr case (for re-price view)
        i_now = len(self.l) - 1
        lo0 = max(0, i_now - self.hybrid_lookback + 1)
        self.last_hybrid_stop = min(self.l[lo0:i_now + 1])
        if self.hybrid_stop_pts is None:
            return sig
        if risk < self.hybrid_stop_pts:
            new_stop = self.last_hybrid_stop
            new_risk = entry - new_stop
            if new_risk > 0:
                sig = dict(side="long", entry_price=entry, stop_price=new_stop,
                           target_price=entry + self.r_multiple * new_risk)
                self.last_modified = True
        return sig


def run_tlb(hybrid_stop_pts=None, hybrid_lookback=20):
    tlb = HybridTLBLive(k=TLB_CFG["pivot_k"], r_multiple=TLB_CFG["r_multiple"],
                        hybrid_stop_pts=hybrid_stop_pts, hybrid_lookback=hybrid_lookback)
    trades = []; busy_until = None
    for j, (bts, bo, bh, bl, bc, bv) in enumerate(bars5):
        et_t = bts.tz_convert(ET).time()
        et_date = bts.tz_convert(ET).date()
        reg = regime.get(et_date)
        s_tlb = tlb.add_bar(bh, bl, bc)
        entry_utc = bts + pd.Timedelta(minutes=5)
        holiday = no_trade(et_date)
        if s_tlb:
            premium = (s_tlb["entry_price"] - s_tlb["stop_price"]) * TLB_PT_VALUE
            gate = (reg is not None and reg.cell in ("BOTH_BULL", "ZONE_B"))
            ok = (gate and not holiday
                  and is_in_window(et_t, TLB_CFG["entry_windows_et"])
                  and premium <= TLB_MAX_PREMIUM_USD
                  and (busy_until is None or entry_utc >= busy_until))
            if ok:
                tr = sim_tlb(bars5, j, s_tlb, entry_utc)
                tr["hybrid_modified"] = tlb.last_modified
                tr["pivot_risk"] = tlb.last_pivot_risk
                tr["hybrid_stop"] = tlb.last_hybrid_stop
                tr["j"] = j
                tr["sig"] = dict(s_tlb)
                trades.append(tr)
                busy_until = tr["exit_utc"]
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
                mdd=mdd, retdd=(net / -mdd if mdd < 0 else float('inf')))


def win(trs, lo=None, hi=None):
    out = []
    for t in trs:
        e = t["entry_utc"]
        if lo is not None and e < lo: continue
        if hi is not None and e >= hi: continue
        out.append(t)
    return out


WINDOWS = [
    ("FULL", None, None),
    ("TRAIN 2024-01->2025-12", pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC")),
    ("TEST 2026-01->present", pd.Timestamp("2026-01-01", tz="UTC"), None),
    ("RECENT 2026-06-01->present", pd.Timestamp("2026-06-01", tz="UTC"), None),
]

base = run_tlb(hybrid_stop_pts=None)
sb = stat(base)
print("\n=== PIVOT BASELINE (full) ===")
print(f"  n={sb['n']} win%={sb['win']:.1f} PF={sb['pf']:.3f} net=${sb['net']:,.0f} maxDD=${sb['mdd']:,.0f} sumR={sb['sumR']:.2f}")
exp = dict(n=285, pf=1.10, net=1782, mdd=-3022, win=55.8)
ok = (sb['n'] == exp['n'] and abs(sb['pf'] - exp['pf']) < 0.02 and abs(sb['net'] - exp['net']) < 30
      and abs(sb['mdd'] - exp['mdd']) < 50 and abs(sb['win'] - exp['win']) < 0.5)
print("  EXPECT n=285 PF~1.10 net~$1,782 maxDD~-$3,022 win~55.8pct  -> REPRODUCE %s" % ("PASS" if ok else "FAIL"))

variants = {"pivot": base}
for thr in (30, 40, 60):
    variants["hyb%d" % thr] = run_tlb(hybrid_stop_pts=float(thr), hybrid_lookback=20)

for wn, lo, hi in WINDOWS:
    print("\n" + "#" * 100); print("### " + wn); print("#" * 100)
    print(f"  {'variant':<8} {'n':>4} {'win%':>6} {'PF':>6} {'net$':>10} {'sumR':>7} {'maxDD$':>9} {'ret/DD':>7}")
    for vn in ("pivot", "hyb30", "hyb40", "hyb60"):
        s = stat(win(variants[vn], lo, hi))
        if s is None: print("  %-8s none" % vn); continue
        pf = "%.2f" % s['pf'] if s['pf'] != float('inf') else "inf"
        rd = "%.2f" % s['retdd'] if s['retdd'] != float('inf') else "inf"
        print(f"  {vn:<8} {s['n']:>4} {s['win']:>6.1f} {pf:>6} {s['net']:>10,.0f} {s['sumR']:>7.2f} {s['mdd']:>9,.0f} {rd:>7}")

print("\n" + "=" * 70); print("PER-YEAR net$  (pivot / hyb40 / delta)"); print("=" * 70)
def yr(t): return t["entry_utc"].tz_convert(ET).year
years = sorted(set(yr(t) for t in variants["pivot"]))
for y in years:
    p = stat([t for t in variants["pivot"] if yr(t) == y])
    h = stat([t for t in variants["hyb40"] if yr(t) == y])
    pn = p['net'] if p else 0; hn = h['net'] if h else 0
    pnn = p['n'] if p else 0; hnn = h['n'] if h else 0
    print(f"  {y}: pivot n={pnn:>3} ${pn:>9,.0f}  |  hyb40 n={hnn:>3} ${hn:>9,.0f}  |  delta ${hn-pn:>+9,.0f}")

print("\n" + "=" * 70); print("MECHANISM CHECK (hyb40)"); print("=" * 70)
h40 = variants["hyb40"]
mod = [t for t in h40 if t["hybrid_modified"]]
unmod = [t for t in h40 if not t["hybrid_modified"]]
print(f"  hyb40 total n={len(h40)}  modified(<40pt pivot risk)={len(mod)}  untouched={len(unmod)}")
sm = stat(mod); su = stat(unmod)
if sm:
    pfm = "inf" if sm['pf'] == float('inf') else f"{sm['pf']:.2f}"
    print(f"  MODIFIED  : n={sm['n']} win%={sm['win']:.1f} PF={pfm} net=${sm['net']:,.0f} sumR={sm['sumR']:.2f}")
if su:
    print(f"  UNTOUCHED : n={su['n']} win%={su['win']:.1f} PF={su['pf']:.2f} net=${su['net']:,.0f} sumR={su['sumR']:.2f}")
pvn = stat(variants['pivot'])['net']; h4n = stat(h40)['net']
print(f"\n  pivot net=${pvn:,.0f}  hyb40 net=${h4n:,.0f}  delta=${h4n-pvn:+,.0f}")

print("\n" + "=" * 70); print("SECONDARY re-price view (pivot entry set held FIXED, just re-stop <40pt)"); print("=" * 70)
pv = variants["pivot"]
lt40 = [t for t in pv if t["pivot_risk"] is not None and t["pivot_risk"] < 40]
print(f"  pivot trades with pivot risk<40pt: {len(lt40)} of {len(pv)}")
sp = stat(lt40)
if sp: print(f"  those {sp['n']} trades AS-IS (pivot stop): win%={sp['win']:.1f} PF={sp['pf']:.2f} net=${sp['net']:,.0f} sumR={sp['sumR']:.2f}")
# Re-price ONLY those <40pt trades with the widened hybrid stop, entries held fixed (no path-dep)
reprice = []
for t in lt40:
    e = t["sig"]["entry_price"]; ns = t["hybrid_stop"]; nr = e - ns
    if nr <= 0:
        reprice.append(t); continue
    nsig = dict(side="long", entry_price=e, stop_price=ns, target_price=e + TLB_CFG["r_multiple"] * nr)
    tr = sim_tlb(bars5, t["j"], nsig, t["entry_utc"])
    reprice.append(tr)
sr = stat(reprice)
if sr: print(f"  those {sr['n']} trades RE-STOPPED (hybrid stop): win%={sr['win']:.1f} PF={sr['pf']:.2f} net=${sr['net']:,.0f} sumR={sr['sumR']:.2f}")
if sp and sr: print(f"  --> re-price delta on the modified trades: net=${sr['net']-sp['net']:+,.0f}  (isolated mechanism, entries fixed)")
