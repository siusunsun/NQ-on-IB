"""_ra_wf.py - multi-fold walk-forward WITH re-optimisation for VWAP_LONG_R3 / VWAP_SHORT_R1 /
TLB_HYB40, over the FULL parameter grid on full history. 5 expanding folds.
Also emits the full-grid SR distribution used for the deflated Sharpe trial variance.
READ-ONLY.
"""
import sys, importlib.util, itertools
import numpy as np, pandas as pd
_s = importlib.util.spec_from_file_location("_ra_common", "/root/v9/bt/_ra_common.py")
C = importlib.util.module_from_spec(_s); sys.modules["_ra_common"] = C; _s.loader.exec_module(C)
bt = C.bt; vsl = C.vsl; ET = C.ET

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
roll20 = pd.Series(l5).rolling(20, min_periods=1).min().values
is_in_window = bt.is_in_window
VWIN = [(0, 360), (570, 720), (720, 870), (1140, 1440)]
TWIN = [(600, 715), (895, 920)]
inV = np.array([is_in_window(t.time(), VWIN) for t in et5])
inT = np.array([is_in_window(t.time(), TWIN) for t in et5])
print("[env] 5min=%d" % n5, flush=True)

# ---------------- VWAP signal pass over the FULL grid ----------------
KS = [1.5, 2.0, 2.5]; SLS = [15, 20, 25]
NBO_L = [4, 5, 6]; NBO_S = [2, 3, 4]
RL = [2.0, 3.0, 4.0]; RS = [0.5, 1.0, 1.5]
states = {}
for k, nb, sl in itertools.product(KS, NBO_L, SLS):
    states[("LONG", k, nb, sl)] = vsl.VwapState(side="LONG", entry_band_k=k, n_bars_outside=nb,
                                                swing_lookback=sl, post_halt_blackout_bars=24)
for k, nb, sl in itertools.product(KS, NBO_S, SLS):
    states[("SHORT", k, nb, sl)] = vsl.VwapState(side="SHORT", entry_band_k=k, n_bars_outside=nb,
                                                 swing_lookback=sl)
print("[vwap pass] states=%d" % len(states), flush=True)
sigs = {kk: [] for kk in states}
for j in range(n5):
    bts = ts5[j]; py = bts.to_pydatetime()
    bh = h5[j]; bl = l5[j]; bc = c5[j]; bv = bars5[j][5]
    for kk, st in states.items():
        s = st.on_bar(py, bh, bl, bc, bv, anchor_hour_utc=0)
        if s is not None:
            sigs[kk].append((j, s["entry_price"], s["stop_price"]))
    if j % 100000 == 0:
        print("   %d/%d" % (j, n5), flush=True)
pos1 = {}
for kk, v in sigs.items():
    for (j, _e, _s2) in v:
        if j not in pos1:
            pos1[j] = int(idx.searchsorted(ts5[j] + pd.Timedelta(minutes=5), side="left"))

def replay_vwap(side, k, nb, sl, r):
    cells = ("BOTH_BULL", "ZONE_A") if side == "LONG" else ("BOTH_BEAR",)
    dirn = "long" if side == "LONG" else "short"
    trs = []; busy = -1
    for (j, epx, spx) in sigs[(side, k, nb, sl)]:
        if hol5[j] or not inV[j] or cell5[j] not in cells:
            continue
        risk = (epx - spx) if side == "LONG" else (spx - epx)
        if risk <= 0 or risk > C.VWAP_MAX_STOP_PTS:
            continue
        p = pos1[j]
        if p <= busy:
            continue
        tgt = epx + r * risk if side == "LONG" else epx - r * risk
        o = C.sim_vwap_fast(E, p, dirn, epx, spx, tgt)
        busy = o["exit_pos"]
        trs.append((date5[j], o["pnl_usd"]))
    return trs

# ---------------- TLB grid ----------------
TLBLive = bt.TLBLive
tsig = {}
for pk in (2, 3, 4):
    st = TLBLive(k=pk, r_multiple=1.0); out = []
    for j in range(n5):
        s = st.add_bar(h5[j], l5[j], c5[j])
        if s is not None:
            out.append((j, s["entry_price"], s["stop_price"]))
    tsig[pk] = out
print("[tlb pass] done", flush=True)

def sim_tlb_fast(j, entry, stop, target, horizon=3000):
    a = j + 1; b = min(n5, a + horizon)
    L = l5[a:b]; H = h5[a:b]; Cc = c5[a:b]; Eo = eod5[a:b]
    BIG = 10 ** 9
    ms = L <= stop; mt = H >= target
    i_s = int(np.argmax(ms)) if ms.any() else BIG
    i_t = int(np.argmax(mt)) if mt.any() else BIG
    i_e = int(np.argmax(Eo)) if Eo.any() else BIG
    if i_s <= i_t and i_s <= i_e and i_s < BIG:
        return a + i_s, stop - entry
    if i_t <= i_e and i_t < BIG:
        return a + i_t, target - entry
    if i_e < BIG:
        return a + i_e, float(Cc[i_e]) - entry
    return b - 1, float(Cc[-1]) - entry

