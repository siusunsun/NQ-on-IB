"""_ra_tlb_sweep.py - TLB_HYB40 parameter sensitivity on FULL history + era split + ungated.
READ-ONLY.
"""
import sys, importlib.util
import numpy as np, pandas as pd
_s = importlib.util.spec_from_file_location("_ra_common", "/root/v9/bt/_ra_common.py")
C = importlib.util.module_from_spec(_s); sys.modules["_ra_common"] = C; _s.loader.exec_module(C)
bt = C.bt; ET = C.ET

E = C.load_env()
bars5 = E["bars5"]; regime = E["regime"]
print("[load] 5min=%d" % len(bars5), flush=True)

vex = bt.vex if hasattr(bt, "vex") else None
TLBLive = bt.TLBLive
is_in_window = bt.is_in_window; no_trade = bt.no_trade
WIN = [(600, 715), (895, 920)]
MAXPREM = 3000.0; PTV = 2.0

n5 = len(bars5)
ts5 = [b[0] for b in bars5]
o5 = np.array([b[1] for b in bars5]); h5 = np.array([b[2] for b in bars5])
l5 = np.array([b[3] for b in bars5]); c5 = np.array([b[4] for b in bars5])
et5 = [t.tz_convert(ET) for t in ts5]
hm5 = np.array([t.hour * 60 + t.minute for t in et5])
eod5 = hm5 >= (15 * 60 + 20)
date5 = np.array([t.date() for t in et5])
year5 = np.array([t.year for t in et5])
inwin5 = np.array([is_in_window(t.time(), WIN) for t in et5])
cell5 = np.array([(regime[d].cell if d in regime else "NONE") for d in date5])
hol5 = np.array([no_trade(d) for d in date5])
# rolling 20-bar low (inclusive), the hybrid-stop source
roll20 = pd.Series(l5).rolling(20, min_periods=1).min().values

PIVK = [2, 3, 4]
sigs = {k: [] for k in PIVK}
print("[signal pass]", flush=True)
for k in PIVK:
    st = TLBLive(k=k, r_multiple=1.0)
    for j in range(n5):
        s = st.add_bar(h5[j], l5[j], c5[j])
        if s is not None:
            sigs[k].append((j, s["entry_price"], s["stop_price"]))
    print("   pivot_k=%d raw signals=%d" % (k, len(sigs[k])), flush=True)

def sim_tlb_fast(j, entry, stop, target, horizon=6000):
    a = j + 1; b = min(n5, a + horizon)
    L = l5[a:b]; H = h5[a:b]; Cc = c5[a:b]; Eo = eod5[a:b]
    BIG = 10 ** 9
    ms = L <= stop; mt = H >= target
    i_s = int(np.argmax(ms)) if ms.any() else BIG
    i_t = int(np.argmax(mt)) if mt.any() else BIG
    i_e = int(np.argmax(Eo)) if Eo.any() else BIG
    if i_s <= i_t and i_s <= i_e and i_s < BIG:
        i = i_s; px = stop; why = "STOP"
    elif i_t <= i_e and i_t < BIG:
        i = i_t; px = target; why = "TARGET"
    elif i_e < BIG:
        i = i_e; px = float(Cc[i_e]); why = "EOD"
    else:
        i = len(Cc) - 1; px = float(Cc[-1]); why = "DATA_END"
    return a + i, px - entry

def replay(pk, hyb, r, gated=True):
    trs = []; busy = -1
    for (j, entry, stop0) in sigs[pk]:
        stop = stop0
        if hyb is not None and (entry - stop0) < hyb:
            ns = roll20[j]
            if entry - ns > 0:
                stop = ns
        risk = entry - stop
        if risk <= 0:
            continue
        if not inwin5[j] or hol5[j]:
            continue
        if gated and cell5[j] not in ("BOTH_BULL", "ZONE_B"):
            continue
        if risk * PTV > MAXPREM:
            continue
        if j + 1 <= busy:
            continue
        tgt = entry + r * risk
        xj, pts = sim_tlb_fast(j, entry, stop, tgt)
        busy = xj
        trs.append((year5[j], pts * C.MNQ, date5[j]))
    return trs

