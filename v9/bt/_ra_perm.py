"""_ra_perm.py - matched random-entry placebo (permutation null) for the 4 live sleeves.
Same n, same trade geometry (risk/target in points), same eligible session+window pool,
same exit engine -> only the ENTRY TIMING is randomised. 300 draws. READ-ONLY.
"""
import sys, importlib.util
import numpy as np, pandas as pd
_s = importlib.util.spec_from_file_location("_ra_common", "/root/v9/bt/_ra_common.py")
C = importlib.util.module_from_spec(_s); sys.modules["_ra_common"] = C; _s.loader.exec_module(C)
bt = C.bt; ET = C.ET
rng = np.random.default_rng(20260827)
NDRAW = 300

E = C.load_env()
df = E["df"]; bars5 = E["bars5"]; regime = E["regime"]; idx = E["idx"]
n5 = len(bars5)
ts5 = [b[0] for b in bars5]
h5 = np.array([b[2] for b in bars5]); l5 = np.array([b[3] for b in bars5]); c5 = np.array([b[4] for b in bars5])
et5 = [t.tz_convert(ET) for t in ts5]
hm5 = np.array([t.hour * 60 + t.minute for t in et5])
date5 = np.array([t.date() for t in et5])
eod5 = hm5 >= (15 * 60 + 20)
cell5 = np.array([(regime[d].cell if d in regime else "NONE") for d in date5])
hol5 = np.array([bt.no_trade(d) for d in date5])
pos1 = np.array([idx.searchsorted(t + pd.Timedelta(minutes=5), side="left") for t in ts5])
print("[env ready] 5min=%d" % n5, flush=True)

def in_windows(hm, wins):
    m = np.zeros(len(hm), bool)
    for lo, hi in wins:
        m |= (hm >= lo) & (hm < hi)
    return m

VWIN = [(0, 360), (570, 720), (720, 870), (1140, 1440)]
TWIN = [(600, 715), (895, 920)]

def load(s):
    d = pd.read_csv("/root/v9/bt/_hist_tr_%s.csv" % s)
    d["entry_utc"] = pd.to_datetime(d["entry_utc"], utc=True)
    et = d["entry_utc"].dt.tz_convert(ET)
    d["hm"] = et.dt.hour * 60 + et.dt.minute
    d["risk"] = (d.pnl_points / d.R.replace(0, np.nan)).abs()
    d["risk"] = d["risk"].fillna(d["risk"].median())
    return d

def sim_tlb_fast(j, entry, stop, target, horizon=3000):
    a = j + 1; b = min(n5, a + horizon)
    L = l5[a:b]; H = h5[a:b]; Cc = c5[a:b]; Eo = eod5[a:b]
    BIG = 10 ** 9
    ms = L <= stop; mt = H >= target
    i_s = int(np.argmax(ms)) if ms.any() else BIG
    i_t = int(np.argmax(mt)) if mt.any() else BIG
    i_e = int(np.argmax(Eo)) if Eo.any() else BIG
    if i_s <= i_t and i_s <= i_e and i_s < BIG:
        return stop - entry
    if i_t <= i_e and i_t < BIG:
        return target - entry
    if i_e < BIG:
        return float(Cc[i_e]) - entry
    return float(Cc[-1]) - entry

SPEC = {
    "VWAP_LONG_R3":  dict(dirn="long",  cells=("BOTH_BULL", "ZONE_A"), wins=VWIN, eng="vwap"),
    "VWAP_SHORT_R1": dict(dirn="short", cells=("BOTH_BEAR",),          wins=VWIN, eng="vwap"),
    "TLB_HYB40":     dict(dirn="long",  cells=("BOTH_BULL", "ZONE_B"), wins=TWIN, eng="tlb"),
    "R3_RE":         dict(dirn="long",  cells=("BOTH_BULL", "ZONE_A"), wins=None, eng="vwap"),
}

print("\n%-14s %8s %12s %12s %10s %10s %8s" % ("sleeve", "n", "REAL net", "null mean", "null sd", "null p95", "pctile"))
out = []
for s, sp in SPEC.items():
    d = load(s)
    nT = len(d)
    real_net = float(d.pnl_usd.sum() - C.COST_RT * nT)
    wins = sp["wins"]
    if wins is None:
        wins = [(int(d.hm.min()), int(d.hm.max()) + 1)]
    pool = np.where(in_windows(hm5, wins) & np.isin(cell5, sp["cells"]) & (~hol5))[0]
    # exclude bars too close to the end of data
    pool = pool[pool < n5 - 200]
    geo = d[["risk", "R"]].copy()
    tgt_pts = (d.pnl_points / d.R).abs() * 0.0
    # target distance = r_multiple * risk; recover r_multiple per sleeve from trades that hit TARGET
    risk = d["risk"].values
    # r_multiple: use the sleeve's structural value
    RMULT = {"VWAP_LONG_R3": 3.0, "VWAP_SHORT_R1": 1.0, "TLB_HYB40": 1.0}
    if s in RMULT:
        rm = np.full(nT, RMULT[s])
    else:
        # R3_RE target is a moving VWAP/band magnet -> use each real trade's realised
        # target distance where it hit TARGET, else the sleeve median
        tt = d[d.exit_reason == "TARGET"]
        med = float(tt.R.abs().median()) if len(tt) else 1.0
        rm = np.where(d.exit_reason.values == "TARGET", d.R.abs().values, med)
    nets = np.empty(NDRAW)
    for b in range(NDRAW):
        js = rng.choice(pool, size=nT, replace=False)
        perm = rng.permutation(nT)
        rk = risk[perm]; rmm = rm[perm]
        tot = 0.0
        for t in range(nT):
            j = int(js[t]); e = float(c5[j]); r_ = float(rk[t])
            if sp["dirn"] == "long":
                stop = e - r_; targ = e + rmm[t] * r_
            else:
                stop = e + r_; targ = e - rmm[t] * r_
            if sp["eng"] == "tlb":
                pts = sim_tlb_fast(j, e, stop, targ)
            else:
                o = C.sim_vwap_fast(E, int(pos1[j]), sp["dirn"], e, stop, targ, horizon=4000)
                pts = o["pnl_points"]
            tot += pts * C.MNQ
        nets[b] = tot - C.COST_RT * nT
    pct = float((nets < real_net).mean() * 100)
    print("%-14s %8d %12s %12s %10s %10s %7.1f%%  %s"
          % (s, nT, format(real_net, ",.0f"), format(nets.mean(), ",.0f"), format(nets.std(), ",.0f"),
             format(np.percentile(nets, 95), ",.0f"), pct, "PASS" if pct >= 95 else "FAIL"), flush=True)
    out.append(dict(sleeve=s, n=nT, real=real_net, null_mean=nets.mean(), null_sd=nets.std(),
                    p95=np.percentile(nets, 95), pctile=pct))
pd.DataFrame(out).to_csv("/root/v9/bt/_ra_perm.csv", index=False)
print("DONE", flush=True)