def replay_tlb(pk, hyb, r):
    trs = []; busy = -1
    for (j, entry, stop0) in tsig[pk]:
        stop = stop0
        if hyb is not None and (entry - stop0) < hyb:
            ns = roll20[j]
            if entry - ns > 0:
                stop = ns
        risk = entry - stop
        if risk <= 0 or hol5[j] or not inT[j] or cell5[j] not in ("BOTH_BULL", "ZONE_B"):
            continue
        if risk * 2.0 > 3000.0 or j + 1 <= busy:
            continue
        xj, pts = sim_tlb_fast(j, entry, stop, entry + r * risk)
        busy = xj
        trs.append((date5[j], pts * C.MNQ))
    return trs

# ---------------- build all configs ----------------
CONFIGS = {"VWAP_LONG_R3": [], "VWAP_SHORT_R1": [], "TLB_HYB40": []}
for k, nb, sl, r in itertools.product(KS, NBO_L, SLS, RL):
    CONFIGS["VWAP_LONG_R3"].append((("LONG", k, nb, sl, r), lambda a=(k, nb, sl, r): replay_vwap("LONG", *a)))
for k, nb, sl, r in itertools.product(KS, NBO_S, SLS, RS):
    CONFIGS["VWAP_SHORT_R1"].append((("SHORT", k, nb, sl, r), lambda a=(k, nb, sl, r): replay_vwap("SHORT", *a)))
for pk, hyb, r in itertools.product([2, 3, 4], [None, 30.0, 40.0, 60.0], [0.5, 1.0, 1.5]):
    CONFIGS["TLB_HYB40"].append((("TLB", pk, hyb, r), lambda a=(pk, hyb, r): replay_tlb(*a)))

DEPLOY = {"VWAP_LONG_R3": ("LONG", 2.0, 5, 20, 3.0),
          "VWAP_SHORT_R1": ("SHORT", 2.0, 3, 20, 1.0),
          "TLB_HYB40": ("TLB", 3, 40.0, 1.0)}

print("\n[evaluate all configs]", flush=True)
RES = {}
for s, cfgs in CONFIGS.items():
    RES[s] = {}
    for key, fn in cfgs:
        RES[s][key] = fn()
    print("   %s: %d configs" % (s, len(cfgs)), flush=True)

# full-grid SR distribution -> DSR trial variance
gr = []
for s in RES:
    for key, trs in RES[s].items():
        pnl = np.array([t[1] for t in trs]) if trs else np.array([0.0])
        b = C.stat_block(pnl)
        gr.append(dict(sleeve=s, cfg=str(key), n=b["n"], net=round(b["net"]), pf=round(b["pf"], 3),
                       sr=round(b["sr"], 5), deployed=int(key == DEPLOY[s])))
G = pd.DataFrame(gr); G.to_csv("/root/v9/bt/_ra_fullgrid.csv", index=False)
print("\n[full-grid SR distribution]")
for s in RES:
    g = G[G.sleeve == s]
    d = g[g.deployed == 1].iloc[0]
    rank = int((g.net > d.net).sum()) + 1
    print("   %-14s ngrid=%-3d  SR mean=%.4f sd=%.4f | deployed SR=%.4f net=$%s  RANK %d/%d  (best net=$%s)"
          % (s, len(g), g.sr.mean(), g.sr.std(ddof=1), d.sr, format(d.net, ",.0f"), rank, len(g),
             format(g.net.max(), ",.0f")))

# ---------------- walk-forward ----------------
print("\n" + "=" * 100)
print("WALK-FORWARD, 5 expanding folds, re-optimised on IS net (min 25 IS trades)")
alldates = sorted({d for s in RES for k in RES[s] for (d, _p) in RES[s][k]})
edges = [alldates[int(len(alldates) * i / 6)] for i in range(1, 6)] + [alldates[-1]]
print("fold boundaries: " + ", ".join(str(e) for e in edges))
for s in RES:
    print("\n  --- %s ---" % s)
    oos_sel = []; oos_dep = []
    for i in range(5):
        is_end = edges[i]; oos_end = edges[i + 1]
        best = None
        for key, trs in RES[s].items():
            isp = np.array([p for (d, p) in trs if d < is_end])
            if len(isp) < 25:
                continue
            b = C.stat_block(isp)
            if best is None or b["net"] > best[1]["net"]:
                best = (key, b)
        if best is None:
            print("    fold %d: no IS config" % (i + 1)); continue
        key = best[0]
        op = np.array([p for (d, p) in RES[s][key] if is_end <= d < oos_end])
        dp = np.array([p for (d, p) in RES[s][DEPLOY[s]] if is_end <= d < oos_end])
        bo = C.stat_block(op) if len(op) else dict(n=0, net=0, pf=0, avg=0)
        bd = C.stat_block(dp) if len(dp) else dict(n=0, net=0, pf=0, avg=0)
        oos_sel.append(bo["net"]); oos_dep.append(bd["net"])
        print("    fold %d IS<%s  best=%s  IS n=%d $%s PF %.2f  ->  OOS n=%d $%s PF %.2f  ||  DEPLOYED OOS n=%d $%s PF %.2f"
              % (i + 1, is_end, key, best[1]["n"], format(best[1]["net"], ",.0f"), best[1]["pf"],
                 bo["n"], format(bo["net"], ",.0f"), bo["pf"], bd["n"], format(bd["net"], ",.0f"), bd["pf"]))
    print("    SUM OOS re-optimised=$%s   SUM OOS deployed-fixed=$%s"
          % (format(sum(oos_sel), ",.0f"), format(sum(oos_dep), ",.0f")))
print("DONE", flush=True)