def summarize(trs, tag):
    if not trs:
        return dict(tag=tag, n=0, sr=0.0)
    pnl = np.array([t[1] for t in trs]); yr = np.array([t[0] for t in trs])
    f = C.stat_block(pnl); e1 = C.stat_block(pnl[yr <= 2023]); e2 = C.stat_block(pnl[yr >= 2024])
    return dict(tag=tag, n=f["n"], net=round(f["net"]), pf=round(f["pf"], 3), sr=round(f["sr"], 4),
                n1=e1["n"], net1=round(e1["net"]), pf1=round(e1["pf"], 3),
                n2=e2["n"], net2=round(e2["net"]), pf2=round(e2["pf"], 3))

BASE = dict(pk=3, hyb=40.0, r=1.0)
GRID = dict(pk=[2, 3, 4], hyb=[None, 30.0, 40.0, 60.0], r=[0.5, 1.0, 1.5])
rows = []
print("\n[replay: one-at-a-time around pk=3 hyb=40 r=1.0]", flush=True)
for p, vals in GRID.items():
    for v in vals:
        c = dict(BASE); c[p] = v
        trs = replay(c["pk"], c["hyb"], c["r"], gated=True)
        d = summarize(trs, "TLB|%s=%s" % (p, v)); d.update(param=p, value=str(v), **c)
        rows.append(d)
        print("  %-16s n=%-4d net=%-8s pf=%-6s | e1 n=%-4d net=%-8s pf=%-6s | e2 n=%-4d net=%-8s pf=%s"
              % (d["tag"], d["n"], d["net"], d["pf"], d["n1"], d["net1"], d["pf1"],
                 d["n2"], d["net2"], d["pf2"]), flush=True)

print("\n[full 3x4x3 grid -> for DSR trial variance]", flush=True)
grid_rows = []
for pk in GRID["pk"]:
    for hyb in GRID["hyb"]:
        for r in GRID["r"]:
            trs = replay(pk, hyb, r, gated=True)
            d = summarize(trs, "pk%s_h%s_r%s" % (pk, hyb, r)); d.update(pk=pk, hyb=str(hyb), r=r)
            grid_rows.append(d)
pd.DataFrame(grid_rows).to_csv("/root/v9/bt/_ra_tlb_sweep.csv", index=False)
g = pd.DataFrame(grid_rows).sort_values("net", ascending=False)
print(g[["tag", "n", "net", "pf", "sr", "net1", "pf1", "net2", "pf2"]].to_string(index=False))

print("\n[ungated baseline]", flush=True)
trs = replay(3, 40.0, 1.0, gated=False)
d = summarize(trs, "TLB|UNGATED")
print("  %-16s n=%-4d net=%-8s pf=%-6s | e1 n=%-4d net=%-8s pf=%-6s | e2 n=%-4d net=%-8s pf=%s"
      % (d["tag"], d["n"], d["net"], d["pf"], d["n1"], d["net1"], d["pf1"],
         d["n2"], d["net2"], d["pf2"]))
pd.DataFrame(trs, columns=["year", "pnl_usd", "date"]).to_csv("/root/v9/bt/_ra_ungated_TLB.csv", index=False)

print("\n[per-year, deployed cfg]", flush=True)
trs = replay(3, 40.0, 1.0, gated=True)
pnl = np.array([t[1] for t in trs]); yr = np.array([t[0] for t in trs])
for y in sorted(set(yr)):
    b = C.stat_block(pnl[yr == y])
    print("   %d n=%-4d net=$%-8s pf=%.2f avg=$%.1f" % (y, b["n"], format(b["net"], ",.0f"), b["pf"], b["avg"]))
print("DONE", flush=True)
